"""Part 3 FREE benchmark (post-result, exploratory, illustrative only, NOT validation). Protocol fixed in eval_runs.md ("part 3", committed BEFORE any result):
for each drug and each year-end cutoff, the Evans-flagged triage survivors that a LATER label version names (match rule below) form the benchmark; the metric is
the retention rate: the share of those terms that stay in the final list of 150 under the locked triage versus stratified triage, pooled over the cutoffs where
the cap binds (survivors > 150). Statistics and label text only: NO LLM call, no Label Checker, no Skeptic, no evaluation run.

MATCH RULE (conservative; the same exact-phrase matcher as the locked triage, restricted):
  a survivor term T at cutoff c is a BENCHMARK instance if T's exact phrase (locked folding of US/UK spelling and increased/elevated variants, word boundaries)
  appears in a chunk tagged as an adverse-reactions or warnings/precautions section (tag contains ADVERSE, WARNING or PRECAUTION, or is a numbered section 5 or 6)
  of at least one label version dated AFTER c and within 5 years of c. One-word terms shorter than 6 letters are excluded (too generic).
AMBIGUOUS (counted and listed, excluded from the primary benchmark): A1 = the phrase appears in a later label only OUTSIDE adverse-reactions/warnings sections
  (or in untagged chunks); A2 = a one-word term shorter than 6 letters that matches. The looser secondary rule also credits A1.
SPLIT: development gabapentin, linezolid, lithium (run first, only to check the implementation); held-out pirfenidone, lamotrigine, dasatinib, rivaroxaban,
  atorvastatin, ixazomib (run ONCE: the script refuses to run the held-out split again once its result file exists).
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python -u analysis/benchmark_retention.py dev      |      ... heldout"""
import collections, datetime as dt, json, os, re, sys, time

import pandas as pd

import eval_config as E
import triage, triage_v2
from agents import miner, triage_step

DEV = ["gabapentin", "linezolid", "lithium"]
HELD = ["pirfenidone", "lamotrigine", "dasatinib", "rivaroxaban", "atorvastatin", "ixazomib"]
YEARS = range(2004, 2026)
MIN_REPORTS = 50
HORIZON_DAYS = 5 * 366
BIG = 10 ** 6

# the pre-registered target cases, for the "where do they land" report (drug, cutoff, term)
TARGETS = [("gabapentin", "2007-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"), ("gabapentin", "2008-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"),
           ("gabapentin", "2009-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"), ("linezolid", "2019-12-31", "INAPPROPRIATE ANTIDIURETIC HORMONE SECRETION"),
           ("lithium", "2017-12-31", "DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"), ("lithium", "2017-12-31", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"),
           ("pirfenidone", "2015-12-31", "HEPATOTOXICITY"), ("lamotrigine", "2004-12-31", "MENINGITIS ASEPTIC"), ("dasatinib", "2007-12-31", "CHYLOTHORAX"),
           ("dasatinib", "2024-12-31", "CHYLOTHORAX"), ("lamotrigine", "2010-12-31", "MENINGITIS ASEPTIC")]

_versions = None


def versions():
    """{drug: {label_date YYYYMMDD: [(section, text), ...]}} from the indexed label histories."""
    global _versions
    if _versions is None:
        _versions = collections.defaultdict(lambda: collections.defaultdict(list))
        for l in open("data/rag/chunks.jsonl", encoding="utf-8"):
            r = json.loads(l)
            _versions[r["drug"]][r["label_date"]].append((r["section"], r["text"]))
    return _versions


def is_ar_section(section):
    s = section.upper().strip()
    return "ADVERSE" in s or "WARNING" in s or "PRECAUTION" in s or re.match(r"^[56](\.\d+)?(\s|$)", s) is not None


_folded = {}


def label_texts(drug, date):
    """(folded text of the adverse-reactions and warnings sections, folded text of the whole label) of one version."""
    k = (drug, date)
    if k not in _folded:
        chunks = versions()[drug][date]
        _folded[k] = (triage.fold(" ".join(t for s, t in chunks if is_ar_section(s))), triage.fold(" ".join(t for s, t in chunks)))
    return _folded[k]


def matcher(term):
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(v) for v in sorted(triage.variants(term))) + r")(?![a-z0-9])")


