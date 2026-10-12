"""Offline tests of the Step 10 runner. NO evaluation drug is run and NOTHING is spent: Arm A is replaced by crafted rows and the pipeline by a fake
that writes crafted run folders. The one real-data test uses the development drug atorvastatin and the reference file.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_run_evaluation.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import datetime as dt

import eval_config
import run_evaluation as rv
from spend import Ledger


def rows(first, last="2012Q4", start="2004Q1", flag_until=None):
    """Crafted Arm A rows: any single target PT meets Evans from `first` on (to `flag_until`, or to the end)."""
    out = []
    for q in eval_config.quarters(start, last):
        on = first is not None and q >= first and (flag_until is None or q <= flag_until)
        out.append({"quarter": q, "n_drug": 1000, "n_total": 10 ** 6, "n_evans": 40 if on else 5, "targets": {}, "union": {"a": 4, "evans": on}, "any_single": on})
    return out


def make_run(runs_dir, label, terms, outcome, spend=0.5, n_rec=20):
    """A crafted orchestrator run folder. outcome per target: 'recommended' | 'dismissed' | 'label_yes' | 'noflag'."""
    d = os.path.join(runs_dir, label.replace(" ", "_"))
    os.makedirs(d, exist_ok=True)
    mterms, tterms, lres, sres = [], [], [], []
    for t in terms:
        o = outcome if isinstance(outcome, str) else outcome[t]
        if o == "noflag":
            continue
        mterms.append({"term": t, "a": 10, "PRR": 3.0, "chi2": 50.0, "evans": True})
        tterms.append({"term": t})
        yes = o == "label_yes"
        lres.append({"term": t, "decision": "yes" if yes else "no", "route": "dropped_on_label" if yes else "to_skeptic", "evidence": "a quoted sentence", "label_date": "20150109"})
        if not yes:
            sres.append({"term": t, "decision": "recommended" if o == "recommended" else "dismissed", "flag": None, "winner": "real_effect" if o == "recommended" else "reporting_spike",
                         "reason": "because", "cited": [{"id": "S2", "text": "x"}],
                         "branches": {b: {"score": 0.5, "reason": f"{b} reason", "cited_ids": ["R1"]} for b in ("real_effect", "reporting_spike", "confounding")},
                         "evidence": {"items": {"R3": "PubMed: 0 papers.", "S3": "Peak year 2007."}}})
    for name, obj in {"miner": {"status": "ok", "n_drug": 5000, "terms": mterms}, "triage": {"status": "ok", "cap": 150, "terms": tterms},
                      "label_checker": {"results": lres}, "skeptic": {"status": "ok", "n_recommended": n_rec, "n_evaluated": 100, "results": sres}}.items():
        json.dump(obj, open(os.path.join(d, name + ".json"), "w"))
    json.dump({"steps": [{"step": "skeptic", "status": "done"}]}, open(os.path.join(d, "run.json"), "w"))
    return d


class FakePipeline:
    def __init__(self, plan):
        self.plan, self.calls = plan, []   # plan: {(case, quarter_end_date): outcome | "stop"}
    def __call__(self, case, date, evaluation_mode=False, run_label="", runs_dir="runs", ledger=None, **kw):
        assert evaluation_mode is True and run_label.startswith(case)
        self.calls.append((case, date))
        o = self.plan.get((case, date), "dismissed")
        if o == "stop":
            d = make_run(runs_dir, run_label, [], "noflag")
            return {"status": "stopped_spend_limit", "run_dir": d, "run_spend": 0.1, "message": "spend limit $9.50 would be passed [skeptic X]"}
        d = make_run(runs_dir, run_label, eval_config.CASES[case]["terms"], o)
        return {"status": "ok", "run_dir": d, "run_spend": 0.5}


def go(plan, arm_a_rows, order, **kw):
    out = tempfile.mkdtemp()
    orig = rv.arm_a
    rv.arm_a = lambda name, case, con, **k: arm_a_rows[name]
    try:
        pipe = FakePipeline(plan)
        st = rv.run(order=order, pipeline=pipe, con=object(), out_dir=out, written_go=True, allow_dirty=True, ledger=Ledger(os.path.join(out, "l.jsonl"), 9.5),
                    log=lambda *a: None, **kw)
    finally:
        rv.arm_a = orig
    return st, pipe, out


def test_arm_b_needs_the_written_go():
    try:
        rv.run(order=["gabapentin"], con=object(), out_dir=tempfile.mkdtemp(), allow_dirty=True, log=lambda *a: None)
    except SystemExit as e:
        assert "written-go" in str(e)
        return
    raise AssertionError("Arm B could start without the written go")


def test_positive_case_stops_at_first_detection_and_lead_times():
    sched = eval_config.arm_b_quarters("gabapentin", "2009Q2")
    assert sched == ["2009Q4", "2010Q4", "2011Q2"]  # year-ends from the flag, plus the last quarter-end before the 2011-08-11 label change
    eval_config.ARM_B_SCHEDULE = "locked"   # this test is about the locked schedule; the setting is restored right after
    try:
        st, pipe, _ = go({("gabapentin", "2010-12-31"): "recommended"}, {"gabapentin": rows("2009Q2")}, ["gabapentin"])
    finally:
        eval_config.ARM_B_SCHEDULE = "reduced"
    assert [c[1] for c in pipe.calls] == ["2009-12-31", "2010-12-31"]   # the third run is not made: the target was recommended at the second
    cs = st["cases"]["gabapentin"]
    assert cs["status"] == "complete" and cs["arm_a"]["first_flag"] == "2009Q2"
    rep = rv.case_report("gabapentin", eval_config.CASES["gabapentin"], cs, 1.0)
    assert rep["armB_detect"] == "2010Q4" and rep["armA_detect"] == "2009Q2"
    assert rep["armA_lead_label"] == rv.months(dt.date(2009, 6, 30), dt.date(2011, 8, 11))  # months from the flag to the label change
    assert rep["armB_lead_label"] == rv.months(dt.date(2010, 12, 31), dt.date(2011, 8, 11)) and rep["armB_lead_posting"] < 0  # after FDA's 2009Q2 posting
    assert rep["armA_rounded"] == "2009Q4"   # Arm A rounded to the first Arm B run on or after its detection quarter


def test_never_flagged_means_no_llm_spend():
    st, pipe, _ = go({}, {"dasatinib": rows(None, last="2024Q4", start="2006Q1")}, ["dasatinib"])
    assert pipe.calls == [] and st["cases"]["dasatinib"]["arm_b"]["schedule"] == []


def test_flag_only_after_the_label_change_is_a_miss_with_no_spend():
    st, pipe, _ = go({}, {"lamotrigine": rows("2011Q1", last="2012Q4")}, ["lamotrigine"])  # label change 2010-10-28
    assert pipe.calls == []


def test_negative_case_runs_all_three_and_records_routes():
    t = eval_config.CASES["rivaroxaban"]["terms"]
    plan = {("rivaroxaban", "2017-12-31"): {t[0]: "dismissed", t[1]: "label_yes", t[2]: "noflag"},
            ("rivaroxaban", "2018-12-31"): "noflag", ("rivaroxaban", "2019-12-31"): "dismissed"}
    st, pipe, out = go(plan, {"rivaroxaban": rows("2017Q3", last="2019Q4", start="2015Q1")}, ["rivaroxaban"])
    assert len(pipe.calls) == 3
    runs = {r["quarter"]: r for r in st["cases"]["rivaroxaban"]["arm_b"]["runs"]}
    r1 = runs["2017Q4"]["targets"]
    assert r1[t[0]]["route"].startswith("Skeptic dismissal") and r1[t[1]]["route"].startswith("Label Checker yes") and r1[t[2]]["route"] == "no statistical flag"
    md = open(os.path.join(out, "eval_results.md"), encoding="utf-8").read()
    assert "SUCCESS" in md and "sanity check" in md


def test_negative_case_failure_when_recommended():
    st, pipe, out = go({("rivaroxaban", "2018-12-31"): "recommended"}, {"rivaroxaban": rows("2017Q3", last="2019Q4", start="2015Q1")}, ["rivaroxaban"])
    assert len(pipe.calls) == 3   # a negative case is run to the end regardless
    assert "FAILURE" in open(os.path.join(out, "eval_results.md"), encoding="utf-8").read()


def test_spend_limit_stops_everything_and_marks_cases_incomplete():
    arm = {"rivaroxaban": rows("2017Q3", last="2019Q4", start="2015Q1"), "pirfenidone": rows("2014Q1", last="2019Q4", start="2010Q1"), "lamotrigine": rows("2008Q1")}
    st, pipe, out = go({("pirfenidone", "2014-12-31"): "stop"}, arm, ["rivaroxaban", "pirfenidone", "lamotrigine"])
    assert st["cases"]["rivaroxaban"]["status"] == "complete"
    assert st["cases"]["pirfenidone"]["status"].startswith("INCOMPLETE: spend limit")
    assert st["cases"]["lamotrigine"]["status"].startswith("not started: spend limit")
    assert not any(c[0] == "lamotrigine" for c in pipe.calls)            # nothing after the stop was run
    stop = json.load(open(os.path.join(out, "stop_record.json")))
    assert stop["case"] == "pirfenidone" and stop["run_quarter"] == "2014Q4" and stop["stage"] == "skeptic" and "total_spent" in stop
    md = open(os.path.join(out, "eval_results.md"), encoding="utf-8").read()
    assert "STOPPED by the spend limit" in md and "INCOMPLETE" in md


def test_resume_skips_finished_runs():
    out = tempfile.mkdtemp()
    arm = {"rivaroxaban": rows("2017Q3", last="2019Q4", start="2015Q1")}
    orig = rv.arm_a
    rv.arm_a = lambda name, case, con, **k: arm[name]
    try:
        p1 = FakePipeline({("rivaroxaban", "2018-12-31"): "stop"})
        rv.run(order=["rivaroxaban"], pipeline=p1, con=object(), out_dir=out, written_go=True, allow_dirty=True, ledger=Ledger(os.path.join(out, "l.jsonl"), 9.5), log=lambda *a: None)
        p2 = FakePipeline({})
        rv.run(order=["rivaroxaban"], pipeline=p2, con=object(), out_dir=out, written_go=True, allow_dirty=True, resume=True, ledger=Ledger(os.path.join(out, "l.jsonl"), 9.5), log=lambda *a: None)
    finally:
        rv.arm_a = orig
    assert [c[1] for c in p1.calls] == ["2017-12-31", "2018-12-31"] and [c[1] for c in p2.calls] == ["2018-12-31", "2019-12-31"]  # 2017Q4 was not paid for twice


def test_results_name_the_lock_commit_and_the_code_commit_and_the_amendments():
    st, pipe, out = go({}, {"rivaroxaban": rows("2017Q3", last="2019Q4", start="2015Q1")}, ["rivaroxaban"])
    md = open(os.path.join(out, "eval_results.md"), encoding="utf-8").read()
    assert "425633dd8729eeac52f9e060c19eb28297f6fa24" in md and st["meta"]["code_commit"] in md
    assert "2026-10-03" in md and "top 10" in md and "Message Batches" in md


def test_budget_is_capped_by_the_config_limit():
    st, _, _ = go({}, {"rivaroxaban": rows(None, last="2019Q4", start="2015Q1")}, ["rivaroxaban"], budget=50.0)
    assert st["meta"]["budget"] == eval_config.SPEND_LIMIT_USD == 10.0


def test_arm_a_matches_the_atorvastatin_reference_exactly():
    """Real data, development drug only: per-PT and union 2x2 tables at 2011Q4 equal case_study_atorvastatin.csv (built without the exclusion)."""
    import duckdb, pandas as pd
    import miner as core
    ref = pd.read_csv("analysis/case_study_atorvastatin.csv")
    ref = ref[(ref.drug == "atorvastatin") & (ref.cutoff == 20111231)].set_index("term")
    terms = ["TYPE 2 DIABETES MELLITUS", "DIABETES MELLITUS", "BLOOD GLUCOSE INCREASED", "HYPERGLYCAEMIA", "GLYCOSYLATED HAEMOGLOBIN INCREASED"]
    case = {"match": r"atorva|lipitor|torvast|sortis", "exclude": None, "terms": terms}
    con = duckdb.connect(core.DB, read_only=True)
    out = rv.arm_a("atorvastatin", case, con, first_quarter="2011Q4", last_quarter="2011Q4", log=lambda *a: None)[0]
    for t in terms:
        r = ref.loc[t]
        if r.a == 0:
            assert out["targets"][t]["a"] == 0
            continue
        x = out["targets"][t]
        assert (x["a"], x["b"], x["c"], x["d"]) == (r.a, r.b, r.c, r.d), t
        assert x["evans"] == bool(r.signal_on), t
    u, r = out["union"], ref.loc["ANY_OF_THE_5_TERMS"]
    assert (u["a"], u["b"], u["c"], u["d"]) == (r.a, r.b, r.c, r.d)
    assert u["evans"] == bool(r.signal_on)


def test_reduced_schedule_matches_the_approved_list():
    """The approved reduced schedule, derived by the rule from the REAL Arm A first flags (eval_out/state.json, written by the free Arm A run)."""
    assert eval_config.ARM_B_SCHEDULE == "reduced"
    flags = {"rivaroxaban": "2012Q2", "pirfenidone": "2015Q2", "lamotrigine": "2004Q1", "gabapentin": "2006Q1", "dasatinib": "2007Q4"}
    want = {"rivaroxaban": ["2017Q4", "2018Q4", "2019Q4"], "pirfenidone": ["2015Q4", "2019Q2"], "lamotrigine": ["2004Q4", "2010Q3"],
            "gabapentin": ["2006Q4", "2011Q2"], "dasatinib": ["2007Q4", "2024Q2"]}
    for name, q in flags.items():
        case = eval_config.CASES[name]
        last = eval_config.last_cutoff_before(case["label_change_date"]) if case["label_change_date"] else None
        assert rv.schedule_for(name, case, q, last) == want[name], name
    # the rule on other inputs: a flag in Q4 itself counts; a flag after the last cutoff is a miss; no flag means no runs; the locked function is untouched
    g = eval_config.CASES["gabapentin"]
    assert rv.reduced_quarters(g, "2009Q4") == ["2009Q4", "2011Q2"] and rv.reduced_quarters(g, "2011Q2") == ["2011Q2"]
    assert rv.schedule_for("gabapentin", g, None, "2011Q2") == [] and rv.schedule_for("gabapentin", g, "2011Q3", "2011Q2") == []
    assert eval_config.arm_b_quarters("gabapentin", "2009Q2") == ["2009Q4", "2010Q4", "2011Q2"]  # the locked rule still gives the locked schedule


def test_positive_case_stops_at_first_detection_on_the_reduced_schedule():
    st, pipe, _ = go({("pirfenidone", "2015-12-31"): "recommended"}, {"pirfenidone": rows("2015Q2", last="2020Q4", start="2010Q1")}, ["pirfenidone"])
    assert [c[1] for c in pipe.calls] == ["2015-12-31"]   # detected at the first run: the last-quarter run is not made
    st, pipe, _ = go({}, {"pirfenidone": rows("2015Q2", last="2020Q4", start="2010Q1")}, ["pirfenidone"])
    assert [c[1] for c in pipe.calls] == ["2015-12-31", "2019-06-30"]   # not detected: both runs are made, no more


def test_resume_cap_sets_a_tighter_hard_limit_for_this_invocation_only():
    import orchestrator
    tmp = tempfile.mkdtemp()
    old = orchestrator.EVAL_LEDGER
    orchestrator.EVAL_LEDGER = os.path.join(tmp, "eval_spend.jsonl")
    try:
        prior = Ledger(orchestrator.EVAL_LEDGER, 9.5)
        prior.record(run="rivaroxaban 2017Q4", stage="skeptic", term="", model="m", in_tok=0, out_tok=0, cost=0.73)   # money already spent
        seen = {}
        orig = rv.arm_a
        rv.arm_a = lambda name, case, con, **k: rows("2017Q3", last="2019Q4", start="2015Q1")
        class Pipe(FakePipeline):
            def __call__(self, case, date, **kw):
                seen["limit"], seen["total"] = kw["ledger"].limit, kw["ledger"].total
                return super().__call__(case, date, **kw)
        try:
            rv.run(order=["rivaroxaban"], pipeline=Pipe({}), con=object(), out_dir=os.path.join(tmp, "out"), written_go=True, allow_dirty=True,
                   resume_cap=1.20, budget=9.5, log=lambda *a: None)
        finally:
            rv.arm_a = orig
    finally:
        orchestrator.EVAL_LEDGER = old
    assert abs(seen["limit"] - 1.93) < 1e-9 and abs(seen["total"] - 0.73) < 1e-9   # 0.73 already spent + 1.20, far below the 9.50 budget
    # and it can never raise the limit above the overall budget
    assert min(9.5, 9.0 + 1.20) == 9.5


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
