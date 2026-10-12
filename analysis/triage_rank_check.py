"""Part 2 FREE check (post-result, exploratory, illustrative only, NOT validation): do alternative ranking scores keep rare Evans-flagged terms inside the cap?
The scores and the rest of the protocol were fixed in eval_runs.md BEFORE any result (PRR lower bound, IC025, best of both; cap 150; no tuning, nothing else
will be tried). Statistics only: the Miner and the triage rules. NO LLM call, no Label Checker, no Skeptic, no evaluation run.
Run from the repo root: PYTHONPATH=. PYTHONIOENCODING=utf-8 python -u analysis/triage_rank_check.py"""
import json, time

import pandas as pd

import eval_config as E
import triage, triage_v2
from agents import miner, triage_step

HOW = triage_v2.RANKINGS          # ("prr_lb", "ic025", "best_of")
BIG = 10 ** 6
OLDDRESS = "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"
DRESS = "DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"
# the five cases named in the pre-registration (plus the lithium older-name term as an extra row)
TARGETS = [("gabapentin 2007Q4", "gabapentin", "2007-12-31", OLDDRESS), ("gabapentin 2008Q4", "gabapentin", "2008-12-31", OLDDRESS),
           ("gabapentin 2009Q4", "gabapentin", "2009-12-31", OLDDRESS), ("linezolid 2019Q4 (demo) SIADH", "linezolid", "2019-12-31", "INAPPROPRIATE ANTIDIURETIC HORMONE SECRETION"),
           ("lithium 2017Q4 (demo) DRESS", "lithium", "2017-12-31", DRESS), ("lithium 2017Q4 (demo) DRESS older name (extra)", "lithium", "2017-12-31", OLDDRESS)]
EVAL_RUNS = [("rivaroxaban", q) for q in ("2017Q4", "2018Q4", "2019Q4")] + [("pirfenidone", q) for q in ("2015Q4", "2019Q2")] + \
            [("lamotrigine", q) for q in ("2004Q4", "2010Q3")] + [("gabapentin", q) for q in ("2006Q4", "2007Q4", "2008Q4", "2009Q4", "2010Q4", "2011Q2")] + \
            [("dasatinib", q) for q in ("2007Q4", "2024Q2")]
CAUGHT = {("pirfenidone", "2015Q4"): "HEPATOTOXICITY", ("lamotrigine", "2004Q4"): "MENINGITIS ASEPTIC", ("dasatinib", "2007Q4"): "CHYLOTHORAX"}   # the previously caught targets

_cache = {}


def analyse(drug, date):
    """Locked list and the three new lists for one cutoff; every survivor's old and new rank."""
    if (drug, date) in _cache:
        return _cache[(drug, date)]
    m = miner.run(drug, date, allow_evaluation=True)
    _, text = triage_step.label_text(drug, date)
    df = pd.DataFrame(m["terms"]).rename(columns={"term": "pt"}).sort_values("pt", kind="stable")
    old_all, _ = triage.triage(df, text, min_count=3, cap=BIG)
    old_all = old_all.sort_values("chi2", ascending=False, kind="stable").reset_index(drop=True)
    old_list = list(triage.triage(df, text, min_count=3, cap=150)[0].pt)
    new = {}
    for how in HOW:
        kept, c, ranked = triage_v2.select_ranked(df, text, how, cap=150)
        new[how] = {"list": list(kept.pt), "rank": dict(zip(ranked.pt, ranked.new_rank)), "ranked": ranked}
    out = {"n_drug": m["n_drug"], "survivors": len(old_all), "old_rank": {t: i + 1 for i, t in enumerate(old_all.pt)}, "old_list": old_list, "new": new}
    _cache[(drug, date)] = out
    return out


def target_row(label, drug, date, term):
    a = analyse(drug, date)
    row = {"case": label, "term": term, "survivors": a["survivors"], "old_rank": a["old_rank"].get(term), "old_in_cut": term in set(a["old_list"])}
    for how in HOW:
        r = a["new"][how]["rank"].get(term)
        row[how] = {"rank": r, "in_cut": term in set(a["new"][how]["list"])}
    return row


