"""Новости: сбор из RSS, ранжирование источников, кросс-проверка, статусы, измерение задержки.
Статусы MISLEADING/FALSE автоматически НЕ ставятся (нужны доказательства опровержения) —
только UNCONFIRMED + red_flags."""
import datetime as dt
import logging
import os
import re
from urllib.parse import quote, urlparse

import requests

log = logging.getLogger("ssh.news")
TIER1 = ("sec.gov", "globenewswire.com", "prnewswire.com", "businesswire.com", "accessnewswire.com")
TIER2 = ("reuters.com", "bloomberg.com", "apnews.com", "wsj.com", "cnbc.com", "benzinga.com", "ft.com")
HIGH_IMPACT = ["8-k", "contract", "acquisition", "acquire", "merger", "fda", "approval", "guidance",
               "raises", "earnings", "buyback", "partnership", "offering", "dilution", "investigation",
               "lawsuit", "bankruptcy", "halt"]
POS = ["beat", "surge", "raises", "approval", "wins", "record", "upgrade", "growth", "buyback"]
NEG = ["miss", "plunge", "cuts", "downgrade", "lawsuit", "probe", "investigation", "offering", "bankruptcy", "recall"]


def tier_of(domain: str) -> int:
    d = domain.lower()
    if any(d.endswith(x) for x in TIER1):
        return 1
    if any(d.endswith(x) for x in TIER2):
        return 2
    if d:
        return 3
    return 4


def _tokens(t):
    return set(re.findall(r"[a-z0-9$%.]{3,}", t.lower()))


def similar(a, b, thr=0.5):
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= thr


def feed_urls(ticker, name):
    q = quote(f'"{name or ticker}" OR {ticker} stock')
    return [
        ("google_news", f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"),
        ("yahoo_finance", f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"),
        ("sec_edgar", f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type=8-K&dateb=&owner=include&count=10&output=atom"),
    ]


def fetch(ticker, name=None, now=None):
    import feedparser
    now = now or dt.datetime.now(dt.timezone.utc)
    ua = os.environ.get("SEC_USER_AGENT", "StockSignalHunter research contact@example.com")
    items, status = [], {}
    for src, url in feed_urls(ticker, name):
        try:
            r = requests.get(url, headers={"User-Agent": ua}, timeout=15)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            status[src] = f"ok ({len(feed.entries)})"
        except Exception as e:  # noqa: BLE001
            status[src] = f"error: {e}"
            continue
        for e in feed.entries[:15]:
            pub = None
            if e.get("published_parsed"):
                pub = dt.datetime(*e.published_parsed[:6], tzinfo=dt.timezone.utc)
            elif e.get("updated_parsed"):
                pub = dt.datetime(*e.updated_parsed[:6], tzinfo=dt.timezone.utc)
            href = (e.get("source") or {}).get("href") or e.get("link", "")
            domain = "sec.gov" if src == "sec_edgar" else urlparse(href).netloc.replace("www.", "")
            items.append({"ticker": ticker, "title": e.get("title", "").strip(), "url": e.get("link", ""),
                          "publisher": (e.get("source") or {}).get("title") or domain or src,
                          "domain": domain, "feed": src, "tier": tier_of(domain),
                          "published": pub.isoformat() if pub else None,
                          "discovered": now.isoformat(),
                          "latency_min": round((now - pub).total_seconds() / 60, 1) if pub else None})
    return items, status


def verify(items, cfg, now=None):
    """Группирует похожие заголовки и присваивает статус по доказательствам."""
    now = now or dt.datetime.now(dt.timezone.utc)
    hype = [w.lower() for w in cfg["news"]["hype_words"]]
    max_age = cfg["news"]["max_age_days"]
    clusters = []
    for it in items:
        for c in clusters:
            if similar(c[0]["title"], it["title"]):
                c.append(it)
                break
        else:
            clusters.append([it])
    out = []
    for c in clusters:
        domains = {i["domain"] for i in c if i["domain"]}
        best = min(i["tier"] for i in c)
        pubs = [dt.datetime.fromisoformat(i["published"]) for i in c if i["published"]]
        newest = max(pubs) if pubs else None
        age_d = (now - newest).days if newest else None
        title = c[0]["title"]
        flags = [f"hype: {w}" for w in hype if w in title.lower()]
        if best > 2 and not any(i["tier"] <= 2 for i in c):
            flags.append("нет источника уровня 1-2")
        if age_d is not None and age_d > max_age:
            status = "OUTDATED"
        elif best == 1:
            status = "VERIFIED"
        elif (len(domains) >= 2 and best <= 2) or len(domains) >= 3:
            status = "CORROBORATED"
        else:
            status = "UNCONFIRMED"
        tl = title.lower()
        sent = sum(w in tl for w in POS) - sum(w in tl for w in NEG)
        out.append({"ticker": c[0]["ticker"], "title": title, "status": status, "red_flags": flags,
                    "sources": sorted(domains), "best_tier": best, "links": [i["url"] for i in c][:3],
                    "published": newest.isoformat() if newest else None,
                    "latency_min": min((i["latency_min"] for i in c if i["latency_min"] is not None), default=None),
                    "sentiment": "bullish" if sent > 0 else "bearish" if sent < 0 else "neutral",
                    "high_impact": any(k in tl for k in HIGH_IMPACT)})
    return out


def news_score(verified):
    """0-100: 50 = нейтрально. Учитываются только подтверждённые свежие новости."""
    s = 50
    for v in verified:
        w = {"VERIFIED": 1.0, "CORROBORATED": 0.7}.get(v["status"], 0)
        s += 15 * w * {"bullish": 1, "bearish": -1}.get(v["sentiment"], 0)
    return max(0, min(100, round(s)))
