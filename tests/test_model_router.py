"""Offline tests (no API, no spend, no network) for the model selector (Step 3): request adaptation per model, the OpenAI-compatible translation, output validation and
at most two retries, rate-limit backoff and resume, the daily-limit stop, cache keys with provider and model, the cost estimate and confirmation, the evaluation's refusal
of any other model, and that .env is not in git.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_model_router.py"""
import inspect, json, os, subprocess, sys, tempfile
from types import SimpleNamespace
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import canary_config, eval_config, label_checker, model_router as M, orchestrator
from agents import skeptic, writer
from spend import Ledger, LedgerClient

OR = M.OR_FREE
TOOL = label_checker.TOOL


def anth_resp(inp=None, name="record_label_decision", i=100, o=20):
    blocks = [SimpleNamespace(type="tool_use", name=name, input=inp, id="x")] if inp is not None else [SimpleNamespace(type="text", text="no tool")]
    return SimpleNamespace(content=blocks, usage=SimpleNamespace(input_tokens=i, output_tokens=o), stop_reason="tool_use" if inp is not None else "end_turn", model="m")


class FakeAnthropic:
    def __init__(self, resps):
        self.resps, self.calls, self.messages = list(resps), [], self

    def create(self, **kw):
        self.calls.append(kw)
        return self.resps.pop(0)


def or_json(args=None, content="", finish="tool_calls", pt=50, ct=10):
    msg = {"content": content}
    if args is not None:
        msg["tool_calls"] = [{"id": "c1", "type": "function", "function": {"name": "record_label_decision", "arguments": args if isinstance(args, str) else json.dumps(args)}}]
    return {"choices": [{"message": msg, "finish_reason": finish}], "usage": {"prompt_tokens": pt, "completion_tokens": ct}, "model": "nvidia/x"}


class FakeResp:
    def __init__(self, status=200, data=None, text="", headers=None):
        self.status_code, self._d, self.text, self.headers = status, data, text or json.dumps(data or {}), headers or {}

    def json(self):
        return self._d


class FakePost:
    def __init__(self, resps):
        self.resps, self.bodies = list(resps), []

    def __call__(self, body):
        self.bodies.append(body)
        return self.resps.pop(0)


def kw(model, forced=True, system=TOOL and label_checker.SYSTEM):
    d = dict(model=model, max_tokens=300, system=system, tools=[TOOL], messages=[{"role": "user", "content": "Is X on the label?"}])
    d["tool_choice"] = {"type": "tool", "name": TOOL["name"]} if forced else {"type": "auto"}
    return d


GOOD = {"on_label": "yes", "evidence": "q", "confidence": 0.9}


def test_model_list_prices_and_ids_match_the_official_docs():
    assert list(M.MODELS)[:3] == ["claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5"]
    assert M.MODELS[M.HAIKU]["price"] == (1.0, 5.0) and M.MODELS[M.SONNET]["price"] == (2.0, 10.0) and M.MODELS[M.OPUS]["price"] == (4.0, 20.0)
    assert M.OR_FREE.startswith("openrouter:") and M.MODELS[M.OR_FREE]["price"] == (0.0, 0.0) and not M.is_paid(M.OR_FREE)
    assert M.HAIKU == eval_config.MODEL == canary_config.PIPELINE_MODEL            # the default is the exact locked-evaluation model
    assert all(m in label_checker.PRICES for m in M.MODELS)                          # the spend ledger can price every model


def test_haiku_request_is_passed_through_unchanged():
    k = dict(kw(M.HAIKU), extra_body={"temperature": 0})
    assert M.adapt_anthropic(k) is k
    fa = FakeAnthropic([anth_resp(GOOD)])
    M.Router(fa).create(**k)
    assert fa.calls[0] == k


