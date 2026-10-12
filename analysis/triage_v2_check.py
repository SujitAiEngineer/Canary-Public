"""Triage v2 FREE check (post-result, exploratory, illustrative only: it is run on the same cases that exposed the problem, so it is NOT validation).
Statistics only: the Miner and the triage rules. NO LLM call, no Label Checker, no Skeptic, no evaluation run.
For each case it compares the locked triage (chi-squared rank, cap 150) with triage v2 in two modes:
  reserve_first  = the rule as specified (take the 20 best serious terms first, fill the rest by chi-squared);
  reserve_beyond = the variant (20 reserved slots go to the best serious terms outside the plain top 130).
Run from the repo root: PYTHONPATH=. PYTHONIOENCODING=utf-8 python -u analysis/triage_v2_check.py"""
import json, sys, time

import pandas as pd

import eval_config as E
import triage, triage_v2
from agents import miner, triage_step

GROUPS, IME = triage_v2.load_groups(), triage_v2.load_ime()
BIG = 10 ** 6

# (label, drug, cutoff date, the target term(s) as named in the Miner output, and the group name they belong to under v2)
TARGET_CASES = [
    ("gabapentin 2007Q4", "gabapentin", "2007-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"),
    ("gabapentin 2008Q4", "gabapentin", "2008-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"),
    ("gabapentin 2009Q4", "gabapentin", "2009-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"),
    ("gabapentin 2010Q4", "gabapentin", "2010-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"),
    ("linezolid 2019Q4 (demo)", "linezolid", "2019-12-31", "INAPPROPRIATE ANTIDIURETIC HORMONE SECRETION"),
    ("lithium 2017Q4 (demo)", "lithium", "2017-12-31", "DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"),
]
# the scheduled evaluation runs (reduced schedule + the supplementary gabapentin year-ends): for the alert-count check
EVAL_RUNS = [("rivaroxaban", q) for q in ("2017Q4", "2018Q4", "2019Q4")] + [("pirfenidone", q) for q in ("2015Q4", "2019Q2")] + \
            [("lamotrigine", q) for q in ("2004Q4", "2010Q3")] + [("gabapentin", q) for q in ("2006Q4", "2007Q4", "2008Q4", "2009Q4", "2010Q4", "2011Q2")] + \
            [("dasatinib", q) for q in ("2007Q4", "2024Q2")]


def frame(m):
    return pd.DataFrame(m["terms"]).rename(columns={"term": "pt"}).sort_values("pt", kind="stable")


def one(drug, date, targets=()):
    """Run the locked triage and both v2 modes on one cutoff. Returns a dict of numbers; ranks are by chi-squared among the triage survivors."""
    d_m = miner.run(drug, date, allow_evaluation=True)
    v_m = miner.run(drug, date, allow_evaluation=True, term_groups=GROUPS)
    _, text = triage_step.label_text(drug, date)
    d_df, v_df = frame(d_m), frame(v_m)
    d_surv, _ = triage.triage(d_df, text, min_count=3, cap=BIG)
    d_surv = d_surv.sort_values("chi2", ascending=False, kind="stable").reset_index(drop=True)
    d_list, _ = triage.triage(d_df, text, min_count=3, cap=150)
    v_all, vc = triage_v2.select(v_df, text, cap=BIG, reserve=0, ime=IME)           # every v2 survivor, with its serious flag, ranked by chi-squared
    v_all = v_all.reset_index(drop=True)
    first, c1 = triage_v2.select(v_df, text, cap=150, reserve=20, ime=IME, mode="reserve_first")
    beyond, c2 = triage_v2.select(v_df, text, cap=150, reserve=20, ime=IME, mode="reserve_beyond")
    out = {"drug": drug, "date": date, "n_drug": d_m["n_drug"], "n_evans_default": d_m["n_evans"], "n_evans_v2": v_m["n_evans"],
           "survivors_default": len(d_surv), "survivors_v2": len(v_all), "ime_eligible_v2": c1["ime_eligible"],
           "list_default": len(d_list), "list_first": len(first), "list_beyond": len(beyond),
           "reserved_first": c1["reserved"], "reserved_beyond": c2["reserved"],
           "new_vs_default_first": len(set(first.pt) - set(d_list.pt)), "new_vs_default_beyond": len(set(beyond.pt) - set(d_list.pt)),
           "dropped_vs_default_beyond": len(set(d_list.pt) - set(beyond.pt)), "groups_merged": len(v_m.get("term_groups", {}))}
    for t in targets:
        grp = next((g for g, ms in GROUPS.items() if t in ms), t)
        r_old = d_surv.index[d_surv.pt == t]
        r_new = v_all.index[v_all.pt == grp]
        ime_rank = None
        if len(r_new):
            sub = v_all[v_all.ime].reset_index(drop=True)
            hit = sub.index[sub.pt == grp]
            ime_rank = int(hit[0]) + 1 if len(hit) else None
        row = v_df[v_df.pt == grp]
        out["target"] = {"term": t, "group": grp, "old_rank": int(r_old[0]) + 1 if len(r_old) else None, "old_in_list": t in set(d_list.pt),
                         "new_rank": int(r_new[0]) + 1 if len(r_new) else None, "rank_among_serious": ime_rank,
                         "a_old": int(d_df[d_df.pt == t].a.iloc[0]) if (d_df.pt == t).any() else 0, "a_group": int(row.a.iloc[0]) if len(row) else 0,
                         "in_first": grp in set(first.pt), "in_beyond": grp in set(beyond.pt),
                         "beyond_reserved": bool(beyond[beyond.pt == grp].reserved_ime.iloc[0]) if grp in set(beyond.pt) else False}
    return out


if __name__ == "__main__":
    results = {"targets": [], "eval_runs": []}
    for label, drug, date, t in TARGET_CASES:
        t0 = time.time()
        r = one(drug, date, [t]); r["label"] = label
        results["targets"].append(r)
        x = r["target"]
        print(f"{label:24s} {t[:46]:46s} old rank {x['old_rank']} of {r['survivors_default']} (in locked list: {x['old_in_list']}) | v2 rank {x['new_rank']} of {r['survivors_v2']}, "
              f"rank among serious terms {x['rank_among_serious']} of {r['ime_eligible_v2']} | cut: as specified {x['in_first']}, beyond-the-cut variant {x['in_beyond']} | a {x['a_old']}->{x['a_group']} ({time.time() - t0:.0f}s)", flush=True)
    for drug, q in EVAL_RUNS:
        t0 = time.time()
        date = E.quarter_end(q).isoformat()
        tgt = E.CASES[drug]["terms"]
        r = one(drug, date); r["label"] = f"{drug} {q}"
        results["eval_runs"].append(r)
        print(f"{r['label']:20s} Evans flagged {r['n_evans_default']}->{r['n_evans_v2']} | survivors {r['survivors_default']}->{r['survivors_v2']} | list default {r['list_default']}, as specified {r['list_first']} "
              f"(+{r['new_vs_default_first']} new, reserved {r['reserved_first']}), beyond variant {r['list_beyond']} (+{r['new_vs_default_beyond']} new, -{r['dropped_vs_default_beyond']} dropped, reserved {r['reserved_beyond']}) "
              f"| groups merged {r['groups_merged']} ({time.time() - t0:.0f}s)", flush=True)
    json.dump(results, open("analysis/triage_v2_check_results.json", "w"), indent=1, default=str)
    print("\nsaved analysis/triage_v2_check_results.json")
