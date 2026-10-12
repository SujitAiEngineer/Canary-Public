"""Part 5 ADDITIONAL, POST-HOC analyses of EWT (free, offline, no LLM). NOT part of the pre-registered design (eval_runs.md part 5, Step 3); written after the
fresh-split result was known. Illustrative only.
  stats    McNemar's exact test and a bootstrap 95% CI (resampling drug-cutoffs) for the fresh split; retention by report count (< 20 vs >= 20) for all splits
  compose  composition of the terms EWT swapped in and out versus the locked list on the fresh split: median a, median chi-squared, share on the EMA IME list
  targets  rank and in/out under EWT of the pre-registered targets (from analysis/ewt_check_eval.json)
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python -u analysis/ewt_extra.py stats|compose|targets"""
import collections, json, math, os, random, statistics, sys

import numpy as np


def mcnemar_exact(b, c):
    """Two-sided exact binomial p for the discordant pairs b (locked kept, EWT dropped) and c (locked dropped, EWT kept)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def capped_instances(path):
    d = json.load(open(path))
    cap = {(c["drug"], c["cutoff"]): c["capped"] for c in d["cutoffs"]}
    out = [i for i in d["instances"] if i["cls"] == "benchmark" and cap[(i["drug"], f"{i['year']}-12-31")]]
    return d, out


def stats():
    res = {}
    d, inst = capped_instances("analysis/ewt_check_fresh.json")
    n = len(inst)
    b = sum(1 for i in inst if i["in"]["locked"] and not i["in"]["ewt"])
    c = sum(1 for i in inst if not i["in"]["locked"] and i["in"]["ewt"])
    both = sum(1 for i in inst if i["in"]["locked"] and i["in"]["ewt"])
    neither = n - b - c - both
    res["mcnemar"] = {"n": n, "both_kept": both, "locked_only": b, "ewt_only": c, "neither": neither, "p_exact_two_sided": mcnemar_exact(b, c),
                      "locked_rate": (both + b) / n, "ewt_rate": (both + c) / n, "diff": (c - b) / n}
    # bootstrap by drug-cutoff (the unit that shares one list and one label history); 10,000 resamples, fixed seed
    groups = collections.defaultdict(lambda: [0, 0, 0])   # (n, locked kept, ewt kept) per drug-cutoff
    for i in inst:
        g = groups[(i["drug"], i["year"])]
        g[0] += 1; g[1] += i["in"]["locked"]; g[2] += i["in"]["ewt"]
    keys = sorted(groups)
    arr = np.array([groups[k] for k in keys], dtype=float)
    rng = np.random.default_rng(20261004)
    diffs = []
    for _ in range(10000):
        s = arr[rng.integers(0, len(arr), len(arr))].sum(axis=0)
        if s[0] > 0:
            diffs.append((s[2] - s[1]) / s[0])
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    res["bootstrap_by_drug_cutoff"] = {"units": len(keys), "resamples": 10000, "seed": 20261004, "diff_ci95": [round(float(lo), 4), round(float(hi), 4)],
                                       "share_of_resamples_ewt_better": round(float(np.mean(np.array(diffs) > 0)), 4)}
    # secondary: bootstrap by drug (5 clusters) just to show how few clusters there are
    drugs = sorted({k[0] for k in keys})
    per_drug = {dr: (sum(groups[k][2] for k in keys if k[0] == dr) - sum(groups[k][1] for k in keys if k[0] == dr)) / sum(groups[k][0] for k in keys if k[0] == dr) for dr in drugs}
    res["per_drug_diff"] = {k: round(v, 4) for k, v in per_drug.items()}
    # leave-one-drug-out pooled difference
    loo = {}
    for ex in drugs:
        sub = [i for i in inst if i["drug"] != ex]
        loo[ex] = round((sum(i["in"]["ewt"] for i in sub) - sum(i["in"]["locked"] for i in sub)) / len(sub), 4)
    res["leave_one_drug_out_diff"] = loo
    # retention by report count, every split
    by = {}
    for split in ("fresh", "dev", "heldout"):
        p = f"analysis/ewt_check_{split}.json"
        if not os.path.exists(p):
            continue
        _, ins = capped_instances(p)
        row = {}
        for name, sel in (("a<20", lambda a: a < 20), ("a>=20", lambda a: a >= 20)):
            s = [i for i in ins if sel(i["a"])]
            row[name] = {"n": len(s), "locked_kept": sum(i["in"]["locked"] for i in s), "ewt_kept": sum(i["in"]["ewt"] for i in s),
                         "locked_rate": round(sum(i["in"]["locked"] for i in s) / len(s), 4) if s else None, "ewt_rate": round(sum(i["in"]["ewt"] for i in s) / len(s), 4) if s else None}
        by[split] = row
    res["retention_by_report_count"] = by
    json.dump(res, open("analysis/ewt_extra_stats.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


def compose():
    import fresh_drugs
    import triage, triage_ewt as T, triage_v2
    from agents import triage_step
    fresh_drugs.register()
    ime = triage_v2.load_ime()
    BIG = 10 ** 6
    rows = {"in": [], "out": [], "kept_both": [], "all_survivors": []}
    for drug in fresh_drugs.FRESH:
        for y in range(2004, 2026):
            date = f"{y}-12-31"
            df, feats = T.load(drug, date)
            if df is None:
                continue
            _, text = triage_step.label_text(drug, date)
            if text is None:
                continue
            s2, _ = T.survivors(df, feats, text)
            if len(s2) <= 150:
                continue
            lk = triage.triage(df, text, min_count=3, cap=BIG)[0].sort_values("chi2", ascending=False, kind="stable")
            locked = set(lk.head(150).pt)
            ewt, _ = T.select(s2, cap=150)
            ew = set(ewt.pt)
            for r in s2.itertuples():
                rec = (int(r.a), float(r.chi2), r.pt.upper() in ime)
                rows["all_survivors"].append(rec)
                if r.pt in ew and r.pt not in locked:
                    rows["in"].append(rec)
                elif r.pt in locked and r.pt not in ew:
                    rows["out"].append(rec)
                elif r.pt in ew and r.pt in locked:
                    rows["kept_both"].append(rec)
        print(drug, "done", {k: len(v) for k, v in rows.items()}, flush=True)
    out = {}
    for k, v in rows.items():
        out[k] = {"n": len(v), "median_a": statistics.median(x[0] for x in v), "median_chi2": round(statistics.median(x[1] for x in v), 2),
                  "share_ime": round(sum(x[2] for x in v) / len(v), 4), "mean_a": round(statistics.mean(x[0] for x in v), 2)}
    json.dump(out, open("analysis/ewt_extra_compose.json", "w"), indent=1)
    print(json.dumps(out, indent=1))


def targets():
    d = json.load(open("analysis/ewt_check_eval.json"))
    for c in d["cutoffs"]:
        for t in c.get("targets", []):
            print(c["drug"], c["cutoff"], t["kind"], t["term"], "| survivors", c["survivors"], "| locked rank", t["locked_rank"], "in" if t["locked_in"] else "OUT",
                  "| EWT pos", t.get("ewt_pos"), "IN" if t.get("ewt_in") else "OUT", "| a", t.get("a"), "| component ranks", t.get("component_ranks"))


if __name__ == "__main__":
    {"stats": stats, "compose": compose, "targets": targets}[sys.argv[1]]()
