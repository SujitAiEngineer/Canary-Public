"""Orchestrator (Build Step 6): Resolver -> Miner -> Triage -> Label Checker -> Skeptic -> Writer, in order, passing structured JSON.
Every step is logged to runs/<run_id>/trace.jsonl and its full output is saved next to it; the brief is runs/<run_id>/brief.md and the
whole run is summarised in run.json. Every Anthropic call goes through the spend ledger (spend.py): checked before each call, hard stop at
the limit. Agents hold no orchestration logic; the API layer (Step 8) calls run_pipeline() and only relays its events.
Run: PYTHONIOENCODING=utf-8 python -u orchestrator.py --drug Lipitor --date 2011-12-31 [--serious] [--confirm atorvastatin] [--demo-brief]
"""
import argparse, json, os, sys, time

import anthropic
import canary_config
import eval_config
import model_router
import provenance
from agents import resolver, miner, triage_step, label_checker, skeptic, writer
from batching import BatchRunner
from spend import Ledger, LedgerClient, SpendLimit

EVAL_LEDGER = "eval_spend.jsonl"  # the evaluation ledger named in EVALUATION_PLAN.md section 5d
STEPS = ["resolver", "miner", "triage", "label_checker", "skeptic", "writer"]


def summarize(step, out):
    if step == "resolver":
        return out["message"]
    if step == "miner":
        if out["status"] != "ok":
            return out["message"]
        return f"{out['n_drug']:,} {out['drug']} suspect reports before {out['date']}; {out['n_terms']:,} reaction terms; {out['n_evans']} meet Evans criteria ({out['elapsed_s']}s)"
    if step == "triage":
        if out["status"] != "ok":
            return out["message"]
        c = out["counts"]
        by = "evidence score (EWT)" if out.get("ewt") else "chi-squared"
        return f"{c['evans']} flagged, {c['not_exact_on_label']} not named on the label of {out['label_date']}, top {out['cap']} by {by} sent on ({len(out['terms'])} terms)"
    if step == "label_checker":
        if out["status"] != "ok":
            return out["message"]
        d = out["decisions"]
        return f"{out['n_checked']} terms checked against the label of {out['label_date']}: {d['yes']} already on label (dropped), {d['partial']} partial, {d['no']} not on label; ${out['spend']:.3f}"
    if step == "skeptic":
        if out["status"] != "ok":
            return out["message"]
        return f"{out['n_evaluated']} terms weighed on three branches: {out['n_recommended']} recommended, {out['n_dismissed']} dismissed with a cited reason; ${out['spend']:.3f}"
    if step == "writer":
        return f"brief written: {out['n_written']} of {out['n_recommended']} recommended terms in full, {out['removed_uncited']} uncited claims removed; ${out['spend']:.3f}"