if __name__ == "__main__":
    res = {"targets": [], "eval_runs": [], "caught": []}
    print("TARGET TERMS: old rank (chi-squared) -> new rank under PRR lower bound / IC025 / best of both; in the cut of 150?", flush=True)
    for label, drug, date, term in TARGETS:
        t0 = time.time()
        r = target_row(label, drug, date, term); res["targets"].append(r)
        f = lambda h: f"{r[h]['rank']} ({'IN' if r[h]['in_cut'] else 'out'})"
        print(f"  {label:48s} old {r['old_rank']} of {r['survivors']} ({'IN' if r['old_in_cut'] else 'out'}) | PRR_lb {f('prr_lb')} | IC025 {f('ic025')} | best_of {f('best_of')} ({time.time() - t0:.0f}s)", flush=True)

    print("\nFIVE EVALUATION DRUGS, all 16 cutoffs: how each final list differs from the locked list (in / out)", flush=True)
    for drug, q in EVAL_RUNS:
        t0 = time.time()
        date = E.quarter_end(q).isoformat()
        a = analyse(drug, date)
        old = set(a["old_list"])
        row = {"run": f"{drug} {q}", "survivors": a["survivors"], "locked_list": len(old)}
        for how in HOW:
            new = set(a["new"][how]["list"])
            row[how] = {"size": len(new), "swapped_in": len(new - old), "swapped_out": len(old - new)}
        # target terms that are survivors at this cutoff: where do they land?
        tg = []
        for t in E.CASES[drug]["terms"]:
            if t in a["old_rank"]:
                tg.append({"term": t, "old_rank": a["old_rank"][t], "old_in": t in old, **{h: {"rank": a["new"][h]["rank"].get(t), "in": t in set(a["new"][h]["list"])} for h in HOW}})
        row["targets_among_survivors"] = tg
        res["eval_runs"].append(row)
        print(f"  {row['run']:20s} survivors {row['survivors']:3d} locked list {row['locked_list']:3d} | " + " | ".join(
            f"{h}: in {row[h]['swapped_in']:3d} out {row[h]['swapped_out']:3d}" for h in HOW) + f" | targets that are survivors: {[(x['term'][:14], x['old_rank']) for x in tg]} ({time.time() - t0:.0f}s)", flush=True)

    print("\nPREVIOUSLY CAUGHT TARGETS: lost under the new ranking?", flush=True)
    for (drug, q), term in CAUGHT.items():
        a = analyse(drug, E.quarter_end(q).isoformat())
        r = {"run": f"{drug} {q}", "term": term, "survivors": a["survivors"], "old_rank": a["old_rank"].get(term), "old_in": term in set(a["old_list"])}
        for how in HOW:
            r[how] = {"rank": a["new"][how]["rank"].get(term), "in_cut": term in set(a["new"][how]["list"])}
        res["caught"].append(r)
        print(f"  {r['run']:20s} {term:20s} old rank {r['old_rank']} of {r['survivors']} (in list {r['old_in']}) | " + " | ".join(f"{h}: rank {r[h]['rank']} in list {r[h]['in_cut']}" for h in HOW), flush=True)

    # an illustration: what swaps in and out at gabapentin 2008Q4 (the cutoff where the chi-squared cut was closest)
    a = analyse("gabapentin", "2008-12-31")
    res["illustration_gabapentin_2008Q4"] = {}
    for how in HOW:
        new, old = a["new"][how]["list"], a["old_list"]
        res["illustration_gabapentin_2008Q4"][how] = {"first_5_swapped_in": [t for t in new if t not in set(old)][:5], "first_5_swapped_out": [t for t in old if t not in set(new)][:5]}
    for r in res["eval_runs"]:
        r["targets_among_survivors"] = r["targets_among_survivors"]
    json.dump(res, open("analysis/triage_rank_check_results.json", "w"), indent=1, default=str)
    print("\nsaved analysis/triage_rank_check_results.json")
