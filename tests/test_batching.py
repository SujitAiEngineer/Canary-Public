"""Offline tests (no API, no spend) for batching.BatchRunner against a fake Message Batches API: one batch per round, cached terms make no
request, retries go out as a second round, the ledger is checked BEFORE a batch is submitted (automatic stop), costs are recorded at half the
list price with the right term, interrupted batches are resumed rather than paid for twice.
Run as a script (it redirects the on-disk cache before any import):  PYTHONIOENCODING=utf-8 python -u tests/test_batching.py"""
import os, sys, tempfile
TMP = tempfile.mkdtemp()
os.environ["CANARY_CACHE_DIR"] = os.path.join(TMP, "cache")  # nothing fake may reach the real cache
os.makedirs(os.environ["CANARY_CACHE_DIR"])
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
from types import SimpleNamespace as NS

import label_checker as locked
from agents import skeptic
from batching import BatchRunner
from spend import Ledger, SpendLimit


def msg(inp, in_tok=1000, out_tok=100):
    return NS(content=[NS(type="tool_use", input=inp)], usage=NS(input_tokens=in_tok, output_tokens=out_tok), stop_reason="tool_use")


class FakeBatches:
    def __init__(self, respond, polls_to_end=1):
        self.respond, self.polls_to_end = respond, polls_to_end
        self.created, self.polls, self.crash_on_poll = [], 0, False
    def create(self, requests):
        self.created.append(requests)
        return NS(id=f"msgbatch_{len(self.created)}")
    def retrieve(self, batch_id):
        if self.crash_on_poll:
            raise RuntimeError("simulated crash while waiting")
        self.polls += 1
        return NS(processing_status="ended" if self.polls > self.polls_to_end else "in_progress")
    def results(self, batch_id):
        for r in self.created[int(batch_id.split("_")[1]) - 1]:
            yield NS(custom_id=r["custom_id"], result=NS(type="succeeded", message=self.respond(r["params"])))


class FakeClient:
    def __init__(self, respond, **kw):
        self.messages = NS(batches=FakeBatches(respond, **kw), create=lambda **k: (_ for _ in ()).throw(AssertionError("a one-at-a-time call was made")))


def runner(respond, limit=1.0, **kw):
    ledger = Ledger(os.path.join(tempfile.mkdtemp(), "ledger.jsonl"), limit)
    client = FakeClient(respond, **kw)
    return BatchRunner(client, ledger, "test", poll_s=0, state_path=os.path.join(tempfile.mkdtemp(), "state.json")), client, ledger


LC_OK = lambda p: msg({"on_label": "yes", "evidence": "a sentence", "confidence": 0.9})


def check_fn(term):
    def fn(_, cl):
        cl.set_context(term=term)
        return locked.check(cl, term, "[S] some passage", "test label", model="claude-haiku-4-5-20251001")
    return fn


def run_checks(br, terms):
    fns = {t: check_fn(t) for t in terms}
    return br.map(lambda t, cl: fns[t](t, cl), terms)


def test_one_batch_then_cache():
    br, client, ledger = runner(LC_OK)
    terms = ["HEADACHE X1", "NAUSEA X1", "RASH X1"]
    res = run_checks(br, terms)
    b = client.messages.batches
    assert len(b.created) == 1 and len(b.created[0]) == 3
    assert all(r["on_label"] == "yes" and r["valid"] and r["evidence"] == "a sentence" for r in res)
    p = b.created[0][0]["params"]
    assert p["model"] == "claude-haiku-4-5-20251001" and p["temperature"] == 0 and p["tool_choice"]["name"] == "record_label_decision" and "extra_body" not in p
    rows = [json.loads(l) for l in open(ledger.path)]
    assert len(rows) == 3 and abs(rows[0]["cost"] - 0.5 * (1000 * 1.0 + 100 * 5.0) / 1e6) < 1e-9  # half the list price
    assert sorted(r["term"] for r in rows) == sorted(terms) and rows[0]["batch"] == "msgbatch_1"
    again = run_checks(br, terms)  # now in the on-disk cache: no request at all
    assert len(b.created) == 1 and [r["on_label"] for r in again] == ["yes"] * 3


def test_identical_requests_are_sent_once():
    br, client, _ = runner(LC_OK)
    run_checks(br, ["SAME X2", "SAME X2", "OTHER X2"])
    assert len(client.messages.batches.created[0]) == 2


def test_retry_goes_out_as_a_second_round():
    seen = {}
    def respond(p):
        k = json.dumps(p, sort_keys=True)
        seen[k] = seen.get(k, 0) + 1
        full = {b: {"score": 0.5, "reason": "r", "cited_ids": ["R1"]} for b in ("real_effect", "reporting_spike", "confounding")}
        return msg({"real_effect": full["real_effect"]} if seen[k] == 1 else {**full, "winner": "real_effect"})  # first answer incomplete
    br, client, _ = runner(respond)
    pk = {"items": {"R1": "x"}, "blocks": {"R": ["R1"], "S": [], "C": [], "X": []}}
    out = br.map(lambda i, cl: skeptic.judge(cl, "d", "RETRY X3", "2011-12-31", pk), [0])[0]
    assert skeptic._complete(out["verdict"]) and out["attempts"] == 2 and len(client.messages.batches.created) == 2