def test_sonnet_and_opus_requests_are_adapted_to_what_those_models_accept():
    k = dict(kw(M.SONNET), extra_body={"temperature": 0})
    a = M.adapt_anthropic(k)
    assert a["tool_choice"] == {"type": "auto"} and a["messages"][-1]["content"].endswith("Respond only by calling the record_label_decision tool.")
    assert "temperature" not in a["extra_body"] and a["extra_body"]["thinking"] == {"type": "between_tools"} and a["extra_body"]["output_config"] == {"effort": "low"}
    assert a["max_tokens"] == 300 and k["tool_choice"]["type"] == "tool"            # the caller's request was not mutated
    o = M.adapt_anthropic(kw(M.OPUS))
    assert o["tool_choice"] == {"type": "auto"} and "thinking" not in o["extra_body"] and o["extra_body"]["output_config"] == {"effort": "low"}
    assert o["max_tokens"] == 300 + M.OPUS_EXTRA_MAX_TOKENS
    again = M.adapt_anthropic(kw(M.SONNET, forced=False))                            # an already-auto request (the writer's) gets no second instruction
    assert again["messages"][-1]["content"] == "Is X on the label?"


def test_adapted_call_retries_a_missing_tool_call_at_most_twice_and_sums_usage():
    fa = FakeAnthropic([anth_resp(None, i=10, o=5), anth_resp(None, i=10, o=5), anth_resp(GOOD, i=10, o=5)])
    r = M.Router(fa).create(**kw(M.SONNET))
    assert len(fa.calls) == 3 and r.usage.input_tokens == 30 and r.usage.output_tokens == 15 and r.content[0].input == GOOD
    fa = FakeAnthropic([anth_resp(None)] * 5)
    r = M.Router(fa).create(**kw(M.OPUS))
    assert len(fa.calls) == 3 and not any(b.type == "tool_use" for b in r.content)  # 1 + 2 retries, then it is returned without a tool call


def test_openrouter_request_translation_keeps_the_tool_schema():
    body = M.to_openai(kw(M.OR_FREE), "nvidia/x:free")
    assert body["model"] == "nvidia/x:free" and body["messages"][0] == {"role": "system", "content": label_checker.SYSTEM} and body["messages"][1]["role"] == "user"
    t = body["tools"][0]["function"]
    assert t["name"] == TOOL["name"] and t["parameters"] == TOOL["input_schema"]
    assert body["tool_choice"] == {"type": "function", "function": {"name": TOOL["name"]}} and body["temperature"] == 0 and body["provider"] == {"require_parameters": True}
    assert body["max_tokens"] >= 4096
    assert M.to_openai(kw(M.OR_FREE, forced=False), "x")["tool_choice"] == "auto"
    blocks = dict(kw(M.OR_FREE), system=[{"type": "text", "text": "S1"}, {"type": "text", "text": "S2"}], messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}])
    b = M.to_openai(blocks, "x")
    assert b["messages"][0]["content"] == "S1\nS2" and b["messages"][1]["content"] == "hi"


def test_openrouter_response_becomes_an_anthropic_shaped_message():
    r = M.from_openai(or_json(GOOD), [TOOL])
    call = next(b for b in r.content if b.type == "tool_use")
    assert call.input == GOOD and call.name == TOOL["name"] and r.usage.input_tokens == 50 and r.usage.output_tokens == 10 and r.stop_reason == "tool_use"
    r = M.from_openai(or_json(None, content='Here: {"on_label": "no", "evidence": "", "confidence": 0.4} done', finish="stop"), [TOOL])   # JSON in the text is accepted if it validates
    assert next(b for b in r.content if b.type == "tool_use").input["on_label"] == "no" and r.stop_reason == "end_turn"
    assert M.from_openai(or_json(None, finish="length"), [TOOL]).stop_reason == "max_tokens"