def classify(term, later):
    """'benchmark' | 'A1' | 'A2' | None for one survivor term, given the label texts of the later versions in the horizon."""
    rx = matcher(term)
    words = triage.fold(term).split()
    in_ar = any(rx.search(ar) for ar, _ in later)
    if in_ar:
        return "A2" if (len(words) == 1 and len(words[0]) < 6) else "benchmark"
    if any(rx.search(full) for _, full in later):
        return "A1"
    return None


def one_cutoff(drug, year):
    date = f"{year}-12-31"
    m = miner.run(drug, date, allow_evaluation=True)
    if m["status"] != "ok" or m["n_drug"] < MIN_REPORTS:
        return None
    label_date, text = triage_step.label_text(drug, date)
    if text is None:
        return None
    df = pd.DataFrame(m["terms"]).rename(columns={"term": "pt"}).sort_values("pt", kind="stable")
    surv, _ = triage.triage(df, text, min_count=3, cap=BIG)
    surv = surv.sort_values("chi2", ascending=False, kind="stable").reset_index(drop=True)
    default_list = set(surv.head(150).pt)
    strat_kept, sc, strat_all = triage_v2.select_stratified(df, text, cap=150)
    strat_list = set(strat_kept.pt)
    srank = {r.pt: (int(r.band), int(r.rank_in_band)) for r in strat_all.itertuples()}
    cutoff_d = dt.date(year, 12, 31)
    later = []
    for d in sorted(versions()[drug]):
        dd = dt.date(int(d[:4]), int(d[4:6]), int(d[6:]))
        if cutoff_d < dd <= cutoff_d + dt.timedelta(days=HORIZON_DAYS):
            later.append(label_texts(drug, d))
    inst = []
    amb = collections.Counter()
    for i, r in enumerate(surv.itertuples()):
        cls = classify(r.pt, later) if later else None
        if cls in ("A1", "A2"):
            amb[cls] += 1
        if cls:
            inst.append({"term": r.pt, "cls": cls, "a": int(r.a), "chi2": float(r.chi2), "default_rank": i + 1, "in_default": r.pt in default_list,
                         "band": srank[r.pt][0], "rank_in_band": srank[r.pt][1], "in_strat": r.pt in strat_list})
    return {"drug": drug, "year": year, "cutoff": date, "label_date": label_date, "n_drug": m["n_drug"], "survivors": len(surv), "capped": len(surv) > 150,
            "band_survivors": sc["band_quotas"] and sc["band_survivors"], "band_quotas": sc["band_quotas"], "list_default": len(default_list), "list_strat": len(strat_list),
            "swapped_in": len(strat_list - default_list), "swapped_out": len(default_list - strat_list), "later_versions_in_horizon": len(later), "ambiguous": dict(amb), "instances": inst}


def target_report(rows):
    out = []
    for drug, date, term in TARGETS:
        r = next((x for x in rows if x["drug"] == drug and x["cutoff"] == date), None)
        if r is None:
            continue
        m = miner.run(drug, date, allow_evaluation=True)
        _, text = triage_step.label_text(drug, date)
        df = pd.DataFrame(m["terms"]).rename(columns={"term": "pt"}).sort_values("pt", kind="stable")
        surv, _ = triage.triage(df, text, min_count=3, cap=BIG)
        surv = surv.sort_values("chi2", ascending=False, kind="stable").reset_index(drop=True)
        kept, c, allsurv = triage_v2.select_stratified(df, text, cap=150)
        row = allsurv[allsurv.pt == term]
        old = surv.index[surv.pt == term]
        out.append({"drug": drug, "cutoff": date, "term": term, "survivors": len(surv), "old_rank": int(old[0]) + 1 if len(old) else None,
                    "old_in": bool(len(old) and old[0] < 150), "band": int(row.band.iloc[0]) if len(row) else None,
                    "rank_in_band": int(row.rank_in_band.iloc[0]) if len(row) else None, "band_survivors": c["band_survivors"], "band_quotas": c["band_quotas"],
                    "strat_in": bool(term in set(kept.pt)), "a": int(row.a.iloc[0]) if len(row) else None})
    return out


