"""Canary API (Build Step 8). A thin FastAPI layer over orchestrator.run_pipeline: it starts runs in a background thread, relays the
orchestrator's events as server-sent events, and reads the run folder (runs/<id>/) to serve the brief, the trace and the feedback log.
No agent logic lives here. Start from the repo root:  uvicorn api.main:app --port 8000   (or  python start.py  for API + web together)
"""
import asyncio, datetime as dt, json, os, re, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

import canary_config, feedback, model_router, orchestrator, provenance
from agents import resolver
from agents.writer import DISCLAIMER

app = FastAPI(title="Canary")
RUNS = {}  # run_id -> {"events": [...], "done": bool}
CLIENT_FACTORY = None  # tests only: a function model -> client (a fake) used instead of the real provider clients
KEY_CHECK = model_router.check_key  # replaceable in tests
ID = re.compile(r"^[A-Za-z0-9_\-]+$")


class ResolveIn(BaseModel):
    text: str


class RunIn(BaseModel):
    drug: str = ""
    date: str = ""
    serious_only: bool = False
    confirm: str | None = None
    triage: str | None = None   # "ewt" (the product default) or "locked"; None = the default
    model: str | None = None    # one model for all agents (model_router.MODELS); None = the default (Claude Haiku 4.5)
    confirm_cost: bool = False  # a paid model needs this to be true after the estimate was shown
    mesh_synonyms: bool | None = None   # MeSH synonyms in the Label Checker's retrieval; None = the product default (on)
    fresh: bool = False         # fresh run: ignore the answer cache for model calls in this run
    demo_id: str | None = None  # replay a precomputed demo run instead of running the pipeline


class FeedbackIn(BaseModel):
    term: str
    decision: str
    reason: str = ""


def demo_ids():
    """Precomputed demo runs found in demo_data/ (each has a meta.json written by precompute_demo.py)."""
    out = []
    for spec in canary_config.DEMO_RUNS:
        if os.path.exists(os.path.join(canary_config.DEMO_DIR, spec["id"], "meta.json")):
            out.append(spec)
    return out


def full_mode_available():
    """The full pipeline needs the FAERS store and an API key. Without them only the precomputed demos can run."""
    return os.path.exists("data/canary.duckdb") and bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENROUTER_API_KEY"))


def run_dir(run_id):
    if not ID.match(run_id):
        raise HTTPException(400, "bad run id")
    if run_id.startswith("demo--"):  # demo--<demo id>--<timestamp>: the folder is shared by every replay
        d = os.path.join(canary_config.DEMO_DIR, run_id.split("--")[1])
        if not os.path.isdir(d):
            raise HTTPException(404, "unknown demo")
        return d
    d = os.path.join(canary_config.RUNS_DIR, run_id)
    if not os.path.isdir(d):
        raise HTTPException(404, "unknown run")
    return d


