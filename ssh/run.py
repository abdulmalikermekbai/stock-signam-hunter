"""CLI: python -m ssh.run scan|news|commands|analyze TICKER|backtest [--dry]"""
import argparse
import datetime as dt
import json
import logging
import pathlib

import yaml

from . import data, engine, fundamentals, news as news_mod, telegram as tg, tracker

DATA = pathlib.Path("data")
DOCS = pathlib.Path("docs")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ssh.run")


def load_cfg(path="config.yaml"):
    cfg = yaml.safe_load(open(path, encoding="utf-8"))
    cfg["halal"]["confirmed"] = {**(cfg["halal"].get("confirmed") or {}), **tg.load_halal()}
    return cfg


def universe(cfg):
    wl = DATA / "watchlist.json"
    extra = json.loads(wl.read_text()) if wl.exists() else []
    return sorted(set(cfg["universe"]["tickers"]) | set(extra))


def analyze_one(t, df, cfg, fx, bench=None):
    tech = engine.technical_setup(df, cfg)
    if not tech.get("ok"):
        return None
    info = data.info(t)
    name = info.get("shortName") or info.get("longName")
    items, _ = news_mod.fetch(t, name)
    ver = news_mod.verify(items, cfg)
    ins = data.insider_buys_90d(t)
    sig = engine.evaluate(t, tech, fundamentals.score(info, cfg), fundamentals.halal(t, info, cfg),
                          ver, news_mod.news_score(ver), data.earnings_date(t), fx, cfg, name=name,
                          bench=bench, insider=ins)
    sig["insider_buys_90d"] = ins
    return sig


def scan(cfg, dry=False, tickers=None):
    tickers = tickers or universe(cfg)
    frames, status = data.download(list(tickers) + ["SPY"])
    spy = frames.pop("SPY", None)
    bench = {"ret63": float(spy["Close"].iloc[-1] / spy["Close"].iloc[-64] - 1)} if spy is not None and len(spy) > 64 else None
    if not frames:
        log.error("нет рыночных данных — скан прерван")
        tg.send("❗ Скан не выполнен: провайдер данных не вернул котировки.", dry)
        _save({"signals": [], "meta": {"generated_at": _now(), "provider": status}})
        return []
    fx, fx_src = data.usdkzt(cfg["fx_fallback_usdkzt"])
    # стадия 1: дешёвый технический отбор
    pre = []
    for t, df in frames.items():
        try:
            tech = engine.technical_setup(df, cfg)
            if tech.get("ok") and (tech["setup"] or tech["acc"]["score"] >= cfg["thresholds"]["watchlist_accumulation"]):
                pre.append((t, tech["tech_score"]))
        except Exception as e:  # noqa: BLE001
            log.warning("%s: ошибка техники %s", t, e)
    pre = [t for t, _ in sorted(pre, key=lambda x: -x[1])[: cfg["universe"]["max_deep_candidates"]]]
    log.info("глубокий анализ: %s", pre)
    sigs = []
    for t in pre:
        try:
            s = analyze_one(t, frames[t], cfg, fx, bench)
            if s:
                sigs.append(s)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: ошибка анализа %s", t, e)
    order = {"STRONG_BUY": 0, "BUY": 1, "WAIT": 2, "WATCHLIST": 3, "AVOID": 4}
    sigs.sort(key=lambda s: (order[s["status"]], -s["six"]["rank"]))
    meta = {"generated_at": _now(), "provider": status, "fx": {"usdkzt": fx, "source": fx_src},
            "universe": len(tickers), "analyzed": len(frames), "deep": len(pre)}
    _save({"signals": sigs, "meta": meta})
    _history(sigs)
    perf = tracker.update(frames)
    st = tg.load_state()
    today = dt.date.today().isoformat()
    sent = 0
    for s in sigs:
        if s["status"] in ("BUY", "STRONG_BUY"):
            key = tg.dedupe_key("sig", s["ticker"], s["status"], s["technical"]["setup"]["level"], today)
            sent += tg.send_once(st, key, tg.fmt_signal(s), dry)
    for row in perf["rows"]:  # өз BUY-ларың SL/TP1-ге тиген кезде хабар
        if row["kind"] == "live" and row["state"] in ("SL", "TP1"):
            tg.send_once(st, tg.dedupe_key("close", row["ticker"], row["date"], row["state"]),
                         f"{'🎯' if row['state'] == 'TP1' else '🛑'} {row['ticker']}: {row['state']} "
                         f"({row['r']:+}R) — вход {row['entry']}, {row['bars']} бар ішінде", dry)
    tg.send_once(st, tg.dedupe_key("digest", today), tg.fmt_digest(sigs, today), dry)
    tg.save_state(st)
    return sigs


def news_monitor(cfg, dry=False):
    st = tg.load_state()
    now = dt.datetime.now(dt.timezone.utc)
    for t in universe(cfg):
        try:
            items, status = news_mod.fetch(t, None, now)
            for v in news_mod.verify(items, cfg, now):
                if v["high_impact"] and v["status"] in ("VERIFIED", "CORROBORATED"):
                    key = tg.dedupe_key("news", t, v["title"][:60])
                    lat = f"{v['latency_min']} мин" if v["latency_min"] is not None else "?"
                    tg.send_once(st, key, f"🗞 {t} [{v['status']}] {v['sentiment']}\n{v['title']}\n"
                                 f"Источники: {', '.join(v['sources'][:3])}\nЗадержка обнаружения: {lat}\n"
                                 f"{v['links'][0]}", dry)
        except Exception as e:  # noqa: BLE001
            log.warning("news %s: %s", t, e)
    tg.save_state(st)


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _save(payload):
    DATA.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    s = json.dumps(payload, ensure_ascii=False, default=str, indent=1)
    (DATA / "signals.json").write_text(s, encoding="utf-8")
    (DOCS / "signals.json").write_text(s, encoding="utf-8")


def _history(sigs):
    with open(DATA / "history.jsonl", "a", encoding="utf-8") as f:  # хранится ВСЁ, включая WAIT/WATCHLIST
        for s in sigs:
            s2 = {k: s[k] for k in ("ticker", "status", "composite", "generated_at", "gates_failed", "six", "six")}
            s2["setup"] = s["technical"].get("setup")
            s2["last_bar"] = s["technical"]["ind"]["last_bar"]
            s2["only_halal_blocks"] = bool(s.get("only_halal_blocks"))
            f.write(json.dumps(s2, ensure_ascii=False, default=str) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "news", "commands", "analyze", "backtest"])
    ap.add_argument("ticker", nargs="?")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--config", default="config.yaml")
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    if a.cmd == "scan":
        scan(cfg, a.dry)
    elif a.cmd == "news":
        news_monitor(cfg, a.dry)
    elif a.cmd == "commands":
        tg.process_commands(cfg, a.dry)
    elif a.cmd == "analyze":
        t = a.ticker.upper()
        frames, _ = data.download([t])
        fx, _ = data.usdkzt(cfg["fx_fallback_usdkzt"])
        s = analyze_one(t, frames[t], cfg, fx) if t in frames else None
        print(tg.fmt_signal(s) if s and s["technical"].get("setup") and s["technical"]["setup"].get("type") == "level_retest"
              else json.dumps(s, ensure_ascii=False, default=str, indent=1))
    elif a.cmd == "backtest":
        from . import backtest
        backtest.run(cfg, universe(cfg))


if __name__ == "__main__":
    main()
