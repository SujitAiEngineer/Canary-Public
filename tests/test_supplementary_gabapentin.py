"""Offline tests of run_supplementary_gabapentin.py: fake pipeline, temp ledger and output folder. Nothing is spent and no real result file is touched.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_supplementary_gabapentin.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval_config, orchestrator
import run_supplementary_gabapentin as sup
from spend import Ledger
from test_run_evaluation import make_run

T = eval_config.CASES["gabapentin"]["terms"]


class Pipe:
    def __init__(self, plan):
        self.plan, self.calls = plan, []
    def __call__(self, case, date, evaluation_mode=False, run_label="", runs_dir="runs", ledger=None, **kw):
        assert evaluation_mode is True
        self.calls.append((case, date, run_label, ledger.limit))
        o = self.plan.get(date, "noflag")
        if o == "error":
            return {"status": "crashed", "run_dir": None, "message": "boom"}
        if o == "stop":
            return {"status": "stopped_spend_limit", "run_dir": make_run(runs_dir, run_label, [], "noflag"), "run_spend": 0.1, "message": "spend limit would be passed"}
        d = make_run(runs_dir, run_label, T, o)
        return {"status": "ok", "run_dir": d, "run_spend": 0.5}


def go(plan):
    tmp = tempfile.mkdtemp()
    old = orchestrator.EVAL_LEDGER
    orchestrator.EVAL_LEDGER = os.path.join(tmp, "eval_spend.jsonl")
    try:
        prior = Ledger(orchestrator.EVAL_LEDGER, 9.5)
        prior.record(run="gabapentin 2006Q4", stage="skeptic", term="", model="m", in_tok=0, out_tok=0, cost=3.416268, batch="old")
        pipe = Pipe(plan)
        for q in sup.QUARTERS:   # stand-ins for the ledger rows the real pipeline would write
            prior.record(run=f"gabapentin-SUPP {q}", stage="skeptic", term="", model="m", in_tok=1, out_tok=1, cost=0.5, batch=f"msgbatch_{q}")
        out = os.path.join(tmp, "out"); os.makedirs(out)
        try:
            res = sup.run(pipeline=pipe, out_dir=out, log=lambda *a: None, allow_dirty=True)
        except SystemExit as e:
            res = {"exit": str(e)}
        return res, pipe, out
    finally:
        orchestrator.EVAL_LEDGER = old


def test_stops_at_first_detection_and_labels_the_ledger_separately_and_caps_the_spend():
    res, pipe, out = go({"2008-12-31": "recommended"})
    assert [c[1] for c in pipe.calls] == ["2007-12-31", "2008-12-31"]               # 2009Q4 and 2010Q4 are not run
    assert all(c[2].startswith("gabapentin-SUPP ") for c in pipe.calls)              # ledger rows stay separate from the main gabapentin runs
    assert res["status"].startswith("detected at 2008Q4")
    md = open(os.path.join(out, "eval_supplementary_gabapentin.md"), encoding="utf-8").read()
    assert "post-result supplementary run" in md and "3 of 4" in md and "Detected at 2008Q4" in md and "msgbatch_2008Q4" in md
    assert "months vs the FDA posting" in md and "+" in md.split("Lead time of that detection:")[1][:40]   # a positive lead time vs posting


def test_all_four_run_when_nothing_is_detected_and_it_says_a_miss():
    res, pipe, out = go({})
    assert [c[1] for c in pipe.calls] == ["2007-12-31", "2008-12-31", "2009-12-31", "2010-12-31"]
    assert res["status"] == "not detected at any of the four year-ends"
    md = open(os.path.join(out, "eval_supplementary_gabapentin.md"), encoding="utf-8").read()
    assert "Not detected at any run made" in md and "a miss, not zero lead time" in md


def test_hard_stop_is_start_plus_two_fifty():
    res, pipe, out = go({})
    limit = pipe.calls[0][3]
    assert abs(limit - min(9.5, (3.416268 + 0.5 * 4) + 2.50)) < 1e-6   # the start total here includes the 4 stand-in rows; the real run starts at 3.416268 -> 5.916268


def test_a_spend_stop_and_an_error_both_stop_the_run():
    res, pipe, out = go({"2007-12-31": "stop"})
    assert res["status"].startswith("STOPPED by the spend limit at 2007Q4") and len(pipe.calls) == 1
    res, pipe, out = go({"2007-12-31": "error"})
    assert "ERROR at 2007Q4" in res["exit"] and len(pipe.calls) == 1


def test_it_never_touches_the_main_result_files():
    src = open("run_supplementary_gabapentin.py", encoding="utf-8").read()
    assert "eval_results.md" in src and "state.json" in src   # mentioned only in comments about NOT touching them
    for forbidden in ('open(os.path.join(out_dir, "eval_results.md")', 'state.json", "w"'):
        assert forbidden not in src


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
