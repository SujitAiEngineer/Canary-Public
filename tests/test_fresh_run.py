"""Offline tests (no model, no network, no spend) for the fresh-run option and the single not-medical-advice notice.
A fresh run ignores the answer cache for model calls (Label Checker, Skeptic, brief writer) but still writes new answers to it; it is off by default;
the brief's provenance line says "fresh run"; the notice appears once at the top of brief.md.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_fresh_run.py"""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace

import canary_config, label_checker, model_router as M, orchestrator, provenance
from agents import skeptic, writer
from agents.writer import DISCLAIMER
from spend import Ledger, LedgerClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAIKU = M.HAIKU
VERDICT = {"winner": "real_effect", **{b: {"score": 0.5, "reason": "r", "cited_ids": ["R1"]} for b in skeptic.BRANCHES}}
PACKET = {"items": {"R1": "x"}, "blocks": {"R": ["R1"], "S": [], "C": [], "X": []}}
SK = {"term": "T", "winner": "real_effect", "evidence": PACKET, "evidence_hash": "h", "label_decision": "no", "label_date": "d",
      "branches": {b: {"score": 0.5} for b in skeptic.BRANCHES}}


class Fake:
    """Answers every forced tool call and counts the calls. Usable as a bare client, or under LedgerClient."""
    def __init__(self):
        self.n = 0
        self.messages = self

    def create(self, **kw):
        self.n += 1
        name = kw["tools"][0]["name"]
        data = {"record_label_decision": {"on_label": "no", "evidence": "", "confidence": 0.5}, "record_brief": {"claims": []}}.get(name, VERDICT)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name=name, input=data)], usage=SimpleNamespace(input_tokens=10, output_tokens=5), stop_reason="tool_use")


def with_temp_cache(fn):
    olds = (label_checker.CACHE_DIR, skeptic.CACHE_DIR, writer.CACHE_DIR)
    label_checker.CACHE_DIR = skeptic.CACHE_DIR = writer.CACHE_DIR = tempfile.mkdtemp()   # the fakes' answers never reach the real cache
    try:
        fn()
    finally:
        label_checker.CACHE_DIR, skeptic.CACHE_DIR, writer.CACHE_DIR = olds


def calls(fn_with_client, fresh):
    c = Fake()
    cl = LedgerClient(c, Ledger(os.path.join(tempfile.mkdtemp(), "l.jsonl"), 5.0), "t")
    cl.fresh = fresh
    fn_with_client(cl)
    return c.n


ONE = {
    "label_checker": lambda cl: label_checker.check(cl, "RASH", "[S] text", "x label of d", model=HAIKU),
    "skeptic": lambda cl: skeptic.judge(cl, "drug", "T", "2010-01-01", PACKET, HAIKU),
    "writer": lambda cl: writer.write_claims(cl, "drug", "2010-01-01", SK, [], HAIKU),
}


def test_off_by_default_the_cache_is_used():
    for name, fn in ONE.items():
        def run():
            assert calls(fn, False) == 1, name
            assert calls(fn, False) == 0, name   # second identical call: cache hit, no model call
        with_temp_cache(run)
    assert LedgerClient(Fake(), Ledger(os.path.join(tempfile.mkdtemp(), "l.jsonl"), 1.0)).fresh is False


def test_fresh_ignores_the_cache_but_still_writes_to_it():
    for name, fn in ONE.items():
        def run():
            assert calls(fn, False) == 1, name        # fills the cache
            assert calls(fn, True) == 1, name         # fresh: a real call although the answer is cached
            assert calls(fn, True) == 1, name         # and again: still real
            assert os.listdir(label_checker.CACHE_DIR), name   # new answers are written
            assert calls(fn, False) == 0, name        # a normal run afterwards uses what the fresh run wrote
        with_temp_cache(run)


def test_fresh_run_writes_the_answer_for_a_term_never_seen():
    def run():
        assert calls(ONE["label_checker"], True) == 1
        assert calls(ONE["label_checker"], False) == 0
    with_temp_cache(run)


