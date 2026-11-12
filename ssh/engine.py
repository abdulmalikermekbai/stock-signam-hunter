"""Signal Engine: технический сетап + жёсткие гейты + статусы BUY/WAIT/WATCHLIST/AVOID."""
import datetime as dt

import numpy as np

from . import risk as risk_mod, scoring
from .indicators import atr, ema, rel_volume, rolling_vwap, rsi, weekly, macd
from .levels import accumulation, breakout_retest, false_breakout, find_levels, gaps


def technical_setup(df, cfg: dict) -> dict:
    t = cfg["technical"]
    d = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    if len(d) < 150:
        return {"ok": False, "reason": "мало истории (<150 баров)"}
    close = d["Close"]
    a = float(atr(d).iloc[-1])
    price = float(close.iloc[-1])
    e20, e50, e200 = ema(close, 20), ema(close, 50), ema(close, 200)
    vw = rolling_vwap(d, 20)
    r = float(rsi(close).iloc[-1])
    rv_s = rel_volume(d["Volume"])
    m_line, m_sig, _ = macd(close)
    wk = weekly(d)
    wema = ema(wk["Close"], 10)
    weekly_up = bool(len(wk) > 12 and wk["Close"].iloc[-1] > wema.iloc[-1] and wema.iloc[-1] >= wema.iloc[-4])
    levels = find_levels(d, tol=t["level_tol"])
    acc = accumulation(d)
    gp = gaps(d)
    ind = {"price": round(price, 2), "atr": round(a, 3), "atr_pct": round(a / price * 100, 2),
           "ema20": round(float(e20.iloc[-1]), 2), "ema50": round(float(e50.iloc[-1]), 2),
           "ema200": round(float(e200.iloc[-1]), 2), "vwap20": round(float(vw.iloc[-1]), 2),
           "rsi": round(r, 1), "rel_volume": round(float(rv_s.iloc[-1]), 2),
           "macd_hist_pos": bool(m_line.iloc[-1] > m_sig.iloc[-1]), "weekly_up": weekly_up,
           "avg_volume_20": int(d["Volume"].tail(20).mean()),
           "last_bar": str(d.index[-1].date()),
           "spark": [round(float(x), 2) for x in close.tail(90)],
           "ret63": round(float(price / close.iloc[-64] - 1), 4) if len(close) > 64 else None,
           "above_ema50": bool(price > e50.iloc[-1]), "above_ema200": bool(price > e200.iloc[-1]),
           "ema50_rising": bool(e50.iloc[-1] > e50.iloc[-10]),
           "dist_high_pct": round(float((1 - price / d["High"].tail(252).max()) * 100), 1),
           "note_qqe": "QQE не реализован (нет утверждённой формулы)"}
    best = None
    for L in levels:
        if L.price < price and price - L.price <= 3 * a:
            br = breakout_retest(d, L.price, a)
            if br["state"] == "retest_confirmed" and (best is None or L.touches > best[0].touches):
                best = (L, br)
    res = [L for L in levels if L.price > price * 1.01]
    near = min(levels, key=lambda L: abs(L.price - price)) if levels else None
    fb = false_breakout(d, near.price, a) if near and abs(near.price - price) <= 3 * a else None
    base = {"ok": True, "ind": ind, "acc": acc, "gaps": gp[-3:], "false_breakout": fb,
            "levels": [{"price": round(l.price, 2), "touches": l.touches, "mirror": l.flipped} for l in levels
                       if abs(l.price - price) / price < 0.25],
            "setup": None}
    # ранний пробой (WATCHLIST): цена под сильным сопротивлением, накопление, EMA50 растёт
    early = next((L for L in res if (L.price - price) / price <= 0.03 and L.touches >= 3), None)
    if best is None:
        if early and acc["score"] >= 50 and e50.iloc[-1] > e50.iloc[-10]:
            base["setup"] = {"type": "early_breakout", "level": round(early.price, 2)}
        base["tech_score"] = min(100, int(acc["score"] * 0.5 + (20 if weekly_up else 0) + (15 if price > e50.iloc[-1] else 0)))
        return base
    L, br = best
    sl = L.price - 1.0 * a
    risk_ps = price - sl
    tps = [round(x.price, 2) for x in res[:3]]
    src = "levels"
    mults = [2, 3, 4]
    while len(tps) < 3:
        proj = round(price + risk_ps * mults[len(tps)], 2)
        if tps and proj <= tps[-1]:
            proj = round(tps[-1] + risk_ps, 2)  # цели строго по возрастанию
        tps.append(proj)
        src = "levels+ATR-проекция"
    tps = sorted(tps)
    vol_ok = bool(d["Volume"].iloc[br["k"]] / d["Volume"].iloc[br["k"] - 20: br["k"]].mean() >= 1.2)
    score = 30
    score += 15 if (price > e50.iloc[-1] and price > vw.iloc[-1]) else 0
    score += 10 if e50.iloc[-1] > e50.iloc[-10] else 0
    score += 15 if weekly_up else 0
    score += 10 if vol_ok else 0
    score += 10 if 45 <= r <= 70 else 0
    score += int(acc["score"] * 0.1)
    score -= 20 if (fb and fb["bias"] == "bearish") else 0
    score = max(0, min(100, score))
    base.update({
        "tech_score": score, "volume_confirm": vol_ok,
        "setup": {"type": "level_retest", "level": round(L.price, 2), "touches": L.touches, "mirror": L.flipped,
                  "entry": round(price, 2), "entry_zone": [round(L.price, 2), round(L.price + 0.5 * a, 2)],
                  "sl": round(sl, 2), "sl_atr": round(risk_ps / a, 2), "tps": tps, "tp_source": src,
                  "rr_tp1": round((tps[0] - price) / risk_ps, 2), "rr_tp2": round((tps[1] - price) / risk_ps, 2),
                  "hold": "2–8 недель (swing)"},
        "price_above_vwap_ema50": bool(price > e50.iloc[-1] and price > vw.iloc[-1]),
        "ema50_rising": bool(e50.iloc[-1] > e50.iloc[-10]),
    })
    return base