def summarise(rows, drugs):
    """Pooled retention per drug and in total: primary (capped cutoffs, benchmark class only), secondary (all cutoffs; and the looser rule crediting A1)."""
    res = {}
    for d in drugs + ["POOLED"]:
        rs = [r for r in rows if d == "POOLED" or r["drug"] == d]
        def tally(rule, capped_only):
            n = kd = ks = 0
            for r in rs:
                if capped_only and not r["capped"]:
                    continue
                for i in r["instances"]:
                    if i["cls"] in rule:
                        n += 1; kd += i["in_default"]; ks += i["in_strat"]
            return {"n": n, "default_kept": kd, "strat_kept": ks, "default_rate": round(kd / n, 4) if n else None, "strat_rate": round(ks / n, 4) if n else None}
        res[d] = {"cutoffs": len(rs), "capped_cutoffs": sum(r["capped"] for r in rs),
                  "primary_capped": tally(("benchmark",), True), "secondary_all": tally(("benchmark",), False), "loose_capped": tally(("benchmark", "A1"), True),
                  "ambiguous_A1": sum(r["ambiguous"].get("A1", 0) for r in rs), "ambiguous_A2": sum(r["ambiguous"].get("A2", 0) for r in rs)}
    return res


if __name__ == "__main__":
    split = sys.argv[1]
    drugs = DEV if split == "dev" else HELD
    path = f"analysis/benchmark_retention_{split}.json"
    if split == "heldout" and os.path.exists(path):
        sys.exit(f"The held-out split was already run ONCE and reported ({path}). It is not run again (protocol, eval_runs.md part 3).")
    rows = []
    t0 = time.time()
    for drug in drugs:
        for y in YEARS:
            r = one_cutoff(drug, y)
            if r:
                rows.append(r)
                n = sum(1 for i in r["instances"] if i["cls"] == "benchmark")
                print(f"{drug:12s} {y} survivors {r['survivors']:3d}{'*' if r['capped'] else ' '} bench {n:3d} kept default {sum(1 for i in r['instances'] if i['cls']=='benchmark' and i['in_default']):3d} "
                      f"strat {sum(1 for i in r['instances'] if i['cls']=='benchmark' and i['in_strat']):3d} | swapped {r['swapped_in']:3d} | A1 {r['ambiguous'].get('A1',0)} A2 {r['ambiguous'].get('A2',0)} ({time.time()-t0:.0f}s)", flush=True)
    summary = summarise(rows, drugs)
    out = {"split": split, "drugs": drugs, "protocol": "eval_runs.md part 3 (committed before any result)", "summary": summary, "targets": target_report(rows),
           "cutoffs": [{k: v for k, v in r.items() if k != "instances"} for r in rows], "instances": [{"drug": r["drug"], "year": r["year"], **i} for r in rows for i in r["instances"]]}
    json.dump(out, open(path, "w"), indent=1, default=str)
    print("\nRETENTION (primary: capped cutoffs, benchmark class only)")
    for d, s in summary.items():
        p = s["primary_capped"]
        print(f"  {d:12s} capped cutoffs {s['capped_cutoffs']:2d} | benchmark terms {p['n']:5d} | locked kept {p['default_kept']:5d} ({p['default_rate']}) | stratified kept {p['strat_kept']:5d} ({p['strat_rate']}) | A1 {s['ambiguous_A1']} A2 {s['ambiguous_A2']}")
    print("\nsaved", path)
