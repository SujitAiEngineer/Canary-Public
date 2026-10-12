"""Build Step 8: API smoke tests against a finished run folder (no API calls, no new run). Skipped if no finished Lipitor run exists.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_api.py"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import canary_config
import api.main as m

c = TestClient(m.app)


def finished_run():
    runs = sorted(d for d in os.listdir(canary_config.RUNS_DIR) if d.endswith("lipitor") and os.path.exists(os.path.join(canary_config.RUNS_DIR, d, "brief.md")))
    return runs[-1] if runs else None


def test_resolve():
    r = c.post("/api/resolve", json={"text": "Zyvox"}).json()
    assert r["status"] == "resolved" and r["generic_name"] == "linezolid"
    assert c.post("/api/resolve", json={"text": "atorvastatn"}).json()["status"] == "needs_confirmation"


def test_bad_input_rejected():
    assert c.post("/api/runs", json={"drug": "x", "date": "2999-01-01"}).status_code == 400
    assert c.post("/api/runs", json={"drug": "x", "date": "not-a-date"}).status_code == 400
    assert c.get("/api/runs/nope").status_code == 404
    assert c.get("/api/runs/..%2F..%2Fx").status_code in (400, 404)


def test_run_info_signals_and_events():
    rid = finished_run()
    if not rid:
        print("skipped: no finished run"); return
    info = c.get(f"/api/runs/{rid}").json()
    assert info["status"] == "ok" and info["trace"] and "brief" not in info
    s = c.get(f"/api/runs/{rid}/signals").json()
    assert s["counts"]["written"] == len(s["signals"]) and "not medical advice" in s["disclaimer"]
    sig = s["signals"][0]
    assert sig["claims"] and set(sig["branches"]) == {"real_effect", "reporting_spike", "confounding"}
    assert all(i in sig["evidence"] for cl in sig["claims"] for i in cl["cited_ids"])  # every citation can be shown with its evidence text
    with c.stream("GET", f"/api/runs/{rid}/events") as r:
        evs = [l for l in r.iter_lines() if l.startswith("data: {\"ts")]
    assert len(evs) >= 6
    assert "Canary brief" in c.get(f"/api/runs/{rid}/brief.md").text


def test_card_for_any_recommended_term():
    rid = finished_run()
    if not rid:
        return
    s = c.get(f"/api/runs/{rid}/signals").json()
    assert "mode" in s and s["signals"][0]["rank"] == 1
    other = s["also_recommended"][0]
    card = c.get(f"/api/runs/{rid}/card", params={"term": other["term"]}).json()
    assert card["claims"] == [] and card["rank"] == other["rank"] and card["evidence"] and set(card["branches"]) == {"real_effect", "reporting_spike", "confounding"}
    assert c.get(f"/api/runs/{rid}/card", params={"term": "NOT A TERM"}).status_code == 404


def test_feedback_endpoint(tmp=None):
    rid = finished_run()
    if not rid:
        return
    import feedback, tempfile
    feedback.LOG = os.path.join(tempfile.mkdtemp(), "fb.jsonl")  # never touch the real log from a test
    term = c.get(f"/api/runs/{rid}/signals").json()["signals"][0]["term"]
    assert c.post(f"/api/runs/{rid}/feedback", json={"term": term, "decision": "reject", "reason": ""}).status_code == 400
    assert c.post(f"/api/runs/{rid}/feedback", json={"term": "NOT A SIGNAL", "decision": "approve"}).status_code == 400
    assert c.post(f"/api/runs/{rid}/feedback", json={"term": term, "decision": "reject", "reason": "already covered"}).status_code == 200
    assert c.get(f"/api/runs/{rid}/signals").json()["signals"][0]["feedback"]["decision"] == "reject"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
