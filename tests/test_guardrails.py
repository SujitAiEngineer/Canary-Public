"""Build Step 7: each guardrail is triggered once. No API calls are made (a stub client fails the test if it is ever used).
Run: PYTHONIOENCODING=utf-8 python -u tests/test_guardrails.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import canary_config, feedback, orchestrator
from agents.writer import validate_claims, run as write_brief, DISCLAIMER
from spend import Ledger, LedgerClient, SpendLimit


class NoApi:
    """Stands in for the Anthropic client. Any call is a test failure."""
    class messages:
        @staticmethod
        def create(**kw):
            raise AssertionError("the API must not be called")


def pipeline(drug, date, **kw):
    tmp = tempfile.mkdtemp()
    return orchestrator.run_pipeline(drug, date, runs_dir=tmp, client=NoApi, ledger=Ledger(os.path.join(tmp, "l.jsonl"), 1.0), **kw)


def test_1_did_you_mean():
    r = pipeline("atorvastatn", "2011-12-31")
    assert r["status"] == "needs_confirmation" and [s["step"] for s in r["steps"] if s["status"] != "start"] == ["resolver"]
    assert "atorvastatin" in r["message"]  # the suggestion is shown, nothing was mined


def test_2_not_enough_data():
    r = pipeline("ixazomib", "2013-12-31")
    assert r["status"] == "not_enough_data" and "suspect reports" in r["message"]
    assert "triage" not in [s["step"] for s in r["steps"]]  # the run stops at the Miner


def test_3_citation_rule():
    pk = {"items": {"R1": "x"}}
    kept, removed = validate_claims([{"text": "no source", "cited_ids": [], "label_quote": ""}, {"text": "sourced", "cited_ids": ["R1"], "label_quote": ""}], pk, [""])
    assert [k["text"] for k in kept] == ["sourced"] and len(removed) == 1


def test_4_spend_stop():
    tmp = tempfile.mkdtemp()
    ledger = Ledger(os.path.join(tmp, "l.jsonl"), 0.0005)  # half a tenth of a cent
    lc = LedgerClient(NoApi, ledger)
    try:
        lc.create(model="claude-haiku-4-5-20251001", max_tokens=500, system="s", tools=[], messages=[{"role": "user", "content": "x" * 3000}])
    except SpendLimit as e:
        assert "limit" in str(e)
        return
    raise AssertionError("the spend limit did not stop the call")


def test_4b_ledger_records_real_cost():
    class R:  # a fake API response with usage
        class usage:
            input_tokens, output_tokens = 1000, 100
    class C:
        class messages:
            @staticmethod
            def create(**kw): return R
    tmp = tempfile.mkdtemp()
    ledger = Ledger(os.path.join(tmp, "l.jsonl"), 1.0)
    LedgerClient(C, ledger, "run1").create(model="claude-haiku-4-5-20251001", max_tokens=100, system="s", tools=[], messages=[])
    assert abs(ledger.total - (1000 * 1.0 + 100 * 5.0) / 1e6) < 1e-9
    assert Ledger(os.path.join(tmp, "l.jsonl"), 1.0).total == ledger.total  # survives a restart


def test_5_notice_in_every_brief():
    sk = {"agent": "skeptic", "drug": "x", "date": "2011-12-31", "results": []}
    brief = write_brief(sk, {"results": []}, {"terms": []}, client=NoApi)["brief_markdown"]
    assert "not medical advice" in brief and brief.strip().endswith("*") and DISCLAIMER in brief
    runs = sorted(os.listdir(canary_config.RUNS_DIR))
    real = [r for r in runs if r.endswith("lipitor")]
    if real:
        assert "not medical advice" in open(os.path.join(canary_config.RUNS_DIR, real[-1], "brief.md"), encoding="utf-8").read()


def test_6_feedback_log():
    tmp, log = tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "fb.jsonl")
    os.makedirs(os.path.join(tmp, "run1"))
    json.dump({"drug": "d", "date": "2011-12-31", "results": [{"term": "A", "decision": "recommended"}, {"term": "B", "decision": "dismissed"}]},
              open(os.path.join(tmp, "run1", "skeptic.json"), "w"))
    row = feedback.record("run1", "A", "approve", "", tmp, log)
    feedback.record("run1", "A", "reject", "label already covers it", tmp, log)
    for bad in (("A", "reject", " "), ("B", "approve", "x"), ("A", "maybe", "x")):  # no reason, not a recommended term, unknown decision
        try:
            feedback.record("run1", *bad, runs_dir=tmp, log=log)
        except ValueError:
            continue
        raise AssertionError(bad)
    lines = [json.loads(l) for l in open(log)]
    assert len(lines) == 2 and lines[1]["reason"] == "label already covers it" and row["drug"] == "d"


def test_7_evaluation_drug_blocked():
    r = pipeline("Neurontin", "2008-12-31", confirm=None)  # Neurontin resolves to gabapentin, an evaluation drug
    assert r["status"] == "blocked" and "evaluation drug" in r["message"]


def test_8_evaluation_mode_is_explicit():
    from agents import miner as miner_agent
    for d in ("gabapentin", "pirfenidone", "lamotrigine", "dasatinib", "rivaroxaban"):
        try:
            miner_agent.guard(d)
        except PermissionError:
            miner_agent.guard(d, allow_evaluation=True)  # only the explicit flag passes
            continue
        raise AssertionError(d)
    miner_agent.guard("atorvastatin")
    try:  # evaluation mode refuses any model but the evaluation model, before anything runs
        orchestrator.run_pipeline("gabapentin", "2008-12-31", evaluation_mode=True, brief_model=canary_config.DEMO_BRIEF_MODEL, client=NoApi)
    except ValueError:
        return
    raise AssertionError("evaluation mode accepted a Sonnet brief")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
