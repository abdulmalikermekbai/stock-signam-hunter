"""Технические индикаторы (чистые pandas-функции, без внешних зависимостей)."""
import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["Close"].shift()
    return pd.concat(
        [df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR по Уайлдеру."""
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rel_volume(vol: pd.Series, n: int = 20) -> pd.Series:
    return vol / vol.rolling(n).mean().shift(1)


def rolling_vwap(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Скользящий VWAP по дневным барам (прокси: внутридневной VWAP бесплатно ненадёжен)."""
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = (tp * df["Volume"]).rolling(n).sum()
    return pv / df["Volume"].rolling(n).sum()


def macd(close: pd.Series, fast=12, slow=26, sig=9):
    line = ema(close, fast) - ema(close, slow)
    signal = ema(line, sig)
    return line, signal, line - signal


def weekly(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.resample("W-FRI")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna()
    )
