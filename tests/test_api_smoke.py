"""Smoke test of EVERY selector option through the API path the UI takes, with fake provider clients (no model call, no network, no spend): each of the four models
(Haiku, Sonnet 5.5, Opus 5.5, the free OpenRouter model) with each of the two triage options (EWT, locked): 8 full pipeline runs (resolver, Miner, triage, Label
Checker, Skeptic, Writer) on atorvastatin 2011-12-31 with a short term list. For each run the same calls as the React app: POST /api/runs, the event stream read until 'end',
GET the run, the signals and the brief. Checks the options reach the run, the provenance on every brief, the cost confirmation for paid models, and the ledger.
Needs the local FAERS store, the label index and the extension store (like the other pipeline tests).
Run: PYTHONIOENCODING=utf-8 python -u tests/test_api_smoke.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient

import smoke_support as S

DRUG, DATE = "atorvastatin", "2011-12-31"
STEPS = ["resolver", "miner", "triage", "label_checker", "skeptic", "writer"]


def run_option(c, model, triage):
    paid = model != "openrouter:nvidia/nemotron-3-super-120b-a12b:free" and not model.startswith("openrouter:")
    body = {"drug": DRUG, "date": DATE, "model": model, "triage": triage}
    if paid:   # a paid model is refused until the cost is confirmed, with the models that can be used instead
        r = c.post("/api/runs", json=body)
        assert r.status_code == 402 and r.json()["code"] == "confirmation_required" and r.json()["alternatives"], (model, r.text)
        body["confirm_cost"] = True
    r = c.post("/api/runs", json=body)
    assert r.status_code == 200, (model, triage, r.text)
    rid = r.json()["run_id"]
    events = []
    with c.stream("GET", f"/api/runs/{rid}/events") as s:   # what the page does: open the stream, read it until the 'end' event
        for line in s.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
            if line.startswith("event: end"):
                break
    return rid, events


def check(c, model, triage, mid, label, rid, events):
    import canary_config, model_router as M, provenance
    info = c.get(f"/api/runs/{rid}").json()
    assert info["status"] == "ok", (model, triage, info.get("status"), info.get("message"))
    assert info["models"] == {"pipeline": model, "brief": model} and info["triage"] == triage
    p = info["provenance"]
    assert p["pipeline_model"] == model and p["brief_model"] == model and p["triage"] == triage and p["triage_label"] == label and p["triage_inferred"] is False
    done = [e["step"] for e in events if e["status"] == "done"]
    assert done == STEPS, (model, triage, done)
    tri = next(e for e in events if e["step"] == "triage" and e["status"] == "done")["summary"]
    assert ("evidence score (EWT)" in tri) == (triage == "ewt") and ("chi-squared" in tri) == (triage == "locked"), tri
    sig = c.get(f"/api/runs/{rid}/signals").json()
    assert sig["counts"]["examined"] == S.CAP and sig["counts"]["written"] > 0 and sig["provenance"]["triage"] == triage and sig["provenance"]["notice"]
    md = c.get(f"/api/runs/{rid}/brief.md").text
    assert "Produced by" in md and provenance.model_id(model) in md and M.MODELS[model]["provider_label"] in md and label in md and "not medical advice" in md
    run_md = open(os.path.join(canary_config.RUNS_DIR, rid, "brief.md"), encoding="utf-8").read()
    assert run_md.count("Produced by") == 1                            # written once, by the orchestrator
    return sig


def test_every_model_with_every_triage_runs_through_the_api_with_fake_clients():
    import canary_config, model_router as M
    import api.main as api
    tmp = S.install()
    try:
        c = TestClient(api.app)
        catalog = c.get("/api/models").json()
        opts = {o["id"]: o["label"] for o in catalog["triage_options"]}
        assert [m["id"] for m in catalog["models"]] == list(M.MODELS) and set(opts) == {"ewt", "locked"}
        # with the real $6.00 limit for paid runs, Opus (about $7.58 a run) is refused even when the cost is confirmed, and other models are offered
        r = c.post("/api/runs", json={"drug": DRUG, "date": DATE, "model": M.OPUS, "confirm_cost": True})
        assert r.status_code == 402 and r.json()["code"] == "limit" and "hard limit" in r.json()["detail"] and r.json()["alternatives"], r.text
        canary_config.RUN_LIMIT_USD = 50.0      # only so that the Opus code path can be run with the fake client
        results = {}
        for m in catalog["models"]:
            for triage in ("ewt", "locked"):
                rid, events = run_option(c, m["id"], triage)
                sig = check(c, m["id"], triage, m["model_id"], opts[triage], rid, events)
                results[(m["id"], triage)] = (rid, sig["counts"])
                print(f"  ok {m['provider_label']:10s} {m['model_id'][:38]:38s} {triage:6s} examined {sig['counts']['examined']} recommended {sig['counts']['recommended']}", flush=True)
        assert len(results) == 8
        rows = [json.loads(l) for l in open(canary_config.RUN_LEDGER, encoding="utf-8")]
        by_model = {}
        for r in rows:
            by_model[r["model"]] = by_model.get(r["model"], 0.0) + r["cost"]
        assert set(by_model) == set(M.MODELS) and by_model[M.OR_FREE] == 0.0 and all(by_model[m] > 0 for m in (M.HAIKU, M.SONNET, M.OPUS))   # every call priced, the free one at $0
        # the EWT and the locked triage chose their lists differently for the same drug and date (the option really reached the triage step)
        a = json.load(open(os.path.join(canary_config.RUNS_DIR, results[(M.HAIKU, "ewt")][0], "triage.json"), encoding="utf-8"))
        b = json.load(open(os.path.join(canary_config.RUNS_DIR, results[(M.HAIKU, "locked")][0], "triage.json"), encoding="utf-8"))
        assert a["counts"]["variant"] == "EWT" and "variant" not in b["counts"] and {t["term"] for t in a["terms"]} != {t["term"] for t in b["terms"]}
    finally:
        S.restore()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