def test_validator_checks_enum_range_required_and_nested_types():
    s = TOOL["input_schema"]
    assert M.validate(GOOD, s) == []
    assert M.validate({"on_label": "maybe", "evidence": "q", "confidence": 0.9}, s)
    assert M.validate({"on_label": "yes", "evidence": "q", "confidence": 1.5}, s)
    assert M.validate({"on_label": "yes", "evidence": "q"}, s)
    assert M.validate({"on_label": "yes", "evidence": 3, "confidence": 0.5}, s)
    assert M.validate({"on_label": "yes", "evidence": "q", "confidence": True}, s)         # a bool is not a number
    nested = {"type": "object", "required": ["claims"], "properties": {"claims": {"type": "array", "items": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}}}}
    assert M.validate({"claims": [{"text": "a"}]}, nested) == [] and M.validate({"claims": [{}]}, nested)
    for tool in (skeptic.TOOL, writer.TOOL):                                           # the real tool schemas only use constructs the validator knows
        M.validate({}, tool["input_schema"])


def test_invalid_openrouter_output_is_retried_at_most_twice_then_marked_invalid():
    bad = or_json({"on_label": "maybe", "evidence": "q", "confidence": 0.9})
    post = FakePost([FakeResp(data=bad)] * 3)
    r = M.Router(openrouter=M.OpenRouterBackend(post=post, sleep=lambda s: None)).create(**kw(M.OR_FREE))
    assert len(post.bodies) == 3 and r.invalid is True and not any(b.type == "tool_use" for b in r.content)   # 1 + 2 retries, then no tool call (the agent marks it invalid)
    assert r.usage.input_tokens == 150 and r.usage.output_tokens == 30                                         # every attempt is counted
    post = FakePost([FakeResp(data=bad), FakeResp(data=or_json(GOOD))])
    r = M.Router(openrouter=M.OpenRouterBackend(post=post, sleep=lambda s: None)).create(**kw(M.OR_FREE))
    assert len(post.bodies) == 2 and r.invalid is False and next(b for b in r.content if b.type == "tool_use").input == GOOD


def test_rate_limits_back_off_and_resume():
    sleeps = []
    post = FakePost([FakeResp(429, text="rate limited", headers={"Retry-After": "7"}), FakeResp(429, text="rate limited"), FakeResp(data=or_json(GOOD))])
    be = M.OpenRouterBackend(post=post, sleep=sleeps.append, rng=lambda: 0.0)
    r = M.Router(openrouter=be).create(**kw(M.OR_FREE))
    assert next(b for b in r.content if b.type == "tool_use").input == GOOD
    assert sleeps == [7.0, 5 * 2 ** 1]          # Retry-After honoured, then exponential (attempt 2 -> 10 s)
    be = M.OpenRouterBackend(post=FakePost([FakeResp(429, text="x", headers={"Retry-After": "100"})] * 5), sleep=lambda s: None, max_wait_s=150, rng=lambda: 0.0)
    try:
        be.call({})
    except M.RateLimited:
        pass
    else:
        raise AssertionError("did not stop after the wait budget")


def test_daily_limit_stops_at_once_and_server_errors_retry_then_raise():
    sleeps = []
    be = M.OpenRouterBackend(post=FakePost([FakeResp(429, text="Rate limit exceeded: free-models-per-day")]), sleep=sleeps.append)
    try:
        be.call({})
    except M.DailyLimit:
        assert sleeps == []
    else:
        raise AssertionError("daily limit not raised")
    be = M.OpenRouterBackend(post=FakePost([FakeResp(503, text="down"), FakeResp(data=or_json(GOOD))]), sleep=sleeps.append)
    assert be.call({})["choices"]
    be = M.OpenRouterBackend(post=FakePost([FakeResp(503, text="down")] * 10), sleep=lambda s: None)
    try:
        be.call({})
    except RuntimeError:
        pass
    else:
        raise AssertionError("persistent 5xx did not raise")
    be = M.OpenRouterBackend(post=FakePost([FakeResp(401, text="bad key")]), sleep=lambda s: None)
    try:
        be.call({})
    except RuntimeError as e:
        assert "401" in str(e)


