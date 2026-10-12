"""Build Step 4 test: the Label Checker agent reproduces the 32-row gold set (27 of 32, +-1) and every output cites a sentence and
a label date. Haiku, cached (the locked prompt and retrieval give identical prompts, so this normally costs $0).
Run: PYTHONIOENCODING=utf-8 python -u tests/test_label_checker_agent.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anthropic
import pandas as pd
from agents import label_checker as agent

CUTOFF = "2026-10-02"  # the gold set was scored against the current label, as in task7_label_checker.py


def test_gold_set():
    gold = pd.read_csv("label_gold_set_v2_DRAFT.csv", comment="#")
    client, hit, spend, valid, cited = anthropic.Anthropic(), 0, 0.0, 0, 0
    for g in gold.itertuples():
        r = agent.check_term(client, g.drug, g.reaction, CUTOFF)
        spend += r["cost"]
        hit += r["decision"] == g.gold_on_label
        valid += r["valid"]
        assert r["label_date"], g.reaction
        assert r["decision"] == "no" or r["evidence"], (g.reaction, r)  # yes/partial must quote a sentence
        cited += bool(r["evidence"]) or r["decision"] == "no"
        assert r["backend"] == "chroma"
    print(f"gold set: {hit}/{len(gold)} correct, {valid}/{len(gold)} valid tool calls, spend ${spend:.4f}", flush=True)
    assert len(gold) == 32 and valid == 32 and abs(hit - 27) <= 1, hit


def test_routing_rule():
    """yes drops, partial and no pass on (partial marked), invalid never drops."""
    tr = {"status": "ok", "drug": "atorvastatin", "date": CUTOFF,
          "terms": [{"term": t, "a": 1, "b": 1, "c": 1, "d": 1, "PRR": 1.0, "chi2": 1.0} for t in ("MYALGIA", "MYOCARDIAL INFARCTION", "LIVER INJURY")]}
    r = agent.run(tr)
    by = {x["term"]: x for x in r["results"]}
    assert by["MYALGIA"]["route"] == "dropped_on_label"
    assert by["MYOCARDIAL INFARCTION"]["route"] == "to_skeptic"  # owner's decision: gold "no"
    for x in r["results"]:
        if x["decision"] == "partial":
            assert x["route"] == "to_skeptic" and x["note"].startswith("partially labeled")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
