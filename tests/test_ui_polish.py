"""Offline tests (no model, no network, no spend) for the UI polish: model and triage options, provenance on every brief, the free model's tested label read from
model_portability.md, clear errors for a missing or rejected key with other models offered, the free model's waiting state and resume, the daily-limit stop, demo mode
without keys, and that the React source carries what the page must show. The full run of every option is in tests/test_api_smoke.py.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_ui_polish.py"""
import contextlib, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from types import SimpleNamespace
from fastapi.testclient import TestClient

import api.main as api
import canary_config, label_checker, model_router as M, orchestrator, provenance
from spend import Ledger
from smoke_support import FakeResp

C = TestClient(api.app)
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "src")
TOOL = label_checker.TOOL
GOOD = {"on_label": "no", "evidence": "", "confidence": 0.5}


@contextlib.contextmanager
def env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    for k, v in kw.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def src(*p):
    return open(os.path.join(WEB, *p), encoding="utf-8").read()


# ---------------------------------------------------------------- options shown in the UI
def test_models_endpoint_shows_provider_model_id_cost_and_a_free_label():
    d = C.get("/api/models").json()
    by = {m["id"]: m for m in d["models"]}
    assert set(by) == set(M.MODELS) and len(by) == 4
    for m in d["models"]:
        assert m["provider_label"] in ("Anthropic", "OpenRouter") and m["model_id"] and "estimate_usd" in m and "available" in m
    for mid in (M.HAIKU, M.SONNET, M.OPUS):
        assert by[mid]["provider_label"] == "Anthropic" and by[mid]["free"] is False and by[mid]["paid"] is True and by[mid]["estimate_usd"] > 0
        assert by[mid]["model_id"] == mid
    f = by[M.OR_FREE]
    assert f["provider_label"] == "OpenRouter" and f["free"] is True and f["paid"] is False and f["estimate_usd"] == 0.0
    assert f["model_id"] == canary_config.OPENROUTER_FREE_MODEL and not f["model_id"].startswith("openrouter:")


def test_the_free_models_tested_label_is_read_from_model_portability_md():
    md = open("model_portability.md", encoding="utf-8").read()
    assert "<!-- free-model-label: tested on the 32-row gold set: 81.3% vs Haiku 84.4% -->" in md
    f = next(m for m in C.get("/api/models").json()["models"] if m["id"] == M.OR_FREE)
    assert f["note"] == "tested on the 32-row gold set: 81.3% vs Haiku 84.4%" and "free-tier daily cap" in f["caveat"]
    tmp = os.path.join(tempfile.mkdtemp(), "m.md")                                    # the wording comes from the file: change the file, the label changes
    open(tmp, "w", encoding="utf-8").write("<!-- free-model-label: some other label -->\n<!-- free-model-caveat: a caveat -->\n")
    assert M.free_model_notes(tmp) == {"label": "some other label", "caveat": "a caveat"}
    assert M.free_model_notes(os.path.join(tempfile.mkdtemp(), "missing.md"))["label"].startswith("tested on the 32-row gold set")   # fallback to canary_config


def test_triage_options_default_to_ewt_with_a_one_line_explanation_each():
    d = C.get("/api/models").json()
    opts = {o["id"]: o for o in d["triage_options"]}
    assert d["triage_default"] == "ewt" and set(opts) == {"ewt", "locked"} and opts["ewt"]["default"] and not opts["locked"]["default"]
    assert opts["locked"]["label"] == "Locked (original)" and "Evidence-Weighted" in opts["ewt"]["label"]
    for o in opts.values():
        assert o["explanation"] and "\n" not in o["explanation"] and len(o["explanation"]) < 260


def test_a_paid_model_needs_an_explicit_confirmation_and_a_refusal_offers_other_models():
    r = C.post("/api/runs", json={"drug": "x", "date": "2020-12-31", "model": M.SONNET})
    j = r.json()
    assert r.status_code == 402 and j["code"] == "confirmation_required" and "paid model" in j["detail"] and "$" in j["detail"]
    assert any(a["id"] == M.OR_FREE for a in j["alternatives"]) and all(a["id"] != M.SONNET for a in j["alternatives"])   # the chosen model is not offered back


