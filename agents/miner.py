"""Miner agent (Build Step 2): wraps the existing DuckDB Miner (miner.py, locked behaviour) as an agent step.
Input: generic drug name, time machine date, serious-only flag. Output (dict, JSON-safe): every reaction term with
a, b, c, d, PRR, chi2 and the Evans flag, yearly counts (drug cases by year of first receipt, and per term), or
status "not_enough_data" when the drug has too few suspect reports before the date.
Drug-level counting only (PS or SS), latest case version per case known at the cutoff (miner.mine).
Run: PYTHONIOENCODING=utf-8 python -u agents/miner.py atorvastatin 2011-12-31
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duckdb
import eval_config
import miner

MIN_DRUG_CASES = 50  # below this the 2x2 tables are too thin to mean anything
FIRST_YEAR, LAST_Q = 2004, None

# Name patterns (regex on lower-cased DRUGNAME + " " + PROD_AI). Evaluation and development drugs use the frozen eval_config
# patterns; the demo drugs use the patterns from the demo scouting. Any other drug falls back to its generic name only.
PATTERNS = {name: (c["match"], c["exclude"]) for name, c in {**eval_config.CASES, **eval_config.DEV}.items()}
PATTERNS.update({
    "simvastatin": (r"simvastatin|zocor", eval_config.COMPOUNDED),
    "linezolid": (r"linezol|zyvox", eval_config.COMPOUNDED),
    "lithium": (r"lithium|lithobid|eskalith", eval_config.COMPOUNDED),
    "ixazomib": (r"ixazom|ninlaro", eval_config.COMPOUNDED),
    # montelukast is not an evaluation drug (fresh drug of the EWT test). Early reports name the brand: at 2009-12-31 the generic-name-only fallback found 148 suspect
    # reports, "montelukast|singulair" finds 4,474 (product change, eval_runs.md).
    "montelukast": (r"montelukast|singulair", eval_config.COMPOUNDED),
})
EVALUATION_DRUGS = {n for n, c in eval_config.CASES.items()}  # off limits until Build Step 10


def quarter_index(date):
    """Quarter index of the last full quarter that ends on or before `date` (YYYY-MM-DD). The time machine: a report counts only
    if it was loaded in a quarter that had ended by then. Returns None if before 2004Q1 ends."""
    y, m, d = (int(x) for x in date.split("-"))
    q = (m - 1) // 3 + 1                      # quarter containing the date
    last_day = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}[q]
    if (m, d) < last_day:                      # quarter not finished yet: use the previous one
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    idx = miner.qidx_of(y, q)
    return idx if idx >= 0 else None


def guard(generic, allow_evaluation=False):
    """Evaluation drugs are off limits unless the caller passes allow_evaluation=True. Only the Step 10 evaluation runner does."""
    if generic.lower().strip() in EVALUATION_DRUGS and not allow_evaluation:
        raise PermissionError(f"{generic.lower().strip()} is an evaluation drug: not allowed outside evaluation mode (Build Step 10)")


def drug_pattern(generic):
    g = generic.lower().strip()
    if g in PATTERNS:
        return PATTERNS[g][0], PATTERNS[g][1], "configured"
    return re.escape(g), eval_config.COMPOUNDED, "generic name only"


def _yearly(con, regex, exclude, qcut, serious_only, min_a):
    """Drug cases by year of first receipt, and per-term cases by year (terms with at least min_a drug cases)."""
    sev = "AND rid IN (SELECT rid FROM outc)" if serious_only else ""
    ex = f"AND NOT regexp_matches(drugname || ' ' || prod_ai, '{exclude}')" if exclude else ""
    con.execute(f"""CREATE OR REPLACE TEMP TABLE drug_year AS
        WITH first AS (SELECT caseid, min(qidx) AS fq FROM cases WHERE qidx <= {qcut} AND caseid IS NOT NULL GROUP BY caseid),
        dr AS (SELECT DISTINCT d.rid FROM drugs d JOIN latest l USING (rid) WHERE d.name_id IN
               (SELECT name_id FROM names WHERE regexp_matches(drugname || ' ' || prod_ai, '{regex}') {ex}))
        SELECT c.rid, {FIRST_YEAR} + f.fq // 4 AS yr FROM cases c JOIN first f USING (caseid)
        WHERE c.rid IN (SELECT rid FROM dr) AND c.rid IN (SELECT rid FROM latest) {sev.replace('rid IN', 'c.rid IN')}""")
    cases = con.execute("SELECT yr, count(*) FROM drug_year GROUP BY yr ORDER BY yr").fetchall()
    per_term = con.execute(f"""
        WITH r AS (SELECT DISTINCT rid, pt FROM reac WHERE rid IN (SELECT rid FROM drug_year))
        SELECT r.pt, y.yr, count(*) FROM r JOIN drug_year y USING (rid) GROUP BY r.pt, y.yr
        HAVING r.pt IN (SELECT pt FROM r GROUP BY pt HAVING count(*) >= {min_a})""").fetchall()
    terms = {}
    for pt, yr, n in per_term:
        terms.setdefault(pt, {})[int(yr)] = int(n)
    return {int(y): int(n) for y, n in cases}, terms


def run(generic, date, serious_only=False, min_a=3, db=miner.DB, allow_evaluation=False, use_exclusion=True, term_groups=None):
    """term_groups: OPT-IN, default None (the locked behaviour). Triage v2 (post-result, exploratory) passes triage_v2.load_groups(): renamed or synonymous
    terms are then merged into one row per group with each report counted once."""
    t0 = time.time()
    g = generic.lower().strip()
    guard(g, allow_evaluation)
    qcut = quarter_index(date)
    out = {"agent": "miner", "drug": g, "date": date, "serious_only": serious_only, "quarter_index": qcut}
    if qcut is None:
        return {**out, "status": "not_enough_data", "message": f"No FAERS data before {date} (data starts 2004Q1)."}
    regex, exclude, source = drug_pattern(g)
    exclude = exclude if use_exclusion else None  # off only to reproduce reference files built without it
    con = duckdb.connect(db, read_only=True)
    try:
        df, n_total, n_drug = miner.mine(con, regex, qcut, serious_only=serious_only, min_a=1, exclude=exclude)
        out.update(pattern=regex, pattern_source=source, n_total=int(n_total), n_drug=int(n_drug))
        if n_drug < MIN_DRUG_CASES:
            return {**out, "status": "not_enough_data", "elapsed_s": round(time.time() - t0, 1),
                    "message": f"Only {n_drug} suspect reports for {g} before {date} (need at least {MIN_DRUG_CASES})."}
        by_year, term_year = _yearly(con, regex, exclude, qcut, serious_only, min_a)
        gstats = None
        if term_groups:  # needs the temp tables `latest` and `drug_rids` that miner.mine() built on this connection
            import triage_v2
            gstats = triage_v2.group_stats(con, term_groups, n_total, n_drug)
    finally:
        con.close()
    df = df.sort_values("pt", kind="stable").sort_values(["evans", "PRR"], ascending=False, kind="stable")  # exact ties in alphabetical order, so every run is the same
    cols = ["pt", "a", "b", "c", "d", "PRR", "chi2", "evans"]
    terms = [{"term": r.pt, "a": int(r.a), "b": int(r.b), "c": int(r.c), "d": int(r.d),
              "PRR": None if r.PRR != r.PRR else float(r.PRR), "chi2": None if r.chi2 != r.chi2 else float(r.chi2),
              "evans": bool(r.evans), "yearly": term_year.get(r.pt, {})} for r in df[cols].itertuples()]
    if gstats:
        import triage_v2
        terms = triage_v2.merge_terms(terms, gstats)
        terms.sort(key=lambda t: (not t["evans"], -(t["PRR"] or 0), t["term"]))  # the same order the Miner agent uses
        out["term_groups"] = {g: v["members"] for g, v in gstats.items()}
    return {**out, "status": "ok", "n_terms": len(terms), "n_evans": sum(t["evans"] for t in terms),
            "drug_cases_by_year": by_year, "terms": terms, "elapsed_s": round(time.time() - t0, 1)}


if __name__ == "__main__":
    r = run(sys.argv[1], sys.argv[2], serious_only="--serious" in sys.argv)
    print(json.dumps({k: v for k, v in r.items() if k != "terms"}, indent=1))
    for t in r.get("terms", [])[:8]:
        print(t["term"], t["a"], round(t["PRR"] or 0, 2), round(t["chi2"] or 0, 1), t["evans"], t["yearly"])
