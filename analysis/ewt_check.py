"""Part 5 FREE check of Evidence-Weighted Triage, EWT (post-result, exploratory, illustrative only, NOT validation). Protocol fixed in eval_runs.md
("part 5, Step 3", committed BEFORE any ranking). Statistics and local label text only: NO LLM call, no Label Checker, no Skeptic, no evaluation run, no network.
Modes:
  eval      the evaluation-drug cutoffs (reduced schedule + supplementary gabapentin year-ends) plus the two demo target cutoffs: targets, caught targets,
            list sizes and swaps. Output analysis/ewt_check_eval.json
  dev       retention benchmark, development drugs (gabapentin, linezolid, lithium): implementation check; TUNED evidence. Output analysis/ewt_check_dev.json
  heldout   retention benchmark, held-out drugs: THIRD look (used in parts 3 and 4). Run ONCE: refuses to run again. Output analysis/ewt_check_heldout.json
  fresh     retention benchmark, the five fresh drugs: the CLEAN test. Run ONCE: refuses to run again. Output analysis/ewt_check_fresh.json
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python -u analysis/ewt_check.py eval|dev|heldout|fresh"""
import collections, datetime as dt, importlib.util, json, os, sys, time

import eval_config as E
import fresh_drugs
import triage, triage_ewt as T
from agents import triage_step

