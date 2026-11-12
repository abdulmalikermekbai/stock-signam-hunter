"""Уровни, пробой/ретест, ложные пробои, гэпы, аккумуляция — формализация правил из PDF."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import atr as atr_fn, ema


@dataclass
class Level:
    price: float
    touches: int
    flipped: bool  # зеркальный: цена пересекала уровень в обе стороны


def pivots(df: pd.DataFrame, w: int = 5):
    hi, lo = df["High"], df["Low"]
    ph = hi[hi == hi.rolling(2 * w + 1, center=True).max()].dropna()
    pl = lo[lo == lo.rolling(2 * w + 1, center=True).min()].dropna()
    return ph, pl


def cluster(values, tol: float):
    groups = []
    for v in sorted(values):
        if groups and abs(v - np.mean(groups[-1])) / np.mean(groups[-1]) <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def _flipped(close: pd.Series, p: float, tol: float) -> bool:
    s = np.where(close > p * (1 + tol / 2), 1, np.where(close < p * (1 - tol / 2), -1, 0))
    s = s[s != 0]
    return bool(len(s) > 1 and (np.diff(s) != 0).any())


def find_levels(df: pd.DataFrame, tol: float = 0.015, min_touches: int = 2, lookback: int = 250):
    d = df.tail(lookback)
    ph, pl = pivots(d)
    levels = []
    for g in cluster(list(ph.values) + list(pl.values), tol):
        if len(g) >= min_touches:
            p = float(np.mean(g))
            levels.append(Level(p, len(g), _flipped(d["Close"], p, tol)))
    return sorted(levels, key=lambda l: l.price)


def breakout_retest(df: pd.DataFrame, level: float, a: float, lookback: int = 15) -> dict:
    """Пробой вверх -> тест/ретест -> удержание. Вход только при retest_confirmed."""
    c, lo = df["Close"].values, df["Low"].values
    n = len(c)
    k = None
    for i in range(n - 1, max(n - lookback, 1) - 1, -1):
        if c[i - 1] <= level and c[i] > level + 0.1 * a:
            k = i
            break
    if k is None:
        return {"state": "none", "k": None}
    ext = c[-1] - level > 1.5 * a
    retest = any(lo[j] <= level + 0.35 * a and c[j] >= level - 0.25 * a for j in range(k + 1, n))
    if c[-1] <= level:
        return {"state": "failed", "k": k}
    if ext:
        return {"state": "extended", "k": k}  # не догоняем свечу
    return {"state": "retest_confirmed" if retest else "breakout_no_retest", "k": k}


def false_breakout(df: pd.DataFrame, level: float, a: float, look: int = 3):
    """Типы: wick (хвост), two_candle (две свечи), combo; bear_trap — ложный пробой вниз."""
    d = df.tail(look + 1)
    h, l, c = d["High"].values, d["Low"].values, d["Close"].values
    wick = any(h[i] > level + 0.1 * a and c[i] < level for i in range(1, len(d)))
    two = any(c[i - 1] > level and c[i] < level for i in range(1, len(d)))
    if wick and two:
        return {"kind": "combo", "bias": "bearish"}
    if wick:
        return {"kind": "wick", "bias": "bearish"}
    if two:
        return {"kind": "two_candle", "bias": "bearish"}
    if any(l[i] < level - 0.1 * a and c[i] > level for i in range(1, len(d))):
        return {"kind": "bear_trap", "bias": "bullish"}
    return None


def gaps(df: pd.DataFrame, recent: int = 10):
    """Common / Breakaway / Runaway / Exhaustion."""
    a = atr_fn(df)
    e50 = ema(df["Close"], 50)
    out = []
    n = len(df)
    for i in range(max(n - recent, 32), n):
        pc, op = df["Close"].iloc[i - 1], df["Open"].iloc[i]
        gap = op - pc
        if np.isnan(a.iloc[i - 1]) or abs(gap) < 0.5 * a.iloc[i - 1]:
            continue
        up = gap > 0
        w = df.iloc[i - 21 : i - 1]
        consolidating = (w["High"].max() - w["Low"].min()) / pc < 0.12
        run30 = pc / df["Close"].iloc[i - 31] - 1
        trend = (pc > e50.iloc[i - 1] and e50.iloc[i - 1] > e50.iloc[i - 11]) if up else (
            pc < e50.iloc[i - 1] and e50.iloc[i - 1] < e50.iloc[i - 11])
        if consolidating:
            kind = "breakaway"
        elif (up and run30 > 0.25) or (not up and run30 < -0.25):
            kind = "exhaustion"
        elif trend:
            kind = "runaway"
        else:
            kind = "common"
        later = df.iloc[i:]
        filled = bool((later["Low"] <= pc).any()) if up else bool((later["High"] >= pc).any())
        out.append({"date": str(df.index[i].date()), "pct": round(gap / pc * 100, 2),
                    "direction": "up" if up else "down", "kind": kind, "filled": filled})
    return out


def accumulation(df: pd.DataFrame, lb: int = 60) -> dict:
    """Score 0-100: боковик + объём покупок > продаж + повторные отскоки от низа.
    Это косвенные признаки, а не доказательство действий институционалов."""
    d = df.tail(lb)
    width = (d["High"].max() - d["Low"].min()) / d["Close"].mean()
    drift = abs(d["Close"].iloc[-1] / d["Close"].iloc[0] - 1)
    chg = d["Close"].diff()
    up_v, dn_v = d["Volume"][chg > 0].sum(), d["Volume"][chg < 0].sum()
    ratio = up_v / dn_v if dn_v else 2.0
    _, pl = pivots(d, 3)
    touches = int((pl <= d["Low"].min() * 1.03).sum())
    pos = (d["Close"].iloc[-1] - d["Low"].min()) / max(d["High"].max() - d["Low"].min(), 1e-9)
    e50 = ema(df["Close"], 50)
    flat = width < 0.30 and drift < 0.15
    score = 0
    score += 30 if flat else 0
    score += int(min(max((ratio - 1.0) / 0.5, 0), 1) * 25)
    score += 25 if touches >= 2 else (10 if touches == 1 else 0)
    score += 10 if e50.iloc[-1] >= e50.iloc[-10] else 0
    score += 10 if 0.1 <= pos <= 0.7 else 0
    distribution = width < 0.30 and ratio < 0.8 and pos > 0.7
    return {"score": score, "flat": bool(flat), "vol_ratio": round(float(ratio), 2),
            "low_touches": touches, "range_pct": round(float(width) * 100, 1),
            "distribution": bool(distribution)}