# ---------------------------------------------------------------- provenance on every brief
def test_provenance_names_the_model_the_triage_and_the_notice():
    p = provenance.of_run({"models": {"pipeline": M.OR_FREE, "brief": M.OR_FREE}, "triage": "ewt"})
    assert p["triage"] == "ewt" and p["triage_inferred"] is False and p["pipeline_provider"] == "openrouter" and "not medical advice" in p["notice"]
    h = provenance.header_markdown(p)
    assert "Produced by" in h and canary_config.OPENROUTER_FREE_MODEL in h and "OpenRouter" in h and "Evidence-Weighted" in h and "not medical advice" in h
    two = provenance.header_markdown(provenance.of_run({"models": {"pipeline": M.HAIKU, "brief": M.SONNET}, "triage": "locked"}))
    assert "Label Checker and Skeptic" in two and M.SONNET in two and "Locked (original)" in two


def test_a_brief_gets_the_header_once_and_old_runs_are_labelled_as_locked():
    md = "# Canary brief: x\n*notice*\n\nbody\n"
    run = {"models": {"pipeline": M.HAIKU, "brief": M.HAIKU}}      # no triage key: made before the choice existed
    once = provenance.with_header(md, run)
    assert once.count("Produced by") == 1 and provenance.with_header(once, run) == once and once.startswith("# Canary brief: x\n")
    assert "predates the triage choice" in once and "Locked (original)" in once
    assert provenance.of_run(run)["triage_inferred"] is True


def test_the_demo_runs_work_without_any_key_and_say_what_produced_them():
    with env(ANTHROPIC_API_KEY=None, OPENROUTER_API_KEY=None, CANARY_DEMO="1"):
        mode = C.get("/api/mode").json()
        assert mode["default_demo"] is True and mode["demos"]
        d = C.get("/api/models").json()
        assert d["keys"] == {"anthropic": False, "openrouter": False} and all(m["available"] is False and m["note_unavailable"] for m in d["models"])
        demo = mode["demos"][0]["id"]
        rid = C.post("/api/runs", json={"demo_id": demo}).json()["run_id"]
        info = C.get(f"/api/runs/{rid}").json()
        assert info["demo"] is True and info["status"] == "ok" and info["provenance"]["triage"] == "locked" and info["provenance"]["triage_inferred"] is True
        sig = C.get(f"/api/runs/{rid}/signals").json()
        assert sig["provenance"]["pipeline_model"] == M.HAIKU and sig["provenance"]["notice"]
        md = C.get(f"/api/runs/{rid}/brief.md").text
        assert "Produced by" in md and "not medical advice" in md and M.HAIKU in md


# ---------------------------------------------------------------- keys: missing, rejected
def test_missing_key_is_refused_clearly_with_other_models_offered():
    with env(ANTHROPIC_API_KEY=None, OPENROUTER_API_KEY="present"):
        r = C.post("/api/runs", json={"drug": "x", "date": "2020-12-31", "model": M.HAIKU, "confirm_cost": True})
        j = r.json()
        assert r.status_code == 400 and j["code"] == "missing_key" and "ANTHROPIC_API_KEY" in j["detail"] and ".env" in j["detail"]
        assert all(a["provider"] != "anthropic" for a in j["alternatives"]) and any(a["id"] == M.OR_FREE and a["available"] for a in j["alternatives"])
    with env(OPENROUTER_API_KEY=None):
        r = C.post("/api/runs", json={"drug": "x", "date": "2020-12-31", "model": M.OR_FREE})
        j = r.json()
        assert r.status_code == 400 and j["code"] == "missing_key" and "OPENROUTER_API_KEY" in j["detail"]
        assert any(a["provider"] == "anthropic" for a in j["alternatives"])


def test_a_rejected_key_is_caught_before_the_run_starts():
    old = api.KEY_CHECK
    api.KEY_CHECK = lambda provider: {"ok": False, "code": "rejected", "message": f"{provider} rejected the API key in .env (HTTP 401). Replace the key, or pick another model."}
    try:
        r = C.post("/api/runs", json={"drug": "x", "date": "2020-12-31", "model": M.OR_FREE})
        j = r.json()
        assert r.status_code == 401 and j["code"] == "key_rejected" and "rejected" in j["detail"]
        assert all(a["provider"] != "openrouter" for a in j["alternatives"]) and j["alternatives"]
    finally:
        api.KEY_CHECK = old