def load(run_id, name):
    p = os.path.join(run_dir(run_id), name + ".json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


@app.get("/api/mode")
def mode():
    """What this installation can do. default_demo is true when the full pipeline is unavailable or CANARY_DEMO=1 (python start.py --demo)."""
    full = full_mode_available()
    return {"full_available": full, "default_demo": os.getenv("CANARY_DEMO") == "1" or not full,
            "demos": [{"id": s["id"], "title": s["title"], "drug": s["drug"], "date": s["date"]} for s in demo_ids()]}


@app.get("/api/models")
def models():
    """The model and triage choices for the UI: provider, model ID, estimated cost of one run (the free model is labelled free and carries its tested label),
    whether its key is present, and the triage options with a one-line explanation each. A paid run needs confirmation."""
    out = []
    for m, c in model_router.MODELS.items():
        est = model_router.estimate_run_cost(m)
        have = model_router.key_present(c["provider"])
        out.append({**est, "id": m, "available": have,
                    "note_unavailable": "" if have else f"{model_router.KEY_ENV[c['provider']]} is not set in .env"})
    return {"default": canary_config.PIPELINE_MODEL, "triage_default": canary_config.DEFAULT_TRIAGE, "triage_choices": list(canary_config.TRIAGE_CHOICES),
            "triage_options": canary_config.TRIAGE_OPTIONS, "ledger_limit_usd": canary_config.RUN_LIMIT_USD, "ledger_spent_usd": round(_run_ledger_total(), 4),
            "keys": {p: model_router.key_present(p) for p in model_router.KEY_ENV}, "models": out}


def refuse(status, code, message, model=None, provider=None):
    """A clear refusal: the usual `detail` string, plus a `code` and the models that can be used instead (the UI offers them)."""
    return JSONResponse(status_code=status, content={"detail": message, "code": code, "alternatives": model_router.alternatives(failed_model=model, failed_provider=provider)})


def _run_ledger_total():
    from spend import Ledger
    return Ledger(canary_config.RUN_LEDGER, canary_config.RUN_LIMIT_USD).total


@app.post("/api/resolve")
def resolve(body: ResolveIn):
    return resolver.resolve(body.text)


@app.post("/api/runs")
def start_run(body: RunIn):
    if body.demo_id:
        return start_demo(body.demo_id)
    try:
        d = dt.date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    if d > dt.date.today():
        raise HTTPException(400, "the time machine date cannot be in the future")
    if body.triage is not None and body.triage not in canary_config.TRIAGE_CHOICES:
        raise HTTPException(400, f"triage must be one of {list(canary_config.TRIAGE_CHOICES)}")
    model = body.model or canary_config.PIPELINE_MODEL
    if model not in model_router.MODELS:
        raise HTTPException(400, f"model must be one of {list(model_router.MODELS)}")
    provider = model_router.provider_of(model)
    if model_router.is_paid(model):
        est = model_router.estimate_run_cost(model)
        if not body.confirm_cost:
            return refuse(402, "confirmation_required", f"{est['label']} is a paid model: estimated cost about ${est['estimate_usd']:.2f} (up to ${est['max_usd']:.2f}); confirm the cost to run it", model=model)
        if _run_ledger_total() + est["estimate_usd"] > canary_config.RUN_LIMIT_USD:
            return refuse(402, "limit", f"the estimated cost would pass the ${canary_config.RUN_LIMIT_USD:.2f} hard limit for paid runs "
                                        f"(spent ${_run_ledger_total():.2f}); use the free model", model=model)
    if CLIENT_FACTORY is None:   # a missing or rejected key is caught before the run starts, with the models that can be used instead
        k = KEY_CHECK(provider)
        if not k["ok"]:
            return refuse(400 if k["code"] == "missing" else 401, "missing_key" if k["code"] == "missing" else "key_rejected", k["message"], provider=provider)
    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + "".join(c for c in body.drug.lower() if c.isalnum())[:20]
    state = RUNS[run_id] = {"events": [], "done": False}

    def work():
        try:
            orchestrator.run_pipeline(body.drug, body.date, body.serious_only, body.confirm, run_id=run_id, triage=body.triage, model=body.model, cost_confirmed=body.confirm_cost, fresh=body.fresh, mesh_synonyms=body.mesh_synonyms,
                                      client=CLIENT_FACTORY(model) if CLIENT_FACTORY else None, on_event=lambda ev: state["events"].append(ev))
        except Exception as e:  # never leave the page waiting
            state["events"].append({"ts": time.strftime("%H:%M:%S"), "step": "orchestrator", "status": "error", "summary": f"{type(e).__name__}: {e}"})
        finally:
            state["done"] = True

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id}


def start_demo(demo_id):
    """Replay a precomputed run: its stored trace is streamed as live events (a pause at each step), no FAERS data and no API key needed."""
    if demo_id not in {s["id"] for s in demo_ids()}:
        raise HTTPException(404, "unknown demo")
    run_id = f"demo--{demo_id}--{time.strftime('%H%M%S')}"
    state = RUNS[run_id] = {"events": [], "done": False}
    events_ = [json.loads(l) for l in open(os.path.join(canary_config.DEMO_DIR, demo_id, "trace.jsonl"), encoding="utf-8")]

    def work():
        for ev in events_:
            time.sleep(0.5 if ev["status"] == "start" else 1.4)
            state["events"].append(ev)
        state["done"] = True

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str):
    """Server-sent events: every orchestrator event so far, then live ones, then a final 'end' event."""
    run_dir(run_id) if run_id not in RUNS else None
    state = RUNS.get(run_id)

    async def gen():
        if state is None:  # a finished run from an earlier session: replay its trace
            for l in open(os.path.join(run_dir(run_id), "trace.jsonl"), encoding="utf-8"):
                yield f"data: {l.strip()}\n\n"
            yield "event: end\ndata: {}\n\n"
            return
        i = 0
        while True:
            while i < len(state["events"]):
                yield f"data: {json.dumps(state['events'][i])}\n\n"
                i += 1
            if state["done"] and i >= len(state["events"]):
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}")
def run_info(run_id: str):
    d = run_dir(run_id)
    info = load(run_id, "run") or {"run_id": run_id, "status": "running"}
    trace = [json.loads(l) for l in open(os.path.join(d, "trace.jsonl"), encoding="utf-8")] if os.path.exists(os.path.join(d, "trace.jsonl")) else []
    return {**{k: v for k, v in info.items() if k not in ("steps", "brief")}, "trace": trace, "demo": run_id.startswith("demo--"), "provenance": provenance.of_run(info)}


def latest_feedback(run_id):
    out = {}
    if os.path.exists(feedback.LOG):
        for l in open(feedback.LOG, encoding="utf-8"):
            r = json.loads(l)
            if r["run_id"] == run_id:
                out[r["term"]] = {"decision": r["decision"], "reason": r["reason"]}
    return out


