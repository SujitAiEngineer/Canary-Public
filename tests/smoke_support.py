"""Fakes for the UI and API smoke tests: no model call, no network, no spend. A fake provider answers every forced tool call with a value generated from the tool's own
JSON schema (so it is schema-valid for the Label Checker, the Skeptic and the Writer), behind the real model_router.Router, the real spend ledger and the real pipeline.
`install(tmp)` points the API at a temporary runs folder and ledger, caps the triage list so a run takes seconds, stubs the PubMed lookup, and sets
api.main.CLIENT_FACTORY, so a run through /api/runs exercises everything except the model itself. `restore()` undoes it."""
import json, os, sys, tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import canary_config, model_router as M, orchestrator

OVERRIDES = {"on_label": "no", "winner": "real_effect", "confidence": 0.6}   # every term passes the Label Checker, the Skeptic recommends it
CAP = 8


def fake_input(schema, overrides=OVERRIDES, key=None):
    t = schema.get("type")
    if key in overrides:
        return overrides[key]
    if t == "object":
        return {k: fake_input(v, overrides, k) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        item = schema.get("items", {})
        return [fake_input(item, overrides)] if item.get("type") == "object" else []
    if t == "number" or t == "integer":
        return 0.5
    if t == "boolean":
        return True
    if "enum" in schema:
        return schema["enum"][0]
    return "" if key in ("evidence", "label_quote") else "A short factual statement."


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: messages.create returns a tool_use block built from the first tool's schema."""
    def __init__(self):
        self.calls, self.messages = [], self

    def create(self, **kw):
        self.calls.append(kw)
        tool = kw["tools"][0]
        block = SimpleNamespace(type="tool_use", id="t", name=tool["name"], input=fake_input(tool["input_schema"]))
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=1200, output_tokens=150), stop_reason="tool_use", model=kw["model"])


class FakeResp:
    def __init__(self, data, status=200, text="", headers=None):
        self.status_code, self._d, self.text, self.headers = status, data, text or json.dumps(data or {}), headers or {}

    def json(self):
        return self._d


def or_post(body):
    """An OpenRouter chat completion for the first tool of the request."""
    tool = body["tools"][0]["function"]
    args = fake_input(tool["parameters"])
    msg = {"content": "", "tool_calls": [{"id": "c", "type": "function", "function": {"name": tool["name"], "arguments": json.dumps(args)}}]}
    return FakeResp({"choices": [{"message": msg, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 1200, "completion_tokens": 150}, "model": body["model"]})


def client_factory(model):
    return M.Router(FakeAnthropic(), M.OpenRouterBackend(post=or_post, sleep=lambda s: None))


_saved = {}


def install(tmp=None, cap=CAP):
    """Patch the running process for a smoke test. Returns the temporary folder used."""
    import api.main as api
    import label_checker
    from agents import skeptic, triage_step, writer
    tmp = tmp or tempfile.mkdtemp()
    _saved.update(limit=canary_config.RUN_LIMIT_USD, caches=(label_checker.CACHE_DIR, skeptic.CACHE_DIR, writer.CACHE_DIR), RUNS_DIR=canary_config.RUNS_DIR, RUN_LEDGER=canary_config.RUN_LEDGER, CAP=triage_step.CAP, pubmed=skeptic.Evidence.pubmed,
                  factory=api.CLIENT_FACTORY, run_pipeline=orchestrator.run_pipeline, env={k: os.environ.get(k) for k in M.KEY_ENV.values()})
    # The agents cache every answer on disk under a key made of the model, the prompt and the evidence. The fakes' answers must NEVER reach the real cache (they would
    # be served later as if a model had given them), so every agent writes to a temporary cache folder for the duration of the test.
    fake_cache = os.path.join(tmp, "cache")
    os.makedirs(fake_cache, exist_ok=True)
    label_checker.CACHE_DIR = skeptic.CACHE_DIR = writer.CACHE_DIR = fake_cache
    import mesh_synonyms   # the pipeline's MeSH step must neither reach the network nor write the real data/mesh_cache.json: no synonyms in the fake runs
    _saved["mesh"] = (mesh_synonyms.lookup, mesh_synonyms.lookup_many)
    mesh_synonyms.lookup = lambda term, **k: {"term": term, "synonyms": [], "descriptor": [], "status": "none", "source": "fake"}
    mesh_synonyms.lookup_many = lambda terms, **k: [mesh_synonyms.lookup(t) for t in terms]
    canary_config.RUNS_DIR = os.path.join(tmp, "runs")
    canary_config.RUN_LEDGER = os.path.join(tmp, "run_spend.jsonl")
    os.makedirs(canary_config.RUNS_DIR, exist_ok=True)
    triage_step.CAP = cap
    skeptic.Evidence.pubmed = lambda self, term: (0, [], "stubbed PubMed query")   # no network
    api.CLIENT_FACTORY = client_factory
    real = orchestrator.run_pipeline
    orchestrator.run_pipeline = lambda *a, **k: real(*a, **{**k, "runs_dir": canary_config.RUNS_DIR})   # api.main calls orchestrator.run_pipeline at run time
    for v in M.KEY_ENV.values():   # the models list marks a model usable when its key exists; the fakes need none
        os.environ[v] = os.environ.get(v) or "dummy-key-for-smoke-test"
    return tmp


def restore():
    import api.main as api
    import label_checker
    from agents import skeptic, triage_step, writer
    label_checker.CACHE_DIR, skeptic.CACHE_DIR, writer.CACHE_DIR = _saved["caches"]
    import mesh_synonyms
    mesh_synonyms.lookup, mesh_synonyms.lookup_many = _saved["mesh"]
    canary_config.RUN_LIMIT_USD = _saved["limit"]
    canary_config.RUNS_DIR, canary_config.RUN_LEDGER = _saved["RUNS_DIR"], _saved["RUN_LEDGER"]
    triage_step.CAP, skeptic.Evidence.pubmed, api.CLIENT_FACTORY = _saved["CAP"], _saved["pubmed"], _saved["factory"]
    orchestrator.run_pipeline = _saved["run_pipeline"]
    for k, v in _saved["env"].items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
