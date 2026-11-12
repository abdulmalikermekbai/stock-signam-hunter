"""Фундаментальный чек-лист (из PDF) + AAOIFI-скрининг. N/A никогда не считается пройденным."""
import datetime as dt


def _g(info, k):
    v = info.get(k)
    return None if v is None or v != v else v


def score(info: dict, cfg: dict) -> dict:
    f = cfg["fundamentals"]
    de = _g(info, "debtToEquity")
    de = de / 100 if de is not None else None  # Yahoo отдаёт в процентах
    checks = [
        ("Revenue growth YoY > 0", _g(info, "revenueGrowth"), lambda v: v > 0),
        ("Earnings growth YoY > 0 (прокси EPS/NetIncome)", _g(info, "earningsGrowth"), lambda v: v > 0),
        (f"P/E > {f['pe_min']}", _g(info, "trailingPE"), lambda v: v > f["pe_min"]),
        (f"P/B > {f['pb_min']}", _g(info, "priceToBook"), lambda v: v > f["pb_min"]),
        (f"ROE > {f['roe_min']:.0%}", _g(info, "returnOnEquity"), lambda v: v > f["roe_min"]),
        (f"ROA > {f['roa_min']:.0%}", _g(info, "returnOnAssets"), lambda v: v > f["roa_min"]),
        (f"Debt/Equity < {f['de_max']}", de, lambda v: v < f["de_max"]),
        (f"Current Ratio > {f['current_ratio_min']}", _g(info, "currentRatio"), lambda v: v > f["current_ratio_min"]),
        (f"Quick Ratio > {f['quick_ratio_min']}", _g(info, "quickRatio"), lambda v: v > f["quick_ratio_min"]),
        ("Free Cash Flow > 0", _g(info, "freeCashflow"), lambda v: v > 0),
        ("Avg volume > min", _g(info, "averageVolume"), lambda v: v > cfg["technical"]["min_avg_volume"]),
    ]
    rows, avail, passed = [], 0, 0
    for name, val, fn in checks:
        if val is None:
            rows.append({"check": name, "value": "N/A", "ok": None})
            continue
        ok = bool(fn(val))
        avail += 1
        passed += ok
        rows.append({"check": name, "value": round(float(val), 4), "ok": ok})
    ratio = passed / avail if avail else 0
    return {"score": round(ratio * 100), "available": avail, "passed": passed, "rows": rows,
            "gate_ok": avail >= f["min_available"] and ratio >= f["min_pass_ratio"],
            "note": "ROI в бесплатных данных недоступен (N/A). Growth — квартальный YoY по Yahoo."}


def halal(ticker: str, info: dict, cfg: dict, today: dt.date | None = None) -> dict:
    h = cfg["halal"]
    today = today or dt.date.today()
    industry = f"{info.get('industry', '')} {info.get('sector', '')}".lower()
    mcap = _g(info, "marketCap")
    debt, cash = _g(info, "totalDebt"), _g(info, "totalCash")
    calc = {"industry": industry.strip() or "N/A"}
    bad = [k for k in h["prohibited_keywords"] if k in industry]
    if bad:
        return {"status": "NOT_HALAL", "reason": f"запрещённая отрасль: {bad}", "calc": calc}
    if not mcap or debt is None or cash is None:
        return {"status": "NEEDS_REVIEW", "reason": "нет данных по капитализации/долгу/кэшу", "calc": calc}
    d_r, c_r = debt / mcap, cash / mcap
    calc.update({"debt_to_mcap": round(d_r, 3), "cash_to_mcap": round(c_r, 3),
                 "method": "AAOIFI-подход; текущая капитализация вместо 36-мес. средней"})
    if d_r >= h["max_debt_to_mcap"] or c_r >= h["max_cash_to_mcap"]:
        return {"status": "NOT_HALAL", "reason": "превышены финансовые пороги", "calc": calc}
    conf = h.get("confirmed", {}).get(ticker)
    if not conf:
        return {"status": "NEEDS_REVIEW", "calc": calc,
                "reason": "доля процентного дохода (<5%) не проверяется автоматически; подтвердите вручную "
                          "(Musaffa/Zoya/10-K) и добавьте в config halal.confirmed"}
    age = (today - dt.date.fromisoformat(str(conf))).days
    if age > h["validity_days"]:
        return {"status": "EXPIRED", "reason": f"проверка устарела ({age} дн.)", "checked": str(conf), "calc": calc}
    return {"status": "HALAL", "reason": "числовые пороги пройдены + ручное подтверждение", "checked": str(conf), "calc": calc}
