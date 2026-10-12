"""Model selector (Step 3): one model for all agents, chosen by the user, routed by provider. Product feature only: the evaluation runner never goes through here
(orchestrator.run_pipeline refuses any model but eval_config.MODEL in evaluation mode, and its batch path does not use this router).

Models offered (IDs and prices verified against the Anthropic models overview, 2026-10-04):
  claude-haiku-4-5-20251001   Claude Haiku 4.5   $1 / $5 per million tokens   (the default: the exact model of the locked evaluation)
  claude-sonnet-5-5           Claude Sonnet 5.5  $2 / $10
  claude-opus-5-5             Claude Opus 5.5    $4 / $20
  openrouter:<id>             one free OpenRouter model (canary_config.OPENROUTER_FREE_MODEL), $0, OpenAI-compatible chat completions

The agents keep calling `client.messages.create(model=..., tools=[...], tool_choice=forced, ...)` with the Anthropic request shape. `Router` sits under the spend
ledger and translates it per model:
  Haiku: passed through unchanged (byte for byte what the locked pipeline sends).
  Sonnet 5.5 / Opus 5.5: these 400 on a forced tool_choice and on non-default sampling, so a forced tool_choice becomes "auto" plus a sentence naming the tool,
      sampling is dropped, effort is set to "low" (Sonnet: thinking off with {"type": "between_tools"}; Opus cannot turn thinking off, so max_tokens gets headroom).
      NOT live-tested (no paid calls were made for Step 3): the request shapes are unit-tested only.
  openrouter:<id>: translated to /chat/completions with the same tool schemas; the output is validated against the tool schema, an invalid output is retried at most
      twice and then returned WITHOUT a tool call (the agents mark it invalid, as for any missing tool call); rate limits are backed off and resumed.
Cache keys: every agent already puts the model string in its on-disk cache key. Anthropic IDs are unchanged (so existing caches stay valid); the OpenRouter model string
carries its provider prefix ("openrouter:..."), so provider and model ID are both in every key and a result never crosses providers."""
import copy, json, os, random, re, time
from types import SimpleNamespace

import canary_config
import eval_config
import label_checker

HAIKU = eval_config.MODEL
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
OR_PREFIX = "openrouter:"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_FREE = OR_PREFIX + canary_config.OPENROUTER_FREE_MODEL

MODELS = {   # id -> label, provider, $ per 1M tokens (input, output)
    HAIKU: {"label": "Claude Haiku 4.5", "provider": "anthropic", "price": (1.00, 5.00)},
    SONNET: {"label": "Claude Sonnet 5.5", "provider": "anthropic", "price": (2.00, 10.00)},
    OPUS: {"label": "Claude Opus 5.5", "provider": "anthropic", "price": (4.00, 20.00)},
    OR_FREE: {"label": "Nemotron 3 Super (free)", "provider": "openrouter", "price": (0.0, 0.0)},
}
PROVIDER_LABELS = {"anthropic": "Anthropic", "openrouter": "OpenRouter"}
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}


def free_model_notes(path=None):
    """The free model's tested label and caveat, read from the machine-readable comment lines at the top of model_portability.md (the single source of the wording);
    falls back to canary_config.OPENROUTER_FREE_MODEL_NOTE if the file or the lines are missing."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_portability.md")
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    get = lambda key: (re.search(r"<!--\s*" + key + r":\s*(.*?)\s*-->", text) or [None, ""])[1]
    label, caveat = get("free-model-label"), get("free-model-caveat")
    if not label:
        label, _, caveat = canary_config.OPENROUTER_FREE_MODEL_NOTE.partition("; ")
    return {"label": label, "caveat": caveat}


for _m, _c in MODELS.items():
    _c["provider_label"] = PROVIDER_LABELS[_c["provider"]]
    _c["model_id"] = _m[len(OR_PREFIX):] if _m.startswith(OR_PREFIX) else _m
    _c["free"] = _c["price"] == (0.0, 0.0)
MODELS[OR_FREE].update(note=free_model_notes()["label"], caveat=free_model_notes()["caveat"])
for _m, _c in MODELS.items():                       # the spend ledger prices every call by model string
    label_checker.PRICES.setdefault(_m, _c["price"])

# Measured means per call on Haiku, from the project's own ledgers (input tokens, output tokens): the basis of the cost estimate.
CALL_TOKENS = {"label_checker": (2338, 76), "skeptic": (2398, 379), "writer": (1898, 447)}
SKEPTIC_SHARE = 0.93          # share of the triage list that reaches the Skeptic (the rest is dropped as already on the label), from the saved runs
TOKENIZER_FACTOR = {HAIKU: 1.0, SONNET: 1.3, OPUS: 1.3}   # Opus 4.7+ style tokenizers use about 1.0 to 1.35 times as many tokens (Anthropic docs); assumed 1.3
OPUS_THINK_OUT = 400          # assumed extra output tokens per call at low effort for a model that cannot turn thinking off (not measured)
OPUS_EXTRA_MAX_TOKENS = 1500  # headroom so that thinking cannot eat the whole max_tokens of a short call


class ConfirmationRequired(Exception):
    pass


class DailyLimit(Exception):
    """The free provider's daily quota is used up: resuming tomorrow is the only fix (results already obtained are cached by the agents)."""


class RateLimited(Exception):
    pass


class KeyRejected(RuntimeError):
    """The provider answered 401 or 403: the key in .env is wrong, revoked or without access. The run stops with a clear message and offers another model."""

    def __init__(self, provider, detail=""):
        self.provider = provider
        super().__init__(f"{PROVIDER_LABELS.get(provider, provider)} rejected the API key (provider error 401 or 403) {detail}".strip())


def provider_of(model):
    return MODELS[check_model(model)]["provider"]


def key_present(provider, env=None):
    return bool((env if env is not None else os.environ).get(KEY_ENV[provider], "").strip())


def check_key(provider, get=None, env=None):
    """Free key check (no model call, no cost): Anthropic lists models, OpenRouter shows the key's status. Returns {"ok", "code", "message"} with code one of
    ok, missing, rejected, unreachable (a network problem does not block a run: the run itself will say if the key is bad). `get` and `env` are injectable."""
    env = env if env is not None else os.environ
    name = PROVIDER_LABELS[provider]
    if not key_present(provider, env):
        return {"ok": False, "code": "missing", "message": f"{KEY_ENV[provider]} is not set. Put it in the .env file (which is not in git) and restart, or pick another model."}
    key = env[KEY_ENV[provider]].strip()
    try:
        if get is None:
            import requests
            get = requests.get
        if provider == "anthropic":
            r = get("https://api.anthropic.com/v1/models?limit=1", headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=20)
        else:
            r = get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=20)
    except Exception as e:
        return {"ok": True, "code": "unreachable", "message": f"could not reach {name} to check the key ({type(e).__name__}); trying anyway"}
    if r.status_code in (401, 403):
        return {"ok": False, "code": "rejected", "message": f"{name} rejected the API key in .env (HTTP {r.status_code}). Replace the key, or pick another model."}
    return {"ok": True, "code": "ok", "message": f"{name} key accepted"}


def is_paid(model):
    return MODELS[model]["price"] != (0.0, 0.0)


def check_model(model):
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; choose one of {list(MODELS)}")
    return model


def estimate_run_cost(model, n_terms=150, max_briefs=canary_config.MAX_BRIEFS_DEFAULT):
    """Dollars for one normal run with nothing cached (a rerun of the same drug and date is cheaper: cached calls cost nothing). `max_usd` adds 50% for retries
    and for thinking. Basis: measured Haiku tokens per call (CALL_TOKENS) times the model's list price (no batch discount in the interactive path)."""
    check_model(model)
    pin, pout = MODELS[model]["price"]
    f = TOKENIZER_FACTOR.get(model, 1.0)
    extra = OPUS_THINK_OUT if model == OPUS else 0
    calls = {"label_checker": n_terms, "skeptic": round(SKEPTIC_SHARE * n_terms), "writer": max_briefs}
    cost = sum(n * (CALL_TOKENS[s][0] * f * pin + (CALL_TOKENS[s][1] + extra) * pout) / 1e6 for s, n in calls.items())
    c = MODELS[model]
    return {"model": model, "label": c["label"], "provider": c["provider"], "provider_label": c["provider_label"], "model_id": c["model_id"], "free": c["free"],
            "note": c.get("note", ""), "caveat": c.get("caveat", ""), "paid": is_paid(model), "estimate_usd": round(cost, 2), "max_usd": round(cost * 1.5, 2),
            "calls": calls, "basis": "measured Haiku tokens per call x list price, nothing cached; max_usd adds 50% for retries and thinking"}


def alternatives(failed_model=None, failed_provider=None, env=None):
    """Models to offer when a run cannot use the chosen one: every other model whose key is present, the free one first when the failure was a paid provider's."""
    out = []
    for m, c in MODELS.items():
        if m == failed_model or (failed_provider and c["provider"] == failed_provider):
            continue
        e = estimate_run_cost(m)
        out.append({"id": m, "label": c["label"], "provider": c["provider"], "provider_label": c["provider_label"], "model_id": c["model_id"], "free": c["free"],
                    "paid": e["paid"], "estimate_usd": e["estimate_usd"], "available": key_present(c["provider"], env)})
    return sorted(out, key=lambda a: (not a["available"], a["paid"], a["estimate_usd"]))


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Schema validation (the tool schemas use object, string, number, integer, array, boolean, enum, required, minimum, maximum)
# ---------------------------------------------------------------------------------------------------------------------------------------------------
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}


def validate(value, schema, path="$"):
    """Returns a list of problems (empty = valid)."""
    t = schema.get("type")
    if t:
        ok = isinstance(value, _TYPES[t]) and not (t in ("integer", "number") and isinstance(value, bool))
        if not ok:
            return [f"{path}: expected {t}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} not in enum"]
    out = []
    if t in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: above maximum")
    if t == "object":
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}.{k}: missing")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                out += validate(value[k], sub, f"{path}.{k}")
    if t == "array" and "items" in schema:
        for i, v in enumerate(value):
            out += validate(v, schema["items"], f"{path}[{i}]")
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Request adaptation
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def forced_tool(kw):
    tc = kw.get("tool_choice") or {}
    if tc.get("type") == "tool":
        return tc["name"]
    if tc.get("type") == "any" and kw.get("tools"):
        return kw["tools"][0]["name"]
    return None


def adapt_anthropic(kw):
    """The request a model other than Haiku accepts (see the module docstring). Haiku and non-forced requests are only changed where the model requires it."""
    model = kw["model"]
    if model == HAIKU:
        return kw
    kw = copy.deepcopy(kw)
    kw.pop("temperature", None)
    extra = dict(kw.pop("extra_body", None) or {})
    extra.pop("temperature", None)
    name = forced_tool(kw)
    if name:                                   # a forced tool_choice returns a 400 on Sonnet 5.5 and Opus 5.5: ask in words, steer with auto
        kw["tool_choice"] = {"type": "auto"}
        msgs = kw["messages"]
        last = msgs[-1]
        sentence = f"\n\nRespond only by calling the {name} tool."
        if isinstance(last["content"], str):
            last["content"] += sentence
        else:
            last["content"] = list(last["content"]) + [{"type": "text", "text": sentence.strip()}]
    extra["output_config"] = {"effort": "low"}
    if model == SONNET:
        extra["thinking"] = {"type": "between_tools"}      # the way to turn thinking off on Sonnet 5.5 ("disabled" is a 400)
    if model == OPUS:
        kw["max_tokens"] = kw.get("max_tokens", 1024) + OPUS_EXTRA_MAX_TOKENS   # thinking cannot be disabled on Opus 5.5
    kw["extra_body"] = extra
    return kw


def to_openai(kw, model_id):
    """Anthropic Messages request -> OpenAI-compatible chat completion body (system, messages, tools, forced tool choice)."""
    def text_of(c):
        return c if isinstance(c, str) else "\n".join(b.get("text", "") for b in c if b.get("type") == "text")
    msgs = []
    if kw.get("system"):
        msgs.append({"role": "system", "content": text_of(kw["system"])})
    for m in kw["messages"]:
        msgs.append({"role": m["role"], "content": text_of(m["content"])})
    body = {"model": model_id, "messages": msgs, "temperature": 0,
            "max_tokens": max(4096, 3 * kw.get("max_tokens", 1024))}   # reasoning models spend part of the budget thinking; the free model costs nothing
    if kw.get("tools"):
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}} for t in kw["tools"]]
        name = forced_tool(kw)
        body["tool_choice"] = {"type": "function", "function": {"name": name}} if name else "auto"
        body["provider"] = {"require_parameters": True}      # do not silently route to an endpoint that ignores tools
    return body


def _json_in(text):
    """The first JSON object found in a text (models sometimes answer in text instead of a tool call)."""
    if not text:
        return None
    for m in re.finditer(r"\{", text):
        depth, in_str, esc = 0, False, False
        for i in range(m.start(), len(text)):
            ch = text[i]
            if in_str:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        v = json.loads(text[m.start():i + 1])
                        return v if isinstance(v, dict) else None
                    except ValueError:
                        break
    return None


def from_openai(data, tools):
    """Chat completion JSON -> an object shaped like an Anthropic message: .content blocks (text / tool_use with .input), .usage, .stop_reason."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    blocks = []
    if msg.get("content"):
        blocks.append(SimpleNamespace(type="text", text=msg["content"]))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = _json_in(fn.get("arguments") or "") or {}
        blocks.append(SimpleNamespace(type="tool_use", id=tc.get("id", ""), name=fn.get("name"), input=args))
    if tools and not any(b.type == "tool_use" for b in blocks):    # no tool call: accept a JSON object in the text if one is there (it is validated below)
        obj = _json_in(msg.get("content") or "")
        if obj is not None:
            blocks.append(SimpleNamespace(type="tool_use", id="", name=tools[0]["name"], input=obj))
    u = data.get("usage") or {}
    stop = {"length": "max_tokens", "tool_calls": "tool_use"}.get(choice.get("finish_reason"), "end_turn")
    return SimpleNamespace(content=blocks, usage=SimpleNamespace(input_tokens=int(u.get("prompt_tokens", 0)), output_tokens=int(u.get("completion_tokens", 0))),
                           stop_reason=stop, model=data.get("model"))


def tool_call_problems(resp, tools):
    """Problems with the tool call of a response against its tool's schema; [] = valid. No tools requested -> always valid."""
    if not tools:
        return []
    call = next((b for b in resp.content if b.type == "tool_use"), None)
    if call is None:
        return ["no tool call"]
    tool = next((t for t in tools if t["name"] == call.name), None)
    if tool is None:
        return [f"unknown tool {call.name}"]
    return validate(call.input, tool["input_schema"])


def _sum_usage(resps):
    return SimpleNamespace(input_tokens=sum(r.usage.input_tokens for r in resps), output_tokens=sum(r.usage.output_tokens for r in resps))


class OpenRouterBackend:
    """Chat completions over HTTPS with the key from the environment (.env). `post` and `sleep` are injectable for tests."""

    def __init__(self, post=None, sleep=time.sleep, max_wait_s=1800, key=None, rng=random.random, on_wait=None):
        self._post, self.sleep, self.max_wait_s, self._key, self.rng = post, sleep, max_wait_s, key, rng
        self.on_wait = on_wait   # on_wait(seconds, reason, resumed): told before each rate-limit wait and again when it ends (the UI shows a waiting state)
        self.waited = 0.0

    def key(self):
        k = self._key or os.environ.get("OPENROUTER_API_KEY", "")
        if not k:
            raise RuntimeError("OPENROUTER_API_KEY is not set (it lives only in .env, which is not in git)")
        return k

    def post(self, body):
        if self._post:
            return self._post(body)
        import requests
        return requests.post(OPENROUTER_URL, json=body, timeout=180, headers={"Authorization": f"Bearer {self.key()}", "Content-Type": "application/json",
                                                                               "X-Title": "Canary"})

    def call(self, body):
        """One request with backoff: 429 waits (Retry-After if given, else 5, 10, 20 ... 120 s plus jitter) and resumes until max_wait_s is used; a daily quota
        raises DailyLimit at once; 5xx and network errors retry 5 times. Returns the parsed JSON."""
        net_tries = 0
        attempt = 0
        while True:
            try:
                r = self.post(body)
            except Exception as e:                      # network error or timeout
                net_tries += 1
                if net_tries > 5:
                    raise
                self.sleep(min(2 ** net_tries, 30))
                continue
            if r.status_code == 429:
                text = (getattr(r, "text", "") or "").lower()
                if "per-day" in text or "per day" in text or "daily" in text:
                    raise DailyLimit(f"the provider's daily limit is used up: {r.text[:200]}")
                attempt += 1
                ra = (getattr(r, "headers", {}) or {}).get("Retry-After")
                wait = float(ra) if ra and str(ra).replace(".", "", 1).isdigit() else min(5 * 2 ** (attempt - 1), 120)
                wait += self.rng() * 2
                if self.waited + wait > self.max_wait_s:
                    raise RateLimited(f"rate limited for more than {self.max_wait_s}s in total")
                self.waited += wait
                if self.on_wait:
                    self.on_wait(wait, "the free model's rate limit", False)
                self.sleep(wait)
                if self.on_wait:
                    self.on_wait(0, "the free model's rate limit", True)
                continue
            if r.status_code >= 500:
                net_tries += 1
                if net_tries > 5:
                    raise RuntimeError(f"provider error {r.status_code}: {r.text[:200]}")
                self.sleep(min(2 ** net_tries, 30))
                continue
            if r.status_code in (401, 403):
                raise KeyRejected("openrouter", f": {r.text[:200]}")
            if r.status_code >= 400:
                raise RuntimeError(f"provider error {r.status_code}: {r.text[:300]}")
            return r.json()


class Router:
    """Drop-in for the Anthropic client where agents only use client.messages.create(...). Sits UNDER the spend ledger."""
    MAX_INVALID_RETRIES = 2

    def __init__(self, anthropic_client=None, openrouter=None, on_wait=None):
        self._a, self._or, self._on_wait = anthropic_client, openrouter, on_wait
        self.messages = self

    def anthropic(self):
        if self._a is None:
            import anthropic
            self._a = anthropic.Anthropic(max_retries=5)
        return self._a

    def openrouter(self):
        if self._or is None:
            self._or = OpenRouterBackend(on_wait=self._on_wait)
        return self._or

    def create(self, **kw):
        model = kw["model"]
        check_model(model)
        if model.startswith(OR_PREFIX):
            return self._create_openrouter(kw)
        try:
            if model == HAIKU:
                return self.anthropic().messages.create(**kw)
            return self._create_adapted(kw)
        except Exception as e:   # a wrong or revoked Anthropic key (HTTP 401 or 403) becomes KeyRejected, which the orchestrator reports clearly
            if type(e).__name__ in ("AuthenticationError", "PermissionDeniedError"):
                raise KeyRejected("anthropic", f": {e}") from e
            raise

    def _create_adapted(self, kw):
        """Sonnet 5.5 / Opus 5.5: adapted request; a forced call that comes back without a tool call is retried at most twice (usage is summed for the ledger)."""
        want_tool = forced_tool(kw) is not None
        adapted = adapt_anthropic(kw)
        resps = []
        for _ in range(1 + (self.MAX_INVALID_RETRIES if want_tool else 0)):
            r = self.anthropic().messages.create(**adapted)
            resps.append(r)
            if not want_tool or any(b.type == "tool_use" for b in r.content):
                break
        last = resps[-1]
        if len(resps) == 1:
            return last
        return SimpleNamespace(content=last.content, usage=_sum_usage(resps), stop_reason=last.stop_reason, model=getattr(last, "model", kw["model"]))

    def _create_openrouter(self, kw):
        model_id = kw["model"][len(OR_PREFIX):]
        body = to_openai(kw, model_id)
        resps = []
        for _ in range(1 + self.MAX_INVALID_RETRIES):
            resp = from_openai(self.openrouter().call(body), kw.get("tools"))
            resps.append(resp)
            if not tool_call_problems(resp, kw.get("tools")):
                break
        last = resps[-1]
        if tool_call_problems(last, kw.get("tools")):    # still invalid after the retries: no tool call is returned, the agent marks the output invalid
            last = SimpleNamespace(content=[b for b in last.content if b.type != "tool_use"], usage=last.usage, stop_reason=last.stop_reason, model=last.model, invalid=True)
        return SimpleNamespace(content=last.content, usage=_sum_usage(resps), stop_reason=last.stop_reason, model=last.model, invalid=getattr(last, "invalid", False))


def build_client(anthropic_client=None, openrouter=None, on_wait=None):
    return Router(anthropic_client, openrouter, on_wait=on_wait)
