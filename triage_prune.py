"""Triage part 4: POST-RESULT EXPLORATORY WORK, opt-in, default OFF. Not part of the locked evaluation (see eval_runs.md, 2026-10-04, "part 4", where every rule
below was fixed BEFORE any result). The locked triage.py (chi-squared rank, cap 150) stays the default and is not touched.

Four variants (all return min(cap, survivors) terms):
  P    prune, then cap: remove Evans-flagged terms (a >= 3) that (1) are named on the label in force (exact PT, locked matcher), (2) are on the fixed
       non-clinical list, or (3) equal one of the drug's top 3 FAERS indications up to the cutoff; rank the rest by chi-squared.
  PN   P + up to 40 slots for novel terms (share of the term's reports in the last 4 quarters >= 2 x the drug's own share in that window), best by chi-squared.
  PL   P, ranked by the average of the chi-squared rank and the label-distance rank (label distance = 1 - highest cosine similarity to any chunk of the
       label version in force). Nothing is dropped for being close to the label.
  PLN  PL + the 40-slot novelty quota of PN.
"""
import re

import numpy as np
import pandas as pd

import triage  # the locked module: fold(), on_label_exact(), MIN_COUNT

CAP = 150
QUOTA = 40
VARIANTS = ("P", "PN", "PL", "PLN")
WINDOW_QUARTERS = 4
NOVELTY_FACTOR = 2.0
N_INDICATIONS = 3
UNKNOWN_INDICATION = r"UNKNOWN|NOT REPORTED|NO INDICATION|INDICATION NOT"   # the Skeptic's pattern

# Rule 2: the fixed non-clinical list (written into eval_runs.md before any result; do not edit)
NON_CLINICAL_EXACT = frozenset([
    "DRUG INTERACTION", "TREATMENT FAILURE", "THERAPEUTIC RESPONSE DECREASED", "THERAPEUTIC RESPONSE UNEXPECTED",
    "THERAPEUTIC PRODUCT EFFECT DECREASED", "THERAPEUTIC PRODUCT EFFECT INCOMPLETE", "TREATMENT NONCOMPLIANCE", "CONDITION AGGRAVATED",
    "DRUG USE FOR UNKNOWN INDICATION", "PRESCRIBED OVERDOSE", "PRESCRIBED UNDERDOSE", "UNDERDOSE"])
NON_CLINICAL_PATTERNS = [re.compile(p) for p in (
    r"^PRODUCT\b", r"MEDICATION ERROR", r"\bWRONG\b", r"\bINCORRECT\b", r"OFF[- ]LABEL", r"UNAPPROVED INDICATION", r"^NO ADVERSE EVENT",
    r"^DRUG INEFFECTIVE", r"^DRUG DOSE OMISSION", r"^EXTRA DOSE", r"^DEVICE\b", r"^EXPIRED (PRODUCT|DRUG)", r"^INAPPROPRIATE SCHEDULE OF",
    r"DRUG DISPENSING ERROR", r"^INTENTIONAL (PRODUCT|DRUG) (MISUSE|USE ISSUE)")]


def is_non_clinical(pt):
    t = pt.upper().strip()
    return t in NON_CLINICAL_EXACT or any(p.search(t) for p in NON_CLINICAL_PATTERNS)


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Pure selection logic (no database, no model): unit-testable
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def prune(df, label_text, indications, min_count=triage.MIN_COUNT):
    """df: one row per term (pt, a, chi2, evans, ...). indications: the drug's top 3 indication PTs (upper case).
    Returns (flagged: all Evans a >= min_count terms with bool columns r1 r2 r3 and `locked_survivor`, kept: the terms that survive all three rules, counts)."""
    ev = df[df.evans & (df.a >= min_count)].copy()
    fl = triage.fold(label_text)
    ind = {i.upper() for i in indications}
    ev["r1"] = [triage.on_label_exact(p, fl) for p in ev.pt]
    ev["r2"] = [is_non_clinical(p) for p in ev.pt]
    ev["r3"] = [p.upper().strip() in ind for p in ev.pt]
    ev["locked_survivor"] = ~ev.r1
    kept = ev[~(ev.r1 | ev.r2 | ev.r3)]
    ls = ev[ev.locked_survivor]
    counts = {"evans_a3": len(ev), "rule1_on_label": int(ev.r1.sum()), "rule2_non_clinical": int(ev.r2.sum()), "rule3_indication": int(ev.r3.sum()),
              "locked_survivors": len(ls), "removed_rule2_of_survivors": int(ls.r2.sum()), "removed_rule3_of_survivors": int(ls.r3.sum()),
              "removed_total_of_survivors": int((ls.r2 | ls.r3).sum()), "pruned_survivors": len(kept)}
    return ev, kept, counts