def test_missing_key_is_a_clear_error_and_the_key_is_never_in_a_request_body():
    old = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        try:
            M.OpenRouterBackend().key()
        except RuntimeError as e:
            assert "OPENROUTER_API_KEY" in str(e) and ".env" in str(e)
        else:
            raise AssertionError("no error without a key")
    finally:
        if old is not None:
            os.environ["OPENROUTER_API_KEY"] = old
    assert "key" not in json.dumps(M.to_openai(kw(M.OR_FREE), "x")).lower().replace("require_parameters", "")


def test_cache_keys_include_provider_and_model():
    tmp = tempfile.mkdtemp()
    old = label_checker.CACHE_DIR
    label_checker.CACHE_DIR = tmp
    try:
        fa = FakeAnthropic([anth_resp(GOOD)] * 10)
        post = FakePost([FakeResp(data=or_json(GOOD))] * 5)
        router = M.Router(fa, M.OpenRouterBackend(post=post, sleep=lambda s: None))
        r1 = label_checker.check(router, "RASH", "passage", "desc", model=M.HAIKU)
        r2 = label_checker.check(router, "RASH", "passage", "desc", model=M.SONNET)
        r3 = label_checker.check(router, "RASH", "passage", "desc", model=M.OR_FREE)
        assert not (r1["cached"] or r2["cached"] or r3["cached"])                 # the same question under three models never hits another model's cache
        assert len([f for f in os.listdir(tmp) if f.startswith("lc_")]) == 3
        assert label_checker.check(router, "RASH", "passage", "desc", model=M.OR_FREE)["cached"] is True and len(post.bodies) == 1
        assert r3["model"].startswith("openrouter:")                                # provider and model ID are in the record and the key input
    finally:
        label_checker.CACHE_DIR = old
    for src in (inspect.getsource(skeptic.judge), inspect.getsource(writer.write_claims), inspect.getsource(label_checker.check)):
        assert "model" in src.split("key = ")[1].split("\n")[0]                   # every agent hashes the model string into its cache key


def test_cost_estimate_is_zero_for_the_free_model_ordered_for_paid_ones_and_confirmation_is_required():
    f = M.estimate_run_cost(M.OR_FREE)
    assert f["estimate_usd"] == 0.0 and f["paid"] is False
    h, s, o = (M.estimate_run_cost(m) for m in (M.HAIKU, M.SONNET, M.OPUS))
    assert 0.5 < h["estimate_usd"] < 2.0 and h["estimate_usd"] < s["estimate_usd"] < o["estimate_usd"] and h["max_usd"] > h["estimate_usd"]
    assert 2.0 < s["estimate_usd"] / h["estimate_usd"] < 2.7                           # twice the Haiku price, and 1.3x the input tokens
    tmp = tempfile.mkdtemp()
    for m in (M.HAIKU, M.SONNET, M.OPUS):
        try:
            orchestrator.run_pipeline("gabapentin", "2008-12-31", runs_dir=tmp, ledger=Ledger(os.path.join(tmp, "l.jsonl"), 3.5), model=m)
        except M.ConfirmationRequired as e:
            assert "paid model" in str(e) and "$" in str(e)
        else:
            raise AssertionError(f"{m} ran without a confirmed cost")


def test_evaluation_mode_refuses_any_other_model():
    for m in (M.SONNET, M.OPUS, M.OR_FREE):
        try:
            orchestrator.run_pipeline("gabapentin", "2008-12-31", evaluation_mode=True, model=m, runs_dir=tempfile.mkdtemp())
        except ValueError as e:
            assert "evaluation" in str(e)
        else:
            raise AssertionError(f"evaluation mode accepted {m}")
    try:
        orchestrator.run_pipeline("gabapentin", "2008-12-31", model="gpt-4")
    except ValueError as e:
        assert "unknown model" in str(e)
    else:
        raise AssertionError("an unknown model was accepted")