def pipeline(fresh, tmp):
    seen = {}
    olds = (orchestrator.resolver.resolve, orchestrator.miner.run, orchestrator.triage_step.run, orchestrator.label_checker.run, orchestrator.skeptic.run, orchestrator.writer.run)
    orchestrator.resolver.resolve = lambda t: {"status": "resolved", "generic_name": "gabapentin", "rxcui": "1", "confidence": 1.0, "message": "ok"}
    orchestrator.miner.run = lambda *a, **k: {"status": "ok", "drug": "gabapentin", "date": "d", "n_drug": 1, "n_terms": 1, "n_evans": 1, "elapsed_s": 0, "terms": []}
    orchestrator.triage_step.run = lambda m, **k: {"status": "ok", "label_date": "x", "counts": {"evans": 0, "not_exact_on_label": 0}, "cap": 150, "terms": []}

    def lc(tr, client=None, **k):
        seen["fresh"] = client.fresh
        return {"status": "ok", "spend": 0, "n_checked": 0, "decisions": {"yes": 0, "partial": 0, "no": 0}, "label_date": "x", "n_to_skeptic": 0, "results": []}
    orchestrator.label_checker.run = lc
    orchestrator.skeptic.run = lambda m, lab, client=None, **k: {"status": "ok", "spend": 0, "n_evaluated": 0, "n_recommended": 0, "n_dismissed": 0, "results": []}
    # the real writer's head: title, then the notice, then the counts line
    md = f"# Canary brief: gabapentin, data before d\n*{DISCLAIMER}*\n\nTerms examined by the Skeptic: 0.\n"
    orchestrator.writer.run = lambda sk, lab, m, client=None, **k: {"status": "ok", "spend": 0, "brief_markdown": md, "n_written": 0, "n_recommended": 0, "removed_uncited": 0}
    try:
        res = orchestrator.run_pipeline("gabapentin", "2008-12-31", runs_dir=tmp, run_id="r1", ledger=Ledger(os.path.join(tmp, "l.jsonl"), 3.5), client=Fake(), fresh=fresh)
    finally:
        (orchestrator.resolver.resolve, orchestrator.miner.run, orchestrator.triage_step.run, orchestrator.label_checker.run, orchestrator.skeptic.run, orchestrator.writer.run) = olds
    return res, seen, open(os.path.join(tmp, "r1", "brief.md"), encoding="utf-8").read()


def test_the_run_flag_reaches_the_agents_client_and_the_provenance_line():
    res, seen, md = pipeline(True, tempfile.mkdtemp())
    assert seen["fresh"] is True and res["fresh"] is True and res["provenance"]["fresh"] is True
    assert "fresh run" in md.split("\n\n")[1] and "fresh run" in provenance.header_markdown(res["provenance"])
    res, seen, md = pipeline(False, tempfile.mkdtemp())
    assert seen["fresh"] is False and "fresh run" not in md


def test_the_notice_appears_once_at_the_top_of_the_brief():
    for fresh in (False, True):
        _, _, md = pipeline(fresh, tempfile.mkdtemp())
        assert md.count(DISCLAIMER) == 1, md[:700]
        assert md.startswith("# Canary brief") and "Terms examined by the Skeptic" in md
    old = f"# T\n\n> **Produced by**: model `m` (Anthropic); triage: X.\n> *{DISCLAIMER}*\n\n*{DISCLAIMER}*\n\nTerms examined: 1.\n"   # a brief written before the fix
    fixed = provenance.with_header(old, {})
    assert fixed.count(DISCLAIMER) == 1 and "Terms examined: 1." in fixed and fixed.startswith("# T\n\n> **Produced by**")
    assert provenance.with_header(fixed, {}) == fixed          # idempotent


def test_a_fresh_run_is_refused_in_evaluation_mode():
    try:
        orchestrator.run_pipeline("gabapentin", "2008-12-31", evaluation_mode=True, fresh=True, runs_dir=tempfile.mkdtemp())
    except ValueError as e:
        assert "fresh" in str(e)
    else:
        raise AssertionError("evaluation mode accepted a fresh run")


def test_the_cli_flag_and_ui_checkbox_exist_and_default_to_off():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, os.path.join(ROOT, "orchestrator.py"), "--help"], capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT).stdout
    assert "--fresh" in out
    import inspect
    assert inspect.signature(orchestrator.run_pipeline).parameters["fresh"].default is False
    web = lambda *p: open(os.path.join(ROOT, "web", "src", *p), encoding="utf-8").read()
    form, api_js = web("components", "RunForm.jsx"), web("api.js")
    assert "Fresh run" in form and "choice.fresh" in form and "fresh: false" in web("App.jsx") and "fresh" in api_js and "p.fresh" in web("components", "Brief.jsx")
    from api.main import RunIn
    assert RunIn().fresh is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
