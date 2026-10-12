"""Offline tests of run_ewt_confirmation_gabapentin.py and the opt-in triage argument: fake pipeline, temp ledger and output folder. Nothing is spent and no real
result file is touched.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_ewt_confirmation.py"""
import inspect, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval_config, orchestrator
import run_ewt_confirmation_gabapentin as conf
from spend import Ledger
from test_run_evaluation import make_run

T = eval_config.CASES["gabapentin"]["terms"]


class Pipe:
    def __init__(self, plan):
        self.plan, self.calls = plan, []

    def __call__(self, case, date, evaluation_mode=False, run_label="", runs_dir="runs", ledger=None, triage="locked", **kw):
        assert evaluation_mode is True
        self.calls.append((case, date, run_label, ledger.limit, triage))
        o = self.plan.get(date, "noflag")
        if o == "error":
            return {"status": "crashed", "run_dir": None, "message": "boom"}
        d = make_run(runs_dir, run_label, T, o)
        json.dump({"status": "ok", "ewt": True, "counts": {"variant": "EWT"}, "terms": [{"term": t} for t in T]}, open(os.path.join(d, "triage.json"), "w"))
        open(os.path.join(d, "brief.md"), "w", encoding="utf-8").write("# Brief\n\n## " + T[0] + "\nA short paragraph about the term [R1].\n")
        return {"status": "ok", "run_dir": d, "run_spend": 0.2}


def go(plan):
    tmp = tempfile.mkdtemp()
    old = orchestrator.EVAL_LEDGER
    orchestrator.EVAL_LEDGER = os.path.join(tmp, "eval_spend.jsonl")
    try:
        prior = Ledger(orchestrator.EVAL_LEDGER, 9.5)
        prior.record(run="gabapentin 2006Q4", stage="skeptic", term="", model="m", in_tok=0, out_tok=0, cost=5.2, batch="old")
        for q in conf.QUARTERS:   # stand-ins for the ledger rows the real pipeline would write
            prior.record(run=f"gabapentin-EWT {q}", stage="skeptic", term="", model="m", in_tok=1, out_tok=1, cost=0.3, batch=f"msgbatch_{q}")
        pipe = Pipe(plan)
        out = os.path.join(tmp, "out")
        try:
            res = conf.run(pipeline=pipe, out_dir=out, log=lambda *a: None, allow_dirty=True)
        except SystemExit as e:
            res = {"exit": str(e)}
        return res, pipe, out
    finally:
        orchestrator.EVAL_LEDGER = old


def test_both_cutoffs_run_with_ewt_triage_even_after_a_detection_and_the_ledger_is_separate():
    res, pipe, out = go({"2008-12-31": "recommended"})
    assert [c[1] for c in pipe.calls] == ["2008-12-31", "2009-12-31"]            # both run although the first detected
    assert all(c[2].startswith("gabapentin-EWT ") and c[4] == "ewt" for c in pipe.calls)
    assert res["status"].startswith("both cutoffs run") and "2008Q4 DETECTED" in res["status"] and "2009Q4 not detected" in res["status"]
    md = open(os.path.join(out, "ewt_confirmation_gabapentin.md"), encoding="utf-8").read()
    assert "3 of 4" in md and "tuned evidence" in md and "msgbatch_2008Q4" in md and "Brief excerpt" in md and "McNemar" in md
    assert os.listdir(out) and not os.path.exists("eval_out/ewt_confirmation_gabapentin.json")


def test_the_hard_stop_is_one_dollar_above_the_ledger_total_at_start():
    res, pipe, out = go({})
    start = 5.2 + 0.3 * len(conf.QUARTERS)
    assert abs(pipe.calls[0][3] - (start + 1.00)) < 1e-6


def test_an_error_stops_the_run_at_once():
    res, pipe, out = go({"2008-12-31": "error"})
    assert "ERROR at 2008Q4" in res["exit"] and len(pipe.calls) == 1


def test_run_pipeline_passes_the_triage_choice_through():
    src = inspect.getsource(orchestrator.run_pipeline)
    assert "triage=triage" in src and inspect.signature(orchestrator.run_pipeline).parameters["triage"].default is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
