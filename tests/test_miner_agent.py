"""Build Step 2 test: Miner agent matches case_study_atorvastatin.csv exactly at 2011 and 2013, runs in under 10 s,
returns "not enough data" for a tiny drug, and refuses evaluation drugs.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_miner_agent.py"""
import math, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from agents import miner as agent

REF = pd.read_csv("analysis/case_study_atorvastatin.csv")
REF = REF[(REF.drug == "atorvastatin") & ~REF.term.str.contains("ANY_OF|CONCEPT")]


def check_year(year, use_exclusion):
    t0 = time.time()
    r = agent.run("atorvastatin", f"{year}-12-31", use_exclusion=use_exclusion)
    dt = time.time() - t0
    assert r["status"] == "ok"
    by_term = {t["term"]: t for t in r["terms"]}
    ref = REF[REF.cutoff == int(f"{year}1231")]
    assert len(ref) == 5
    for x in ref.itertuples():
        t = by_term.get(x.term)
        if x.a == 0:
            assert t is None, x.term
            continue
        assert (t["a"], t["b"], t["c"], t["d"]) == (x.a, x.b, x.c, x.d), (year, x.term, t, x)
        assert math.isclose(t["PRR"], x.PRR, rel_tol=1e-9) and math.isclose(t["chi2"], x.chi2, rel_tol=1e-9), (year, x.term)
        assert t["evans"] == bool(x.signal_on), (year, x.term)
    assert r["n_drug"] == int(ref.n_drug.iloc[0]) and r["n_total"] == int(ref.cases_total.iloc[0])
    assert dt < 10, f"took {dt:.1f}s"
    print(f"pass atorvastatin {year}: {len(ref)} reference rows identical; {r['n_terms']} terms, {r['n_evans']} Evans; {dt:.1f}s", flush=True)


# case_study_atorvastatin.csv was built without the compounded-mixture exclusion, so the exact match is checked with it off.
def test_2011(): check_year(2011, False)
def test_2013(): check_year(2013, False)


def test_exclusion_effect_2013():
    """With the exclusion on (the default, as the plan requires) only compounded-cream cases leave the drug group: 3 cases at 2013."""
    on, off = agent.run("atorvastatin", "2013-12-31"), agent.run("atorvastatin", "2013-12-31", use_exclusion=False)
    assert off["n_drug"] - on["n_drug"] == 3 and on["n_total"] == off["n_total"]
    assert agent.run("atorvastatin", "2011-12-31")["n_drug"] == agent.run("atorvastatin", "2011-12-31", use_exclusion=False)["n_drug"]


def test_yearly_counts():
    r = agent.run("atorvastatin", "2011-12-31")
    assert sum(r["drug_cases_by_year"].values()) == r["n_drug"]
    assert max(r["drug_cases_by_year"]) <= 2011
    t = next(t for t in r["terms"] if t["term"] == "DIABETES MELLITUS")
    assert sum(t["yearly"].values()) == t["a"]


def test_not_enough_data():
    r = agent.run("ixazomib", "2013-12-31")  # not yet in trials reports: zero suspect cases
    assert r["status"] == "not_enough_data", r.get("n_drug")
    assert agent.run("atorvastatin", "2003-12-31")["status"] == "not_enough_data"


def test_evaluation_drug_blocked():
    try:
        agent.run("gabapentin", "2008-12-31")
    except PermissionError:
        return
    raise AssertionError("evaluation drug was not blocked")


def test_quarter_index():
    assert agent.quarter_index("2011-12-31") == agent.quarter_index("2012-01-15") == 31
    assert agent.quarter_index("2011-12-30") == 30  # Q4 not finished yet


def test_montelukast_uses_the_brand_name_too():
    """Early reports name the brand: at 2009-12-31 the generic-name-only fallback found 148 suspect reports, montelukast|singulair finds 4,474."""
    import eval_config
    regex, exclude, source = agent.drug_pattern("montelukast")
    assert regex == "montelukast|singulair" and exclude == eval_config.COMPOUNDED and source == "configured"
    assert "montelukast" not in agent.EVALUATION_DRUGS      # not an evaluation drug: the guard is unaffected
    r = agent.run("montelukast", "2009-12-31")
    assert r["status"] == "ok" and r["n_drug"] == 4474, r.get("n_drug")


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); n += 1; print("pass", name, flush=True)
    print(f"{n} tests passed")

