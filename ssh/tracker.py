"""Paper-trading tracker: әр сигналдың нақты нәтижесін бақылайды (SL / TP1 / мерзімі өтті).
Бір барда SL мен TP1 қатар қозғалса — консервативті түрде SL деп саналады.
Сигнал түрлері: live = BUY/STRONG_BUY; pending_halal = халяль ғана бөгеп тұрған сетап ("болар еді")."""
import json
import pathlib

import pandas as pd

HORIZONS = {"1w": 5, "2w": 10, "4w": 20, "8w": 40}


def load_history(path="data/history.jsonl"):
    p = pathlib.Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def track_one(df: pd.DataFrame, setup: dict, last_bar: str, max_hold: int = 40) -> dict:
    entry, sl, tp1 = setup["entry"], setup["sl"], setup["tps"][0]
    risk = entry - sl
    bars = df[df.index > pd.Timestamp(last_bar)]
    res = {"state": "open", "bars": len(bars), "r": None, "mtm_r": 0.0, "horizons": {}}
    if risk <= 0 or bars.empty:
        return res
    for i, (_, row) in enumerate(bars.iloc[:max_hold].iterrows(), 1):
        if row["Low"] <= sl:
            res.update(state="SL", r=-1.0, bars=i)
            break
        if row["High"] >= tp1:
            res.update(state="TP1", r=round((tp1 - entry) / risk, 2), bars=i)
            break
    else:
        if len(bars) >= max_hold:
            res.update(state="EXPIRED", bars=max_hold, r=round((bars["Close"].iloc[max_hold - 1] - entry) / risk, 2))
    for name, n in HORIZONS.items():
        if len(bars) >= n:
            res["horizons"][name] = round((bars["Close"].iloc[n - 1] / entry - 1) * 100, 2)
    res["mtm_r"] = res["r"] if res["r"] is not None else round((bars["Close"].iloc[-1] - entry) / risk, 2)
    return res


def summarize(rows):
    out = {}
    for kind in ("live", "pending_halal"):
        rs = [r for r in rows if r["kind"] == kind]
        closed = [r for r in rs if r["state"] in ("SL", "TP1", "EXPIRED")]
        rr = [r["r"] for r in closed]
        wins = [x for x in rr if x > 0]
        out[kind] = {"total": len(rs), "open": len(rs) - len(closed), "closed": len(closed),
                     "win_rate": round(len(wins) / len(rr), 3) if rr else None,
                     "avg_R": round(sum(rr) / len(rr), 2) if rr else None,
                     "total_R": round(sum(rr), 2) if rr else 0.0}
    return out


def update(frames: dict, history_path="data/history.jsonl", out_path="docs/performance.json"):
    rows, seen = [], set()
    for h in load_history(history_path):
        s = h.get("setup")
        if not s or s.get("type") != "level_retest":
            continue
        kind = "live" if h["status"] in ("BUY", "STRONG_BUY") else ("pending_halal" if h.get("only_halal_blocks") else None)
        if not kind:
            continue
        key = (h["ticker"], s["level"], kind)
        if key in seen:
            continue
        seen.add(key)
        df = frames.get(h["ticker"])
        if df is None:
            continue
        r = track_one(df, s, h.get("last_bar") or h["generated_at"][:10])
        rows.append({"ticker": h["ticker"], "kind": kind, "date": h.get("last_bar") or h["generated_at"][:10],
                     "entry": s["entry"], "sl": s["sl"], "tp1": s["tps"][0], **r})
    payload = {"summary": summarize(rows), "rows": rows[-200:]}
    p = pathlib.Path(out_path)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload
