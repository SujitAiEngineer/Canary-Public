"""Offline tests (no API, no spend): the spend ledger under parallel calls, and 429 retry with backoff.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_spend_concurrency.py"""
import json, os, sys, tempfile, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS

import anthropic
import httpx2 as httpx  # the HTTP library this version of the anthropic SDK uses
from spend import Ledger, LedgerClient, SpendLimit

MODEL = "claude-haiku-4-5-20251001"
IN_TOK, OUT_TOK = 150, 40   # a real call: about 4 characters per token in, and never more than max_tokens (50) out
REAL = (IN_TOK * 1.0 + OUT_TOK * 5.0) / 1e6


class SlowFake:
    """A client whose calls take a little time, so several are in flight together."""
    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()
        self.messages = self
    def create(self, **kw):
        time.sleep(0.01)
        with self.lock:
            self.calls += 1
        return NS(usage=NS(input_tokens=IN_TOK, output_tokens=OUT_TOK), content=[])


def kw():
    return dict(model=MODEL, max_tokens=50, system="s", tools=[], messages=[{"role": "user", "content": "x" * 600}])


def hammer(limit, n_threads=8, per_thread=25):
    ledger = Ledger(os.path.join(tempfile.mkdtemp(), "l.jsonl"), limit)
    fake = SlowFake()
    lc = LedgerClient(fake, ledger, "t")
    ok, stopped = [0], [0]
    m = threading.Lock()

    def worker():
        for _ in range(per_thread):
            try:
                lc.create(**kw())
                with m: ok[0] += 1
            except SpendLimit:
                with m: stopped[0] += 1
    ts = [threading.Thread(target=worker) for _ in range(n_threads)]
    [t.start() for t in ts]; [t.join() for t in ts]
    return ledger, fake, ok[0], stopped[0]


def test_parallel_calls_never_pass_the_limit_or_miscount():
    limit = 0.05  # room for roughly 100 of the 200 attempted calls
    ledger, fake, ok, stopped = hammer(limit)
    rows = [json.loads(l) for l in open(ledger.path)]
    assert ok + stopped == 200 and 0 < ok < 200 and stopped > 0           # the stop really triggered
    assert fake.calls == ok == len(rows)                                   # every real call recorded exactly once: none skipped, none double counted
    assert abs(sum(r["cost"] for r in rows) - ledger.total) < 1e-9        # total equals the sum of the file
    assert abs(ledger.total - ok * REAL) < 1e-6
    assert ledger.total <= limit + 1e-9, (ledger.total, limit)             # real spend never passed the limit
    assert ledger.inflight < 1e-9                                          # every reservation was given back


def test_a_failed_call_gives_its_reservation_back():
    ledger = Ledger(os.path.join(tempfile.mkdtemp(), "l.jsonl"), 1.0)
    class Boom:
        messages = NS(create=lambda **k: (_ for _ in ()).throw(ConnectionError("down")))
    lc = LedgerClient(Boom, ledger, "t")
    try:
        lc.create(**kw())
    except ConnectionError:
        pass
    assert ledger.inflight == 0.0 and ledger.total == 0.0 and not os.path.exists(ledger.path)


def test_429_is_retried_with_backoff_then_succeeds():
    seen = []
    def handler(request):
        seen.append(time.time())
        if len(seen) <= 2:  # the first two attempts are rate limited
            return httpx.Response(429, headers={"retry-after": "0"}, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
        return httpx.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": MODEL, "content": [{"type": "text", "text": "ok"}],
                                         "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 2}})
    client = anthropic.Anthropic(api_key="test", max_retries=5, http_client=httpx.Client(transport=httpx.MockTransport(handler)))  # as the orchestrator builds it
    r = client.messages.create(model=MODEL, max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    assert r.content[0].text == "ok" and len(seen) == 3                    # two 429s, then success
    assert seen[2] - seen[0] > 0.1                                         # it waited between attempts (backoff), it did not hammer


def test_429_gives_up_after_max_retries():
    n = [0]
    def handler(request):
        n[0] += 1
        return httpx.Response(429, headers={"retry-after": "0"}, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
    client = anthropic.Anthropic(api_key="test", max_retries=2, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        client.messages.create(model=MODEL, max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    except anthropic.RateLimitError:
        assert n[0] == 3  # the first try plus 2 retries, then the error is raised, not swallowed
        return
    raise AssertionError("a permanent 429 was not raised")


def test_orchestrator_client_is_built_with_retries():
    import inspect, orchestrator
    assert "max_retries=5" in inspect.getsource(orchestrator.run_pipeline)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
