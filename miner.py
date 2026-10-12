"""Miner (Phase 2 Task 4): drug-level PRR + Evans criteria for every reaction, from data/canary.duckdb (see miner_store.py).
Drug-level = the drug is Primary or Secondary Suspect (PS/SS). Latest case version known at the cutoff quarter. 
Run: PYTHONIOENCODING=utf-8 python -u miner.py atorvastatin 2011     (cutoff = end of that year)
"""
import sys, time
import duckdb
import numpy as np
import pandas as pd

DB = "data/canary.duckdb"
DRUG_REGEX = {"atorvastatin": r"atorva|lipitor|torvast|sortis", "rosuvastatin": r"rosuvastatin|crestor",
              "simvastatin": r"simvastatin|zocor", "pravastatin": r"pravastatin|pravachol"}


def qidx_of(year, quarter=4):
    return (year - 2004) * 4 + quarter - 1


def prepare(con, qcut):
    """Temp table `latest`: one report per case, the latest version loaded in a quarter <= qcut."""
    con.execute("DROP TABLE IF EXISTS latest")
    con.execute(f"""CREATE TEMP TABLE latest AS
        SELECT max_by(rid, (qidx::HUGEINT * 100000 + ver) * 10000000000000 + rid) AS rid FROM cases
        WHERE qidx <= {qcut} AND caseid IS NOT NULL GROUP BY caseid""")


def mine(con, regex, qcut, serious_only=False, min_a=1, exclude=None):
    """Returns (DataFrame one row per reaction PT with a,b,c,d,PRR,chi2,evans, n_total, n_drug)."""
    prepare(con, qcut)
    con.execute("DROP TABLE IF EXISTS drug_rids")
    con.execute(f"""CREATE TEMP TABLE drug_rids AS SELECT DISTINCT d.rid FROM drugs d JOIN latest l USING (rid)
        WHERE d.name_id IN (SELECT name_id FROM names WHERE regexp_matches(drugname || ' ' || prod_ai, '{regex}')
        {f"AND NOT regexp_matches(drugname || ' ' || prod_ai, '{exclude}')" if exclude else ""})""")
    sev = "AND rid IN (SELECT rid FROM outc)" if serious_only else ""
    n_total = con.execute(f"SELECT count(*) FROM latest WHERE true {sev}").fetchone()[0]
    n_drug = con.execute(f"SELECT count(*) FROM drug_rids WHERE true {sev}").fetchone()[0]
    df = con.execute(f"""
        WITH r AS (SELECT DISTINCT rid, pt FROM reac WHERE rid IN (SELECT rid FROM latest {'WHERE rid IN (SELECT rid FROM outc)' if serious_only else ''}))
        SELECT pt, count(*) AS term_total, count(*) FILTER (WHERE rid IN (SELECT rid FROM drug_rids)) AS a FROM r GROUP BY pt
        HAVING count(*) FILTER (WHERE rid IN (SELECT rid FROM drug_rids)) >= {min_a}""").df()
    a = df["a"].to_numpy(float)
    b = n_drug - a
    c = df["term_total"].to_numpy(float) - a
    d = (n_total - n_drug) - c
    with np.errstate(divide="ignore", invalid="ignore"):
        prr = (a / (a + b)) / (c / (c + d))
        n = a + b + c + d
        chi2 = n * (a * d - b * c) ** 2 / ((a + b) * (c + d) * (a + c) * (b + d))
    df["b"], df["c"], df["d"], df["PRR"], df["chi2"] = b, c, d, prr, chi2
    df["evans"] = (df["a"] >= 3) & (df["PRR"] >= 2) & (df["chi2"] >= 4)
    return df.sort_values(["evans", "PRR"], ascending=False), n_total, n_drug


if __name__ == "__main__":
    drug, year = sys.argv[1], int(sys.argv[2])
    con = duckdb.connect(DB, read_only=True)
    t0 = time.time()
    df, n_total, n_drug = mine(con, DRUG_REGEX[drug], qidx_of(year))
    print(f"{drug} cutoff {year}-12-31: {n_total:,} cases, {n_drug:,} drug cases, {len(df):,} reaction terms, "
          f"{int(df.evans.sum())} meet Evans; {time.time() - t0:.1f}s")
    print(df.head(10).to_string(index=False))
