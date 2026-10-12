"""POST-RESULT ILLUSTRATIVE CONFIRMATION (paid, capped at $1.00 of additional spend; announced in eval_runs.md BEFORE it ran, commit 01829fc). NOT part of the locked
headline, which stays 3 of 4. EWT was designed after seeing gabapentin, so this is tuned evidence. Runs gabapentin 2008Q4 and 2009Q4 with the EWT triage
(triage="ewt") and then the same Label Checker, Skeptic and Writer, model, prompts, batching and spend ledger (eval_spend.jsonl, rows "gabapentin-EWT <quarter>") as
the locked evaluation. Both cutoffs are run even if the first detects. Writes ONLY to ewt_confirmation/ (never eval_out/, eval_results.md or state.json).
Run: PYTHONIOENCODING=utf-8 python -u run_ewt_confirmation_gabapentin.py --written-go"""
import argparse, json, os, re, subprocess, time

import eval_config, orchestrator
import run_evaluation as rv
import run_supplementary_gabapentin as sup
from spend import Ledger

CASE = "gabapentin"
QUARTERS = ["2008Q4", "2009Q4"]
CAP = 1.00
OUT = "ewt_confirmation"


def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()


def excerpt(brief, term, n=700):
    """The part of the brief that mentions the target term (the block after the nearest heading), or None if the term got no brief."""
    for block in re.split(r"\n(?=#)", brief or ""):
        if term.lower() in block.lower():
            return block.strip()[:n]
    return None