def make_card(r, stats, claims, rank, featured, fb):
    """One signal card: the numbers, label status, the three branches with the Skeptic's reasons, the claims (empty if the term was not
    written up) and every evidence item behind a citation."""
    return {"term": r["term"], "stats": stats, "label": {"decision": r["label_decision"], "evidence": r["label_evidence"], "date": r["label_date"]},
            "winner": r["winner"], "flag": r["flag"], "branches": r["branches"], "claims": claims, "evidence": r["evidence"]["items"],
            "rank": rank, "featured": featured, "written": bool(claims) or False, "feedback": fb,
            "decision": r["decision"], "reason": r["reason"]}


def recommended_ranked(run_id):
    sk, lab = load(run_id, "skeptic"), load(run_id, "label_checker")
    stats = {r["term"]: r["stats"] for r in lab["results"]}
    rec = sorted((r for r in sk["results"] if r["decision"] == "recommended"), key=lambda r: (-(stats[r["term"]]["chi2"] or 0), r["term"]))
    return sk, stats, rec


@app.get("/api/runs/{run_id}/signals")
def signals(run_id: str):
    """The brief as structured data for the UI: written signals (with claims and the evidence text behind every citation), the other
    recommended terms (each can be opened as a card), and the dismissals with their cited reasons."""
    run_dir(run_id)
    wr = load(run_id, "writer")
    if not wr:
        raise HTTPException(409, "the brief is not ready")
    sk, stats, rec = recommended_ranked(run_id)
    if sk.get("status") != "ok":
        raise HTTPException(409, "the brief is not ready")
    run = load(run_id, "run") or {}
    by_term = {r["term"]: r for r in sk["results"]}
    fb = latest_feedback(run_id)
    rank = {r["term"]: i + 1 for i, r in enumerate(rec)}
    featured = set(wr.get("featured", []))
    written = [make_card(by_term[t["term"]], stats[t["term"]], t["claims"], rank[t["term"]], t["term"] in featured, fb.get(t["term"])) for t in wr["terms"]]
    shown = {t["term"] for t in wr["terms"]}
    dismissed_highlights = [make_card(by_term[t], stats[t], [], None, True, None) for t in wr.get("featured", []) if t in by_term and by_term[t]["decision"] == "dismissed"]
    return {"drug": sk["drug"], "date": sk["date"], "disclaimer": DISCLAIMER, "provenance": provenance.of_run(run), "mode": run.get("mode", ""), "dismissed_highlights": dismissed_highlights, "models": {"pipeline": sk["model"], "brief": wr["model"]},
            "counts": {"examined": sk["n_evaluated"], "recommended": sk["n_recommended"], "dismissed": sk["n_dismissed"], "written": len(written)},
            "signals": written,
            "also_recommended": [{"term": r["term"], "rank": rank[r["term"]], "a": stats[r["term"]]["a"], "PRR": stats[r["term"]]["PRR"], "chi2": stats[r["term"]]["chi2"],
                                  "label": r["label_decision"], "feedback": fb.get(r["term"])} for r in rec if r["term"] not in shown],
            "dismissed": [{"term": r["term"], "winner": r["winner"], "reason": r["reason"], "cited": r["cited"], "rank": None, "a": stats[r["term"]]["a"],
                           "PRR": stats[r["term"]]["PRR"], "chi2": stats[r["term"]]["chi2"], "feedback": None} for r in sk["results"] if r["decision"] == "dismissed"]}


@app.get("/api/runs/{run_id}/card")
def card(run_id: str, term: str):
    """The card of any term the Skeptic examined (recommended or dismissed, written up or not), for the lists in the brief."""
    run_dir(run_id)
    sk, stats, rec = recommended_ranked(run_id)
    wr = load(run_id, "writer") or {"terms": [], "featured": []}
    r = next((x for x in sk["results"] if x["term"] == term), None)
    if r is None:
        raise HTTPException(404, "this term was not examined by the Skeptic in this run")
    claims = next((t["claims"] for t in wr["terms"] if t["term"] == term), [])
    rank = [x["term"] for x in rec].index(term) + 1 if r["decision"] == "recommended" else None
    return make_card(r, stats[term], claims, rank, term in wr.get("featured", []), latest_feedback(run_id).get(term))

@app.post("/api/runs/{run_id}/feedback")
def post_feedback(run_id: str, body: FeedbackIn):
    run_dir(run_id)
    try:
        return feedback.record(run_id, body.term, body.decision, body.reason, run_path=run_dir(run_id))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/runs/{run_id}/brief.md", response_class=PlainTextResponse)
def brief_md(run_id: str):
    p = os.path.join(run_dir(run_id), "brief.md")
    if not os.path.exists(p):
        raise HTTPException(409, "the brief is not ready")
    return provenance.with_header(open(p, encoding="utf-8").read(), load(run_id, "run") or {})
