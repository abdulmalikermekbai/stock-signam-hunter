import datetime as dt

import numpy as np
import pandas as pd
import pytest
import yaml

from ssh import engine, scoring, telegram, tracker

CFG = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def _tech(rr=3.0):
    return {"ok": True, "tech_score": 95, "acc": {"score": 80, "distribution": False},
            "ind": {"last_bar": dt.date.today().isoformat(), "avg_volume_20": 5_000_000, "atr_pct": 2.5, "weekly_up": True,
                    "above_ema50": True, "above_ema200": True, "ema50_rising": True, "rel_volume": 1.5,
                    "ret63": 0.2, "dist_high_pct": 3.0},
            "setup": {"type": "level_retest", "level": 100, "touches": 3, "mirror": True, "entry": 10.0, "sl": 9.5,
                      "sl_atr": 1.2, "tps": [11.5, 12.5, 13.5], "tp_source": "levels", "rr_tp1": rr, "rr_tp2": 5.0},
            "volume_confirm": True, "price_above_vwap_ema50": True, "ema50_rising": True}


FUND = {"score": 90, "gate_ok": True, "passed": 9, "available": 10, "rows": []}


def test_six_scores_in_range_and_halal_ordering():
    z = engine.evaluate("ABC", _tech(), FUND, {"status": "HALAL", "reason": "t", "calc": {"debt_to_mcap": .05, "cash_to_mcap": .05}},
                        [], 50, None, 500, CFG, bench={"ret63": 0.05})["six"]
    assert all(0 <= z[k] <= 100 for k in ("signal", "halal", "momentum", "entry", "catalyst", "risk", "rank"))
    h = lambda st: scoring.halal_score({"status": st, "calc": {"debt_to_mcap": .1, "cash_to_mcap": .1}}, CFG)
    assert h("NOT_HALAL") == 0 < h("EXPIRED") < h("NEEDS_REVIEW") < h("HALAL")
    assert scoring.momentum_score({"above_ema50": True, "above_ema200": True, "ema50_rising": True, "weekly_up": True,
                                   "rel_volume": 1.5, "ret63": .3, "dist_high_pct": 2}, {"ret63": 0.05}) > 80
    assert scoring.momentum_score({"above_ema50": False, "above_ema200": False, "ema50_rising": False, "weekly_up": False,
                                   "rel_volume": .5, "ret63": -.2, "dist_high_pct": 40}, {"ret63": 0.05}) < 20


def test_only_halal_blocks_flag():
    s = engine.evaluate("ABC", _tech(), FUND, {"status": "NEEDS_REVIEW", "reason": "t"}, [], 50, None, 500, CFG)
    assert s["status"] == "WAIT" and s["only_halal_blocks"] is True
    s2 = engine.evaluate("ABC", _tech(rr=1.0), FUND, {"status": "NEEDS_REVIEW", "reason": "t"}, [], 50, None, 500, CFG)
    assert not s2.get("only_halal_blocks")  # RR тағы бөгеп тұр


def _sig(t, status, halal_only=False):
    s = engine.evaluate(t, _tech(), FUND, {"status": "HALAL" if status == "BUY" else "NEEDS_REVIEW", "reason": "t"}, [], 50, None, 500, CFG)
    s["status"] = status
    s["only_halal_blocks"] = halal_only
    return s


def test_digest_lists_every_company_no_truncation():
    sigs = [_sig(f"T{i:02d}", "WAIT") for i in range(11)] + [_sig(f"W{i}", "WATCHLIST") for i in range(6)]
    txt = telegram.fmt_digest(sigs, "2026-11-12")
    assert all(s["ticker"] in txt for s in sigs)
    assert "WAIT=11" in txt and "WATCHLIST=6" in txt


def test_digest_ready_section_has_plan():
    txt = telegram.fmt_digest([_sig("VRTX", "WAIT", True)], "2026-11-12")
    assert "СЕТАП ДАЙЫН" in txt and "/halal TICKER" in txt and "SL 9.5" in txt


def test_halal_confirm_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram, "DATA", tmp_path)
    telegram.set_halal("AAPL", True, dt.date(2026, 11, 12))
    assert telegram.load_halal() == {"AAPL": "2026-11-12"}
    telegram.set_halal("AAPL", False)
    assert telegram.load_halal() == {}


def _bars(rows):
    idx = pd.bdate_range("2026-01-05", periods=len(rows))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx).assign(Volume=1e6)


SETUP = {"entry": 10.0, "sl": 9.0, "tps": [12.0, 13.0, 14.0]}


def test_tracker_tp_sl_and_same_bar_conservative():
    base = [(10, 10, 10, 10)]
    tp = tracker.track_one(_bars(base + [(10, 10.5, 9.8, 10.2), (10.2, 12.2, 10.1, 12.0)]), SETUP, "2026-01-05")
    assert tp["state"] == "TP1" and tp["r"] == 2.0 and tp["bars"] == 2
    sl = tracker.track_one(_bars(base + [(10, 10.2, 8.9, 9.0)]), SETUP, "2026-01-05")
    assert sl["state"] == "SL" and sl["r"] == -1.0
    both = tracker.track_one(_bars(base + [(10, 12.5, 8.5, 11)]), SETUP, "2026-01-05")
    assert both["state"] == "SL"  # бір барда екеуі де — SL деп саналады
    open_ = tracker.track_one(_bars(base + [(10, 10.6, 9.9, 10.5)]), SETUP, "2026-01-05")
    assert open_["state"] == "open" and open_["mtm_r"] == 0.5


def test_tracker_summary_and_update(tmp_path):
    hist = tmp_path / "h.jsonl"
    import json
    row = {"ticker": "AAA", "status": "BUY", "generated_at": "2026-01-05T20:00:00", "last_bar": "2026-01-05",
           "setup": {"type": "level_retest", "level": 100, "entry": 10.0, "sl": 9.0, "tps": [12.0, 13.0, 14.0]}}
    hist.write_text("\n".join(json.dumps(x) for x in [row, row, dict(row, status="WAIT", only_halal_blocks=True, ticker="BBB")]))
    frames = {"AAA": _bars([(10, 10, 10, 10), (10, 12.5, 9.9, 12)]), "BBB": _bars([(10, 10, 10, 10), (10, 10.2, 8.8, 9)])}
    out = tracker.update(frames, str(hist), str(tmp_path / "p.json"))
    assert len(out["rows"]) == 2  # дубликат тек бір рет саналады
    assert out["summary"]["live"]["win_rate"] == 1.0 and out["summary"]["pending_halal"]["win_rate"] == 0.0
