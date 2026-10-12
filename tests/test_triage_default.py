"""Offline tests (no API, no spend) for the product default triage (Step 2): normal runs use EWT, the locked chi-squared triage is an explicit option, the evaluation
mode always uses the locked triage, an invalid choice is refused, and the API passes the choice through. The reproduction of the locked output on all 12 saved runs
(with the locked option called explicitly) is in tests/test_triage_ewt.py and tests/test_triage_prune.py.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_triage_default.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import canary_config, orchestrator
from agents import triage_step
from spend import Ledger


def capture(**kw):
    """Run the orchestrator up to the triage step with stubs (no data store, no API) and return the triage choice it passed on."""
    seen = {}
    old = (orchestrator.resolver.resolve, orchestrator.miner.run, orchestrator.triage_step.run)
    orchestrator.resolver.resolve = lambda text: {"status": "resolved", "generic_name": "gabapentin", "rxcui": "1", "confidence": 1.0, "message": "ok"}
    orchestrator.miner.run = lambda *a, **k: {"status": "ok", "drug": "gabapentin", "date": "2008-12-31", "n_drug": 1, "n_terms": 1, "n_evans": 1, "elapsed_s": 0, "terms": []}

    def fake(m, cap=150, **k):
        seen.update(k)
        return {"status": "no_label", "message": "stop here"}
    orchestrator.triage_step.run = fake
    try:
        tmp = tempfile.mkdtemp()
        res = orchestrator.run_pipeline("gabapentin", "2008-12-31", runs_dir=tmp, ledger=Ledger(os.path.join(tmp, "l.jsonl"), 1.0), client=object(), **kw)
    finally:
        orchestrator.resolver.resolve, orchestrator.miner.run, orchestrator.triage_step.run = old
    return seen.get("triage"), res


def test_config_default_is_ewt_and_choices_are_fixed():
    assert canary_config.DEFAULT_TRIAGE == "ewt" and canary_config.TRIAGE_CHOICES == ("ewt", "locked")


def test_normal_runs_use_ewt_by_default():
    assert capture()[0] == "ewt"


def test_the_locked_triage_is_an_explicit_option():
    assert capture(triage="locked")[0] == "locked"
    assert capture(triage="ewt")[0] == "ewt"


def test_evaluation_mode_always_uses_the_locked_triage_unless_told_otherwise_explicitly():
    t, res = capture(evaluation_mode=True, run_label="gabapentin 2008Q4")
    assert t == "locked" and res["triage"] == "locked"
    assert capture(evaluation_mode=True, run_label="gabapentin-EWT 2008Q4", triage="ewt")[0] == "ewt"   # the labelled post-result confirmation run only


def test_the_run_records_which_triage_it_used():
    assert capture()[1]["triage"] == "ewt" and capture(triage="locked")[1]["triage"] == "locked"


def test_invalid_triage_is_refused():
    for bad in ("chi2", "EWT ", "v2"):
        try:
            capture(triage=bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    try:
        triage_step.run({"status": "ok"}, triage="nope")
    except ValueError:
        return
    raise AssertionError("triage_step accepted an unknown choice")


def test_triage_step_explicit_locked_ignores_every_opt_in_flag():
    out = triage_step.run({"status": "skipped", "drug": "x", "date": "2020-12-31"}, triage="locked", ewt=True, prune="P", stratified=True)
    assert out["status"] == "skipped"          # reached the same early return: the flags did not matter


def test_api_passes_the_choice_and_rejects_a_bad_one():
    from fastapi.testclient import TestClient
    import api.main as m
    c = TestClient(m.app)
    assert c.post("/api/runs", json={"drug": "x", "date": "2020-12-31", "triage": "bogus"}).status_code == 400
    assert m.RunIn(drug="x", date="2020-12-31").triage is None and m.RunIn(drug="x", date="2020-12-31", triage="locked").triage == "locked"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
