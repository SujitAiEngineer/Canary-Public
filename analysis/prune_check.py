"""Part 4 FREE check (post-result, exploratory, illustrative only, NOT validation). Protocol fixed in eval_runs.md ("part 4", committed BEFORE any computation).
Statistics, local label text and the local embedding model only: NO LLM call, no Label Checker, no Skeptic, no evaluation run, no network.
Modes:
  eval      the evaluation-drug cutoffs (reduced schedule + supplementary gabapentin year-ends) plus the two demo target cutoffs: pruning safety, slots freed,
            targets, caught targets, list sizes and swaps. Output analysis/prune_check_eval.json
  dev       retention benchmark, development drugs (gabapentin, linezolid, lithium): implementation check only. Output analysis/prune_check_dev.json
  heldout   retention benchmark, held-out drugs (second look: they were already used once in part 3). Run ONCE: refuses to run again.
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python -u analysis/prune_check.py eval|dev|heldout"""
import collections, datetime as dt, glob, importlib.util, json, os, sys, time

import pandas as pd

import eval_config as E
import triage, triage_prune as T
from agents import triage_step

spec = importlib.util.spec_from_file_location("benchmark_retention", os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmark_retention.py"))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)   # reuses the part-3 match rule (classify, label_texts, versions) unchanged

BIG = 10 ** 6
EVAL_RUNS = [("rivaroxaban", q) for q in ("2017Q4", "2018Q4", "2019Q4")] + [("pirfenidone", q) for q in ("2015Q4", "2019Q2")] + \
            [("lamotrigine", q) for q in ("2004Q4", "2010Q3")] + [("gabapentin", q) for q in ("2006Q4", "2007Q4", "2008Q4", "2009Q4", "2010Q4", "2011Q2")] + \
            [("dasatinib", q) for q in ("2007Q4", "2024Q2")]
DEMO_CUTOFFS = [("linezolid", "2019-12-31"), ("lithium", "2017-12-31")]
# (drug, date, term, kind). kind: target = missed by the locked triage, caught = detected in the locked evaluation
TARGETS = [("gabapentin", "2007-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "target"), ("gabapentin", "2008-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "target"),
           ("gabapentin", "2009-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "target"), ("linezolid", "2019-12-31", "INAPPROPRIATE ANTIDIURETIC HORMONE SECRETION", "target"),
           ("lithium", "2017-12-31", "DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "target"), ("lithium", "2017-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "target-older-name"),
           ("pirfenidone", "2015-12-31", "HEPATOTOXICITY", "caught"), ("lamotrigine", "2004-12-31", "MENINGITIS ASEPTIC", "caught"), ("dasatinib", "2007-12-31", "CHYLOTHORAX", "caught"),
           ("lamotrigine", "2010-09-30", "MENINGITIS ASEPTIC", "caught-later"), ("dasatinib", "2024-06-30", "CHYLOTHORAX", "caught-later")]


def later_versions(drug, cutoff):
    d0 = dt.date.fromisoformat(cutoff)
    out = []
    for d in sorted(B.versions()[drug]):
        dd = dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))
        if d0 < dd <= d0 + dt.timedelta(days=B.HORIZON_DAYS):
            out.append(B.label_texts(drug, d))
    return out


def saved_label_answers():
    """{(drug, date): {term: decision}} from the saved evaluation runs (the cached Label Checker answers)."""
    out = {}
    for d in sorted(glob.glob("eval_out/runs/*")):
        if not (os.path.exists(f"{d}/run.json") and os.path.exists(f"{d}/label_checker.json")):
            continue
        r, l = json.load(open(f"{d}/run.json", encoding="utf-8")), json.load(open(f"{d}/label_checker.json", encoding="utf-8"))
        out[(r["input"]["drug"], r["input"]["date"])] = {x["term"]: x["decision"] for x in l.get("results", [])}
    return out