spec = importlib.util.spec_from_file_location("benchmark_retention", os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmark_retention.py"))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)   # reuses the part-3 match rule (classify, label_texts, versions) unchanged
spec2 = importlib.util.spec_from_file_location("prune_check", os.path.join(os.path.dirname(os.path.abspath(__file__)), "prune_check.py"))
P = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(P)  # reuses the evaluation cutoffs and the target list of part 4 (import only; its __main__ block does not run)
fresh_drugs.register()

BIG = 10 ** 6
FRESH = list(fresh_drugs.FRESH)


def analyse(drug, date):
    df, feats = T.load(drug, date)
    if df is None:
        return {"drug": drug, "cutoff": date, "skipped": feats}
    label_date, text = triage_step.label_text(drug, date)
    if text is None:
        return {"drug": drug, "cutoff": date, "skipped": "no label in force"}
    s2, counts = T.survivors(df, feats, text)
    locked_all, _ = triage.triage(df, text, min_count=3, cap=BIG)
    locked_all = locked_all.sort_values("chi2", ascending=False, kind="stable").reset_index(drop=True)
    assert set(locked_all.pt) == set(s2.pt), (drug, date)          # EWT starts from exactly the locked survivors
    lk_rank = {p: i + 1 for i, p in enumerate(locked_all.pt)}
    locked = set(locked_all.head(150).pt)
    ewt, sc = T.select(s2, cap=150)
    ewt_set = set(ewt.pt)
    pos = dict(zip(sc.pt, sc.pos.astype(int)))
    later = P.later_versions(drug, date)
    inst = []
    for r in locked_all.itertuples():
        cls = B.classify(r.pt, later) if later else None
        if cls:
            inst.append({"term": r.pt, "cls": cls, "a": int(r.a), "chi2": float(r.chi2), "locked_rank": lk_rank[r.pt], "ewt_pos": pos[r.pt],
                         "in": {"locked": r.pt in locked, "ewt": r.pt in ewt_set}})
    rec = {"drug": drug, "cutoff": date, "label_date": label_date, "counts": counts,
           "survivors": len(locked_all), "capped": len(locked_all) > 150, "list_sizes": {"locked": len(locked), "ewt": len(ewt_set)},
           "swapped_in": len(ewt_set - locked), "swapped_out": len(locked - ewt_set), "instances": inst}
    scx = sc.set_index("pt")
    tg = []
    for tdrug, tdate, term, kind in P.TARGETS:
        if tdrug != drug or tdate != date:
            continue
        row = {"term": term, "kind": kind, "survivor": term in scx.index, "locked_rank": lk_rank.get(term), "locked_in": term in locked}
        if term in scx.index:
            x = scx.loc[term]
            row.update({"ewt_pos": int(x.pos), "ewt_in": int(x.pos) <= 150, "score": float(x.score), "a": int(x.a), "chi2": float(x.chi2),
                        "components": {c: float(x[c]) for c in T.COMPONENTS}, "component_ranks": {c: float(x["r_" + c]) for c in T.COMPONENTS}})
        tg.append(row)
    rec["targets"] = tg
    return rec


def summarise(rows, drugs):
    res = {}
    for d in drugs + ["POOLED"]:
        rs = [r for r in rows if "instances" in r and (d == "POOLED" or r["drug"] == d)]

        def tally(rule, capped_only):
            n = kl = ke = 0
            for r in rs:
                if capped_only and not r["capped"]:
                    continue
                for i in r["instances"]:
                    if i["cls"] in rule:
                        n += 1; kl += i["in"]["locked"]; ke += i["in"]["ewt"]
            return {"n": n, "locked_kept": kl, "ewt_kept": ke, "locked_rate": round(kl / n, 4) if n else None, "ewt_rate": round(ke / n, 4) if n else None}
        res[d] = {"cutoffs": len(rs), "capped_cutoffs": sum(r["capped"] for r in rs), "primary_capped": tally(("benchmark",), True),
                  "secondary_all": tally(("benchmark",), False), "loose_capped": tally(("benchmark", "A1"), True)}
    return res


if __name__ == "__main__":
    mode = sys.argv[1]
    t0 = time.time()
    if mode == "eval":
        jobs = [(d, E.quarter_end(q).isoformat()) for d, q in P.EVAL_RUNS] + P.DEMO_CUTOFFS
        rows = []
        for drug, date in jobs:
            r = analyse(drug, date)
            rows.append(r)
            if "skipped" in r:
                print(drug, date, "SKIPPED", r["skipped"], flush=True); continue
            print(f"{drug:12s} {date} survivors {r['survivors']:3d} | lists {r['list_sizes']} | swapped in {r['swapped_in']:3d} out {r['swapped_out']:3d} "
                  f"| targets {[(t['kind'], t['locked_rank'], t.get('ewt_pos')) for t in r['targets']]} ({time.time()-t0:.0f}s)", flush=True)
        json.dump({"mode": "eval", "protocol": "eval_runs.md part 5, Step 3 (committed before any ranking)", "cutoffs": rows}, open("analysis/ewt_check_eval.json", "w"), indent=1, default=str)
        print("saved analysis/ewt_check_eval.json")
    else:
        drugs = {"dev": B.DEV, "heldout": B.HELD, "fresh": FRESH}[mode]
        path = f"analysis/ewt_check_{mode}.json"
        if mode in ("heldout", "fresh") and os.path.exists(path):
            sys.exit(f"The {mode} split was already run once for part 5 ({path}). It is not run again (protocol, eval_runs.md part 5).")
        rows = []
        for drug in drugs:
            for y in B.YEARS:
                r = analyse(drug, f"{y}-12-31")
                if "skipped" in r:
                    continue
                rows.append(r)
                bi = [i for i in r["instances"] if i["cls"] == "benchmark"]
                print(f"{drug:12s} {y} survivors {r['survivors']:3d}{'*' if r['capped'] else ' '} bench {len(bi):3d} kept locked {sum(i['in']['locked'] for i in bi):3d} "
                      f"ewt {sum(i['in']['ewt'] for i in bi):3d} | swapped {r['swapped_in']:3d} ({time.time()-t0:.0f}s)", flush=True)
        summary = summarise(rows, drugs)
        json.dump({"split": mode, "drugs": drugs, "protocol": "eval_runs.md part 5, Step 3 (committed before any ranking)", "summary": summary,
                   "cutoffs": [{k: v for k, v in r.items() if k != "instances"} for r in rows],
                   "instances": [{"drug": r["drug"], "year": r["cutoff"][:4], **i} for r in rows for i in r["instances"]]}, open(path, "w"), indent=1, default=str)
        print("\nRETENTION (primary: capped cutoffs, benchmark class only)")
        for d, s in summary.items():
            p = s["primary_capped"]
            print(f"  {d:12s} capped {s['capped_cutoffs']:2d} | n {p['n']:5d} | locked {p['locked_kept']} ({p['locked_rate']}) | ewt {p['ewt_kept']} ({p['ewt_rate']})")
        print("saved", path)
