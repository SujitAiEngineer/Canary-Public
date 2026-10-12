"""MeSH synonyms for the Label Checker's retrieval (product only; the locked evaluation never calls this: orchestrator.run_pipeline turns it off in evaluation mode).
A MedDRA reaction term is looked up in the free NLM MeSH service (id.nlm.nih.gov/mesh) as a descriptor name AND as an entry term (for example "Allergic Granulomatous
Angiitis" is an entry term of the descriptor "Churg-Strauss Syndrome"). The result is the descriptor name plus the entry terms of the concept that holds the term.
Lookups are cached in a local JSON file (data/mesh_cache.json, not in git): a repeat run makes no network call. If the service fails, or finds nothing, the answer is
an empty list with a status, and retrieval falls back to the original query alone (the caller logs the status).
Run: PYTHONIOENCODING=utf-8 python -u mesh_synonyms.py "allergic granulomatous angiitis" nightmare"""
import concurrent.futures, json, os, re, sys, threading, time
from urllib.parse import quote

import requests

LOOKUP = "https://id.nlm.nih.gov/mesh/lookup/{kind}?label={label}&match=exact"
SPARQL = "https://id.nlm.nih.gov/mesh/sparql"
CACHE_FILE = os.getenv("CANARY_MESH_CACHE", os.path.join("data", "mesh_cache.json"))
MAX_SYNONYMS = 5
MIN_INTERVAL = 0.2   # polite pace: at most 5 requests a second in total, whatever the number of workers (it was a 0.3 s pause between sequential requests)
WORKERS = 8          # lookups of different terms run this many at a time
RETRIES = 3
TIMEOUT = 40
PREFIX = ("PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>\nPREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n")
stats = {"live_requests": 0, "cache_hits": 0, "retries": 0}


def _norm(term):
    return re.sub(r"\s+", " ", term.lower().replace("/", " ")).strip()


class _Pace:
    """One shared pace for every thread: a request may start only MIN_INTERVAL after the previous one started (5 requests a second at most, however many workers)."""

    def __init__(self, interval):
        self.interval, self.next, self.lock = interval, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next)
            self.next = start + self.interval
        if start > now:
            time.sleep(start - now)


_pace = _Pace(MIN_INTERVAL)
_stats_lock = threading.Lock()


def _bump(name):
    with _stats_lock:
        stats[name] += 1


