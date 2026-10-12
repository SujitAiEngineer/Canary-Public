"""Triage between the Miner and the Label Checker (Phase 2 planning). Cheap, deterministic, no LLM.
Steps: (1) Evans flag with a minimum count (3), (2) drop terms whose exact PT phrase (US/UK spelling folded) appears anywhere in the
label version in force, (3) rank the rest and keep the top CAP for the LLM Label Checker.
"""
import re

MIN_COUNT = 3     # a >= 3 (the Evans minimum). Tested against 5 on atorvastatin: same 6 of 6 cutoffs inside the cap, so 3 is used
CAP = 25          # at most this many terms go to the LLM per drug and cutoff
RANK_BY = "chi2"  # chi-squared, descending

_SPELL = [("haem", "hem"), ("aem", "em"), ("oed", "ed"), ("oea", "ea"), ("ae", "e")]


def fold(text):
    t = text.lower()
    for a, b in _SPELL:
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def variants(pt):
    """Exact folded phrase plus increase/decrease inflections."""
    p = fold(pt)
    out = {p}
    for a, b in (("increased", "increase"), ("decreased", "decrease"), ("increased", "elevated")):
        if p.endswith(a):
            out.add(p[: -len(a)] + b)
    return out


def on_label_exact(pt, folded_label):
    return any(re.search(rf"(?<![a-z0-9]){re.escape(v)}(?![a-z0-9])", folded_label) for v in variants(pt))


def triage(df, label_text, min_count=MIN_COUNT, cap=CAP, rank_by=RANK_BY):
    """df: Miner output. Returns (survivors sent to the LLM, dict of step counts)."""
    ev = df[df.evans]
    s1 = ev[ev.a >= min_count]
    fl = fold(label_text)
    s2 = s1[[not on_label_exact(p, fl) for p in s1.pt]]
    s3 = s2.sort_values(rank_by, ascending=False).head(cap)
    return s3, {"evans": len(ev), f"a>={min_count}": len(s1), "not_exact_on_label": len(s2), f"cap{cap}": len(s3)}
