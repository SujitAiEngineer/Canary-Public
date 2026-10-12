"""Offline tests (no network, no API, no spend) of the dropped-connection retry added after the rivaroxaban 2017Q4 run: connection errors are retried
at most 3 times with a short backoff and the SAME request; every other error is raised at once; a lookup that keeps failing raises and stops the run
(never skipped silently); batch polling and result reading retry without double-recording a cost.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_network_retry.py"""
import os, sys, tempfile
TMP = tempfile.mkdtemp()
os.environ["CANARY_CACHE_DIR"] = os.path.join(TMP, "cache")  # nothing from a test may reach the real cache
os.makedirs(os.environ["CANARY_CACHE_DIR"])
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS

import requests

import net_retry
from agents import resolver, skeptic
from agents.skeptic import Evidence
import batching
from batching import BatchRunner
from spend import Ledger

SLEPT = []
net_retry.time.sleep = lambda s: SLEPT.append(s)   # no real waiting in tests; the waits are recorded


def reset(e=ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")):
    return e


class Flaky:
    """Fails with `error` the first `fails` times, then returns `value`. Records every call's arguments."""
    def __init__(self, fails, error, value="ok"):
        self.fails, self.error, self.value, self.calls = fails, error, value, []
    def __call__(self, *a, **k):
        self.calls.append((a, k))
        if len(self.calls) <= self.fails:
            raise self.error
        return self.value


def test_retries_a_dropped_connection_with_short_backoff_and_the_same_request():
    SLEPT.clear()
    f = Flaky(2, ConnectionResetError(10054, "reset"))
    assert net_retry.retry_connection(f, "url", {"q": 1}, what="t", log=lambda m: None) == "ok"
    assert len(f.calls) == 3 and SLEPT == [1.0, 2.0]                       # two failures, then success; short waits
    assert f.calls[0] == f.calls[1] == f.calls[2] == (("url", {"q": 1}), {})  # identical request every time


def test_gives_up_after_three_attempts_and_raises_the_original_error():
    f = Flaky(99, ConnectionResetError(10054, "reset"))
    try:
        net_retry.retry_connection(f, what="t", log=lambda m: None)
    except ConnectionResetError:
        assert len(f.calls) == 3      # at most 3 attempts, never more
        return
    raise AssertionError("a permanent connection failure was swallowed")


def test_only_connection_errors_are_retried():
    for err in (ValueError("bad data"), KeyError("x"), requests.exceptions.HTTPError("500"), requests.exceptions.ReadTimeout("slow")):
        f = Flaky(99, err)
        try:
            net_retry.retry_connection(f, what="t", log=lambda m: None)
        except type(err):
            assert len(f.calls) == 1, type(err).__name__   # raised at once, not retried
            continue
        raise AssertionError(type(err).__name__)
    for err in (ConnectionResetError(), ConnectionAbortedError(), requests.exceptions.ConnectionError("reset"), requests.exceptions.ChunkedEncodingError("cut")):
        f = Flaky(1, err)
        assert net_retry.retry_connection(f, what="t", log=lambda m: None) == "ok" and len(f.calls) == 2, type(err).__name__  # these ARE retried


def evidence_stub():
    e = object.__new__(Evidence)   # no database needed to test the PubMed lookup
    e.drug, e.date = "rivaroxaban", "2018-12-31"
    return e


def pubmed_fake(fails, error, calls):
    def fake_get_json(url, params=None, notes=None):
        calls.append((url, dict(params or {})))
        if len(calls) <= fails:
            raise error
        if "esearch" in url:
            return {"esearchresult": {"count": "1", "idlist": ["111"]}}
        return {"result": {"111": {"pubdate": "2015 Jan", "epubdate": "", "title": "A paper"}}}
    return fake_get_json


def test_pubmed_survives_two_resets_with_the_same_query_and_date_limit():
    SLEPT.clear()
    calls = []
    real = skeptic.get_json
    skeptic.get_json = pubmed_fake(2, requests.exceptions.ConnectionError(ConnectionResetError(10054, "reset")), calls)
    try:
        n, top, q = evidence_stub().pubmed("HEPATOTOXICITY")
    finally:
        skeptic.get_json = real
    assert n == 1 and top[0][0] == "111" and len(calls) == 4          # 2 failed esearch attempts, the 3rd worked, then esummary
    es = [c for c in calls if "esearch" in c[0]]
    assert len(es) == 3 and es[0] == es[1] == es[2]                    # the same URL and parameters every attempt
    assert es[0][1]["maxdate"] == "2018/12/31" and es[0][1]["datetype"] == "pdat" and '"hepatotoxicity"[Title/Abstract]' in es[0][1]["term"]  # date limit and exact phrase kept
    assert SLEPT == [1.0, 2.0]


def test_pubmed_that_keeps_failing_raises_and_stops_the_run():
    calls = []
    real = skeptic.get_json
    skeptic.get_json = pubmed_fake(99, requests.exceptions.ConnectionError("reset"), calls)
    try:
        evidence_stub().pubmed("HEPATOTOXICITY")
    except requests.exceptions.ConnectionError:
        assert len(calls) == 3   # tried 3 times, then raised: no empty evidence was invented, no lookup was skipped
        return
    finally:
        skeptic.get_json = real
    raise AssertionError("a failing PubMed lookup was skipped silently")


def test_a_pubmed_failure_stops_skeptic_run_through_the_pool():
    """The failure surfaces from the parallel evidence step (agents.parallel.pmap) as an exception, not as missing evidence."""
    from agents import parallel
    real = skeptic.get_json
    skeptic.get_json = pubmed_fake(99, ConnectionResetError(10054, "reset"), [])
    ev = evidence_stub()
    try:
        parallel.pmap(ev.pubmed, ["A X1", "B X1", "C X1"], 2)
    except ConnectionResetError:
        return
    finally:
        skeptic.get_json = real
    raise AssertionError("the pool swallowed the failure")


def test_resolver_rxnorm_calls_retry_too():
    SLEPT.clear()
    f = Flaky(1, ConnectionResetError(10054, "reset"), value={"idGroup": {"rxnormId": ["999"]}})
    real = resolver._get_json
    resolver._get_json = f
    try:
        assert resolver._exact_rxcui("somedrug") == "999"
    finally:
        resolver._get_json = real
    assert len(f.calls) == 2


# ---- batches: polling and reading results
def msg(inp):
    return NS(content=[NS(type="tool_use", input=inp)], usage=NS(input_tokens=1000, output_tokens=100), stop_reason="tool_use")


class FlakyBatches:
    """retrieve fails twice with a dropped connection; reading the results fails once in the MIDDLE of the stream."""
    def __init__(self):
        self.created, self.polls, self.result_reads = [], 0, 0
    def create(self, requests):
        self.created.append(requests); return NS(id="b_1")
    def retrieve(self, bid):
        self.polls += 1
        if self.polls <= 2:
            raise ConnectionResetError(10054, "reset")
        return NS(processing_status="ended")
    def results(self, bid):
        self.result_reads += 1
        for j, r in enumerate(self.created[0]):
            if self.result_reads == 1 and j == 2:
                raise ConnectionResetError(10054, "reset mid-stream")
            yield NS(custom_id=r["custom_id"], result=NS(type="succeeded", message=msg({"on_label": "no", "evidence": "", "confidence": 0.5})))


def test_batch_polling_and_results_survive_drops_without_double_recording():
    import label_checker as locked
    SLEPT.clear()
    batches = FlakyBatches()
    client = NS(messages=NS(batches=batches, create=lambda **k: (_ for _ in ()).throw(AssertionError("direct call"))))
    ledger = Ledger(os.path.join(tempfile.mkdtemp(), "l.jsonl"), 1.0)
    br = BatchRunner(client, ledger, "t", poll_s=0, state_path=os.path.join(tempfile.mkdtemp(), "s.json"))
    terms = [f"T{i} X9" for i in range(5)]
    res = br.map(lambda t, cl: locked.check(cl, t, "[S] passage", "label", model="claude-haiku-4-5-20251001"), terms)
    assert [r["on_label"] for r in res] == ["no"] * 5
    assert batches.polls == 3 and batches.result_reads == 2 and len(batches.created) == 1   # status retried; results re-read; the batch was submitted ONCE
    import json
    rows = [json.loads(l) for l in open(ledger.path)]
    assert len(rows) == 5 and abs(ledger.total - 5 * 0.5 * (1000 * 1.0 + 100 * 5.0) / 1e6) < 1e-9  # every request recorded exactly once (no double count)
    assert ledger.inflight < 1e-12


def test_batch_create_is_not_retried_by_us():
    """Retrying a create could submit the batch twice (and pay twice), so a dropped create is raised, not repeated."""
    class B:
        created = 0
        def create(self, requests):
            B.created += 1
            raise ConnectionResetError(10054, "reset")
    import label_checker as locked
    client = NS(messages=NS(batches=B(), create=None))
    ledger = Ledger(os.path.join(tempfile.mkdtemp(), "l.jsonl"), 1.0)
    br = BatchRunner(client, ledger, "t", poll_s=0, state_path=os.path.join(tempfile.mkdtemp(), "s.json"))
    try:
        br.map(lambda t, cl: locked.check(cl, t, "[S] passage", "label", model="claude-haiku-4-5-20251001"), ["ONE X10"])
    except ConnectionResetError:
        assert B.created == 1 and ledger.inflight < 1e-12 and ledger.total == 0.0   # one attempt, the reservation was given back, nothing recorded
        return
    raise AssertionError("a failed create was swallowed")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