def _get(url, params=None):
    """One polite request: paced, and retried (RETRIES times; 1, 2, 4 s or the server's Retry-After) on a timeout, a connection error, HTTP 429 or a 5xx. Other HTTP errors are raised at once."""
    for attempt in range(RETRIES + 1):
        _pace.wait()
        _bump("live_requests")
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT, headers={"Accept": "application/json"})
        except (requests.ConnectionError, requests.Timeout):
            if attempt == RETRIES:
                raise
            _bump("retries")
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == RETRIES:
                r.raise_for_status()
            _bump("retries")
            ra = r.headers.get("Retry-After", "")
            time.sleep(float(ra) if ra.replace(".", "", 1).isdigit() else 2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()


def _lookup(kind, label):
    """MeSH lookup service: [{'resource': iri, 'label': ...}] for an exact descriptor name or entry term."""
    return _get(LOOKUP.format(kind=kind, label=quote(label)))


def _sparql(query):
    return _get(SPARQL, {"query": query, "format": "JSON", "limit": "200"})["results"]["bindings"]


def _variants(term):
    t = _norm(term)
    out = [t]
    for v in (t + "s", t[:-1] if t.endswith("s") else None):   # MedDRA is singular, MeSH entry terms are often plural ("Nightmares")
        if v and v not in out:
            out.append(v)
    return out


def _concept_terms(iri, is_descriptor, sparql=None):
    """(descriptor labels, term labels of the concept that holds the term, or of the descriptor's preferred concept)."""
    if is_descriptor:
        q = PREFIX + (f"SELECT DISTINCT ?dl ?tl WHERE {{ BIND(<{iri}> AS ?d) ?d rdfs:label ?dl . ?d meshv:preferredConcept ?c . "
                      f"?c (meshv:preferredTerm|meshv:term) ?t . ?t meshv:prefLabel ?tl }}")
    else:
        q = PREFIX + (f"SELECT DISTINCT ?dl ?tl WHERE {{ ?c (meshv:preferredTerm|meshv:term) <{iri}> . ?d (meshv:preferredConcept|meshv:concept) ?c . ?d rdfs:label ?dl . "
                      f"?c (meshv:preferredTerm|meshv:term) ?t . ?t meshv:prefLabel ?tl }}")
    rows = (sparql or _sparql)(q)
    return sorted({r["dl"]["value"] for r in rows}), [r["tl"]["value"] for r in rows]


_STOP = {"and", "or", "of", "the", "in", "with", "for", "to", "a", "an", "due", "syndrome", "disease", "disorder", "disorders", "reaction", "reactions"}


def _stems(text):
    """Content words reduced to their first five letters, so that "angiitis" and "angiitides" or "allergic" and "allergy" count as one word."""
    return {w[:5] for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 2}


def _pick(original, descriptors, entry_terms, cap):
    """Descriptor name(s) first, then entry terms as MeSH lists them. A synonym is kept only if it adds vocabulary: it must contain a content word that neither the
    original term nor an earlier synonym has (the entry terms of "Allergic Granulomatous Angiitis" are mostly re-wordings of it, and five near-copies of a query out-vote one
    good one when the rankings are merged). Also dropped: the original itself (and its plural or singular), and comma-inverted word orders (the same words). Abbreviations are kept ("DRESS Syndrome": labels use them). At most `cap`."""
    seen, out, known = set(_variants(original)), [], _stems(original)
    for cand in list(descriptors) + sorted(entry_terms):
        c = cand.strip()
        key = _norm(c)
        new = _stems(c) - known
        if key in seen or ("," in c and c not in descriptors) or not new:
            continue
        seen.add(key)
        known |= new
        out.append(c)
    return out[:cap]


def _load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def _resolve(term, cap, lk, sp):
    """The live lookup of one term (no cache, no file access, safe to run in a thread). lk(kind, label) and sp(query) are the two kinds of request."""
    key = _norm(term)
    descriptors, entry_terms = [], []
    for v in _variants(term):
        for kind in ("descriptor", "term"):
            for row in lk(kind, v):
                d, t = _concept_terms(row["resource"], kind == "descriptor", sp)
                descriptors += d
                entry_terms += t
        if descriptors or entry_terms:
            break
    result = {"term": term, "descriptor": sorted(set(descriptors)), "synonyms": _pick(key, sorted(set(descriptors)), entry_terms, cap), "cap": cap}
    result["status"] = "found" if result["synonyms"] else "none"
    result["source"] = "api"
    return result


_file_lock = threading.Lock()


def lookup_many(terms, cap=MAX_SYNONYMS, cache_path=None, fetch=None, fresh=False, workers=WORKERS):
    """One answer per term, in the order given, each {"term", "synonyms": [...], "descriptor": [...], "status": "found" | "none" | "error", "source": "cache" | "api" | "api failed",
    "error": str}. Terms not in the cache are looked up live, `workers` at a time under one shared request pace; the cache file is read once and written once at the end.
    fresh=True (a fresh run): the cache file is not READ, so every lookup is live; the answers are still WRITTEN to it. A failed lookup is not cached (the caller falls back to
    the original query). `fetch` is for tests: a function ("lookup", kind, label) or ("sparql", query)."""
    path = cache_path or CACHE_FILE
    cache = {} if fresh else _load(path)
    out, todo = [None] * len(terms), {}
    for i, term in enumerate(terms):
        key = _norm(term)
        hit = cache.get(key)
        if hit and hit.get("cap", cap) >= cap:
            _bump("cache_hits")
            out[i] = {**hit, "synonyms": hit["synonyms"][:cap], "term": term, "source": "cache"}
        else:
            todo.setdefault(key, []).append(i)
    lk, sp = ((lambda kind, label: fetch("lookup", kind, label)), (lambda q: fetch("sparql", q))) if fetch else (_lookup, _sparql)

    def work(key):
        term = terms[todo[key][0]]
        try:
            return key, _resolve(term, cap, lk, sp)
        except Exception as e:                   # network, HTTP or parsing problem: not cached, the caller falls back to the original query and logs it
            return key, {"term": term, "synonyms": [], "descriptor": [], "status": "error", "source": "api failed", "error": f"{type(e).__name__}: {e}"}
    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            done = list(pool.map(work, list(todo)))
        new_cache = {}
        for key, r in done:
            for i in todo[key]:
                out[i] = {**r, "term": terms[i]}
            if r["status"] != "error":
                new_cache[key] = {k: r[k] for k in ("descriptor", "synonyms", "status", "cap")}
        if new_cache:
            with _file_lock:
                merged = _load(path)
                merged.update(new_cache)
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                json.dump(merged, open(path, "w", encoding="utf-8"), indent=1)
    return out


def lookup(term, cap=MAX_SYNONYMS, cache_path=None, fetch=None, fresh=False):
    """The answer for one term (see lookup_many)."""
    return lookup_many([term], cap, cache_path, fetch, fresh, workers=1)[0]


if __name__ == "__main__":
    for r in lookup_many(sys.argv[1:]):
        print(json.dumps(r, indent=1))
    print(stats)