def run_pipeline(drug_text, date, serious_only=False, confirm=None, brief_model=None,
                 max_briefs=canary_config.MAX_BRIEFS_DEFAULT, on_event=None, runs_dir=canary_config.RUNS_DIR,
                 ledger=None, client=None, mode_note="", evaluation_mode=False, run_label="", run_id=None, review_cap=None, featured=(), batch=None, triage=None, model=None, cost_confirmed=False, fresh=False, mesh_synonyms=None):
    """evaluation_mode=True is for the Step 10 evaluation runner ONLY (there is deliberately no command-line flag for it). It allows the five
    evaluation drugs, uses the separate evaluation ledger and its limit (eval_config.SPEND_LIMIT_USD), forces the evaluation model (Haiku,
    no Sonnet), sends the Label Checker, Skeptic and brief calls as Message Batches (eval_config.USE_BATCHES), and writes briefs only for the case's
    target terms and the top eval_config.BRIEF_TOP_N recommended terms (approved 2026-10-03). run_label (for example "gabapentin 2009Q4") is recorded in the ledger in place of the run id."""
    # triage: None = the product default (canary_config.DEFAULT_TRIAGE, EWT) for normal runs, and ALWAYS the locked chi-squared triage in evaluation mode.
    # "ewt" or "locked" is an explicit choice (the post-result gabapentin confirmation runs evaluation mode with "ewt" on purpose, labelled as such).
    if triage is not None and triage not in canary_config.TRIAGE_CHOICES:
        raise ValueError(f"triage must be one of {canary_config.TRIAGE_CHOICES}, not {triage!r}")
    # mesh_synonyms: None = the product default (canary_config.MESH_SYNONYMS) for normal runs, and ALWAYS off in evaluation mode (the evaluation retrieves exactly as it was locked).
    if mesh_synonyms and evaluation_mode:
        raise ValueError("MeSH synonyms are a product option; the locked evaluation retrieves without them")
    mesh_synonyms = False if evaluation_mode else (canary_config.MESH_SYNONYMS if mesh_synonyms is None else bool(mesh_synonyms))
    if fresh and evaluation_mode:
        raise ValueError("a fresh run is a product option; the evaluation always uses its cache and batches")
    triage = triage or ("locked" if evaluation_mode else canary_config.DEFAULT_TRIAGE)
    # model: ONE model for every agent (Label Checker, Skeptic, brief writer). None = the pipeline default (Haiku, the exact model of the locked evaluation).
    # The evaluation refuses any other model. brief_model overrides the writer only (the demo precompute uses it).
    model = model_router.check_model(model or canary_config.PIPELINE_MODEL)
    brief_model = model_router.check_model(brief_model or model)
    if evaluation_mode and model != eval_config.MODEL:
        raise ValueError(f"evaluation mode runs the evaluation model only ({eval_config.MODEL}), not {model}")
    if review_cap or featured:  # demo-only settings: the evaluation always uses the locked cap of 150 and no featured terms
        if evaluation_mode:
            raise ValueError("review_cap and featured terms are demo-only; the evaluation uses the locked cap of 150")
        mode_note = mode_note or (canary_config.DEMO_REVIEW_LABEL if review_cap == canary_config.DEMO_REVIEW_CAP else f"extended review mode (top {review_cap})" if review_cap else "")
    if evaluation_mode:
        if brief_model != eval_config.MODEL:
            raise ValueError("evaluation mode uses the evaluation model for every call, including the brief")
        max_briefs = eval_config.BRIEF_TOP_N
        mode_note = mode_note or "evaluation"
        ledger = ledger or Ledger(EVAL_LEDGER, eval_config.SPEND_LIMIT_USD)
    elif client is None and any(model_router.is_paid(m) for m in {model, brief_model}) and not cost_confirmed:
        est = model_router.estimate_run_cost(model)
        raise model_router.ConfirmationRequired(f"{est['label']} is a paid model: estimated cost about ${est['estimate_usd']:.2f} (up to ${est['max_usd']:.2f}) for this run; "
                                                f"confirm to run it (ledger hard limit ${canary_config.RUN_LIMIT_USD:.2f})")
    run_id = run_id or time.strftime("%Y%m%d_%H%M%S") + "_" + "".join(c for c in drug_text.lower() if c.isalnum())[:20]
    rdir = os.path.join(runs_dir, run_id)
    os.makedirs(rdir, exist_ok=True)
    ledger = ledger or Ledger(canary_config.RUN_LEDGER, canary_config.RUN_LIMIT_USD)
    use_batch = (evaluation_mode and eval_config.USE_BATCHES) if batch is None else batch
    def _waiting(seconds, reason, resumed):   # the free model's rate limit: a waiting state in the UI, then the run resumes by itself
        if resumed:
            event(step, "resumed", f"Waited for {reason}; resuming")
        else:
            event(step, "waiting", f"Waiting {seconds:.0f} s for {reason}; the run resumes by itself")

    lc = (BatchRunner if use_batch else LedgerClient)(client or (anthropic.Anthropic(max_retries=5) if use_batch else model_router.build_client(on_wait=_waiting)),
                                                      ledger, run_label or run_id)
    lc.fresh = bool(fresh)   # fresh run: ignore the answer cache for model calls in this run (new answers are still cached)
    t_start, steps, outs = time.time(), [], {}
    result = {"run_id": run_id, "run_dir": rdir, "input": {"drug": drug_text, "date": date, "serious_only": serious_only},
              "status": "running", "steps": steps, "models": {"pipeline": model, "brief": brief_model},
              "triage": triage, "fresh": bool(fresh), "mesh_synonyms": mesh_synonyms, "mode": mode_note or "standard (cap 150)", "evaluation_mode": evaluation_mode, "batched": use_batch, "review_cap": review_cap, "featured": list(featured)}

    def event(step, status, summary="", out=None):
        ev = {"ts": time.strftime("%H:%M:%S"), "step": step, "status": status, "summary": summary,
              "run_spend": round(lc.run_spend, 4), "ledger_total": round(ledger.total, 4)}
        if out is not None:
            json.dump(out, open(os.path.join(rdir, f"{step}.json"), "w", encoding="utf-8"), indent=1, default=str)
            ev["artifact"] = f"{step}.json"
        with open(os.path.join(rdir, "trace.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")
        steps.append(ev)
        print(f"[{ev['ts']}] {step:14s} {status:8s} {summary}", flush=True)
        if on_event:
            on_event(ev)

    def finish(status, message=""):
        result["provenance"] = provenance.of_run(result)
        result.update(status=status, message=message, elapsed_s=round(time.time() - t_start, 1), run_spend=round(lc.run_spend, 4),
                      ledger_total=round(ledger.total, 4), budget=ledger.limit)
        json.dump(result, open(os.path.join(rdir, "run.json"), "w", encoding="utf-8"), indent=1, default=str)
        return result

    step = "resolver"
    try:
        event(step, "start", f"resolving '{drug_text}'")
        r = resolver.resolve(drug_text)
        if r["status"] == "needs_confirmation" and confirm:
            pick = next((s for s in r["suggestions"] if confirm.lower() in (s["generic_name"], s["rxcui"])), None)
            if pick:
                r = {**r, "status": "resolved", "generic_name": pick["generic_name"], "rxcui": pick["rxcui"], "confidence": 1.0,
                     "message": f"confirmed by the user: {pick['generic_name']} (RxCUI {pick['rxcui']})"}
        event(step, "done" if r["status"] == "resolved" else "stopped", r["message"], r)
        if r["status"] != "resolved":
            result["resolver"] = r
            return finish(r["status"], r["message"])
        generic = r["generic_name"]

        step = "miner"
        event(step, "start", f"mining {generic} before {date}")
        m = miner.run(generic, date, serious_only=serious_only, allow_evaluation=evaluation_mode)
        event(step, "done" if m["status"] == "ok" else "stopped", summarize(step, m), m)
        if m["status"] != "ok":
            return finish("not_enough_data", m["message"])

        step = "triage"
        event(step, "start", "keeping flagged terms not already named on the label")
        tr = triage_step.run(m, cap=review_cap or triage_step.CAP, triage=triage)
        event(step, "done" if tr["status"] == "ok" else "stopped", summarize(step, tr), tr)
        if tr["status"] != "ok":
            return finish("no_label", tr["message"])

        step = "label_checker"
        lc.set_context(stage=step)
        event(step, "start", f"checking {len(tr['terms'])} terms against the label")
        before = lc.run_spend
        lab = label_checker.run(tr, client=lc, model=model, **({"mesh": True} if mesh_synonyms else {}))
        lab["spend"] = round(lc.run_spend - before, 4)  # actual cost (batch calls are half the list price the per-term costs show)
        event(step, "done", summarize(step, lab), lab)

        step = "skeptic"
        lc.set_context(stage=step)
        event(step, "start", f"weighing {lab['n_to_skeptic']} terms: real effect, reporting spike, confounding")
        before = lc.run_spend
        sk = skeptic.run(m, lab, client=lc, model=model, allow_evaluation=evaluation_mode)
        sk["spend"] = round(lc.run_spend - before, 4)
        event(step, "done" if sk["status"] == "ok" else "stopped", summarize(step, sk) if sk["status"] == "ok" else sk["message"], sk)

        step = "writer"
        lc.set_context(stage=step)
        event(step, "start", "writing the brief with citations")
        before = lc.run_spend
        w = writer.run(sk, lab, m, client=lc, model=brief_model, max_briefs=max_briefs, mode_note=mode_note,
                         featured=(eval_config.CASES.get(generic) or eval_config.DEV.get(generic) or {}).get("terms", []) if evaluation_mode else featured, tag="target term" if evaluation_mode else "demo highlight") if sk["status"] == "ok" else \
            {"status": "skipped", "brief_markdown": "No term reached the Skeptic.", "n_written": 0, "n_recommended": 0, "removed_uncited": 0, "spend": 0}
        w["spend"] = round(lc.run_spend - before, 4)
        event(step, "done", summarize(step, w) if sk["status"] == "ok" else "nothing to write", w)
        md = provenance.with_header(w["brief_markdown"], result)   # every brief says which model and which triage produced it, and carries the notice
        open(os.path.join(rdir, "brief.md"), "w", encoding="utf-8").write(md)
        result.update(brief=md, generic=generic)
        return finish("ok")
    except SpendLimit as e:
        event(step, "stopped", f"SPEND LIMIT: {e}")
        return finish("stopped_spend_limit", str(e))
    except model_router.KeyRejected as e:   # a wrong or revoked key: say so clearly and offer the models that can still be used
        event(step, "stopped", str(e))
        result["alternatives"] = model_router.alternatives(failed_provider=e.provider)
        return finish("key_rejected", str(e))
    except model_router.DailyLimit as e:
        msg = f"The free model's daily limit is used up ({e}). Pick another model, or try again tomorrow: the answers already obtained are cached, so a rerun resumes."
        event(step, "stopped", msg)
        result["alternatives"] = model_router.alternatives(failed_model=model)
        return finish("daily_limit", msg)
    except model_router.RateLimited as e:
        msg = f"The free model stayed rate limited for too long ({e}). Try again later, or pick another model: the answers already obtained are cached."
        event(step, "stopped", msg)
        result["alternatives"] = model_router.alternatives(failed_model=model)
        return finish("rate_limited", msg)
    except PermissionError as e:
        event(step, "stopped", str(e))
        return finish("blocked", str(e))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--drug", required=True)
    ap.add_argument("--date", required=True, help="time machine date YYYY-MM-DD")
    ap.add_argument("--serious", action="store_true", help="serious reports only")
    ap.add_argument("--confirm", help="generic name or RxCUI to accept when the resolver asks 'did you mean?'")
    ap.add_argument("--triage", choices=canary_config.TRIAGE_CHOICES, default=None, help=f"triage: default {canary_config.DEFAULT_TRIAGE} (Evidence-Weighted Triage); locked = the locked chi-squared triage")
    ap.add_argument("--model", choices=list(model_router.MODELS), default=None, help="one model for all agents (default: Claude Haiku 4.5, the model of the locked evaluation)")
    ap.add_argument("--fresh", action="store_true", help="fresh run: ignore the answer cache for model calls (real, paid calls even for a drug and date run before); new answers are still cached")
    ap.add_argument("--no-mesh", action="store_true", help="do not use MeSH synonyms in the Label Checker's retrieval (default: on)")
    ap.add_argument("--yes", action="store_true", help="confirm the estimated cost of a paid model without asking")
    ap.add_argument("--demo-brief", action="store_true", help="write the brief with the demo model (Sonnet)")
    ap.add_argument("--max-briefs", type=int, default=canary_config.MAX_BRIEFS_DEFAULT, help="0 = write up every recommended term")
    a = ap.parse_args()
    model = a.model or canary_config.PIPELINE_MODEL
    brief = canary_config.DEMO_BRIEF_MODEL if a.demo_brief else model
    paid = [m for m in {model, brief} if model_router.is_paid(m)]
    confirmed = a.yes
    if paid and not confirmed:   # a paid model shows its estimate and needs a yes
        est = model_router.estimate_run_cost(model)
        print(f"{est['label']} is a paid model: estimated cost about ${est['estimate_usd']:.2f} (up to ${est['max_usd']:.2f}) for one run; "
              f"ledger hard limit ${canary_config.RUN_LIMIT_USD:.2f} for all paid runs ({canary_config.RUN_LEDGER}).")
        confirmed = sys.stdin.isatty() and input("Run it? [y/N] ").strip().lower() == "y"
        if not confirmed:
            sys.exit("Not confirmed: nothing was run. Use --yes to confirm in a script, or pick the free model.")
    res = run_pipeline(a.drug, a.date, a.serious, a.confirm, brief if a.demo_brief else None, a.max_briefs or None, triage=a.triage, model=a.model,
                       cost_confirmed=confirmed, fresh=a.fresh, mesh_synonyms=False if a.no_mesh else None)
    print(f"\nstatus: {res['status']}  run spend ${res['run_spend']:.4f}  ledger total ${res['ledger_total']:.4f} of ${res['budget']:.2f}  folder: {res['run_dir']}")
    if res["status"] == "ok":
        print("\n" + res["brief"])
    else:
        print(res.get("message", ""))
        for s in res.get("resolver", {}).get("suggestions", []):
            print("  did you mean:", s["generic_name"], s["rxcui"])