def analyse(drug, date, answers=None, keep_pruned=False):
    df, feats = T.load(drug, date)
    if df is None:
        return {"drug": drug, "cutoff": date, "skipped": feats}
    label_date, text = triage_step.label_text(drug, date)
    if text is None:
        return {"drug": drug, "cutoff": date, "skipped": "no label in force"}
    flagged, kept, counts = T.prepare(df, feats, text, drug, label_date)
    if kept.dist.isna().any() and len(kept):
        return {"drug": drug, "cutoff": date, "skipped": "no indexed label vectors (PL, PLN skipped)"}
    locked_all, _ = triage.triage(df, text, min_count=3, cap=BIG)
    locked_all = locked_all.sort_values("chi2", ascending=False, kind="stable").reset_index(drop=True)
    locked = list(locked_all.head(150).pt)
    lk = set(locked)
    orders = {v: T.variant_order(kept, v) for v in T.VARIANTS}
    lists = {v: list(o.head(150).pt) for v, o in orders.items()}
    later = later_versions(drug, date)
    fl = flagged.set_index("pt")
    inst, pruned_rows = [], []
    for i, r in enumerate(locked_all.itertuples()):
        cls = B.classify(r.pt, later) if later else None
        pr = bool(fl.loc[r.pt, "r2"] or fl.loc[r.pt, "r3"])
        if cls:
            inst.append({"term": r.pt, "cls": cls, "a": int(r.a), "chi2": float(r.chi2), "locked_rank": i + 1, "pruned": pr, "r2": bool(fl.loc[r.pt, "r2"]), "r3": bool(fl.loc[r.pt, "r3"]),
                         "in": {"locked": r.pt in lk, **{v: r.pt in set(lists[v]) for v in T.VARIANTS}}})
        if pr and (i < 150 or keep_pruned):
            pruned_rows.append({"term": r.pt, "locked_rank": i + 1, "in_locked_150": i < 150, "r2": bool(fl.loc[r.pt, "r2"]), "r3": bool(fl.loc[r.pt, "r3"]),
                                "label_answer": (answers or {}).get((drug, date), {}).get(r.pt), "later_label_class": B.classify(r.pt, later) if later else None})
    top150_prunable = sum(1 for p in locked if bool(fl.loc[p, "r2"] or fl.loc[p, "r3"]))
    rec = {"drug": drug, "cutoff": date, "label_date": label_date, "n_drug": feats["n_drug"], "win_drug": feats["win_drug"], "indications": feats["indications"],
           "indication_counts": feats["indication_counts"], "counts": counts, "survivors": len(locked_all), "capped": len(locked_all) > 150, "pruned_survivors": len(kept),
           "n_novel": int(kept.novel.sum()), "top150_prunable": top150_prunable, "list_sizes": {"locked": len(locked), **{v: len(l) for v, l in lists.items()}},
           "swapped_in": {v: len(set(l) - lk) for v, l in lists.items()}, "swapped_out": {v: len(lk - set(l)) for v, l in lists.items()},
           "label_answers_available": bool(answers and (drug, date) in answers), "pruned_in_locked_top150": pruned_rows, "instances": inst}
    # targets at this cutoff
    tg = []
    for tdrug, tdate, term, kind in TARGETS:
        if tdrug != drug or tdate != date:
            continue
        row = {"term": term, "kind": kind, "locked_rank": None, "locked_in": term in lk}
        hit = locked_all.index[locked_all.pt == term]
        if len(hit):
            row["locked_rank"] = int(hit[0]) + 1
        f1 = flagged[flagged.pt == term]
        row["evans_a3"] = bool(len(f1)); row["r1_on_label"] = bool(len(f1) and f1.r1.iloc[0]); row["pruned_by_r2"] = bool(len(f1) and f1.r2.iloc[0]); row["pruned_by_r3"] = bool(len(f1) and f1.r3.iloc[0])
        row["a"] = int(f1.a.iloc[0]) if len(f1) else None
        row["novel"] = bool(kept[kept.pt == term].novel.iloc[0]) if (kept.pt == term).any() else None
        row["label_distance"] = float(kept[kept.pt == term].dist.iloc[0]) if (kept.pt == term).any() else None
        for v, o in orders.items():
            x = o[o.pt == term]
            row[v] = {"pos": int(x.pos.iloc[0]), "in": bool(x.pos.iloc[0] <= 150), "r_chi2": int(x.r_chi2.iloc[0]), "r_dist": None if x.r_dist.isna().iloc[0] else int(x.r_dist.iloc[0]),
                      "quota_slot": bool(x.quota_slot.iloc[0])} if len(x) else None
        row["pruned_survivors"] = len(kept)
        tg.append(row)
    rec["targets"] = tg
    return rec


