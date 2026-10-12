"""Spend ledger and the one choke point for Anthropic calls. LedgerClient wraps an anthropic client: before every call it checks that the
running total plus the calls already in flight plus this call's projected cost stays under the limit (hard stop: raises SpendLimit), after
the call it appends the real cost (from the usage the API reports) to a JSONL ledger. Safe to use from several threads (the Label Checker and
Skeptic run about 5 calls at a time): the check and the reservation happen under one lock, so concurrent calls cannot overshoot the limit.
Cached calls never reach the client, so they cost nothing and are not recorded. Used by the orchestrator for every agent."""
import json, os, threading, time

from label_checker import PRICES  # $/1M tokens (input, output) for each model


class SpendLimit(Exception):
    pass


class Ledger:
    def __init__(self, path, limit_usd):
        self.path, self.limit = path, limit_usd
        self.total, self.inflight = 0.0, 0.0
        self.lock = threading.Lock()
        if os.path.exists(path):
            self.total = sum(json.loads(l)["cost"] for l in open(path, encoding="utf-8") if l.strip())

    def reserve(self, projected, where=""):
        """Check the limit including calls already in flight, then reserve this call's projected cost."""
        with self.lock:
            if self.total + self.inflight + projected > self.limit:
                raise SpendLimit(f"spend limit ${self.limit:.2f} would be passed (spent ${self.total:.4f}, in flight about ${self.inflight:.4f}, "
                                 f"next call about ${projected:.4f}) {where}")
            self.inflight += projected

    def headroom(self):
        """How much more can be spent right now: the limit minus what is spent and what is already in flight."""
        with self.lock:
            return self.limit - self.total - self.inflight

    def release(self, projected):
        with self.lock:
            self.inflight = max(0.0, self.inflight - projected)

    def check(self, projected, where=""):  # single-threaded convenience: check without reserving
        with self.lock:
            if self.total + self.inflight + projected > self.limit:
                raise SpendLimit(f"spend limit ${self.limit:.2f} would be passed (spent ${self.total:.4f}, next call about ${projected:.4f}) {where}")

    def record(self, release=0.0, **row):
        """Add a real cost to the total and the ledger file. `release` gives back a reservation IN THE SAME lock acquisition, so there is never a
        moment where a finished call is counted neither as in flight nor as spent (which would let a parallel call slip past the limit)."""
        row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **row}
        with self.lock:
            self.inflight = max(0.0, self.inflight - release)
            self.total += row["cost"]
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")


class LedgerClient:
    """Drop-in for anthropic.Anthropic() where agents only use client.messages.create(...). `stage` is shared (set by the orchestrator
    between stages); `term` is per thread, so concurrent workers are attributed correctly."""

    def __init__(self, client, ledger, run=""):
        self._c, self.ledger, self.run, self.stage = client, ledger, run, ""
        self._tl = threading.local()
        self._lock = threading.Lock()
        self.run_spend = 0.0
        self.fresh = False   # True = the agents ignore their answer cache for this run (set by the orchestrator)
        self.messages = self

    @property
    def term(self):
        return getattr(self._tl, "term", "")

    def set_context(self, stage=None, term=None):
        if stage is not None:
            self.stage = stage
        if term is not None:
            self._tl.term = term

    def create(self, **kw):
        pin, pout = PRICES[kw["model"]]
        est_in = len(json.dumps([kw.get("system"), kw.get("tools"), kw.get("messages")])) / 3.0   # conservative: 3 characters per token
        projected = (est_in * pin + kw.get("max_tokens", 1024) * pout) / 1e6
        self.ledger.reserve(projected, f"[{self.stage} {self.term}]")
        try:
            r = self._c.messages.create(**kw)
        except BaseException:
            self.ledger.release(projected)  # the call failed: nothing was spent, give the reservation back
            raise
        cost = (r.usage.input_tokens * pin + r.usage.output_tokens * pout) / 1e6
        with self._lock:
            self.run_spend += cost
        self.ledger.record(release=projected, run=self.run, stage=self.stage, term=self.term, model=kw["model"], in_tok=r.usage.input_tokens,
                           out_tok=r.usage.output_tokens, cost=round(cost, 6))  # release + record are one atomic step
        return r