def novel_flags(a, win, n_drug, win_drug, factor=NOVELTY_FACTOR):
    """Share of the term's reports in the window >= factor x the drug's share in the same window. Integer cross-multiplication would also work; floats are
    exact enough here (the comparison is win/a >= factor x win_drug/n_drug)."""
    if n_drug <= 0:
        return [False] * len(a)
    ds = win_drug / n_drug
    return [bool(w / x >= factor * ds) if x > 0 else False for x, w in zip(a, win)]


def variant_order(kept, variant, quota=QUOTA):
    """The selection order of ALL pruned survivors under a variant: first the quota terms (PN, PLN: the best novel terms by chi-squared, at most `quota`),
    then everything else in the variant's ranking (P, PN: chi-squared; PL, PLN: average of the chi-squared rank and the label-distance rank, ties by chi-squared
    rank, then name). A term is in the final list iff its position is <= cap. Returns (DataFrame in selection order with r_chi2, r_dist, combined, quota_slot, pos)."""
    if variant not in VARIANTS:
        raise ValueError(variant)
    s = kept.sort_values("pt", kind="stable").sort_values("chi2", ascending=False, kind="stable").copy()   # ties in alphabetical order
    s["r_chi2"] = np.arange(1, len(s) + 1)
    if variant in ("PL", "PLN"):
        d = s.sort_values("pt", kind="stable").sort_values("dist", ascending=False, kind="stable")
        s["r_dist"] = pd.Series(np.arange(1, len(d) + 1), index=d.index)
        s["combined"] = (s.r_chi2 + s.r_dist) / 2
        base = s.sort_values(["combined", "r_chi2", "pt"], kind="stable")
    else:
        s["r_dist"], s["combined"] = np.nan, np.nan
        base = s
    s["quota_slot"] = False
    taken = s.iloc[0:0]
    if variant in ("PN", "PLN"):
        taken = s[s.novel].head(quota)          # s is in chi-squared order, so these are the best novel terms by chi-squared
        s.loc[taken.index, "quota_slot"] = True
    order = s.loc[list(taken.index) + list(base.drop(taken.index).index)].copy()
    order["pos"] = np.arange(1, len(order) + 1)
    return order


def select_variant(kept, variant, cap=CAP, quota=QUOTA):
    """kept: the pruned survivors (columns pt, chi2, and for PN/PLN a bool column `novel`, for PL/PLN a float column `dist` = label distance).
    Returns (the final list, min(cap, survivors) terms, in chi-squared order; the selection order of all pruned survivors)."""
    order = variant_order(kept, variant, quota)
    return order.head(cap).sort_values("r_chi2"), order


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Data: indications, novelty window, label distance (local DuckDB store and local Chroma index; no network)
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def drug_features(con, regex, exclude, qcut, n_drug):
    """Needs the temp tables `latest` and `drug_rids` that miner.mine() just built on this connection. Returns the drug's top indications and the window counts."""
    ex = f"AND NOT regexp_matches(drugname || ' ' || prod_ai, '{exclude}')" if exclude else ""
    names = f"SELECT name_id FROM names WHERE regexp_matches(drugname || ' ' || prod_ai, '{regex}') {ex}"
    ind = con.execute(f"""SELECT i.indi_pt, count(DISTINCT i.rid) AS n FROM indi i JOIN drugs d ON d.rid = i.rid AND d.drug_seq = i.drug_seq
        WHERE i.rid IN (SELECT rid FROM drug_rids) AND d.name_id IN ({names}) AND NOT regexp_matches(i.indi_pt, '{UNKNOWN_INDICATION}')
        GROUP BY i.indi_pt ORDER BY 2 DESC, 1 LIMIT {N_INDICATIONS}""").fetchall()
    lo = qcut - WINDOW_QUARTERS + 1
    con.execute(f"""CREATE OR REPLACE TEMP TABLE drug_win AS
        WITH first AS (SELECT caseid, min(qidx) AS fq FROM cases WHERE qidx <= {qcut} AND caseid IS NOT NULL GROUP BY caseid)
        SELECT c.rid FROM cases c JOIN first f USING (caseid) WHERE c.rid IN (SELECT rid FROM drug_rids) AND f.fq >= {lo}""")
    win_drug = con.execute("SELECT count(*) FROM drug_win").fetchone()[0]
    win = dict(con.execute("SELECT pt, count(*) FROM (SELECT DISTINCT rid, pt FROM reac WHERE rid IN (SELECT rid FROM drug_win)) GROUP BY pt").fetchall())
    return {"indications": [r[0] for r in ind], "indication_counts": [int(r[1]) for r in ind], "win": {k: int(v) for k, v in win.items()}, "win_drug": int(win_drug),
            "n_drug": int(n_drug), "window_quarters": [lo, qcut]}


