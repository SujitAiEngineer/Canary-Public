"""Build Step 9: demo mode. Replays the precomputed runs in demo_data/ through the same API, with the FAERS store and API key treated as absent.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_demo_mode.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
import canary_config, feedback
import api.main as m

m.full_mode_available = lambda: False  # as on a copy of the repo without data/ and .env
c = TestClient(m.app)


def test_mode_lists_the_demos():
    mode = c.get("/api/mode").json()
    assert mode["full_available"] is False and mode["default_demo"] is True
    ids = [d["id"] for d in mode["demos"]]
    assert ids == [s["id"] for s in canary_config.DEMO_RUNS], ids  # all three demos are packaged


def replay(demo_id):
    rid = c.post("/api/runs", json={"demo_id": demo_id}).json()["run_id"]
    assert rid.startswith(f"demo--{demo_id}--")
    with c.stream("GET", f"/api/runs/{rid}/events") as r:
        evs = [l for l in r.iter_lines() if l.startswith('data: {"ts')]
    assert len(evs) == 12  # six agents, a start and a done each
    info = c.get(f"/api/runs/{rid}").json()
    assert info["status"] == "ok" and info["demo"] is True
    s = c.get(f"/api/runs/{rid}/signals").json()
    assert s["signals"] and "not medical advice" in s["disclaimer"]
    return rid, s


def test_each_demo_replays_with_its_label_and_highlight():
    for spec in canary_config.DEMO_RUNS:
        rid, s = replay(spec["id"])
        # every demo highlight gets a full card: written up at its real rank if the Skeptic recommended it, or shown as dismissed if it did not
        feat = [x for x in s["signals"] if x["featured"]] + s["dismissed_highlights"]
        assert [x["term"] for x in feat] == spec["featured"], spec["id"]
        for x in feat:
            assert x["decision"] == "dismissed" or x["rank"] > 15  # a recommended highlight keeps its real rank, not promoted into the top 15
            assert set(x["branches"]) == {"real_effect", "reporting_spike", "confounding"} and x["evidence"]
            assert c.get(f"/api/runs/{rid}/card", params={"term": x["term"]}).json()["decision"] == x["decision"]  # any examined term opens
        want = canary_config.DEMO_REVIEW_LABEL if spec["review_cap"] else "standard (cap 150)"
        assert s["mode"] == want, (spec["id"], s["mode"])
        other = s["also_recommended"][0]["term"]
        card = c.get(f"/api/runs/{rid}/card", params={"term": other}).json()
        assert card["evidence"] and card["claims"] == []
        brief = c.get(f"/api/runs/{rid}/brief.md").text
        assert "not medical advice" in brief and (spec["review_cap"] is None or canary_config.DEMO_REVIEW_LABEL in brief)
        print("replayed", spec["id"], s["counts"], s["mode"], flush=True)


def test_feedback_works_in_demo_mode():
    feedback.LOG = os.path.join(tempfile.mkdtemp(), "fb.jsonl")
    rid, s = replay(canary_config.DEMO_RUNS[0]["id"])
    t = s["signals"][0]["term"]
    assert c.post(f"/api/runs/{rid}/feedback", json={"term": t, "decision": "reject", "reason": "test"}).status_code == 200


def test_demo_does_not_need_data_or_key():
    # a replay never opens the FAERS store or constructs an Anthropic client: the run folder is read only
    import duckdb
    real = duckdb.connect
    duckdb.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("demo replay opened the FAERS store"))
    try:
        replay(canary_config.DEMO_RUNS[0]["id"])
    finally:
        duckdb.connect = real


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
