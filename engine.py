"""Scraping and analysis for any Google Play app."""
import math
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from google_play_scraper import Sort, app as gp_app, reviews as gp_reviews, search as gp_search
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ---------------------------------------------------------------- categories
# A trailing * means "word starting with" (crash* matches crashes, crashing).
# Anything else must match as a whole word or phrase.
CATEGORIES = {
    "Crashes & bugs": [
        "crash*", "bug*", "buggy", "glitch*", "freez*", "froze", "not working", "doesn't work",
        "does not work", "stopped working", "won't open", "not opening", "force clos*",
        "keeps closing", "black screen", "blank screen", "error*", "broken",
    ],
    "Speed & performance": [
        "slow*", "lag*", "laggy", "loading", "load time", "takes forever", "battery", "drain*",
        "heat*", "overheat*", "storage", "space", "heavy", "fast", "smooth*", "responsive",
    ],
    "Pricing & payments": [
        "price*", "pricing", "expensive", "costly", "overpriced", "cheap*", "subscription*",
        "subscribe*", "premium", "paywall", "pay", "paid", "payment*", "charg*", "fee*",
        "money", "worth", "free trial", "in-app purchase*", "renew*", "auto-renew*",
    ],
    "Ads": [
        "ad", "ads", "advert*", "commercial*", "too many ads", "pop-up*", "popup*", "ad-free",
    ],
    "Customer support": [
        "customer care", "customer service", "customer support", "support", "helpline",
        "complain*", "contact*", "no response", "no reply", "respond*", "resolv*",
        "resolution", "ticket*", "agent*", "escalat*", "refund*",
    ],
    "UI & design": [
        "ui", "interface", "design*", "layout", "navigation", "navigat*", "button*", "menu*",
        "dark mode", "font*", "user friendly", "user-friendly", "easy to use", "intuitive",
        "confusing", "cluttered", "new look", "looks", "theme", "clean",
    ],
    "Login & account": [
        "login*", "log in", "logged out", "sign in", "signin", "sign up", "signup",
        "register*", "otp", "verification", "verify*", "password*", "account*", "banned",
        "suspended", "blocked",
    ],
}


def _compile(words):
    parts = [re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w) for w in words]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.I)


PATTERNS = {cat: _compile(words) for cat, words in CATEGORIES.items()}

# ---------------------------------------------------------------- sentiment
vader = SentimentIntensityAnalyzer()
vader.lexicon.update({
    "crash": -2.5, "crashes": -2.5, "crashing": -2.5, "crashed": -2.5, "bug": -2.0,
    "bugs": -2.0, "buggy": -2.5, "glitch": -2.0, "glitches": -2.0, "glitchy": -2.2,
    "lag": -1.8, "lags": -1.8, "laggy": -2.0, "freezes": -2.2, "freezing": -2.2,
    "stuck": -1.8, "slow": -1.5, "overpriced": -2.5, "expensive": -1.5, "costly": -1.5,
    "scam": -3.0, "fraud": -3.0, "worst": -3.2, "useless": -2.5, "unusable": -2.8,
    "smooth": 2.0, "seamless": 2.2, "intuitive": 2.0, "fast": 1.2, "clean": 1.2,
})
TEXT_WEIGHT, STAR_WEIGHT = 0.6, 0.4
STAR_TO_SCORE = {1: -1.0, 2: -0.5, 3: 0.0, 4: 0.5, 5: 1.0}

MIN_MENTIONS_PER_CELL = 8
SPIKE_THRESHOLD_PTS = 10
SPIKE_P_VALUE = 0.05

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


# ---------------------------------------------------------------- app lookup
def _info(d):
    return {
        "appId": d.get("appId"), "title": d.get("title"), "developer": d.get("developer"),
        "icon": d.get("icon"), "score": round(d["score"], 2) if d.get("score") else None,
        "installs": d.get("installs"), "genre": d.get("genre"),
    }


def app_details(app_id, country="in"):
    return _info(gp_app(app_id, lang="en", country=country))


