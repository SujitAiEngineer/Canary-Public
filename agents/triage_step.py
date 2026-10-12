"""Triage step (Build Step 3): wires the locked triage.py between the Miner and the Label Checker. No behaviour change.
Minimum count 3, exact first pass against the full text of the brand label in force at the cutoff, top 150 by chi-squared.
NOTE: triage.CAP in the locked file is 25, but EVALUATION_PLAN.md section 4 locks the cap at 150, so the cap is passed
explicitly here (triage() takes it as a parameter) and the locked file is left untouched.
Run: PYTHONIOENCODING=utf-8 python -u agents/triage_step.py atorvastatin 2011-12-31
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import triage as locked_triage   # the locked module (the run() argument named triage would shadow it)
from rag_index import RAG_DIR

CAP = 150  # EVALUATION_PLAN.md section 4
MIN_COUNT = locked_triage.MIN_COUNT  # 3
_rows = []


def label_in_force(drug, cutoff):
    """(label_date YYYYMMDD, file) of the brand label version in force at the cutoff, from the RAG chunk store."""
    if not _rows:
        _rows.extend(json.loads(l) for l in open(f"{RAG_DIR}/chunks.jsonl", encoding="utf-8"))
    cut = cutoff.replace("-", "")
    versions = sorted({(r["label_date"], r["file"]) for r in _rows if r["drug"] == drug and r["label_date"] <= cut})
    return versions[-1] if versions else (None, None)


def label_text(drug, cutoff):
    """Full text (all sections) of the label version in force. Uses the pdftotext file cached by rag_index.raw_text, else the chunks."""
    date, file = label_in_force(drug, cutoff)
    if date is None:
        return None, None
    raw = os.path.join("data/drugsatfda", file + ".raw.txt")
    if os.path.exists(raw):
        return date, open(raw, encoding="utf-8", errors="replace").read()
    return date, " ".join(r["text"] for r in _rows if r["drug"] == drug and r["label_date"] == date)


def run(miner_out, cap=CAP, v2=False, ime=None, rank_by=None, stratified=False, prune=None, ewt=False, triage=None):
    """v2: OPT-IN, default False (the locked triage). True = triage v2 as specified (term groups + 20 reserved serious slots, reserve first);
    "beyond" = the variant where the reserved slots go to serious terms outside the plain top cap-20. Post-result exploratory only."""
    """miner_out: the dict returned by agents.miner.run. Returns the triage step output."""
    if triage not in (None, "locked", "ewt"):
        raise ValueError(f"unknown triage {triage!r}: use 'locked' or 'ewt'")
    if triage == "ewt":   # explicit choice (the product default is set in orchestrator.run_pipeline); "locked" = the locked chi-squared triage with every opt-in off
        ewt = True
    elif triage == "locked":
        v2 = stratified = ewt = False
        rank_by = prune = None
    out = {"agent": "triage", "drug": miner_out["drug"], "date": miner_out["date"]}
    if miner_out.get("status") != "ok":
        return {**out, "status": "skipped", "message": "Miner returned no data."}
    date, text = label_text(miner_out["drug"], miner_out["date"])
    if text is None:
        return {**out, "status": "no_label", "message": f"No brand label version in the index for {miner_out['drug']} on or before {miner_out['date']}."}
    df = pd.DataFrame(miner_out["terms"]).rename(columns={"term": "pt"})
    df = df.sort_values("pt", kind="stable")  # the locked triage() ranks by chi-squared only; an alphabetical input order makes exact ties deterministic
    if ewt:  # OPT-IN, post-result exploratory part 5: Evidence-Weighted Triage (triage_ewt.py: equal-weight average rank of five evidence components); default False = the locked triage
        import triage_ewt
        edf, feats = triage_ewt.load(miner_out["drug"], miner_out["date"])
        if edf is None:
            return {**out, "status": "skipped", "message": f"EWT needs the Miner store and the extension store: {feats}."}
        kept, counts = triage_ewt.run_ewt(edf, feats, text, cap=cap)
        kept = kept.assign(yearly=[{} for _ in range(len(kept))])
    elif prune:  # OPT-IN, post-result exploratory part 4: "P", "PN", "PL" or "PLN" (triage_prune.py: prune, novelty quota, label-aware seating); default None = the locked triage
        import triage_prune
        pdf, feats = triage_prune.load(miner_out["drug"], miner_out["date"])
        if pdf is None:
            return {**out, "status": "skipped", "message": f"Prune variant {prune} needs the Miner store: {feats}."}
        kept, counts = triage_prune.run_variant(pdf, feats, text, miner_out["drug"], date, prune, cap=cap)
        kept = kept.assign(yearly=[{} for _ in range(len(kept))])
    elif stratified:  # OPT-IN, post-result exploratory part 3: stratified triage (report-count bands with quotas), triage_v2.select_stratified; default False = the locked triage
        import triage_v2
        kept, counts, _ = triage_v2.select_stratified(df, text, min_count=MIN_COUNT, cap=cap)
    elif rank_by:  # OPT-IN, post-result exploratory part 2: rank by prr_lb, ic025 or best_of instead of chi-squared (triage_v2.select_ranked); default None = the locked triage
        import triage_v2
        kept, counts, _ = triage_v2.select_ranked(df, text, rank_by, min_count=MIN_COUNT, cap=cap)
    elif v2:  # OPT-IN, post-result exploratory (triage_v2.py): term groups were merged by the Miner agent; up to 20 slots reserved for serious (IME) terms
        import triage_v2
        kept, counts = triage_v2.select(df, text, min_count=MIN_COUNT, cap=cap, ime=ime, mode="reserve_beyond" if v2 == "beyond" else "reserve_first")
    else:
        kept, counts = locked_triage.triage(df, text, min_count=MIN_COUNT, cap=cap)
    print(f"  triage {miner_out['drug']} {miner_out['date']}: label {date}; counts {counts}", flush=True)
    return {**out, "status": "ok", "label_date": date, "counts": counts, "cap": cap, "min_count": MIN_COUNT,
            "triage_v2": (v2 if v2 == "beyond" else bool(v2)), "rank_by": rank_by, "stratified": bool(stratified), "prune": prune, "ewt": bool(ewt),
            "terms": [{k: (None if (not isinstance(v, (list, tuple, dict)) and v != v) else v) for k, v in r.items() if k != "yearly"} | {"term": r["pt"]}
                      for r in kept.drop(columns="yearly").to_dict("records")]}


if __name__ == "__main__":
    from agents import miner
    m = miner.run(sys.argv[1], sys.argv[2])
    r = run(m)
    print(json.dumps({k: v for k, v in r.items() if k != "terms"}, indent=1))
    for t in r["terms"][:5]:
        print(t["term"], t["a"], round(t["chi2"], 1))
