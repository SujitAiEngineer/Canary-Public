"""Step 3: choose the free OpenRouter model BY TEST (exploratory; results in model_portability.md). No paid call: every request goes to a ':free' OpenRouter model
(price $0 in model_router) through model_router.Router, which validates outputs against the tool schema, retries an invalid output at most twice and backs off on rate limits.
Two checks per model:
  A. Label Checker on the locked 32-row gold set (label_gold_set_v2_DRAFT.csv, read with comment="#"), retrieval exactly as in analysis/task7_label_checker.py:
     accuracy, valid tool-call rate, confusion table.
  B. Skeptic format check on 10 terms (saved evidence packets of the atorvastatin 2011 demo run, a development drug): share of verdicts that are complete
     (winner, three branch scores in [0, 1], reasons, cited ids), and agreement of the recommend/dismiss decision with the saved Haiku run (information only).
Every call is cached on disk under the model's own key, so a run stopped by a rate limit resumes where it stopped when started again.
Rule fixed before the run: among the models whose outputs are 100% valid (32 of 32 and 10 of 10), pick the best Label Checker accuracy.
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python -u analysis/model_portability_test.py <openrouter model id, for example nvidia/nemotron-3-super-120b-a12b:free>"""
import json, os, sys, time

import pandas as pd

import label_checker, rag_index
import model_router as M
from agents import skeptic
from canary_utils import load_env

load_env()
CUTOFF = "2026-10-02"
DEMO = "demo_data/atorvastatin-2011/skeptic.json"


def label_part(router, model):
    gold = pd.read_csv("label_gold_set_v2_DRAFT.csv", comment="#")
    rows = []
    t0 = time.time()
    for g in gold.itertuples():
        in_force, hits = rag_index.search(g.drug, f"{g.reaction.lower()} adverse reaction", CUTOFF, k=5)
        passages = "\n\n".join(f"[{h['section']}] {h['text']}" for _, h in hits)
        res = label_checker.check(router, g.reaction, passages, f"{g.drug} label of {in_force}", model=model)
        rows.append({"drug": g.drug, "reaction": g.reaction, "gold": g.gold_on_label, "pred": res["on_label"], "valid": bool(res["valid"]), "cached": res["cached"],
                     "confidence": res["confidence"]})
        print(f"  LC {g.drug[:5]} {g.reaction[:32]:32s} gold={g.gold_on_label:7s} pred={str(res['on_label']):7s} {'OK  ' if res['on_label'] == g.gold_on_label else 'MISS'} "
              f"{'valid' if res['valid'] else 'INVALID'} ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows)
    return df


def skeptic_part(router, model):
    saved = json.load(open(DEMO, encoding="utf-8"))
    pool = [r for r in saved["results"] if r.get("evidence")]
    step = max(1, len(pool) // 10)
    chosen = pool[::step][:10]
    rows = []
    for r in chosen:
        out = skeptic.judge(router, saved["drug"], r["term"], saved["date"], r["evidence"], model=model)
        complete = skeptic._complete(out["verdict"])
        decision = None
        if complete:
            decision = skeptic.decide(out["verdict"], r["evidence"])[0]
        rows.append({"term": r["term"], "complete": bool(complete), "attempts": out.get("attempts"), "decision": decision, "haiku_decision": r["decision"],
                     "agree": complete and decision == r["decision"], "cached": out["cached"]})
        print(f"  SK {r['term'][:40]:40s} complete={complete} decision={decision} haiku={r['decision']} attempts={out.get('attempts')}", flush=True)
    return pd.DataFrame(rows)


def main(model_id):
    model = M.OR_PREFIX + model_id
    M.MODELS.setdefault(model, {"label": model_id, "provider": "openrouter", "price": (0.0, 0.0)})
    label_checker.PRICES.setdefault(model, (0.0, 0.0))
    router = M.Router(openrouter=M.OpenRouterBackend(max_wait_s=3600))
    print(f"=== {model_id}", flush=True)
    out = {"model": model_id, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "status": "complete"}
    path = f"analysis/model_portability_{model_id.replace('/', '_').replace(':', '_')}.json"
    try:
        lc = label_part(router, model)
        out["label_checker"] = {"n": len(lc), "correct": int((lc.gold == lc.pred).sum()), "accuracy": round(float((lc.gold == lc.pred).mean()), 4), "valid": int(lc.valid.sum()),
                                "valid_rate": round(float(lc.valid.mean()), 4), "rows": lc.to_dict("records"),
                                "confusion": pd.crosstab(lc.gold, lc.pred).reindex(index=["yes", "partial", "no"], columns=["yes", "partial", "no"], fill_value=0).to_dict()}
        print(f"LABEL CHECKER {out['label_checker']['correct']}/{len(lc)} = {out['label_checker']['accuracy']}; valid {out['label_checker']['valid']}/{len(lc)}", flush=True)
        sk = skeptic_part(router, model)
        out["skeptic"] = {"n": len(sk), "complete": int(sk.complete.sum()), "complete_rate": round(float(sk.complete.mean()), 4), "agree_with_haiku": int(sk.agree.sum()),
                          "rows": sk.to_dict("records")}
        print(f"SKEPTIC complete {out['skeptic']['complete']}/{len(sk)}; decision agrees with saved Haiku on {out['skeptic']['agree_with_haiku']}", flush=True)
    except (M.DailyLimit, M.RateLimited) as e:
        out["status"] = f"STOPPED: {type(e).__name__}: {e}"
        print(out["status"], flush=True)
    out["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(out, open(path, "w", encoding="utf-8"), indent=1, default=str)
    print("saved", path)


if __name__ == "__main__":
    main(sys.argv[1])
