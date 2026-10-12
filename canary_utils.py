"""Shared helpers for Canary tests: env loading, cached polite HTTP, openFDA queries, RxNorm."""
import hashlib
import json
import os
import time

import requests

FDA_EVENT = "https://api.fda.gov/drug/event.json"
FDA_LABEL = "https://api.fda.gov/drug/label.json"
RXNORM = "https://rxnav.nlm.nih.gov/REST"
CACHE_DIR = os.getenv("CANARY_CACHE_DIR", "cache")  # override to test a cold cache without deleting anything
DELAY = 0.5  # seconds between live (uncached) calls

# One place to change models
MODEL_CHEAP = "claude-haiku-4-5-20251001"
MODEL_STRONG = "claude-sonnet-5-5"

REACTION_FIELD = "patient.reaction.reactionmeddrapt.exact"

def is_fresh(client):
    """True when this run ignores the model-answer cache (the orchestrator sets `fresh` on the run's client). New answers are still written to the cache."""
    return bool(getattr(client, "fresh", False))


stats = {"live_calls": 0, "cache_hits": 0}


def load_env(path=".env"):
    """Tiny .env reader (no python-dotenv needed)."""
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


load_env()


def _cache_path(key_src):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, hashlib.md5(key_src.encode()).hexdigest() + ".json")


def cache_get(key_src):
    p = _cache_path(key_src)
    if os.path.exists(p):
        stats["cache_hits"] += 1
        return json.load(open(p, encoding="utf-8"))
    return None


def cache_put(key_src, data):
    json.dump(data, open(_cache_path(key_src), "w", encoding="utf-8"))


def get_json(url, params=None, notes=None):
    """Cached GET with delay and retry on 429/5xx. Never caches errors.
    404 from openFDA (no matches) returns {"results": [], "error": {...}}.
    `notes` (optional list) collects API problems for a messiness log."""
    params = dict(params or {})
    key_src = url + json.dumps(params, sort_keys=True)  # key excluded from cache key
    hit = cache_get(key_src)
    if hit is not None:
        return hit
    if "api.fda.gov" in url and os.getenv("OPENFDA_API_KEY"):
        params["api_key"] = os.environ["OPENFDA_API_KEY"]
    r = None
    for attempt in range(4):
        time.sleep(DELAY)
        stats["live_calls"] += 1
        r = requests.get(url, params=params, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            if notes is not None:
                notes.append(f"HTTP {r.status_code} attempt {attempt + 1}: {url}")
            time.sleep(5 * (attempt + 1))
            continue
        break
    if r.status_code == 200:
        data = r.json()
    elif r.status_code == 404:
        data = {"error": {"code": "NOT_FOUND"}, "results": []}
    else:
        if notes is not None:
            notes.append(f"HTTP {r.status_code} final: {url}")
        return {"error": {"code": r.status_code}, "results": []}
    cache_put(key_src, data)
    return data


def get_text(url, params=None):
    """Cached GET returning text/bytes-as-text (for HTML pages)."""
    key_src = "TEXT" + url + json.dumps(params or {}, sort_keys=True)
    hit = cache_get(key_src)
    if hit is not None:
        return hit["status"], hit["text"]
    slow = "archive.org" in url  # Wayback rate-limits hard: be extra polite
    r = None
    for attempt in range(5):
        time.sleep(6 if slow else DELAY)
        stats["live_calls"] += 1
        try:
            r = requests.get(url, params=params, timeout=90,
                             headers={"User-Agent": "Mozilla/5.0 (Canary research prototype; public data only)"})
        except requests.ConnectionError:  # e.g. refused while rate-limited: back off and retry
            time.sleep(60 * (attempt + 1))
            continue
        if r.status_code in (429, 503):
            time.sleep(60 * (attempt + 1))
            continue
        break
    if r is None:
        return 0, ""
    if r.status_code == 200 and "Session Verification" not in r.text[:400]:
        cache_put(key_src, {"status": 200, "text": r.text})
    elif r.status_code == 200:
        return 0, r.text  # bot-challenge page, not content
    return r.status_code, r.text


def fda_total(search, endpoint=FDA_EVENT):
    d = get_json(endpoint, {"search": search, "limit": 1} if search else {"limit": 1})
    return d.get("meta", {}).get("results", {}).get("total", 0)


def fda_counts(search, field, limit=100):
    d = get_json(FDA_EVENT, {"search": search, "count": field, "limit": limit})
    return d.get("results", [])


def quote(term):
    return f'"{term}"'


def prr(a, b, c, d):
    """PRR = (a/(a+b)) / (c/(c+d)); NaN if undefined."""
    if a + b == 0 or c + d == 0 or c == 0:
        return float("nan")
    return (a / (a + b)) / (c / (c + d))


def chi_squared(a, b, c, d):
    """Pearson chi-squared (no Yates correction) for the 2x2 table."""
    n = a + b + c + d
    denom = (a + b) * (c + d) * (a + c) * (b + d)
    return n * (a * d - b * c) ** 2 / denom if denom else float("nan")


def rxnorm_ingredient(text):
    """Resolve text to an RxNorm ingredient name via exact then approximate match. Returns (name, method)."""
    ids = get_json(f"{RXNORM}/rxcui.json", {"name": text}).get("idGroup", {}).get("rxnormId")
    method = "exact"
    if not ids:
        cands = get_json(f"{RXNORM}/approximateTerm.json", {"term": text, "maxEntries": 3}) \
            .get("approximateGroup", {}).get("candidate", []) or []
        if not cands:
            return None, None
        ids, method = [cands[0]["rxcui"]], "approximate"
    rel = get_json(f"{RXNORM}/rxcui/{ids[0]}/related.json", {"tty": "IN PIN"})
    for g in rel.get("relatedGroup", {}).get("conceptGroup", []) or []:
        if g["tty"] == "IN" and g.get("conceptProperties"):
            return g["conceptProperties"][0]["name"], method
    props = get_json(f"{RXNORM}/rxcui/{ids[0]}/properties.json").get("properties") or {}
    return props.get("name"), method
