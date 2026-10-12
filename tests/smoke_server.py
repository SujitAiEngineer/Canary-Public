"""The real API with FAKE providers, for the browser smoke test (tests/browser_smoke_ui.py). No model call, no network, no spend, and the agents' answer cache is a temporary
folder (tests/smoke_support.py), so nothing a fake says can reach the real cache.
Modes:  ok       every model answers with schema-valid fake answers (the paid-run limit is raised so that Opus can run)
        limit    like ok, with the real $6.00 limit for paid runs: Opus is refused
        waiting  the free model's first call is rate limited (HTTP 429 with a short Retry-After), then it answers: the page must show a waiting state and resume
        daily    the free model's daily limit is used up (HTTP 429 free-models-per-day)
        rejected the free model's key is rejected (HTTP 401)
        nokey    the Anthropic key is missing (the OpenRouter one is present)
        nokeys   no key at all (the page opens in demo mode)
Run: PYTHONIOENCODING=utf-8 python -u tests/smoke_server.py <mode>        (serves on 127.0.0.1:8000, like python start.py)"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvicorn

import api.main as api
import model_router as M
import smoke_support as S

mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
S.install()
if mode != "limit":
    import canary_config
    canary_config.RUN_LIMIT_USD = 50.0   # only so that Opus (about $7.58 a run) can be run with the fake client; mode "limit" keeps the real $6.00
if mode in ("nokey", "nokeys"):
    os.environ.pop("ANTHROPIC_API_KEY", None)
    if mode == "nokeys":
        os.environ.pop("OPENROUTER_API_KEY", None)
    api.CLIENT_FACTORY = None                      # no fakes: a refused run must be refused by the real key check, and nothing may reach a provider
if mode in ("waiting", "daily", "rejected"):
    api.CLIENT_FACTORY = None
    api.KEY_CHECK = lambda provider: {"ok": True, "code": "ok", "message": "key accepted (fake)"}
    state = {"n": 0}

    def post(body):
        state["n"] += 1
        if mode == "daily":
            return S.FakeResp(None, 429, "Rate limit exceeded: free-models-per-day")
        if mode == "rejected":
            return S.FakeResp(None, 401, '{"error":{"message":"User not found.","code":401}}')
        if state["n"] == 1:
            return S.FakeResp(None, 429, "rate limited", {"Retry-After": "6"})
        return S.or_post(body)
    M.build_client = lambda anthropic_client=None, openrouter=None, on_wait=None: M.Router(
        openrouter=M.OpenRouterBackend(post=post, sleep=lambda s: time.sleep(min(s, 6)), on_wait=on_wait, rng=lambda: 0.0))
uvicorn.run(api.app, host="127.0.0.1", port=8000, log_level="warning")