def run(pipeline=None, ledger=None, out_dir=OUT, log=print, allow_dirty=False):
    case = eval_config.CASES[CASE]
    pipeline = pipeline or orchestrator.run_pipeline
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)eval_out", ":(exclude)ewt_confirmation"))
    if dirty and not allow_dirty:
        raise SystemExit("Uncommitted changes to tracked code or config: commit first so the results can name the commit they ran from.")
    os.makedirs(out_dir, exist_ok=True)
    start_total = Ledger(orchestrator.EVAL_LEDGER, 9.50).total
    ledger = ledger or Ledger(orchestrator.EVAL_LEDGER, min(9.50, start_total + CAP))   # hard stop: $1.00 of additional spend
    log(f"EWT confirmation: hard limit ${ledger.limit:.4f} (ledger at start ${start_total:.4f} + ${CAP:.2f}); quarters {QUARTERS}")

    def wrapped(case_name, date, **kw):   # EWT triage; ledger rows are labelled gabapentin-EWT <quarter>; everything else is unchanged
        kw["run_label"] = kw["run_label"].replace(f"{case_name} ", f"{case_name}-EWT ", 1)
        return pipeline(case_name, date, triage="ewt", **kw)

    res = {"meta": {"purpose": "post-result illustrative confirmation under EWT triage (tuned evidence); the official locked result is unchanged (3 of 4)",
                    "started": time.strftime("%Y-%m-%d %H:%M:%S"), "code_commit": git("rev-parse", "HEAD"), "eval_lock_commit": git("rev-parse", "eval-lock^{commit}"),
                    "announced_in_commit": "01829fc", "model": eval_config.MODEL, "hard_limit": round(ledger.limit, 6), "ledger_total_at_start": round(start_total, 6),
                    "quarters": QUARTERS}, "runs": [], "status": "running"}
    path = os.path.join(out_dir, "ewt_confirmation_gabapentin.json")
    save = lambda: json.dump(res, open(path, "w", encoding="utf-8"), indent=1, default=str)
    for q in QUARTERS:
        log(f"--- gabapentin-EWT {q} ---")
        rec = rv.arm_b_run(CASE, case, q, wrapped, ledger, out_dir, log)
        if rec["status"] == "ok":
            rec["detail"] = sup.describe(rec["run_dir"], case, rec)
            brief = open(os.path.join(rec["run_dir"], "brief.md"), encoding="utf-8").read()
            for t in case["terms"]:
                rec["detail"][t]["brief_excerpt"] = excerpt(brief, t)
            tr = json.load(open(os.path.join(rec["run_dir"], "triage.json"), encoding="utf-8"))
            rec["triage"] = {"ewt": tr.get("ewt"), "counts": tr.get("counts"), "list_size": len(tr["terms"])}
        res["runs"].append(rec)
        save()
        if rec["status"] == "stopped_spend_limit":
            res["status"] = f"STOPPED by the spend limit at {q}: {rec['message']}"
            break
        if rec["status"] != "ok":
            res["status"] = f"ERROR at {q}: {rec['status']}: {rec['message']}"
            save()
            raise SystemExit(res["status"])
    else:
        res["status"] = "both cutoffs run: " + "; ".join(f"{r['quarter']} {'DETECTED' if r.get('found') else 'not detected'}" for r in res["runs"])
    res["meta"]["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    res["meta"]["ledger_total_at_end"] = round(ledger.total, 6)
    save()
    write_md(res, case, os.path.join(out_dir, "ewt_confirmation_gabapentin.md"))
    return res


def write_md(res, case, path):
    rows = [json.loads(l) for l in open(orchestrator.EVAL_LEDGER, encoding="utf-8")]
    L = ["# Gabapentin under EWT triage: post-result illustrative confirmation (2008Q4 and 2009Q4)", "",
         "> **Post-result and illustrative. EWT was designed after seeing gabapentin, so this is tuned evidence. It does not change the official locked result, which stays 3 of 4 "
         "(gabapentin missed).** Announced in `eval_runs.md` before it ran (commit `" + res["meta"]["announced_in_commit"] + "`). Every outcome is reported, including a miss.", "",
         f"- Code commit `{res['meta']['code_commit']}`; eval-lock `{res['meta']['eval_lock_commit']}`; model {res['meta']['model']} (Message Batches); hard stop ${res['meta']['hard_limit'] - res['meta']['ledger_total_at_start']:.2f} of additional spend.",
         f"- **Result: {res['status']}.**", ""]
    for r in res["runs"]:
        L += [f"## gabapentin {r['quarter']} (cutoff {r['date']}): status {r['status']}"]
        if r["status"] != "ok":
            L += [f"- {r['message']}", ""]
            continue
        mine = [x for x in rows if x["run"] == f"gabapentin-EWT {r['quarter']}"]
        cost = sum(x["cost"] for x in mine)
        batches = sorted({(x["batch"], x["stage"]) for x in mine if x.get("batch")}, key=lambda z: z[1])
        L += [f"- EWT triage: {r['triage']['counts']}; list size {r['triage']['list_size']}.",
              f"- Skeptic recommended **{r['n_recommended']}** of **{r['n_examined']}** examined terms. **Cost ${cost:.4f}** ({len(mine)} ledger rows).",
              "- Batch IDs: " + (", ".join(f"`{b}` ({s})" for b, s in batches) or "none (all calls served from the on-disk cache)"), ""]
        for t, d in r["detail"].items():
            kind = "older name" if "RASH" in t else "current name"
            L.append(f"- **{t}** ({kind}): **{d['route']}**. a={d['a']} of {d['n_drug']:,} reports; PRR " + ("n/a" if d["prr"] is None else f"{d['prr']:.2f}") +
                     ", chi-squared " + ("n/a" if d["chi2"] is None else f"{d['chi2']:.1f}") + f"; Evans {'met' if d['evans'] else 'not met'}.")
            if "label" in d:
                L.append(f"  - Label Checker: **{d['label']['answer']}** (label of {d['label']['date']})" + (f", quote \"{d['label']['quote'][:200]}\"" if d["label"]["quote"] else "") + ".")
            if "skeptic" in d:
                s = d["skeptic"]
                L += [f"  - Skeptic: **{s['decision'].upper()}**, winner **{s['winner']}**; scores " + ", ".join(f"{b} {v:.2f}" for b, v in s["scores"].items()) + f"; flag: {s['flag']}.",
                      f"  - Winning reason: {s['winning_reason']}"]
            if d.get("brief_excerpt"):
                L += ["  - Brief excerpt:", "", "    > " + d["brief_excerpt"].replace("\n", "\n    > "), ""]
            else:
                L.append("  - No brief was written for this term.")
        L.append("")
    L += ["## Caveats", "",
          "- **Tuned evidence**: gabapentin's numbers were looked at before EWT was designed; a detection here is an illustration, not a validation.",
          "- **Tiny report counts**: the target term has a single-digit to low-double-digit number of reports.",
          "- **The Skeptic recommends most terms it examines** (about 90% in every locked run), so a detection mostly tracks whether the term survives triage.",
          "- The headline statistic for EWT itself is the bootstrap 95% CI for the retention difference on the fresh drugs (+10.8 to +21.6 points); McNemar's p-value is overstated because terms repeat across cutoffs.", ""]
    open(path, "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--written-go", action="store_true", help="the owner's written go (this run spends money, capped at $1.00)")
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args()
    if not a.written_go:
        raise SystemExit("This run spends money. It runs only with --written-go.")
    r = run(allow_dirty=a.allow_dirty)
    print("\nstatus:", r["status"], "\nresults: ewt_confirmation/ewt_confirmation_gabapentin.md")