def load(drug, date):
    """Run the Miner query once on a read-only connection. Returns (df of all terms as the Miner agent builds it, features) or (None, reason)."""
    import duckdb
    import miner
    from agents import miner as magent
    qcut = magent.quarter_index(date)
    if qcut is None:
        return None, "no data"
    regex, exclude, _ = magent.drug_pattern(drug)
    con = duckdb.connect(miner.DB, read_only=True)
    try:
        df, n_total, n_drug = miner.mine(con, regex, qcut, min_a=1, exclude=exclude)
        if n_drug < magent.MIN_DRUG_CASES:
            return None, f"only {n_drug} reports"
        feats = drug_features(con, regex, exclude, qcut, n_drug)
    finally:
        con.close()
    df = df.sort_values("pt", kind="stable")
    return df, feats


_emb = {}


def label_distances(drug, label_date, terms):
    """{term: 1 - highest cosine similarity to any chunk of the label version `label_date` (YYYYMMDD) of `drug`}. Vectors come from the Chroma collection,
    the term is embedded like the Label Checker's query (query prefix + '<term lower-case> adverse reaction') with the same local model."""
    import chromadb
    from fastembed import TextEmbedding
    import rag_chroma
    from rag_index import MODEL, QPREFIX
    if "model" not in _emb:
        _emb["model"] = TextEmbedding(MODEL)
        _emb["col"] = chromadb.PersistentClient(rag_chroma.CHROMA_DIR).get_collection(rag_chroma.COLLECTION)
    got = _emb["col"].get(where={"$and": [{"drug": drug}, {"label_date": label_date}]}, include=["embeddings"])
    v = np.asarray(got["embeddings"], dtype="float64")
    if len(v) == 0:
        return None
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    q = np.array(list(_emb["model"].embed([QPREFIX + f"{t.lower()} adverse reaction" for t in terms])), dtype="float64")
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    return dict(zip(terms, (1 - (q @ v.T).max(axis=1)).tolist()))


def prepare(df, feats, label_text, drug, label_date, need_dist=True):
    """Everything a variant needs: flagged terms, pruned survivors with `novel` and `dist` columns, counts. df: Miner terms (pt, a, chi2, evans, ...)."""
    flagged, kept, counts = prune(df, label_text, feats["indications"])
    kept = kept.copy()
    kept["win"] = [feats["win"].get(p, 0) for p in kept.pt]
    kept["novel"] = novel_flags(kept.a, kept.win, feats["n_drug"], feats["win_drug"])
    kept["dist"] = np.nan
    if need_dist and len(kept):
        d = label_distances(drug, label_date, list(kept.pt))
        if d is not None:
            kept["dist"] = [d[p] for p in kept.pt]
    flagged["dist"] = np.nan
    flagged.loc[kept.index, "dist"] = kept.dist
    return flagged, kept, counts


def run_variant(df, feats, label_text, drug, label_date, variant, cap=CAP):
    """Used by the opt-in hook in agents/triage_step.py. Returns (kept DataFrame in chi-squared order, counts)."""
    flagged, kept, counts = prepare(df, feats, label_text, drug, label_date, need_dist=variant in ("PL", "PLN"))
    if variant in ("PL", "PLN") and kept.dist.isna().any() and len(kept):
        raise RuntimeError(f"no indexed label vectors for {drug} {label_date}: {variant} cannot be computed")
    out, _ = select_variant(kept, variant, cap=cap)
    return out, {**counts, "variant": variant, "quota_slots_used": int(out.quota_slot.sum()), f"cap{cap}": len(out)}
