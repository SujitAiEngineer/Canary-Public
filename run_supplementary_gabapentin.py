"""SUPPLEMENTARY, post-result run for gabapentin (owner's written go, 2026-10-03; announced in eval_runs.md BEFORE it ran). NOT part of the locked
headline: the official result stays "gabapentin missed" under the approved reduced schedule. It runs only the four year-ends of the original locked
gabapentin schedule that the reduced schedule skipped: 2007Q4, 2008Q4, 2009Q4, 2010Q4. Same models, prompts, tool schemas, batching, brief rule and spend
ledger (eval_spend.jsonl); the run stops at the first detection (as every positive case does); a hard stop at $2.50 of ADDITIONAL spend; it stops at
once on any error. It reuses run_evaluation.arm_b_run / target_routes unchanged and writes ONLY to eval_out/supplementary_gabapentin.json and
eval_out/eval_supplementary_gabapentin.md (it never touches eval_results.md or state.json). Its ledger rows are labelled "gabapentin-SUPP <quarter>".
Run: PYTHONIOENCODING=utf-8 python -u run_supplementary_gabapentin.py --written-go"""
import argparse, datetime as dt, json, os, subprocess, sys, time

import eval_config, orchestrator
import run_evaluation as rv
from spend import Ledger

CASE = "gabapentin"
QUARTERS = ["2007Q4", "2008Q4", "2009Q4", "2010Q4"]
CAP = 2.50
OUT = "eval_out"


def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()


def describe(run_dir, case, rec):
    """Per target PT: report counts, PRR, chi-squared, Label Checker answer, Skeptic scores (everything the owner asked to see)."""
    load = lambda f: json.load(open(os.path.join(run_dir, f + ".json"), encoding="utf-8"))
    m, lab, sk = load("miner"), {r["term"]: r for r in load("label_checker")["results"]}, {r["term"]: r for r in load("skeptic")["results"]}
    mt = {t["term"]: t for t in m["terms"]}
    out = {}
    for t in case["terms"]:
        row, d = mt.get(t), {"route": rec["targets"][t]["route"]}
        if row:
            d.update(a=row["a"], prr=row["PRR"], chi2=row["chi2"], evans=row["evans"], n_drug=m["n_drug"])
        else:
            d.update(a=0, prr=None, chi2=None, evans=False, n_drug=m["n_drug"])
        if t in lab:
            d["label"] = {"answer": lab[t]["decision"], "date": lab[t]["label_date"], "quote": lab[t]["evidence"]}
        if t in sk:
            s = sk[t]
            d["skeptic"] = {"decision": s["decision"], "winner": s["winner"], "flag": s["flag"], "reason": s["reason"],
                            "scores": {b: s["branches"][b]["score"] for b in s["branches"]}, "winning_reason": s["branches"][s["winner"]]["reason"],
                            "S3": s["evidence"]["items"].get("S3", ""), "R3": s["evidence"]["items"].get("R3", "")}
        out[t] = d
    return out