def evaluate(ticker, tech, fund, halal, news_verified, news_sc, earnings, fx, cfg, today=None, name=None,
             bench=None, insider=None):
    """Собирает итоговый сигнал. Любой провал гейта запрещает BUY."""
    sig = _evaluate(ticker, tech, fund, halal, news_verified, news_sc, earnings, fx, cfg, today, name)
    sig["six"] = scoring.all_scores(sig["composite"], tech, fund, halal, news_verified, earnings, insider,
                                    bench, cfg, today or dt.date.today())
    return sig


def _evaluate(ticker, tech, fund, halal, news_verified, news_sc, earnings, fx, cfg, today=None, name=None):
    today = today or dt.date.today()
    w, th = cfg["weights"], cfg["thresholds"]
    s = tech.get("setup")
    acc_score = tech["acc"]["score"]
    comp = (w["technical"] * tech["tech_score"] + w["fundamental"] * fund["score"]
            + w["news"] * news_sc + w["accumulation"] * acc_score)
    sig = {"ticker": ticker, "name": name, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "composite": round(comp, 1),
           "scores": {"technical": tech["tech_score"], "fundamental": fund["score"], "news": news_sc,
                      "accumulation": acc_score},
           "technical": tech, "fundamentals": fund, "halal": halal,
           "news": news_verified[:5], "earnings_date": str(earnings) if earnings else None,
           "gates_failed": [], "reasons": []}
    if not s:
        sig["status"] = "AVOID" if tech["acc"]["distribution"] or halal["status"] == "NOT_HALAL" else "WATCHLIST"
        return sig
    if s["type"] == "early_breakout":
        sig["status"] = "WATCHLIST"
        sig["reasons"].append(f"цена у сопротивления {s['level']}, накопление {acc_score}/100 — вход не подтверждён")
        return sig
    g = sig["gates_failed"]
    ind = tech["ind"]
    last_bar = dt.date.fromisoformat(ind["last_bar"])
    if (today - last_bar).days > 5:
        g.append("нет актуальных котировок")
    if halal["status"] != "HALAL":
        g.append(f"халяль: {halal['status']}")
    if not fund["gate_ok"]:
        g.append("не пройден фундаментальный чек-лист / мало данных")
    if ind["avg_volume_20"] < cfg["technical"]["min_avg_volume"]:
        g.append("недостаточная ликвидность")
    if ind["atr_pct"] < cfg["technical"]["min_atr_pct"]:
        g.append("ATR% слишком мал для swing")
    if s["sl_atr"] > cfg["risk"]["max_stop_atr"]:
        g.append(f"стоп шире {cfg['risk']['max_stop_atr']} ATR")
    if s["rr_tp1"] < cfg["risk"]["min_rr"]:
        g.append(f"RR до TP1 = {s['rr_tp1']} < {cfg['risk']['min_rr']}")
    if earnings and 0 <= (earnings - today).days <= cfg["risk"]["earnings_blackout_days"]:
        g.append(f"отчёт {earnings} — gap risk")
    if any(n["status"] == "FALSE" and n["high_impact"] for n in news_verified):
        g.append("критическая новость опровергнута")
    if not (tech["price_above_vwap_ema50"] and tech["ema50_rising"]):
        g.append("цена не выше VWAP/EMA50 либо EMA50 не растёт")
    pos = risk_mod.position(cfg, s["entry"], s["sl"], fx)
    sig["risk"] = pos
    if pos.get("shares", 0) < 1:
        g.append("капитал не позволяет открыть позицию с заданным риском")
    sig["reasons"] += [f"ретест уровня {s['level']} ({s['touches']} касаний{', зеркальный' if s['mirror'] else ''})",
                       f"weekly тренд {'вверх' if ind['weekly_up'] else 'не вверх'}",
                       f"объём пробоя {'подтверждён' if tech['volume_confirm'] else 'не подтверждён'}"]
    if not g and comp >= th["buy"]:
        sig["status"] = "STRONG_BUY" if comp >= th["strong_buy"] else "BUY"
    elif g:
        sig["status"] = "WAIT"
        sig["only_halal_blocks"] = bool(comp >= th["buy"] and all(x.startswith("халяль") for x in g))
    else:
        sig["status"] = "WATCHLIST"
        sig["reasons"].append(f"композитный балл {comp:.0f} < {th['buy']}")
    return sig
