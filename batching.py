"""Message Batches execution for the evaluation (cost-only change approved 2026-10-03; see eval_runs.md). Same model, prompts, tool schemas
and inputs as the one-call-at-a-time path, so detection logic is unchanged; only HOW the calls are sent differs (batches are half price).

How it works without touching the agents' prompt code: an agent's per-term function is run once against a Collector that records each
messages.create(**kw) it would make (and defers it); all recorded requests go out as ONE batch; the function is then run again and the
Collector hands back the stored results, so the agent's own code parses the response, writes its own on-disk cache and decides retries.
Terms already in the on-disk cache make no request. A response that needs a retry (an incomplete forced tool call) goes out in a second round.

Spend: the ledger is checked BEFORE each batch is submitted (projected cost at the batch price, including calls already in flight); if the
batch would pass the limit nothing is submitted and SpendLimit is raised: the automatic stop. Real costs (half the list price, from the
usage the API reports) are appended to the ledger when results arrive. Batch ids are saved so an interrupted run resumes a submitted batch
instead of paying for it twice."""
import hashlib, json, os, time

from label_checker import PRICES
from net_retry import retry_connection
from spend import LedgerClient, SpendLimit

BATCH_DISCOUNT = 0.5
MAX_ROUNDS = 3
STATE = "eval_batches.json"


class Deferred(Exception):
    """Raised inside an agent's per-term function when its API call has been recorded for the next batch."""


def request_key(kw):
    return hashlib.md5(json.dumps(kw, sort_keys=True, default=str).encode()).hexdigest()


def to_params(kw):
    """messages.create kwargs -> the params of one batch request (extra_body fields are ordinary Messages parameters)."""
    p = {k: v for k, v in kw.items() if k != "extra_body"}
    p.update(kw.get("extra_body") or {})
    return p


class Collector:
    """Stands in for the client while a per-term function runs. Returns a stored result if there is one, else records the request."""

    def __init__(self, store, requested):
        self.store, self.requested = store, requested
        self.requests = []          # (key, n, kw, term): new requests recorded in this round
        self.calls = {}             # per run of one item: how many times each key was asked for (a retry is the 2nd ask)
        self.term = ""
        self.messages = self

    def start_item(self):
        self.calls, self.term = {}, ""

    def set_context(self, stage=None, term=None):
        if term is not None:
            self.term = term

    def create(self, **kw):
        key = request_key(kw)
        n = self.calls[key] = self.calls.get(key, 0) + 1
        if (key, n) in self.store:
            return self.store[(key, n)]
        if (key, n) not in self.requested:  # identical requests from different items are sent once
            self.requested.add((key, n))
            self.requests.append((key, n, kw, self.term))
        raise Deferred()