def test_check_key_is_free_and_classifies_the_answer():
    ok = lambda *a, **k: SimpleNamespace(status_code=200)
    bad = lambda *a, **k: SimpleNamespace(status_code=401)
    def boom(*a, **k):
        raise ConnectionError("down")
    e = {"ANTHROPIC_API_KEY": "k1", "OPENROUTER_API_KEY": "k2"}
    assert M.check_key("anthropic", get=ok, env=e)["code"] == "ok" and M.check_key("openrouter", get=ok, env=e)["ok"]
    assert M.check_key("anthropic", get=bad, env=e)["code"] == "rejected" and "pick another model" in M.check_key("openrouter", get=bad, env=e)["message"]
    assert M.check_key("openrouter", get=boom, env=e) == {"ok": True, "code": "unreachable", "message": M.check_key("openrouter", get=boom, env=e)["message"]}
    assert M.check_key("anthropic", get=ok, env={})["code"] == "missing"
    seen = {}
    M.check_key("anthropic", get=lambda url, **k: seen.update(url=url) or ok(), env=e)
    assert seen["url"].endswith("/v1/models?limit=1")                      # a model list, never a model call


class Post:
    def __init__(self, resps):
        self.resps, self.n = list(resps), 0

    def __call__(self, body):
        self.n += 1
        return self.resps.pop(0)


