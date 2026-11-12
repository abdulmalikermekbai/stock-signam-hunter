"""Провайдер рыночных данных (yfinance). Интерфейс простой — можно заменить на другой."""
import datetime as dt
import logging
import time

import pandas as pd

log = logging.getLogger("ssh.data")
PROVIDER_META = {
    "provider": "yfinance (Yahoo Finance)",
    "frequency": "1d (EOD)",
    "delay": "дневные бары; интрадей-котировки Yahoo могут идти с задержкой ~15 мин",
    "url": "https://finance.yahoo.com",
}


def _retry(fn, tries=3, pause=2):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            log.warning("retry %s/%s: %s", i + 1, tries, e)
            time.sleep(pause * (i + 1))
    return None


def download(tickers, period="2y"):
    import yfinance as yf

    raw = _retry(lambda: yf.download(list(tickers), period=period, interval="1d", group_by="ticker",
                                     auto_adjust=True, threads=True, progress=False))
    out, status = {}, {}
    if raw is None or raw.empty:
        return out, {"ok": False, "error": "yfinance не вернул данные", **PROVIDER_META}
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
            df = df.dropna(subset=["Close"])
            if len(df) > 0:
                out[t] = df
        except Exception as e:  # noqa: BLE001
            log.warning("%s: нет данных (%s)", t, e)
    status = {"ok": bool(out), "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "tickers_ok": len(out), "tickers_requested": len(tickers), **PROVIDER_META}
    return out, status


def info(ticker):
    import yfinance as yf
    return _retry(lambda: yf.Ticker(ticker).info) or {}


def earnings_date(ticker):
    import yfinance as yf
    try:
        cal = yf.Ticker(ticker).calendar
        d = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if d:
            x = d[0] if isinstance(d, (list, tuple)) else d
            return pd.Timestamp(x).date()
    except Exception as e:  # noqa: BLE001
        log.info("earnings %s: %s", ticker, e)
    return None


def insider_buys_90d(ticker):
    """Информативно: число покупок инсайдеров за 90 дней (по данным Yahoo). Не заменяет SEC Form 4."""
    import yfinance as yf
    try:
        df = yf.Ticker(ticker).insider_transactions
        if df is None or df.empty:
            return None
        txt = df["Text"].astype(str).str.lower() if "Text" in df else pd.Series([""] * len(df))
        dates = pd.to_datetime(df["Start Date"]) if "Start Date" in df else pd.Series(pd.NaT, index=df.index)
        recent = dates >= pd.Timestamp.now() - pd.Timedelta(days=90)
        return int((txt.str.contains("purchase") & recent).sum())
    except Exception:  # noqa: BLE001
        return None


def usdkzt(fallback):
    import yfinance as yf
    try:
        h = yf.Ticker("KZT=X").history(period="5d")
        if not h.empty:
            return float(h["Close"].iloc[-1]), "KZT=X (yfinance)"
    except Exception:  # noqa: BLE001
        pass
    return float(fallback), "fallback из config (курс не получен!)"
