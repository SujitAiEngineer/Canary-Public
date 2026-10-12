"""Feedback log: a human approves or rejects each recommended signal, with a reason (required for a rejection). One JSON line per decision in
feedback_log.jsonl (gitignored; it is the reviewer's own record). The term must be one the run actually recommended. The UI (Step 8) calls record()."""
import json, os, time

import canary_config

LOG = "feedback_log.jsonl"


def recommended_terms(run_id, runs_dir=canary_config.RUNS_DIR, run_path=None):
    sk = json.load(open(os.path.join(run_path or os.path.join(runs_dir, run_id), "skeptic.json"), encoding="utf-8"))
    return {r["term"] for r in sk["results"] if r["decision"] == "recommended"}, sk


def record(run_id, term, decision, reason="", runs_dir=canary_config.RUNS_DIR, log=None, run_path=None):
    log = log or LOG  # looked up at call time, so a test can redirect it
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    if decision == "reject" and not reason.strip():
        raise ValueError("a rejection needs a reason")
    terms, sk = recommended_terms(run_id, runs_dir, run_path)
    if term not in terms:
        raise ValueError(f"'{term}' was not a recommended signal in run {run_id}")
    row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": run_id, "drug": sk["drug"], "date": sk["date"], "term": term,
           "decision": decision, "reason": reason.strip()}
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row
