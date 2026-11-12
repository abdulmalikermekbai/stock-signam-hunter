"""Бэктест технического сетапа level_retest (без look-ahead): сигнал по данным до бара i,
вход по открытию i+1, выход по SL/TP1 внутри бара (при одновременном касании считается SL),
комиссии/слиппедж из config. Фундаментал/халяль/новости в бэктесте НЕ участвуют (исторических
срезов бесплатно нет) — это честное ограничение."""
import json
import logging

import numpy as np
import pandas as pd

from . import data
from .engine import technical_setup

log = logging.getLogger("ssh.backtest")


def simulate(df: pd.DataFrame, cfg: dict, step=3, max_hold=40, warmup=200):
    trades, i, n = [], warmup, len(df)
    slip = cfg["risk"]["slippage_bps"] / 10000
    while i < n - 2:
        tech = technical_setup(df.iloc[: i + 1], cfg)
        s = tech.get("setup") if tech.get("ok") else None
        if s and s["type"] == "level_retest" and s["sl_atr"] <= cfg["risk"]["max_stop_atr"] and s["rr_tp1"] >= cfg["risk"]["min_rr"]:
            entry = df["Open"].iloc[i + 1] * (1 + slip)
            sl, tp = s["sl"], s["tps"][0]
            risk = entry - sl
            if risk <= 0:
                i += step
                continue
            exit_p, j = None, i + 1
            for j in range(i + 1, min(i + 1 + max_hold, n)):
                lo, hi = df["Low"].iloc[j], df["High"].iloc[j]
                if lo <= sl:
                    exit_p = sl * (1 - slip); break
                if hi >= tp:
                    exit_p = tp * (1 - slip); break
            if exit_p is None:
                exit_p = df["Close"].iloc[j] * (1 - slip)
            trades.append({"date": str(df.index[i].date()), "r": (exit_p - entry) / risk, "bars": j - i})
            i = j + 1
        else:
            i += step
    return trades


def stats(trades):
    if not trades:
        return {"trades": 0}
    r = np.array([t["r"] for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    eq = np.cumsum(r)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    return {"trades": len(r), "win_rate": round(float((r > 0).mean()), 3), "avg_win_R": round(float(wins.mean()), 2) if len(wins) else 0,
            "avg_loss_R": round(float(losses.mean()), 2) if len(losses) else 0,
            "profit_factor": round(float(wins.sum() / abs(losses.sum())), 2) if len(losses) and losses.sum() != 0 else None,
            "expectancy_R": round(float(r.mean()), 3), "max_drawdown_R": round(dd, 2),
            "sharpe_per_trade": round(float(r.mean() / r.std()), 2) if r.std() > 0 else None}


def run(cfg, tickers):
    frames, _ = data.download(tickers, period="10y")
    allt = []
    for t, df in frames.items():
        tr = simulate(df, cfg)
        for x in tr:
            x["ticker"] = t
        allt += tr
        log.info("%s: %s", t, stats(tr))
    cut = sorted(x["date"] for x in allt)[len(allt) // 2] if allt else None
    rep = {"all": stats(allt),
           "in_sample_first_half": stats([x for x in allt if cut and x["date"] <= cut]),
           "out_of_sample_second_half": stats([x for x in allt if cut and x["date"] > cut]),
           "note": "Только технический сетап; без фундамента/халяль/новостей. Не гарантия будущих результатов."}
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    import pathlib
    pathlib.Path("docs").mkdir(exist_ok=True)
    pathlib.Path("docs/backtest.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    return rep