def test_spend_limit_stops_before_the_batch_is_submitted():
    br, client, ledger = runner(LC_OK, limit=0.0001)
    try:
        run_checks(br, [f"TERM{i} X4" for i in range(20)])
    except SpendLimit as e:
        assert "limit" in str(e) and client.messages.batches.created == []  # nothing was sent, nothing was paid
        return
    raise AssertionError("the limit did not stop the batch")


def test_limit_counts_money_already_spent():
    br, client, ledger = runner(LC_OK, limit=0.01)
    ledger.record(run="x", stage="s", term="t", model="m", in_tok=0, out_tok=0, cost=0.0099)
    try:
        run_checks(br, ["ONE X5"])
    except SpendLimit:
        assert client.messages.batches.created == []
        return
    raise AssertionError("spent money was not counted")


def test_interrupted_batch_is_resumed_not_resubmitted():
    br, client, ledger = runner(LC_OK)
    client.messages.batches.crash_on_poll = True
    try:
        run_checks(br, ["RESUME X6"])
    except RuntimeError:
        pass
    assert len(client.messages.batches.created) == 1 and ledger.total == 0.0
    client.messages.batches.crash_on_poll = False
    br2 = BatchRunner(client, ledger, "test", poll_s=0, state_path=br.state_path)  # a new process, same state file
    res = run_checks(br2, ["RESUME X6"])
    assert len(client.messages.batches.created) == 1 and res[0]["on_label"] == "yes" and ledger.total > 0  # paid for once


def test_failed_request_is_retried_in_the_next_round():
    state = {"n": 0}
    class Flaky(FakeBatches):
        def results(self, bid):
            for r in self.created[int(bid.split("_")[1]) - 1]:
                state["n"] += 1
                ok = len(self.created) > 1  # the first batch fails every request
                yield NS(custom_id=r["custom_id"], result=NS(type="succeeded", message=self.respond(r["params"])) if ok else NS(type="errored"))
    ledger = Ledger(os.path.join(tempfile.mkdtemp(), "l.jsonl"), 1.0)
    client = FakeClient(LC_OK); client.messages.batches = Flaky(LC_OK)
    br = BatchRunner(client, ledger, "t", poll_s=0, state_path=os.path.join(tempfile.mkdtemp(), "s.json"))
    res = run_checks(br, ["FLAKY X7"])
    assert res[0]["on_label"] == "yes" and len(client.messages.batches.created) == 2


def test_stop_midway_splits_the_batch_and_keeps_what_was_paid_for():
    # learn what one request looks like, to size a limit that fits only a few worst cases
    probe, pclient, _ = runner(LC_OK)
    run_checks(probe, ["PROBE X9"])
    wc = BatchRunner._worst_case({**pclient.messages.batches.created[0][0]["params"]})
    br, client, ledger = runner(LC_OK, limit=2.5 * wc)
    terms = [f"TERM{i} X9" for i in range(5)]
    try:
        run_checks(br, terms)
    except SpendLimit as e:
        assert "not sent" in str(e)
    else:
        raise AssertionError("expected the limit to stop the run")
    b = client.messages.batches
    sent = sum(len(x) for x in b.created)
    assert 1 < len(b.created) and 2 <= sent < 5                      # split into chunks that each fit; the rest was never submitted
    assert ledger.total <= ledger.limit and ledger.inflight < 1e-12  # the limit was not passed
    paid = run_checks(BatchRunner(FakeClient(LC_OK), Ledger(os.path.join(tempfile.mkdtemp(), "x.jsonl"), 1.0), "t", poll_s=0,
                                  state_path=os.path.join(tempfile.mkdtemp(), "s.json")), terms[:sent])
    assert all(r["on_label"] == "yes" for r in paid)  # the answers already paid for are in the on-disk cache (no new request needed)


def test_label_checker_agent_through_batches():
    """The real agent (retrieval and all) sends its per-term LLM work through the batch runner."""
    from agents import label_checker as agent
    br, client, ledger = runner(LC_OK)
    tr = {"status": "ok", "drug": "atorvastatin", "date": "2011-12-31",
          "terms": [{"term": t, **{k: 1 for k in ("a", "b", "c", "d", "PRR", "chi2")}} for t in ("TINNITUS X8", "FLATULENCE X8", "PRURITUS X8")]}
    out = agent.run(tr, client=br)
    assert out["status"] == "ok" and len(client.messages.batches.created) == 1 and len(client.messages.batches.created[0]) == 3
    assert [r["route"] for r in out["results"]] == ["dropped_on_label"] * 3 and out["decisions"]["yes"] == 3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