def ok_completion():
    msg = {"content": "", "tool_calls": [{"id": "c", "type": "function", "function": {"name": TOOL["name"], "arguments": json.dumps(GOOD)}}]}
    return FakeResp({"choices": [{"message": msg, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "model": "m"})


def pipeline_with(resps, model=M.OR_FREE):
    """run_pipeline with stubbed agents except that the Label Checker stub makes one real call through the Router, whose provider answers `resps`."""
    tmp = tempfile.mkdtemp()
    events, post = [], Post(resps)
    olds = (orchestrator.resolver.resolve, orchestrator.miner.run, orchestrator.triage_step.run, orchestrator.label_checker.run, orchestrator.skeptic.run,
            orchestrator.writer.run, M.build_client, canary_config.RUNS_DIR)
    orchestrator.resolver.resolve = lambda t: {"status": "resolved", "generic_name": "gabapentin", "rxcui": "1", "confidence": 1.0, "message": "ok"}
    orchestrator.miner.run = lambda *a, **k: {"status": "ok", "drug": "gabapentin", "date": "d", "n_drug": 1, "n_terms": 1, "n_evans": 1, "elapsed_s": 0, "terms": []}
    orchestrator.triage_step.run = lambda m, **k: {"status": "ok", "label_date": "x", "counts": {"evans": 0, "not_exact_on_label": 0}, "cap": 150, "terms": []}

    def lc(tr, client=None, model=None, **k):
        client.messages.create(model=model, max_tokens=300, system="s", tools=[TOOL], tool_choice={"type": "tool", "name": TOOL["name"]}, messages=[{"role": "user", "content": "q"}])
        return {"status": "ok", "spend": 0, "n_checked": 0, "decisions": {"yes": 0, "partial": 0, "no": 0}, "label_date": "x", "n_to_skeptic": 0, "results": []}
    orchestrator.label_checker.run = lc
    orchestrator.skeptic.run = lambda m, lab, client=None, model=None, **k: {"status": "ok", "spend": 0, "n_evaluated": 0, "n_recommended": 0, "n_dismissed": 0, "results": []}
    orchestrator.writer.run = lambda sk, lab, m, client=None, model=None, **k: {"status": "ok", "spend": 0, "brief_markdown": "# Canary brief: x\n*n*\n", "n_written": 0, "n_recommended": 0, "removed_uncited": 0}
    M.build_client = lambda anthropic_client=None, openrouter=None, on_wait=None: M.Router(openrouter=M.OpenRouterBackend(post=post, sleep=lambda s: None, on_wait=on_wait, rng=lambda: 0.0))
    canary_config.RUNS_DIR = tmp
    try:
        res = orchestrator.run_pipeline("gabapentin", "2008-12-31", runs_dir=tmp, run_id="r1", ledger=Ledger(os.path.join(tmp, "l.jsonl"), 3.5), model=model,
                                        cost_confirmed=True, on_event=events.append)
        info = C.get("/api/runs/r1").json()
        return res, events, post, info
    finally:
        (orchestrator.resolver.resolve, orchestrator.miner.run, orchestrator.triage_step.run, orchestrator.label_checker.run, orchestrator.skeptic.run,
         orchestrator.writer.run, M.build_client, canary_config.RUNS_DIR) = olds


def test_a_free_model_rate_limit_shows_a_waiting_state_and_the_run_resumes():
    res, events, post, info = pipeline_with([FakeResp(None, 429, "rate limited", {"Retry-After": "3"}), FakeResp(None, 429, "rate limited"), ok_completion()])
    st = [(e["step"], e["status"]) for e in events]
    assert res["status"] == "ok" and post.n == 3
    waits = [e for e in events if e["status"] == "waiting"]
    assert len(waits) == 2 and all(e["step"] == "label_checker" and "resumes by itself" in e["summary"] for e in waits)
    assert [e["status"] for e in events if e["status"] in ("waiting", "resumed")] == ["waiting", "resumed", "waiting", "resumed"]
    assert st.index(("label_checker", "waiting")) < st.index(("label_checker", "done")) and st[-1][0] == "writer"
    assert info["status"] == "ok" and info["provenance"]["triage"] == "ewt"


def test_the_daily_limit_stops_the_run_clearly_and_offers_other_models():
    res, events, post, info = pipeline_with([FakeResp(None, 429, "Rate limit exceeded: free-models-per-day")])
    assert res["status"] == "daily_limit" and "daily limit" in res["message"] and "cached" in res["message"]
    assert info["status"] == "daily_limit" and info["alternatives"] and all(a["id"] != M.OR_FREE for a in info["alternatives"])
    assert events[-1]["status"] == "stopped" and not any(e["status"] == "waiting" for e in events)


def test_a_key_rejected_during_the_run_stops_it_with_a_clear_message_and_other_providers_offered():
    res, events, post, info = pipeline_with([FakeResp(None, 401, '{"error":{"message":"User not found.","code":401}}')])
    assert res["status"] == "key_rejected" and "rejected the API key" in res["message"] and "OpenRouter" in res["message"]
    assert info["status"] == "key_rejected" and info["alternatives"] and all(a["provider"] != "openrouter" for a in info["alternatives"])
    assert info["provenance"]["pipeline_model"] == M.OR_FREE


def test_a_rejected_anthropic_key_becomes_the_same_clear_error():
    class AuthenticationError(Exception):   # the SDK's class name
        pass

    class Bad:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise AuthenticationError("invalid x-api-key")
    for model in (M.HAIKU, M.SONNET):
        try:
            M.Router(Bad()).create(model=model, max_tokens=10, tools=[TOOL], tool_choice={"type": "tool", "name": TOOL["name"]}, messages=[{"role": "user", "content": "q"}])
        except M.KeyRejected as e:
            assert e.provider == "anthropic" and "Anthropic rejected the API key" in str(e)
        else:
            raise AssertionError("no KeyRejected")


# ---------------------------------------------------------------- what the page must show
def test_the_react_source_shows_the_selectors_the_errors_and_the_provenance():
    form, app, fail, brief, tl, api_js = src("components", "RunForm.jsx"), src("App.jsx"), src("components", "FailurePanel.jsx"), src("components", "Brief.jsx"), src("components", "Timeline.jsx"), src("api.js")
    for needle in ("m.provider_label", "m.model_id", "estimate_usd", ">Free<", "m.note", "m.caveat", "note_unavailable", "Confirm the cost", "catalog.triage_options", "o.explanation", "Triage"):
        assert needle in form, needle
    for code in ("missing_key", "key_rejected", "daily_limit", "rate_limited", "confirmation_required"):
        assert code in fail, code
    assert "Try another model" in fail and "onUse" in fail and "FailurePanel" in app and "alternatives" in app and "getModels" in app
    assert "Provenance" in brief and "Produced by" in brief and "triage_label" in brief and "p.notice" in brief
    assert "'waiting'" in tl and "resumes by itself" in tl and "payload" in api_js


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
