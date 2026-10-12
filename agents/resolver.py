"""Drug Resolver agent (Build Step 1): free text -> generic (ingredient) name via RxNorm, with a "did you mean?" step.
Output is plain JSON:
  {"status": "resolved" | "needs_confirmation" | "not_found", "input", "generic_name", "rxcui", "confidence", "method",
   "suggestions": [{"generic_name", "rxcui", "matched_on"}], "message"}
Never guesses silently: only an exact RxNorm name match (after stripping doses and forms) is "resolved". A spelling-corrected
or approximate match is "needs_confirmation" with suggestions. All RxNorm calls are cached by canary_utils.get_json.
Run: PYTHONIOENCODING=utf-8 python -u agents/resolver.py "lipitor 20mg"
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from canary_utils import get_json as _get_json, RXNORM
from net_retry import retry_connection


def get_json(url, params=None):
    """RxNorm lookups: a dropped connection is retried (3 attempts, short backoff), anything else is raised."""
    return retry_connection(_get_json, url, params, what="RxNorm")

DOSE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|ug|g|ml|iu|units?|%|mg/ml|mg/g)\b", re.I)
FORMS = re.compile(r"\b(?:tablets?|tabs?|capsules?|caps?|injection|oral|solution|suspension|cream|er|xr|sr|cr|dr|hcl)\b", re.I)


def clean(text):
    """Lower-case, strip doses ("20mg"), dosage forms ("tablet") and stray digits/punctuation."""
    t = DOSE.sub(" ", text.lower())
    t = FORMS.sub(" ", t)
    t = re.sub(r"[^a-z0-9\- ]", " ", t)
    t = re.sub(r"\b\d+\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _exact_rxcui(name):
    ids = get_json(f"{RXNORM}/rxcui.json", {"name": name}).get("idGroup", {}).get("rxnormId")
    return ids[0] if ids else None


def _ingredients(rxcui):
    """RxNorm ingredient(s) (tty IN) for any concept. A combination product returns several."""
    props = get_json(f"{RXNORM}/rxcui/{rxcui}/properties.json").get("properties") or {}
    if props.get("tty") == "IN":
        return [(props["rxcui"], props["name"].lower())]
    rel = get_json(f"{RXNORM}/rxcui/{rxcui}/related.json", {"tty": "IN"})
    out = []
    for g in rel.get("relatedGroup", {}).get("conceptGroup", []) or []:
        if g["tty"] == "IN":
            out += [(p["rxcui"], p["name"].lower()) for p in g.get("conceptProperties", [])]
    return out


def _result(text, status, message, method=None, generic=None, rxcui=None, confidence=0.0, suggestions=None):
    return {"status": status, "input": text, "generic_name": generic, "rxcui": rxcui, "confidence": confidence,
            "method": method, "suggestions": suggestions or [], "message": message}


def resolve(text):
    text = (text or "").strip()
    q = clean(text)
    if not q:
        return _result(text, "not_found", "Please type a drug name.")
    # 1. exact RxNorm name (brand or generic) -> ingredient
    rxcui = _exact_rxcui(q)
    if rxcui:
        ings = _ingredients(rxcui)
        if len(ings) == 1:
            return _result(text, "resolved", f"'{q}' is {ings[0][1]} (RxCUI {ings[0][0]}).", "exact", ings[0][1], ings[0][0], 1.0)
        if len(ings) > 1:
            sug = [{"generic_name": n, "rxcui": c, "matched_on": q} for c, n in ings]
            return _result(text, "needs_confirmation", f"'{q}' is a combination product ({', '.join(n for _, n in ings)}). "
                           "Canary analyses one drug at a time: which one?", "exact_combination", suggestions=sug, confidence=1.0)
    # 2. typo: RxNorm spelling suggestions, then approximate match. Never accepted silently.
    cands = get_json(f"{RXNORM}/spellingsuggestions.json", {"name": q}).get("suggestionGroup", {}).get("suggestionList", {}).get("suggestion") or []
    method = "spelling"
    if not cands:
        method = "approximate"
        approx = get_json(f"{RXNORM}/approximateTerm.json", {"term": q, "maxEntries": 5}).get("approximateGroup", {}).get("candidate") or []
        cands = [a.get("name") or a["rxcui"] for a in approx if float(a["score"]) >= 8]  # below ~8 is noise (see tests)
    sug, seen = [], set()
    for c in cands:
        rc = _exact_rxcui(c) if not str(c).isdigit() else c
        for ic, iname in (_ingredients(rc) if rc else []):
            if ic not in seen:
                seen.add(ic)
                sug.append({"generic_name": iname, "rxcui": ic, "matched_on": str(c)})
    if not sug:
        return _result(text, "not_found", f"'{text}' was not found in RxNorm. Check the spelling or try the generic name.")
    conf = 0.8 if method == "spelling" else 0.5
    names = " or ".join(s["generic_name"] for s in sug[:3])
    return _result(text, "needs_confirmation", f"'{text}' is not a known drug name. Did you mean {names}?", method,
                   suggestions=sug[:3], confidence=conf)


if __name__ == "__main__":
    for t in sys.argv[1:] or ["lipitor 20mg"]:
        print(json.dumps(resolve(t), indent=1))
