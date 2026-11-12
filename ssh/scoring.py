"""Алты тәуелсіз балл (0-100). Risk Score: 100 = ең төмен тәуекел.
Halal — гейт, рейтингке салмақпен қосылмайды (жоғары техника халялды алмастырмайды)."""
import datetime as dt


def clip(x):
    return int(max(0, min(100, round(x))))


def halal_score(h: dict, cfg: dict) -> int:
    st, c = h["status"], h.get("calc", {})
    if st == "NOT_HALAL":
        return 0
    d, k = c.get("debt_to_mcap"), c.get("cash_to_mcap")
    if d is None or k is None:
        return 20 if st == "NEEDS_REVIEW" else 30
    hc = cfg["halal"]
    numeric = clip(100 * (1 - max(d / hc["max_debt_to_mcap"], k / hc["max_cash_to_mcap"])))
    if st == "HALAL":
        return clip(70 + 0.3 * numeric)
    if st == "NEEDS_REVIEW":
        return clip(0.6 * numeric)  # қолмен расталмағанша 60-тан аспайды
    return clip(0.4 * numeric)      # EXPIRED


def momentum_score(ind: dict, bench: dict | None) -> int:
    rs = 50.0
    if bench and ind.get("ret63") is not None and bench.get("ret63") is not None:
        rs = clip(50 + (ind["ret63"] - bench["ret63"]) * 250)  # +20% vs SPY -> 100
    trend = 25 * sum([bool(ind.get("above_ema50")), bool(ind.get("above_ema200")),
                      bool(ind.get("ema50_rising")), bool(ind.get("weekly_up"))])
    rv = ind.get("rel_volume") or 0
    vol = 50 if rv >= 1.2 else min(50, rv * 40)
    dh = ind.get("dist_high_pct")
    hi = 50 if dh is not None and dh <= 10 else (max(0, 50 - (dh - 10) * 2) if dh is not None else 25)
    return clip(0.35 * rs + 0.35 * trend + 0.30 * (vol + hi))


def entry_score(tech: dict) -> int:
    s, ind = tech.get("setup"), tech.get("ind", {})
    if not s:
        return clip(10 + (10 if ind.get("above_ema50") else 0) + (10 if ind.get("weekly_up") else 0))
    if s["type"] == "early_breakout":
        return clip(45 + (10 if tech.get("acc", {}).get("score", 0) >= 60 else 0))
    v = 60 + min(20, max(0, (s.get("rr_tp1", 0) - 2) * 10))
    v += 10 if tech.get("volume_confirm") else 0
    v += 10 if tech.get("price_above_vwap_ema50") else 0
    v -= 20 if s.get("sl_atr", 0) > 2 else 0
    return clip(v)


def catalyst_score(news: list, earnings, insider, today: dt.date) -> int:
    v = 30
    hi_bull = sum(1 for n in news if n["status"] in ("VERIFIED", "CORROBORATED") and n["high_impact"] and n["sentiment"] == "bullish")
    hi_bear = sum(1 for n in news if n["status"] in ("VERIFIED", "CORROBORATED") and n["high_impact"] and n["sentiment"] == "bearish")
    oth_bull = sum(1 for n in news if n["status"] in ("VERIFIED", "CORROBORATED") and not n["high_impact"] and n["sentiment"] == "bullish")
    v += min(50, 25 * hi_bull) - 25 * hi_bear + min(20, 10 * oth_bull)
    if earnings:
        days = (earnings - today).days
        v += 15 if 8 <= days <= 30 else (10 if 0 <= days <= 7 else 0)  # 0-7 күн — гейтте gap risk ретінде бөлек
    if insider:
        v += 20 if insider >= 3 else 10
    return clip(v)


def risk_score(tech: dict, fund: dict, earnings, today: dt.date) -> int:
    ind, s = tech.get("ind", {}), tech.get("setup")
    v = 100.0
    ap = ind.get("atr_pct", 0)
    if ap > 5:
        v -= (ap - 5) * 6
    if tech.get("acc", {}).get("distribution"):
        v -= 25
    if s and s.get("sl_atr", 0) > 1.5:
        v -= (s["sl_atr"] - 1.5) * 30
    if earnings and 0 <= (earnings - today).days <= 7:
        v -= 25
    v -= (1 - fund.get("score", 0) / 100) * 25
    if ind.get("above_ema200") is False:
        v -= 15
    if (ind.get("dist_high_pct") or 0) > 35:
        v -= 10
    return clip(v)


def all_scores(signal, tech, fund, halal, news, earnings, insider, bench, cfg, today):
    ind = tech.get("ind", {})
    six = {"signal": clip(signal), "halal": halal_score(halal, cfg), "momentum": momentum_score(ind, bench),
           "entry": entry_score(tech), "catalyst": catalyst_score(news, earnings, insider, today),
           "risk": risk_score(tech, fund, earnings, today)}
    six["rank"] = round(0.40 * six["signal"] + 0.25 * six["entry"] + 0.15 * six["momentum"]
                        + 0.10 * six["catalyst"] + 0.10 * six["risk"], 1)
    return six
