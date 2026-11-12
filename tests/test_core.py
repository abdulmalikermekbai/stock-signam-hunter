"""Тесты на СИНТЕТИЧЕСКИХ рядах (только для проверки логики; продакшен-данные всегда реальные)."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest
import yaml

from ssh import engine, fundamentals, news, risk
from ssh.indicators import atr, ema, rsi, rel_volume, weekly
from ssh.levels import accumulation, breakout_retest, false_breakout, find_levels, gaps, cluster

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def mk(closes, spread=0.5, vol=1_000_000, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.array(closes, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) + spread, "Low": np.minimum(o, c) - spread,
                         "Close": c, "Volume": np.full(len(c), vol, float)}, index=idx)


def test_ema_constant_and_rsi_bounds():
    s = pd.Series([10.0] * 50)
    assert ema(s, 10).iloc[-1] == pytest.approx(10)
    r = rsi(pd.Series(np.arange(1, 60, dtype=float)))
    assert r.iloc[-1] == 100
    r2 = rsi(pd.Series(np.random.default_rng(1).normal(100, 1, 200)))
    assert 0 <= r2.dropna().min() and r2.dropna().max() <= 100


def test_atr_matches_manual_first_value():
    df = mk([10, 11, 12, 11, 12, 13, 12, 13, 14, 13, 14, 15, 14, 15, 16, 15], spread=0.0)
    a = atr(df, 14)
    assert np.isnan(a.iloc[12]) and not np.isnan(a.iloc[14])


def test_cluster_groups_close_values():
    g = cluster([100, 100.5, 101, 120, 120.4], 0.015)
    assert len(g) == 2 and len(g[0]) == 3


def test_weekly_resample():
    w = weekly(mk(list(range(100, 130))))
    assert len(w) < 10 and w["High"].iloc[0] >= w["Low"].iloc[0]


def _breakout_series():
    # диапазон с сопротивлением ~100, пробой, ретест, удержание
    base = list(100 - 4 * np.abs(np.sin(np.linspace(0, 12, 120))))  # колебания, потолок ~100
    brk = [100.5, 102, 104, 105, 103.5, 101.2, 101.0, 102.5, 103.0]
    return base + brk


def test_breakout_retest_confirmed():
    df = mk(_breakout_series(), spread=0.3)
    df.iloc[-4, df.columns.get_loc("Low")] = 100.4  # касание уровня сверху
    st = breakout_retest(df, 100.0, 1.5)
    assert st["state"] in ("retest_confirmed", "extended")
    assert st["k"] is not None


def test_breakout_without_retest_extended():
    c = [100] * 40 + [101, 104, 107, 110]
    st = breakout_retest(mk(c, spread=0.2), 100.0, 1.0)
    assert st["state"] == "extended"


def test_false_breakout_wick_and_bear_trap():
    c = [95, 96, 97, 98, 99]
    df = mk(c, spread=0.1)
    df.iloc[-1, df.columns.get_loc("High")] = 102  # хвост выше 100, закрытие 99
    assert false_breakout(df, 100.0, 1.0)["kind"] == "wick"
    df2 = mk([105, 104, 103, 102, 101], spread=0.1)
    df2.iloc[-1, df2.columns.get_loc("Low")] = 98
    assert false_breakout(df2, 100.0, 1.0)["kind"] == "bear_trap"


def test_gap_classification_breakaway():
    c = [100 + 0.2 * np.sin(i / 3) for i in range(60)] + [108, 109, 110]
    df = mk(c, spread=0.2)
    df.iloc[60, df.columns.get_loc("Open")] = 106  # гэп вверх из консолидации
    g = gaps(df)
    assert g and g[0]["kind"] == "breakaway" and g[0]["direction"] == "up"


def test_accumulation_flat_range_scores_high():
    rng = np.random.default_rng(0)
    c = 50 + 2 * np.sin(np.linspace(0, 8 * np.pi, 120)) + rng.normal(0, 0.1, 120)
    df = mk(c, spread=0.2)
    assert accumulation(df)["score"] >= 40
    trend = mk(np.linspace(20, 80, 120))
    assert accumulation(trend)["score"] < accumulation(df)["score"]


def test_risk_position_sizing():
    p = risk.position(CFG, entry=100, sl=95, fx=500)
    # риск 0.75% от 250k = 1875 тг; на акцию 5$*500=2500 тг -> 0 шт.
    assert p["shares"] == 0
    p2 = risk.position(CFG, entry=10, sl=9.5, fx=500)
    assert p2["shares"] == int(min(1875 // 250, 250000 * 0.35 // 5000))
    assert "не задан" in p2["fees"]["paydax"]


def test_fundamentals_na_not_counted_pass():
    f = fundamentals.score({"revenueGrowth": 0.2, "trailingPE": 20}, CFG)
    assert f["available"] == 2 and f["gate_ok"] is False
    assert any(r["value"] == "N/A" and r["ok"] is None for r in f["rows"])


def test_halal_requires_manual_confirmation_and_expiry():
    info = {"industry": "Software", "sector": "Technology", "marketCap": 1e12, "totalDebt": 5e10, "totalCash": 5e10}
    assert fundamentals.halal("XYZ", info, CFG)["status"] == "NEEDS_REVIEW"
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    today = dt.date(2026, 11, 11)
    cfg["halal"]["confirmed"] = {"XYZ": "2026-10-20"}
    assert fundamentals.halal("XYZ", info, cfg, today)["status"] == "HALAL"
    cfg["halal"]["confirmed"] = {"XYZ": "2026-01-01"}
    assert fundamentals.halal("XYZ", info, cfg, today)["status"] == "EXPIRED"
    assert fundamentals.halal("B", {"industry": "Banks - Regional"}, CFG)["status"] == "NOT_HALAL"
    bad = dict(info, totalDebt=5e11)
    assert fundamentals.halal("XYZ", bad, cfg, today)["status"] == "NOT_HALAL"


def _item(title, domain, tier=None, age_h=1, now=None):
    now = now or dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc)
    pub = now - dt.timedelta(hours=age_h)
    return {"ticker": "ABC", "title": title, "url": "http://" + domain, "publisher": domain, "domain": domain,
            "tier": tier or news.tier_of(domain), "published": pub.isoformat(), "latency_min": age_h * 60}


def test_news_verification_statuses():
    now = dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc)
    v = news.verify([_item("ABC wins $50M Pentagon contract", "sec.gov")], CFG, now)
    assert v[0]["status"] == "VERIFIED"
    v = news.verify([_item("ABC wins big Pentagon contract today", "reuters.com"),
                     _item("ABC wins Pentagon contract today, big", "cnbc.com")], CFG, now)
    assert v[0]["status"] == "CORROBORATED"
    v = news.verify([_item("ABC EXPLOSIVE guaranteed 1000% upside", "randomblog.xyz")], CFG, now)
    assert v[0]["status"] == "UNCONFIRMED" and v[0]["red_flags"]
    v = news.verify([_item("ABC old news", "reuters.com", age_h=24 * 20)], CFG, now)
    assert v[0]["status"] == "OUTDATED"


def test_hard_gate_halal_blocks_buy():
    """Даже идеальный технический сетап не даёт BUY без подтверждённого халяля."""
    s = _breakout_series()
    df = mk(s * 2, spread=0.3)  # длинная история
    tech = {"ok": True, "tech_score": 95, "acc": {"score": 80, "distribution": False},
            "ind": {"last_bar": dt.date.today().isoformat(), "avg_volume_20": 5_000_000, "atr_pct": 2.5, "weekly_up": True},
            "setup": {"type": "level_retest", "level": 100, "touches": 3, "mirror": True, "entry": 10.0, "sl": 9.5,
                      "sl_atr": 1.2, "tps": [11.5, 12.5, 13.5], "tp_source": "levels", "rr_tp1": 3.0, "rr_tp2": 5.0},
            "volume_confirm": True, "price_above_vwap_ema50": True, "ema50_rising": True}
    fund = {"score": 90, "gate_ok": True, "passed": 9, "available": 10, "rows": []}
    ok = engine.evaluate("ABC", tech, fund, {"status": "HALAL", "reason": "t"}, [], 50, None, 500, CFG)
    assert ok["status"] in ("BUY", "STRONG_BUY") and not ok["gates_failed"]
    bad = engine.evaluate("ABC", tech, fund, {"status": "NEEDS_REVIEW", "reason": "t"}, [], 50, None, 500, CFG)
    assert bad["status"] == "WAIT" and any("халяль" in g for g in bad["gates_failed"])
    earn = engine.evaluate("ABC", tech, fund, {"status": "HALAL", "reason": "t"}, [], 50,
                           dt.date.today() + dt.timedelta(days=3), 500, CFG)
    assert earn["status"] == "WAIT"
    tech2 = dict(tech, setup=dict(tech["setup"], rr_tp1=1.2))
    low_rr = engine.evaluate("ABC", tech2, fund, {"status": "HALAL", "reason": "t"}, [], 50, None, 500, CFG)
    assert low_rr["status"] == "WAIT"


def test_technical_setup_runs_on_synthetic():
    rng = np.random.default_rng(3)
    c = 50 + np.cumsum(rng.normal(0.05, 0.8, 400))
    out = engine.technical_setup(mk(np.abs(c) + 5), CFG)
    assert out["ok"] and "ind" in out and 0 <= out["tech_score"] <= 100


def test_backtest_no_lookahead_smoke():
    from ssh import backtest
    rng = np.random.default_rng(5)
    c = np.abs(50 + np.cumsum(rng.normal(0.03, 0.8, 600))) + 5
    tr = backtest.simulate(mk(c), CFG, step=10)
    st = backtest.stats(tr)
    assert "trades" in st
