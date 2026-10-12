"""Triage part 5: Evidence-Weighted Triage (EWT). POST-RESULT EXPLORATORY WORK, opt-in, default OFF. Not part of the locked evaluation (see eval_runs.md,
2026-10-04, "part 5", where every rule below was fixed BEFORE any ranking was computed). The locked triage.py (chi-squared rank, cap 150) stays the default
and is not touched.

Inspired by the WHO Uppsala Monitoring Centre's vigiRank (Caster et al., Drug Safety 2014; Pharmacoepidemiology and Drug Safety 2017), which ranks signals by
strength of evidence and not by volume alone. EWT is our own adaptation with FAERS fields and EQUAL weights; it is not vigiRank and is not called that.

Survivors are the locked ones: Evans-flagged, a >= 3, exact preferred-term name not on the label in force (the locked drop). Each survivor gets five
components, each turned into a rank (1 = best = largest value, ties share the average rank), and the score is the plain average of the five ranks (lower is
better). Ties in the score go to the better chi-squared rank, then the term name. The top `cap` (150) are kept; the list size is always min(cap, survivors).
  1. ic025       lower bound of the information component (BCPNN), from the Miner's a, b, c, d
  2. hp          reports of the term with a health-professional reporter (occupation code MD, HP, PH or RN)
  3. recent      reports of the term whose case was first received in the last 4 quarters up to the cutoff quarter
  4. countries   distinct countries among the term's reports (occurrence country, else reporter country; blank = unknown, not a country)
  5. serious     reports of the term with an outcome code DE, LT, HO, DS, CA or RI (OT "other serious" is NOT counted)
Counts are over the drug's reports (the Miner's drug_rids: suspect PS/SS, latest case version known at the cutoff). Fields come from data/canary_ext.duckdb
(miner_store_ext.py), opened read-only together with the unchanged data/canary.duckdb. No weights are fitted or tuned.
"""
import numpy as np
import pandas as pd

import triage          # the locked module: fold(), on_label_exact(), MIN_COUNT
import triage_v2       # ic025()

CAP = 150
COMPONENTS = ("ic025", "hp", "recent", "countries", "serious")
HP_CODES = ("MD", "HP", "PH", "RN")
SERIOUS_CODES = ("DE", "LT", "HO", "DS", "CA", "RI")
RECENT_QUARTERS = 4
DB_EXT = "data/canary_ext.duckdb"


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Pure selection logic (no database): unit-testable
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def score_frame(s):
    """s: survivors with columns pt, chi2 and the five COMPONENTS (numbers). Returns a copy with r_<component> (1 = best, ties share the average rank),
    r_chi2 (1 = largest chi-squared, ties by name), score (mean of the five ranks) and pos (the EWT selection order: score, then r_chi2, then name)."""
    s = s.sort_values("pt", kind="stable").sort_values("chi2", ascending=False, kind="stable").copy()
    s["r_chi2"] = np.arange(1, len(s) + 1)
    for c in COMPONENTS:
        s["r_" + c] = s[c].rank(ascending=False, method="average")
    s["score"] = s[["r_" + c for c in COMPONENTS]].mean(axis=1)
    order = s.sort_values(["score", "r_chi2"], kind="stable")      # r_chi2 already breaks chi-squared ties by name
    s["pos"] = pd.Series(np.arange(1, len(order) + 1), index=order.index)
    return s


def select(s, cap=CAP):
    """Returns (the final list: min(cap, survivors) terms in chi-squared order, the scored frame of ALL survivors in EWT order)."""
    sc = score_frame(s).sort_values("pos")
    return sc.head(cap).sort_values("r_chi2"), sc


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Data: per-term component counts (local DuckDB stores, read-only; no network)
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def component_counts(con, qcut, ext_path=DB_EXT):
    """Needs the temp tables `drug_rids` that miner.mine() just built on this connection. Returns {pt: (hp, recent, countries, serious)} for every term
    that appears in at least one of the drug's reports."""
    q = lambda codes: ", ".join(f"'{c}'" for c in codes)
    lo = qcut - RECENT_QUARTERS + 1
    con.execute(f"ATTACH '{ext_path}' AS ext (READ_ONLY)")
    try:
        con.execute(f"""CREATE OR REPLACE TEMP TABLE drug_feat AS
            WITH first AS (SELECT caseid, min(qidx) AS fq FROM cases WHERE qidx <= {qcut} AND caseid IS NOT NULL GROUP BY caseid)
            SELECT c.rid, coalesce(e.occp_cod IN ({q(HP_CODES)}), false) AS hp, coalesce(f.fq >= {lo}, false) AS recent, e.country AS country,
                   c.rid IN (SELECT rid FROM outc WHERE outc_cod IN ({q(SERIOUS_CODES)})) AS serious
            FROM cases c LEFT JOIN first f USING (caseid) LEFT JOIN ext.report_ext e ON e.rid = c.rid
            WHERE c.rid IN (SELECT rid FROM drug_rids)""")
        rows = con.execute("""WITH r AS (SELECT DISTINCT rid, pt FROM reac WHERE rid IN (SELECT rid FROM drug_rids))
            SELECT r.pt, count(*) FILTER (WHERE f.hp), count(*) FILTER (WHERE f.recent), count(DISTINCT f.country), count(*) FILTER (WHERE f.serious)
            FROM r JOIN drug_feat f USING (rid) GROUP BY r.pt""").fetchall()
    finally:
        con.execute("DETACH ext")
    return {pt: (int(h), int(rc), int(nc), int(sv)) for pt, h, rc, nc, sv in rows}


def load(drug, date):
    """Run the Miner query once on a read-only connection. Returns (df of all terms as the Miner builds it, {pt: component counts}) or (None, reason)."""
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
        feats = component_counts(con, qcut)
    finally:
        con.close()
    return df.sort_values("pt", kind="stable"), feats


def survivors(df, feats, label_text, min_count=triage.MIN_COUNT):
    """The locked survivors (Evans, a >= min_count, exact name not on the label) with the five component columns. Returns (frame, counts)."""
    ev = df[df.evans]
    s1 = ev[ev.a >= min_count]
    fl = triage.fold(label_text)
    s2 = s1[[not triage.on_label_exact(p, fl) for p in s1.pt]].copy()
    s2["ic025"] = [triage_v2.ic025(r.a, r.b, r.c, r.d) for r in s2.itertuples()]
    for i, c in enumerate(("hp", "recent", "countries", "serious")):
        s2[c] = [feats.get(p, (0, 0, 0, 0))[i] for p in s2.pt]
    return s2, {"evans": len(ev), f"a>={min_count}": len(s1), "not_exact_on_label": len(s2)}


def run_ewt(df, feats, label_text, cap=CAP):
    """Used by the opt-in hook in agents/triage_step.py. Returns (final list in chi-squared order, counts)."""
    s2, counts = survivors(df, feats, label_text)
    out, _ = select(s2, cap=cap)
    return out, {**counts, "variant": "EWT", "components": list(COMPONENTS), f"cap{cap}": len(out)}