def summarise(rows, drugs):
    res = {}
    for d in drugs + ["POOLED"]:
        rs = [r for r in rows if "instances" in r and (d == "POOLED" or r["drug"] == d)]
        def tally(rule, capped_only):
            n = 0
            k = collections.Counter()
            pr = 0
            for r in rs:
                if capped_only and not r["capped"]:
                    continue
                for i in r["instances"]:
                    if i["cls"] in rule:
                        n += 1; pr += i["pruned"]
                        for key, v in i["in"].items():
                            k[key] += v
            return {"n": n, "pruned_benchmark_terms": pr, **{f"{key}_kept": k[key] for key in ("locked",) + T.VARIANTS}, **{f"{key}_rate": round(k[key] / n, 4) if n else None for key in ("locked",) + T.VARIANTS}}
        res[d] = {"cutoffs": len(rs), "capped_cutoffs": sum(r["capped"] for r in rs), "primary_capped": tally(("benchmark",), True), "secondary_all": tally(("benchmark",), False),
                  "loose_capped": tally(("benchmark", "A1"), True)}
    return res


if __name__ == "__main__":
    mode = sys.argv[1]
    t0 = time.time()
    if mode == "eval":
        answers = saved_label_answers()
        jobs = [(d, E.quarter_end(q).isoformat()) for d, q in EVAL_RUNS] + DEMO_CUTOFFS
        rows = []
        for drug, date in jobs:
            r = analyse(drug, date, answers, keep_pruned=True)
            rows.append(r)
            if "skipped" in r:
                print(drug, date, "SKIPPED", r["skipped"], flush=True); continue
            ls = r["list_sizes"]
            print(f"{drug:12s} {date} survivors {r['survivors']:3d} pruned {r['counts']['removed_total_of_survivors']:2d} (r2 {r['counts']['removed_rule2_of_survivors']}, r3 {r['counts']['removed_rule3_of_survivors']}) "
                  f"top150 prunable {r['top150_prunable']:2d} | lists {ls} | swapped in P {r['swapped_in']['P']} PN {r['swapped_in']['PN']} PL {r['swapped_in']['PL']} PLN {r['swapped_in']['PLN']} ({time.time()-t0:.0f}s)", flush=True)
        json.dump({"mode": "eval", "protocol": "eval_runs.md part 4 (committed before any computation)", "cutoffs": rows}, open("analysis/prune_check_eval.json", "w"), indent=1, default=str)
        print("saved analysis/prune_check_eval.json")
    else:
        drugs = B.DEV if mode == "dev" else B.HELD
        path = f"analysis/prune_check_{mode}.json"
        if mode == "heldout" and os.path.exists(path):
            sys.exit(f"The held-out split was already run once for part 4 ({path}). It is not run again (protocol, eval_runs.md part 4).")
        rows = []
        for drug in drugs:
            for y in B.YEARS:
                date = f"{y}-12-31"
                r = analyse(drug, date)
                if "skipped" in r or r["n_drug"] < B.MIN_REPORTS:
                    continue
                rows.append(r)
                bi = [i for i in r["instances"] if i["cls"] == "benchmark"]
                print(f"{drug:12s} {y} survivors {r['survivors']:3d}{'*' if r['capped'] else ' '} bench {len(bi):3d} kept locked {sum(i['in']['locked'] for i in bi):3d} "
                      + " ".join(f"{v} {sum(i['in'][v] for i in bi):3d}" for v in T.VARIANTS) + f" | pruned bench {sum(i['pruned'] for i in bi)} ({time.time()-t0:.0f}s)", flush=True)
        summary = summarise(rows, drugs)
        json.dump({"split": mode, "drugs": drugs, "protocol": "eval_runs.md part 4 (committed before any computation)", "summary": summary,
                   "cutoffs": [{k: v for k, v in r.items() if k not in ("instances", "pruned_in_locked_top150")} for r in rows],
                   "instances": [{"drug": r["drug"], "year": r["cutoff"][:4], **i} for r in rows for i in r["instances"]],
                   "pruned_benchmark": [{"drug": r["drug"], "cutoff": r["cutoff"], **i} for r in rows for i in r["instances"] if i["pruned"]]}, open(path, "w"), indent=1, default=str)
        print("\nRETENTION (primary: capped cutoffs, benchmark class only)")
        for d, s in summary.items():
            p = s["primary_capped"]
            print(f"  {d:12s} capped {s['capped_cutoffs']:2d} | n {p['n']:5d} | locked {p['locked_rate']} | " + " | ".join(f"{v} {p[v+'_rate']}" for v in T.VARIANTS) + f" | pruned benchmark terms {p['pruned_benchmark_terms']}")
        print("saved", path)
