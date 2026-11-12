"""Размер позиции, комиссии, конвертация в KZT. Тарифы берутся только из config."""
import math


def position(cfg, entry, sl, fx):
    r = cfg["risk"]
    cap = cfg["capital_kzt"]
    risk_kzt = cap * r["risk_pct"]
    rps = entry - sl
    if rps <= 0:
        return {"shares": 0, "error": "SL выше или равен входу"}
    by_risk = math.floor(risk_kzt / (rps * fx))
    by_cap = math.floor(cap * r["max_position_pct"] / (entry * fx))
    shares = max(0, min(by_risk, by_cap))
    notional = shares * entry
    slip = notional * r["slippage_bps"] / 10000 * 2
    fees = {}
    for name, b in cfg["brokers"].items():
        if b.get("commission_pct") is None:
            fees[name] = "тариф не задан в config"
            continue
        one_way = max(notional * b["commission_pct"] / 100, b.get("min_fee_usd") or 0)
        fx_cost = notional * (b.get("fx_spread_pct") or 0) / 100 * 2
        fees[name] = {"roundtrip_usd": round(2 * one_way + fx_cost, 2), "as_of": b.get("as_of")}
    return {"shares": shares, "notional_usd": round(notional, 2), "notional_kzt": round(notional * fx),
            "risk_usd": round(shares * rps, 2), "risk_kzt": round(shares * rps * fx),
            "slippage_usd": round(slip, 2), "fees": fees, "fx": fx,
            "limited_by": "риск" if by_risk <= by_cap else "макс. доля позиции"}
