"""Telegram: отправка, дедупликация, команды через getUpdates (без вебхука, работает в GitHub Actions)."""
import hashlib
import json
import logging
import os
import pathlib

import requests

log = logging.getLogger("ssh.telegram")
DATA = pathlib.Path("data")
DISCLAIMER = "\n\n⚠️ Аналитический инструмент, не финансовая рекомендация. Доходность не гарантируется."


def _creds():
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")


def send(text: str, dry: bool = False) -> bool:
    token, chat = _creds()
    if dry or not token or not chat:
        print("---- [DRY / нет токена] ----\n" + text)
        return False
    ok = True
    for i in range(0, len(text), 3900):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text[i:i + 3900], "disable_web_page_preview": True},
                          timeout=20)
        ok &= r.ok
        if not r.ok:
            log.error("telegram %s %s", r.status_code, r.text[:200].replace(token, "***"))
    return ok


def load_state():
    p = DATA / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"sent": [], "offset": 0}


def save_state(st):
    DATA.mkdir(exist_ok=True)
    st["sent"] = st["sent"][-1000:]
    (DATA / "state.json").write_text(json.dumps(st, ensure_ascii=False))


def dedupe_key(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def send_once(st, key, text, dry=False) -> bool:
    if key in st["sent"]:
        return False
    sent = send(text, dry)
    if sent or dry:
        st["sent"].append(key)
    return sent


def fmt_signal(s: dict, tz_label: str = "Алматы") -> str:
    t, f, h = s["technical"], s["fundamentals"], s["halal"]
    ind, st = t["ind"], t["setup"]
    z = s["six"]
    L = [f"📈 STOCK SIGNAL HUNTER AI\n{s['ticker']} — {s.get('name') or ''}\nСтатус: {s['status']} | рейтинг {z['rank']}",
         f"Signal {z['signal']} | Halal {z['halal']} | Momentum {z['momentum']} | Entry {z['entry']} | "
         f"Catalyst {z['catalyst']} | Risk {z['risk']} (100 = төмен тәуекел)",
         f"Время UTC: {s['generated_at'][:16]} | данные до {ind['last_bar']}", ""]
    L.append("1) ПОЧЕМУ: " + "; ".join(s["reasons"]))
    L.append(f"2) ТЕХНИКА: цена {ind['price']}, EMA50 {ind['ema50']}, VWAP20 {ind['vwap20']}, RSI {ind['rsi']}, "
             f"ATR {ind['atr']} ({ind['atr_pct']}%), RelVol {ind['rel_volume']}, weekly {'↑' if ind['weekly_up'] else '–'}")
    L.append(f"   Накопление: {t['acc']['score']}/100 (диапазон {t['acc']['range_pct']}%, vol-ratio {t['acc']['vol_ratio']})")
    L.append(f"3) ФУНДАМЕНТАЛ: {f['passed']}/{f['available']} проверок, балл {f['score']}")
    if st and st.get("type") == "level_retest":
        L.append(f"4) ПЛАН: вход {st['entry']} (зона {st['entry_zone'][0]}–{st['entry_zone'][1]}), SL {st['sl']} "
                 f"({st['sl_atr']} ATR)\n   TP1 {st['tps'][0]} / TP2 {st['tps'][1]} / TP3 {st['tps'][2]} "
                 f"[{st['tp_source']}], RR(TP1) {st['rr_tp1']}")
    r = s.get("risk")
    if r:
        L.append(f"5) РИСК: {r['shares']} шт., риск ≈ {r['risk_kzt']:,} ₸ (${r['risk_usd']}), позиция ≈ {r['notional_kzt']:,} ₸; "
                 f"курс {r['fx']:.1f}; комиссии: {r['fees']}")
    L.append(f"6) ХАЛЯЛЬ: {h['status']} — {h['reason']}")
    if s["news"]:
        n = s["news"][0]
        L.append(f"7) НОВОСТЬ: [{n['status']}] {n['title']} ({', '.join(n['sources'][:3])})")
    if s["gates_failed"]:
        L.append("⛔ ГЕЙТЫ: " + "; ".join(s["gates_failed"]))
    return "\n".join(L) + DISCLAIMER


def fmt_digest(sigs, today) -> str:
    """Толық тізім: ешбір компания қиылмайды (ұзын болса send() бөліктерге бөледі)."""
    cnt = lambda *st: sum(s["status"] in st for s in sigs)
    ready = [s for s in sigs if s.get("only_halal_blocks")]
    L = [f"📊 Дайджест {today}: BUY={cnt('BUY', 'STRONG_BUY')}, WAIT={cnt('WAIT')}, "
         f"WATCHLIST={cnt('WATCHLIST')}, AVOID={cnt('AVOID')} (барлығы {len(sigs)})",
         "Балл: Signal/Halal/Momentum/Entry/Catalyst/Risk (Risk 100 = қауіпсіз)", ""]
    if ready:
        L.append("🟡 СЕТАП ДАЙЫН — тек халяль расталмаған. Musaffa/Zoya-дан тексеріп, /halal TICKER деп жазыңыз:")
        for s in ready:
            st, r = s["technical"]["setup"], s.get("risk") or {}
            L.append(f"• {s['ticker']} #{s['six']['rank']} | вход {st['entry']} SL {st['sl']} TP1 {st['tps'][0]} "
                     f"RR {st['rr_tp1']} | {r.get('shares', 0)} дана ≈ {r.get('notional_kzt', 0):,} ₸")
        L.append("")
    for grp, title in (("BUY", "✅ BUY"), ("WAIT", "⏳ WAIT"), ("WATCHLIST", "👀 WATCHLIST"), ("AVOID", "🚫 AVOID")):
        keys = ("BUY", "STRONG_BUY") if grp == "BUY" else (grp,)
        rows = [s for s in sigs if s["status"] in keys and not (grp == "WAIT" and s.get("only_halal_blocks"))]
        if not rows:
            continue
        L.append(title)
        for s in rows:
            z = s["six"]
            why = f" ⛔ {s['gates_failed'][0]}" if s["gates_failed"] else ""
            L.append(f"• {s['ticker']} #{z['rank']} | {z['signal']}/{z['halal']}/{z['momentum']}/{z['entry']}/{z['catalyst']}/{z['risk']}{why}")
        L.append("")
    return "\n".join(L) + DISCLAIMER


def load_halal() -> dict:
    p = DATA / "halal_confirmed.json"
    return json.loads(p.read_text()) if p.exists() else {}


def set_halal(ticker: str, on: bool, today=None) -> dict:
    import datetime as dt
    h = load_halal()
    if on:
        h[ticker] = (today or dt.date.today()).isoformat()
    else:
        h.pop(ticker, None)
    DATA.mkdir(exist_ok=True)
    (DATA / "halal_confirmed.json").write_text(json.dumps(h, ensure_ascii=False, indent=1))
    return h


def fmt_watch(s):
    return f"👀 WATCHLIST {s['ticker']} | балл {s['composite']} | {'; '.join(s['reasons']) or 'накопление/ожидание входа'}"


def process_commands(cfg, dry=False):
    """/start /help /signals /watchlist /add /remove /status — только от TELEGRAM_CHAT_ID."""
    token, chat = _creds()
    if not token or not chat:
        return
    st = load_state()
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                     params={"offset": st.get("offset", 0), "timeout": 0}, timeout=20)
    if not r.ok:
        return
    wl_path = DATA / "watchlist.json"
    wl = json.loads(wl_path.read_text()) if wl_path.exists() else []
    for u in r.json().get("result", []):
        st["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != str(chat):
            continue  # чужие игнорируем
        txt = (m.get("text") or "").strip()
        if not txt:
            continue
        cmd, *arg = txt.split()
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            send("Командалар: /signals /watchlist /add TICKER /remove TICKER /status\n"
                 "/halal TICKER — халялды өзіңіз тексергеннен кейін растау (Musaffa/Zoya/10-K)\n"
                 "/unhalal TICKER — растауды алып тастау, /halal — расталғандар тізімі\n"
                 "Команданы бот ~15 мин ішінде өңдейді (GitHub Actions кестесі).", dry)
        elif cmd == "/signals":
            p = DATA / "signals.json"
            sigs = json.loads(p.read_text()).get("signals", []) if p.exists() else []
            top = [s for s in sigs if s["status"] in ("BUY", "STRONG_BUY")]
            send("\n\n".join(fmt_signal(s) for s in top[:3]) if top else "Подтверждённых сигналов нет (это нормально).", dry)
        elif cmd == "/watchlist":
            send("Watchlist: " + ", ".join(cfg["universe"]["tickers"] + wl), dry)
        elif cmd == "/add" and arg:
            t = arg[0].upper()
            if t not in wl:
                wl.append(t)
            send(f"Добавлено {t} (попадёт в следующий скан)", dry)
        elif cmd == "/remove" and arg:
            t = arg[0].upper()
            wl = [x for x in wl if x != t]
            send(f"Удалено {t} из личного списка", dry)
        elif cmd == "/halal":
            if arg:
                t = arg[0].upper()
                set_halal(t, True)
                send(f"✅ {t}: халяль қолмен расталды (бүгін). Бот әлі де қаржылық шектерді өзі тексереді. "
                     f"Растау 90 күннен кейін ескіреді.", dry)
            else:
                h = load_halal()
                send("Расталған: " + (", ".join(f"{k} ({v})" for k, v in h.items()) or "жоқ"), dry)
        elif cmd == "/unhalal" and arg:
            set_halal(arg[0].upper(), False)
            send(f"{arg[0].upper()}: растау алынды", dry)
        elif cmd == "/status":
            p = DATA / "signals.json"
            meta = json.loads(p.read_text()).get("meta", {}) if p.exists() else {}
            send(f"Последний скан: {meta.get('generated_at', 'нет данных')}\nПровайдер: {meta.get('provider', {}).get('ok')}", dry)
    DATA.mkdir(exist_ok=True)
    wl_path.write_text(json.dumps(wl))
    save_state(st)