def test_one_model_goes_to_every_agent_and_the_free_model_costs_zero_in_the_ledger():
    seen = {}
    old = (orchestrator.resolver.resolve, orchestrator.miner.run, orchestrator.triage_step.run, orchestrator.label_checker.run, orchestrator.skeptic.run, orchestrator.writer.run)
    orchestrator.resolver.resolve = lambda t: {"status": "resolved", "generic_name": "gabapentin", "rxcui": "1", "confidence": 1.0, "message": "ok"}
    orchestrator.miner.run = lambda *a, **k: {"status": "ok", "drug": "gabapentin", "date": "d", "n_drug": 1, "n_terms": 1, "n_evans": 1, "elapsed_s": 0, "terms": []}
    orchestrator.triage_step.run = lambda m, **k: {"status": "ok", "label_date": "x", "counts": {"evans": 0, "not_exact_on_label": 0}, "cap": 150, "terms": []}
    orchestrator.label_checker.run = lambda tr, client=None, model=None, **k: seen.__setitem__("lc", model) or {"status": "ok", "spend": 0, "n_checked": 0, "decisions": {"yes": 0, "partial": 0, "no": 0}, "label_date": "x", "n_to_skeptic": 0, "results": []}
    orchestrator.skeptic.run = lambda m, lab, client=None, model=None, **k: seen.__setitem__("sk", model) or {"status": "ok", "spend": 0, "n_evaluated": 0, "n_recommended": 0, "n_dismissed": 0, "results": []}
    orchestrator.writer.run = lambda sk, lab, m, client=None, model=None, **k: seen.__setitem__("wr", model) or {"status": "ok", "spend": 0, "brief_markdown": "b", "n_written": 0, "n_recommended": 0, "removed_uncited": 0}
    try:
        tmp = tempfile.mkdtemp()
        res = orchestrator.run_pipeline("gabapentin", "2008-12-31", runs_dir=tmp, ledger=Ledger(os.path.join(tmp, "l.jsonl"), 3.5), model=M.OR_FREE, client=object())
    finally:
        (orchestrator.resolver.resolve, orchestrator.miner.run, orchestrator.triage_step.run, orchestrator.label_checker.run, orchestrator.skeptic.run, orchestrator.writer.run) = old
    assert seen == {"lc": M.OR_FREE, "sk": M.OR_FREE, "wr": M.OR_FREE} and res["models"] == {"pipeline": M.OR_FREE, "brief": M.OR_FREE} and res["status"] == "ok"
    ledger = Ledger(os.path.join(tempfile.mkdtemp(), "l.jsonl"), 3.5)
    lc = LedgerClient(M.Router(openrouter=M.OpenRouterBackend(post=FakePost([FakeResp(data=or_json(GOOD))]), sleep=lambda s: None)), ledger, "run")
    lc.create(**kw(M.OR_FREE))
    assert ledger.total == 0.0 and lc.run_spend == 0.0                                 # the free model is priced at $0 but its tokens are still recorded


def test_api_models_endpoint_and_confirmation_rules():
    from fastapi.testclient import TestClient
    import api.main as api
    c = TestClient(api.app)
    d = c.get("/api/models").json()
    assert d["default"] == M.HAIKU and d["triage_default"] == "ewt" and d["ledger_limit_usd"] == 6.0 and len(d["models"]) == 4
    assert {m["id"] for m in d["models"]} == set(M.MODELS) and all("estimate_usd" in m and "available" in m for m in d["models"])
    r = c.post("/api/runs", json={"drug": "x", "date": "2020-12-31"})                  # the default (Haiku) is paid: needs the confirmation
    assert r.status_code == 402 and "paid model" in r.json()["detail"]
    assert c.post("/api/runs", json={"drug": "x", "date": "2020-12-31", "model": "gpt-4"}).status_code == 400
    old = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        assert c.post("/api/runs", json={"drug": "x", "date": "2020-12-31", "model": M.OR_FREE}).status_code == 400
    finally:
        if old is not None:
            os.environ["OPENROUTER_API_KEY"] = old


def test_env_file_is_not_tracked_by_git():
    out = subprocess.run(["git", "check-ignore", ".env"], capture_output=True, text=True)
    assert out.stdout.strip() == ".env"
    tracked = subprocess.run(["git", "ls-files", ".env"], capture_output=True, text=True).stdout.strip()
    assert tracked == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
