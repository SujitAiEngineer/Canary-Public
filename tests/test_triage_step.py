"""Build Step 3 test: atorvastatin at 2011-12-31 returns 150 terms with the counts per step logged.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_triage_step.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import miner, triage_step


def test_atorvastatin_2011():
    m = miner.run("atorvastatin", "2011-12-31")
    r = triage_step.run(m, triage="locked")
    assert r["status"] == "ok" and r["label_date"] == "20090622"
    c = r["counts"]
    assert c["evans"] == 615 == m["n_evans"], c  # matches the plan's table: 615 flagged at 2011
    assert c["evans"] >= c["a>=3"] >= c["not_exact_on_label"] >= c["cap150"] == 150 == len(r["terms"]), c
    chi = [t["chi2"] for t in r["terms"]]
    assert chi == sorted(chi, reverse=True)
    names = [t["term"] for t in r["terms"]]
    assert "GLYCOSYLATED HAEMOGLOBIN INCREASED" in names  # the plan: best target PT HbA1c increased is inside the cap at 2011
    print("counts:", c, flush=True)


def test_no_label_is_clear():
    m = miner.run("vancomycin", "2019-12-31")  # vancomycin has no label in the RAG index (lithium and then ixazomib were the examples, until they were indexed)
    r = triage_step.run(m, triage="locked")
    assert r["status"] == "no_label" and "vancomycin" in r["message"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