def run(pipeline=None, ledger=None, out_dir=OUT, log=print, allow_dirty=False):
    case = eval_config.CASES[CASE]
    pipeline = pipeline or orchestrator.run_pipeline
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)eval_out"))
    if dirty and not allow_dirty:
        raise SystemExit("Uncommitted changes to tracked code or config: commit first so the results can name the commit they ran from.")
    start_total = Ledger(orchestrator.EVAL_LEDGER, 9.50).total
    ledger = ledger or Ledger(orchestrator.EVAL_LEDGER, min(9.50, start_total + CAP))   # hard stop: $2.50 of additional spend
    log(f"Supplementary gabapentin: hard limit ${ledger.limit:.4f} (ledger at start ${start_total:.4f} + ${CAP:.2f}); quarters {QUARTERS}")

    def wrapped(case_name, date, **kw):   # ledger rows are labelled gabapentin-SUPP <quarter>; everything else is unchanged
        kw["run_label"] = kw["run_label"].replace(f"{case_name} ", f"{case_name}-SUPP ", 1)
        return pipeline(case_name, date, **kw)

    res = {"meta": {"purpose": "post-result supplementary run; the official locked result is unchanged (gabapentin missed under the approved reduced schedule)",
                    "started": time.strftime("%Y-%m-%d %H:%M:%S"), "code_commit": git("rev-parse", "HEAD"), "eval_lock_commit": git("rev-parse", "eval-lock^{commit}"),
                    "model": eval_config.MODEL, "hard_limit": round(ledger.limit, 6), "ledger_total_at_start": round(start_total, 6), "quarters": QUARTERS},
           "runs": [], "status": "running"}
    path = os.path.join(out_dir, "supplementary_gabapentin.json")
    save = lambda: json.dump(res, open(path, "w", encoding="utf-8"), indent=1, default=str)
    for q in QUARTERS:
        log(f"--- gabapentin-SUPP {q} ---")
        rec = rv.arm_b_run(CASE, case, q, wrapped, ledger, out_dir, log)
        if rec["status"] == "ok":
            rec["detail"] = describe(rec["run_dir"], case, rec)
        res["runs"].append(rec)
        save()
        if rec["status"] == "stopped_spend_limit":
            res["status"] = f"STOPPED by the spend limit at {q}: {rec['message']}"
            break
        if rec["status"] != "ok":
            res["status"] = f"ERROR at {q}: {rec['status']}: {rec['message']}"
            save()
            raise SystemExit(res["status"])
        if rec.get("found"):
            res["status"] = f"detected at {q}; later year-ends not run (positive cases stop at the first detection)"
            break
    else:
        res["status"] = "not detected at any of the four year-ends"
    res["meta"]["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    res["meta"]["ledger_total_at_end"] = round(ledger.total, 6)
    save()
    write_md(res, case, os.path.join(out_dir, "eval_supplementary_gabapentin.md"))
    return res


def ledger_rows():
    rows = [json.loads(l) for l in open(orchestrator.EVAL_LEDGER, encoding="utf-8")]
    return [r for r in rows if r["run"].startswith("gabapentin-SUPP")]


def write_md(res, case, path, arm_a_path=os.path.join(OUT, "arm_a_gabapentin.json")):
    ref_post, ref_label = eval_config.quarter_end(case["posting_quarter"]), dt.date.fromisoformat(case["label_change_date"])
    rows = ledger_rows() if os.path.exists(orchestrator.EVAL_LEDGER) else []
    L = ["# SUPPLEMENTARY: gabapentin, the four year-ends skipped by the reduced schedule", "",
         "> **This is a post-result supplementary run, not part of the locked headline.** The official locked result stays **3 of 4: gabapentin was missed** under the approved reduced "
         "schedule (runs at 2006Q4 and 2011Q2). This run was chosen after seeing that Arm A flags gabapentin from 2007Q2 through 2010Q3; it was announced in `eval_runs.md` before it ran; "
         "whatever it shows is reported here, including another miss. `eval_results.md` is not modified.", "",
         f"- Locked plan lock commit `{res['meta']['eval_lock_commit']}`; code ran from commit `{res['meta']['code_commit']}`; model {res['meta']['model']} (Message Batches, same prompts and schemas).",
         f"- Quarters: {', '.join(res['meta']['quarters'])}. Positive cases stop at the first detection. Hard stop: ${res['meta']['hard_limit'] - res['meta']['ledger_total_at_start']:.2f} of additional spend.",
         f"- **Result: {res['status']}.**", ""]
    found = next((r for r in res["runs"] if r.get("found")), None)
    if found:
        d = dt.date.fromisoformat(found["date"])
        L += [f"**Detected at {found['quarter']}** (first of the four year-ends at which a target term was recommended). Lead time of that detection: "
              f"**{rv.months(d, ref_post):+.1f} months vs the FDA posting ({case['posting_quarter']})** and **{rv.months(d, ref_label):+.1f} months vs the label change ({case['label_change_date']})** "
              "(positive = earlier). Resolution is one year-end run, as in the main evaluation.", ""]
    else:
        L += ["**Not detected at any run made.** No target term was recommended, so there is no lead time (a miss, not zero lead time).", ""]
    L += ["## Arm A context (statistics only, from the free quarterly run already reported)", "", "| Quarter-end | Any target PT meets Evans | Older name (DRUG RASH...) a / PRR / chi-squared | Current name (DRUG REACTION...) a |", "|---|---|---|---|"]
    if os.path.exists(arm_a_path):
        a = {x["quarter"]: x for x in json.load(open(arm_a_path, encoding="utf-8"))}
        for q in ["2006Q4"] + res["meta"]["quarters"] + ["2011Q2"]:
            if q in a:
                t = a[q]["targets"]; o, c = t["DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"], t["DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"]
                f = lambda v: "n/a" if v is None else f"{v:.2f}"
                L.append(f"| {q} | {'**yes**' if a[q]['any_single'] else 'no'} | {o['a']} / {f(o['PRR'])} / {f(o['chi2'])} | {c['a']} |")
    for r in res["runs"]:
        L += ["", f"## gabapentin {r['quarter']} (cutoff {r['date']}): status {r['status']}"]
        if r["status"] != "ok":
            L.append(f"- {r['message']}"); continue
        mine = [x for x in rows if x["run"] == f"gabapentin-SUPP {r['quarter']}"]
        cost = sum(x["cost"] for x in mine)
        L += [f"- Skeptic recommended **{r['n_recommended']}** of **{r['n_examined']}** examined terms. Cost **${cost:.4f}** ({len(mine)} calls).",
              "- Batch IDs: " + (", ".join(f"`{b}` ({s}, {n} requests)" for b, s, n in sorted({(x['batch'], x['stage'], sum(1 for y in mine if y['batch'] == x['batch'])) for x in mine}, key=lambda z: z[1]))) + "", ""]
        for t, d in r["detail"].items():
            kind = "older name" if "RASH" in t else "current name"
            L.append(f"- **{t}** ({kind}): **{d['route']}**. Reports a={d['a']} of {d['n_drug']:,} gabapentin reports; PRR " + ("n/a" if d['prr'] is None else f"{d['prr']:.2f}") +
                     ", chi-squared " + ("n/a" if d['chi2'] is None else f"{d['chi2']:.1f}") + f"; Evans {'met' if d['evans'] else 'not met'}.")
            if "label" in d:
                L.append(f"  - Label Checker: **{d['label']['answer']}** (label of {d['label']['date']})" + (f", quote \"{d['label']['quote'][:200]}\"" if d['label']['quote'] else "") + ".")
            if "skeptic" in d:
                s = d["skeptic"]
                L += [f"  - Skeptic: **{s['decision'].upper()}**, winner **{s['winner']}**; scores real effect {s['scores']['real_effect']:.2f}, reporting spike {s['scores']['reporting_spike']:.2f}, confounding {s['scores']['confounding']:.2f}.",
                      f"  - Winning reason: {s['winning_reason']}", f"  - {s['R3']} {s['S3']}"]
    L += ["", "## Caveats (same as for the main results)", "",
          "- **Tiny report counts.** The gabapentin target term is rare: a single-digit or low-double-digit number of reports, so any flag sits close to the Evans minimum (3 reports, PRR 2, chi-squared 4).",
          "- **Flags not sustained.** Arm A flags this term in only 16 of 90 quarters; a flag at one year-end says little about the next.",
          "- **This run was added after seeing the miss**, so a detection here is not a pre-planned result and does not change the locked headline (3 of 4).",
          "- **The Skeptic recommends most terms** it examines (about 90% in every run), so detection mostly tracks whether the target is flagged and survives triage, not the Skeptic's discrimination.",
          "- Arm B lead times are at the resolution of one year-end run.", ""]
    open(path, "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--written-go", action="store_true", help="the owner's written go (this run spends money)")
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args()
    if not a.written_go:
        raise SystemExit("This run spends money. It runs only with --written-go.")
    r = run(allow_dirty=a.allow_dirty)
    print("\nstatus:", r["status"], "\nresults: eval_out/eval_supplementary_gabapentin.md")