def parse_app_id(text):
    """Accept a Play Store URL or a raw package name."""
    text = text.strip()
    m = re.search(r"[?&]id=([\w.]+)", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z][\w]*(\.[\w]+)+", text):
        return text
    return None


def search_apps(query, country="in", n=8):
    direct = parse_app_id(query)
    if direct:
        try:
            return [app_details(direct, country)]
        except Exception:
            return []

    # The Play search page gives the real ranking, including the featured top result
    # that the library returns without an id.
    url = ("https://play.google.com/store/search?c=apps&hl=en&gl=" + country
           + "&q=" + urllib.parse.quote(query))
    html = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15).read().decode()
    ids = list(dict.fromkeys(re.findall(r"/store/apps/details\?id=([\w.]+)", html)))[:n]

    known = {}
    try:
        for d in gp_search(query, lang="en", country=country, n_hits=n):
            if d.get("appId"):
                known[d["appId"]] = _info(d)
    except Exception:
        pass

    missing = [i for i in ids if i not in known]
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, info in zip(missing, ex.map(lambda x: _safe(app_details, x, country), missing)):
            if info:
                known[i] = info
    return [known[i] for i in ids if i in known]


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


# ---------------------------------------------------------------- scraping
def fetch_reviews(app_id, country="in", count=10000, progress=None):
    rows, token = [], None
    while len(rows) < count:
        batch, token = gp_reviews(app_id, lang="en", country=country, sort=Sort.NEWEST,
                                  count=min(200, count - len(rows)), continuation_token=token)
        if not batch:
            break
        rows.extend(batch)
        if progress:
            progress(len(rows))
        if token is None:
            break
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.rename(columns={"reviewCreatedVersion": "version", "score": "stars", "at": "date"})
    return df[["reviewId", "date", "version", "stars", "content", "thumbsUpCount"]]


# ---------------------------------------------------------------- grouping
def _vkey(v):
    return tuple(int(p) if p.isdigit() else 0 for p in str(v).split("."))


def choose_periods(df):
    """Group reviews into releases at whichever version depth gives the most usable releases.
    Falls back to calendar weeks when an app doesn't report versions."""
    total = len(df)
    min_reviews = max(30, int(total * 0.02))
    versions = df.version.dropna().astype(str)

    best = None
    for depth in (1, 2, 3):
        cut = versions.map(lambda v: ".".join(v.split(".")[:depth]))
        counts = cut.value_counts()
        usable = (counts >= min_reviews).sum()
        if best is None or usable > best[1]:
            best = (depth, usable)

    if best and best[1] >= 2 and versions.size >= total * 0.5:
        depth = best[0]
        df = df.dropna(subset=["version"]).copy()
        df["period"] = df.version.astype(str).map(lambda v: ".".join(v.split(".")[:depth]))
        counts = df.period.value_counts()
        keep = counts[counts >= min_reviews].index
        df = df[df.period.isin(keep)]
        order = sorted(keep, key=_vkey)
        return df, order, "version"

    df = df.copy()
    week = pd.to_datetime(df.date).dt.to_period("W").dt.start_time
    df["period"] = week.dt.strftime("%Y-%m-%d")
    counts = df.period.value_counts()
    keep = counts[counts >= min_reviews].index
    df = df[df.period.isin(keep)]
    return df, sorted(keep), "week"


# ---------------------------------------------------------------- analysis
def label(score):
    if score <= -0.6: return "Extremely negative"
    if score <= -0.35: return "Very negative"
    if score <= -0.1: return "Negative"
    if score < 0.1: return "Neutral"
    if score < 0.4: return "Positive"
    if score < 0.6: return "Very positive"
    return "Extremely positive"


SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def extract_mentions(df):
    out = []
    for r in df.itertuples():
        text = str(r.content)
        sentences = SENT_SPLIT.split(text)
        star = STAR_TO_SCORE.get(int(r.stars), 0.0)
        for cat, pat in PATTERNS.items():
            hits = [s for s in sentences if pat.search(s)]
            if not hits:
                continue
            ts = sum(vader.polarity_scores(s)["compound"] for s in hits) / len(hits)
            score = TEXT_WEIGHT * ts + STAR_WEIGHT * star
            out.append({
                "period": r.period, "category": cat, "score": score, "stars": int(r.stars),
                "sentiment": "negative" if score < -0.1 else "positive" if score > 0.1 else "neutral",
                "snippet": " ".join(hits).strip()[:300], "thumbs": int(r.thumbsUpCount or 0),
            })
    return pd.DataFrame(out)


def _quotes(m, sentiment, n=4):
    q = m[(m.sentiment == sentiment) & m.snippet.str.len().between(30, 300)]
    q = q.sort_values("thumbs", ascending=False).drop_duplicates("snippet").head(n)
    return [{"text": r.snippet, "stars": r.stars, "thumbs": r.thumbs} for r in q.itertuples()]


def _pvalue(neg1, n1, neg2, n2):
    p = (neg1 + neg2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    return math.erfc(abs((neg2 / n2 - neg1 / n1) / se) / math.sqrt(2))


def analyze(df):
    if df.empty:
        raise ValueError("This app has no English reviews in the selected country.")
    raw_total = len(df)
    date_from, date_to = str(df.date.min())[:10], str(df.date.max())[:10]
    df, periods, kind = choose_periods(df.dropna(subset=["content"]))
    if len(periods) < 2:
        raise ValueError("Not enough reviews to compare releases. Try pulling more reviews.")

    m = extract_mentions(df)
    stars = {k: round(float(v), 2) for k, v in df.groupby("period").stars.mean().items()}
    counts = df.groupby("period").size().to_dict()

    cells = {}
    for (p, c), g in m.groupby(["period", "category"]):
        if len(g) < MIN_MENTIONS_PER_CELL:
            continue
        avg = g.score.mean()
        cells[f"{p}|{c}"] = {
            "score": round(float(avg), 3), "label": label(avg), "n": int(len(g)),
            "neg": round(float((g.sentiment == "negative").mean()) * 100, 1),
            "quotes": {"negative": _quotes(g, "negative"), "positive": _quotes(g, "positive")},
        }

    spikes = []
    for c in CATEGORIES:
        prev = None
        for p in periods:
            cell = cells.get(f"{p}|{c}")
            if cell is None:
                continue
            if prev is not None:
                pp, pc = prev
                delta = cell["neg"] - pc["neg"]
                if abs(delta) >= SPIKE_THRESHOLD_PTS:
                    pv = _pvalue(pc["neg"] / 100 * pc["n"], pc["n"], cell["neg"] / 100 * cell["n"], cell["n"])
                    worse = delta > 0
                    spikes.append({
                        "category": c, "from": pp, "to": p, "before": pc["neg"], "after": cell["neg"],
                        "change": round(float(delta), 1), "n_before": pc["n"], "n_after": cell["n"],
                        "p": round(pv, 4), "significant": bool(pv < SPIKE_P_VALUE), "worse": bool(worse),
                        "quotes": cell["quotes"]["negative" if worse else "positive"][:2],
                    })
            prev = (p, cell)
    spikes.sort(key=lambda s: (not s["significant"], -abs(s["change"])))

    overall = {}
    for c, g in m.groupby("category"):
        overall[c] = {
            "n": int(len(g)), "neg": round(float((g.sentiment == "negative").mean()) * 100, 1),
            "share": round(len(g) / len(df) * 100, 1), "score": round(float(g.score.mean()), 3),
        }

    return {
        "kind": kind, "periods": periods, "counts": {k: int(v) for k, v in counts.items()},
        "stars": stars, "cells": cells, "spikes": spikes, "overall": overall,
        "reviews_fetched": raw_total, "reviews_used": int(len(df)),
        "mentions": int(len(m)), "date_from": date_from, "date_to": date_to,
    }