class BatchRunner(LedgerClient):
    """A LedgerClient whose agents' per-term work is sent as batches. The orchestrator hands this to the agents in place of the client;
    an agent that finds client.map() uses it instead of its thread pool."""

    def __init__(self, client, ledger, run="", poll_s=20, max_wait_s=6 * 3600, state_path=STATE):
        super().__init__(client, ledger, run)
        self.poll_s, self.max_wait_s, self.state_path = poll_s, max_wait_s, state_path
        self.batches = []  # a record of every batch this runner sent

    def map(self, fn, items):
        """fn(i, client) for each i in items, in order; every API call it makes goes through batches. Returns the list of results."""
        items = list(items)
        results, pending = [None] * len(items), list(range(len(items)))
        store, requested = {}, set()
        for rnd in range(1, MAX_ROUNDS + 1):
            col, still = Collector(store, requested), []
            for i in pending:
                col.start_item()
                try:
                    results[i] = fn(items[i], col)
                except Deferred:
                    still.append(i)
            pending = still
            if not pending:
                return results
            if not col.requests:  # everything pending is waiting on results that already failed twice
                break
            try:
                self._send(col.requests, store, rnd, requested)
            except SpendLimit:
                self._flush(fn, items, pending, store)  # results already paid for are written to the on-disk cache, so they are never bought twice
                raise
        raise RuntimeError(f"{len(pending)} of {len(items)} items could not be completed after {MAX_ROUNDS} batch rounds ({self.stage})")

    def _flush(self, fn, items, pending, store):
        col = Collector(store, set(store))
        for i in pending:
            col.start_item()
            try:
                fn(items[i], col)
            except Deferred:
                pass

    # ---- one batch ---------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _worst_case(kw):
        """An upper bound on what one request can cost at the batch price: output is capped by max_tokens, and the input is estimated at
        3 characters per token with a 10% margin (real text runs about 4). So the real cost can never exceed this."""
        pin, pout = PRICES[kw["model"]]
        est_in = 1.1 * len(json.dumps([kw.get("system"), kw.get("tools"), kw.get("messages")])) / 3.0
        return BATCH_DISCOUNT * (est_in * pin + kw.get("max_tokens", 1024) * pout) / 1e6

    def _load_state(self):
        return json.load(open(self.state_path, encoding="utf-8")) if os.path.exists(self.state_path) else {}

    def _send(self, requests, store, rnd, requested):
        """Send the requests as one batch if their WORST-CASE cost fits under the limit; otherwise as several smaller batches, each of which fits
        (a finished batch costs less than its worst case, so later chunks usually fit more). If not even one request fits: SpendLimit, nothing
        more is submitted. The limit is therefore never passed, whatever the answers turn out to cost."""
        i = 0
        while i < len(requests):
            headroom = self.ledger.headroom()
            chunk, cost = [], 0.0
            for req in requests[i:]:
                c = self._worst_case(req[2])
                if cost + c > headroom:
                    break
                chunk.append(req); cost += c
            if not chunk:
                raise SpendLimit(f"spend limit ${self.ledger.limit:.2f} would be passed (spent ${self.ledger.total:.4f}, room ${headroom:.4f}, next request worst case "
                                 f"${self._worst_case(requests[i][2]):.4f}) [{self.stage}, {len(requests) - i} requests not sent]")
            self._send_chunk(chunk, cost, store, rnd, requested)
            i += len(chunk)

    def _send_chunk(self, requests, projected, store, rnd, requested):
        sig = hashlib.md5("".join(sorted(f"{k}:{n}" for k, n, _, _ in requests)).encode()).hexdigest()
        state = self._load_state()
        resumed = sig in state  # an earlier, interrupted run already submitted exactly this batch (and paid for it only once)
        if not resumed:
            self.ledger.reserve(projected, f"[{self.stage} batch of {len(requests)}]")  # the automatic stop: nothing is submitted past the limit
        try:
            if resumed:
                batch_id = state[sig]["batch_id"]
                print(f"  resuming batch {batch_id} ({self.stage}, {len(requests)} requests)", flush=True)
            else:
                reqs = [{"custom_id": f"r{rnd}-{j}", "params": to_params(kw)} for j, (_, _, kw, _) in enumerate(requests)]
                batch_id = self._c.messages.batches.create(requests=reqs).id
                state[sig] = {"batch_id": batch_id, "stage": self.stage, "run": self.run, "requests": len(requests), "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
                json.dump(state, open(self.state_path, "w", encoding="utf-8"), indent=1)
                print(f"  batch {batch_id} submitted: {self.stage}, {len(requests)} requests, projected ${projected:.3f}", flush=True)
            t0 = time.time()
            while True:
                b = retry_connection(self._c.messages.batches.retrieve, batch_id, what="batch status")
                if b.processing_status == "ended":
                    break
                if time.time() - t0 > self.max_wait_s:
                    raise TimeoutError(f"batch {batch_id} did not finish in {self.max_wait_s}s; rerun to resume it")
                time.sleep(self.poll_s)
        except BaseException:
            if not resumed:
                self.ledger.release(projected)  # nothing came back: give the reservation back
            raise
        done, errors = 0, []
        # read the whole result stream first (retrying a dropped connection), THEN record costs: a retry can never double-record a request
        entries = retry_connection(lambda: list(self._c.messages.batches.results(batch_id)), what="batch results")
        for entry in entries:
            j = int(entry.custom_id.split("-")[1])
            key, n, kw, term = requests[j]
            if entry.result.type != "succeeded":
                errors.append(f"{term or key[:8]}: {entry.result.type}")
                requested.discard((key, n))  # so the next round asks for it again
                continue
            msg = entry.result.message
            store[(key, n)] = msg
            pin, pout = PRICES[kw["model"]]
            cost = BATCH_DISCOUNT * (msg.usage.input_tokens * pin + msg.usage.output_tokens * pout) / 1e6
            with self._lock:
                self.run_spend += cost
            self.ledger.record(run=self.run, stage=self.stage, term=term, model=kw["model"], in_tok=msg.usage.input_tokens,
                               out_tok=msg.usage.output_tokens, cost=round(cost, 6), batch=batch_id)
            done += 1
        if not resumed:
            self.ledger.release(projected)  # all real costs are recorded now (the reservation was held until here, so the check stayed conservative)
        state = self._load_state()  # the batch is consumed: forget it, so a retry round with identical requests is submitted fresh
        if state.pop(sig, None) is not None:
            json.dump(state, open(self.state_path, "w", encoding="utf-8"), indent=1)
        self.batches.append({"batch_id": batch_id, "stage": self.stage, "requests": len(requests), "succeeded": done, "failed": len(errors)})
        print(f"  batch {batch_id} ended: {done}/{len(requests)} succeeded" + (f"; not completed: {errors[:3]}" if errors else ""), flush=True)
