"""Step 10 runner: the locked evaluation, exactly as written in EVALUATION_PLAN.md (with the three amendments approved on 2026-10-03 and recorded in
eval_runs.md: spend limit, briefs for the target terms plus the top 10, Message Batches for cost only).

  Arm A (statistics alone): the Miner at EVERY quarter-end, drug-level counts, the Evans rule on each target PT individually (primary) and on the union
         of the case's term list (secondary). No LLM, no cost.
  Arm B (full Canary): triage, Label Checker, Skeptic and brief at the scheduled cutoffs of eval_config.arm_b_quarters (positive cases) or
         NEGATIVE_RUNS (rivaroxaban), through orchestrator.run_pipeline(evaluation_mode=True). Run order: eval_config.RUN_ORDER.
  Stops: a positive case stops at its first run where a target PT is recommended; any SpendLimit stops everything, writes eval_out/stop_record.json
         and reports the unfinished cases as incomplete. It does not continue without the owner's written approval of a higher limit.

Safety: Arm B spends money, so it only runs with --written-go. Without it, only Arm A (free) can run (--arm-a-only).
Outputs (eval_out/): state.json (resumable), arm_a_<case>.json, runs/ (one folder per Arm B run), stop_record.json, eval_results.md.
Run: PYTHONIOENCODING=utf-8 python -u run_evaluation.py --arm-a-only      |      ... --written-go [--budget 9.50] [--resume]
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, time

import duckdb
import pandas as pd

import canary_config, eval_config, orchestrator, triage
import miner as core_miner
from agents import triage_step
from canary_utils import chi_squared, prr
from spend import Ledger

OUT = "eval_out"
BUDGET_DEFAULT = 9.50   # the owner's working budget: the whole Anthropic balance ($10.00) minus the rehearsal and a margin. Never above eval_config.SPEND_LIMIT_USD.
MONTH = 30.4375
COST_PER_RUN = 0.54     # measured Arm B cost per full run at the batch price, no cache: Label Checker 0.22 + Skeptic 0.30 + about 12 briefs 0.025 (eval_runs.md, 2026-10-03)


# ------------------------------------------------------------------------------------------------------------------------------ helpers
def evans(a, b, c, d):
    if a < 3 or a + b == 0 or c + d == 0 or c == 0:
        return False
    p, x = prr(a, b, c, d), chi_squared(a, b, c, d)
    return bool(p >= 2 and x >= 4)


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def months(frm, to):
    """Positive when `frm` (the detection date) is earlier than `to` (the reference date)."""
    return round((to - frm).days / MONTH, 1)


def qlabel(qi):
    return f"{2004 + qi // 4}Q{qi % 4 + 1}"


def last_data_quarter(con):
    return qlabel(con.execute("SELECT max(qidx) FROM cases").fetchone()[0])


# ------------------------------------------------------------------------------------------------------------------------------ Arm A
def arm_a(name, case, con, last_quarter=None, first_quarter="2004Q1", log=print):
    """Drug-level Evans test at every quarter-end. Each target PT on its own (primary) and the union of the list (secondary)."""
    terms = case["terms"]
    last = last_quarter or last_data_quarter(con)
    rows = []
    in_list = ", ".join("'" + t.replace("'", "''") + "'" for t in terms)
    t0 = time.time()
    for q in eval_config.quarters(first_quarter, last):
        df, N, n = core_miner.mine(con, case["match"], eval_config.qidx(q), serious_only=False, min_a=1, exclude=case["exclude"])
        per = {}
        for t in terms:
            r = df[df.pt == t]
            if r.empty:
                per[t] = {"a": 0, "b": int(n), "c": None, "d": None, "PRR": None, "chi2": None, "evans": False}
            else:
                r = r.iloc[0]
                per[t] = {"a": int(r.a), "b": int(r.b), "c": int(r.c), "d": int(r.d), "PRR": None if r.PRR != r.PRR else float(r.PRR),
                          "chi2": None if r.chi2 != r.chi2 else float(r.chi2), "evans": bool(r.evans)}
        a_u = con.execute(f"SELECT count(DISTINCT rid) FROM reac WHERE pt IN ({in_list}) AND rid IN (SELECT rid FROM drug_rids)").fetchone()[0]
        any_u = con.execute(f"SELECT count(DISTINCT rid) FROM reac WHERE pt IN ({in_list}) AND rid IN (SELECT rid FROM latest)").fetchone()[0]
        b_u, c_u = n - a_u, any_u - a_u
        d_u = (N - n) - c_u
        union = {"a": int(a_u), "b": int(b_u), "c": int(c_u), "d": int(d_u), "evans": evans(a_u, b_u, c_u, d_u)}
        rows.append({"quarter": q, "n_drug": int(n), "n_total": int(N), "n_evans": int(df.evans.sum()), "targets": per, "union": union,
                     "any_single": any(v["evans"] for v in per.values())})
        if len(rows) % 10 == 0:
            log(f"  arm A {name}: {q} done ({len(rows)} quarters, {time.time() - t0:.0f}s)")
    return rows


def arm_a_summary(case, rows):
    """First flag (primary), the quarter after which the flag stays on up to the last cutoff before the label change, and the union's first flag."""
    flags = [r["quarter"] for r in rows if r["any_single"]]
    first = flags[0] if flags else None
    union_first = next((r["quarter"] for r in rows if r["union"]["evans"]), None)
    last_cut = eval_config.last_cutoff_before(case["label_change_date"]) if case.get("label_change_date") else None
    sustained = None
    if first and last_cut:
        seq = [r for r in rows if r["quarter"] <= last_cut]
        k = len(seq)
        while k > 0 and seq[k - 1]["any_single"]:
            k -= 1
        sustained = seq[k]["quarter"] if k < len(seq) else None
    return {"first_flag": first, "sustained_from": sustained, "union_first_flag": union_first, "last_cutoff_before_label": last_cut}


# ------------------------------------------------------------------------------------------------------------------------------ Arm B
def reduced_quarters(case, first_flag_quarter):
    """The reduced Arm B schedule (eval_config.ARM_B_SCHEDULE = "reduced"): the first year-end (Q4) on or after the first Arm A flag, plus the last
    quarter-end strictly before the label change. A positive case still stops at its first detection."""
    last = eval_config.last_cutoff_before(case["label_change_date"])
    first_ye = next((f"{y}Q4" for y in range(int(first_flag_quarter[:4]), int(last[:4]) + 1)
                     if eval_config.quarter_end(f"{y}Q4") >= eval_config.quarter_end(first_flag_quarter) and f"{y}Q4" <= last), None)
    return list(dict.fromkeys([q for q in (first_ye, last) if q]))


def schedule_for(name, case, first_flag, last_cut):
    if case["role"] == "negative":
        return list(eval_config.NEGATIVE_RUNS)
    if first_flag is None or (last_cut and first_flag > last_cut):
        return []   # never flagged before the label change: a miss, and no LLM spend
    if eval_config.ARM_B_SCHEDULE == "reduced":
        return reduced_quarters(case, first_flag)
    return eval_config.arm_b_quarters(name, first_flag)


def load_json(d, name):
    return json.load(open(os.path.join(d, name + ".json"), encoding="utf-8"))


def target_routes(run_dir, case, drug, date):
    """For every target PT of one Arm B run: where it went (EVALUATION_PLAN 5c): no flag; removed by triage (exact on-label match, or below the
    cap); Label Checker 'yes'; Skeptic dismissal with its reason; or recommended."""
    m = load_json(run_dir, "miner")
    tr = load_json(run_dir, "triage")
    out = {}
    terms_m = {t["term"]: t for t in m.get("terms", [])}
    kept = {t["term"] for t in tr.get("terms", [])}
    lab = {r["term"]: r for r in load_json(run_dir, "label_checker").get("results", [])} if os.path.exists(os.path.join(run_dir, "label_checker.json")) else {}
    sk = {r["term"]: r for r in load_json(run_dir, "skeptic").get("results", [])} if os.path.exists(os.path.join(run_dir, "skeptic.json")) else {}
    for t in case["terms"]:
        row = terms_m.get(t)
        if row is None or not row["evans"] or row["a"] < 3:
            out[t] = {"route": "no statistical flag", "a": 0 if row is None else row["a"], "PRR": None if row is None else row["PRR"]}
            continue
        base = {"a": row["a"], "PRR": row["PRR"], "chi2": row["chi2"]}
        if t not in kept:
            ldate, text = triage_step.label_text(drug, date)
            if text is not None and triage.on_label_exact(t, triage.fold(text)):
                out[t] = {**base, "route": "removed by triage: exact match on the label in force"}
            else:
                df = pd.DataFrame(m["terms"]).rename(columns={"term": "pt"}).sort_values("pt", kind="stable")
                surv, _ = triage.triage(df, text, min_count=triage.MIN_COUNT, cap=10 ** 6)
                rank = [x for x in surv.sort_values("chi2", ascending=False).pt].index(t) + 1 if t in set(surv.pt) else None
                out[t] = {**base, "route": f"removed by triage: below the cap of {tr['cap']} (rank {rank} of {len(surv)} by chi-squared)"}
            continue
        l = lab.get(t)
        if l and l["route"] == "dropped_on_label":
            out[t] = {**base, "route": "Label Checker yes (already on the label)", "label_date": l["label_date"], "label_quote": l["evidence"]}
            continue
        s = sk.get(t)
        if s is None:
            out[t] = {**base, "route": "reached the Skeptic but no result (incomplete run)"}
        elif s["decision"] == "dismissed":
            out[t] = {**base, "route": f"Skeptic dismissal ({s['winner']})", "reason": s["reason"], "cited": [c["id"] for c in s["cited"]]}
        else:
            out[t] = {**base, "route": "recommended" + (f" ({s['flag']})" if s["flag"] else ""), "winner": s["winner"],
                      "label_decision": l["decision"] if l else None, "label_quote": l["evidence"] if l else ""}
    return out


def arm_b_run(case_name, case, q, pipeline, ledger, out_dir, log=print):
    date = eval_config.quarter_end(q).isoformat()
    res = pipeline(case_name, date, evaluation_mode=True, run_label=f"{case_name} {q}", runs_dir=os.path.join(out_dir, "runs"), ledger=ledger)
    rec = {"quarter": q, "date": date, "status": res["status"], "run_dir": res.get("run_dir"), "run_spend": res.get("run_spend"), "message": res.get("message", "")}
    if res["status"] == "ok":
        sk = load_json(res["run_dir"], "skeptic") if os.path.exists(os.path.join(res["run_dir"], "skeptic.json")) else {}
        rec["targets"] = target_routes(res["run_dir"], case, case_name, date)
        rec["n_recommended"] = sk.get("n_recommended", 0)
        rec["n_examined"] = sk.get("n_evaluated", 0)
        rec["found"] = any(v["route"].startswith("recommended") for v in rec["targets"].values())
    elif res["status"] in ("not_enough_data", "no_label"):
        rec["targets"] = {t: {"route": "no statistical flag" if res["status"] == "not_enough_data" else "no label in the index"} for t in case["terms"]}
        rec["found"] = False
    return rec


# ------------------------------------------------------------------------------------------------------------------------------ the evaluation
def run(cases=None, order=None, pipeline=None, con=None, out_dir=OUT, budget=BUDGET_DEFAULT, written_go=False, arm_a_only=False, resume=False, log=print,
        allow_dirty=False, ledger=None, resume_cap=None):
    cases = cases or eval_config.CASES
    order = order or eval_config.RUN_ORDER
    pipeline = pipeline or orchestrator.run_pipeline
    budget = min(budget, eval_config.SPEND_LIMIT_USD)
    if not (arm_a_only or written_go):
        raise SystemExit("Arm B spends money. It runs only with --written-go (the owner's written go). Use --arm-a-only for the free statistics arm.")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)eval_out"))  # code and config only: the runner's own output folder does not count
    if dirty and not allow_dirty:
        raise SystemExit("The working tree has uncommitted changes to tracked files. Commit first, so the results can name the commit they ran from (or --allow-dirty).")
    os.makedirs(out_dir, exist_ok=True)
    state_path = os.path.join(out_dir, "state.json")
    state = json.load(open(state_path, encoding="utf-8")) if resume and os.path.exists(state_path) else {}
    state.setdefault("meta", {})
    state["meta"].update({"started": state["meta"].get("started") or time.strftime("%Y-%m-%d %H:%M:%S"), "eval_lock_commit": git("rev-parse", "eval-lock^{commit}"),
                          "code_commit": git("rev-parse", "HEAD"), "working_tree_dirty": dirty, "model": eval_config.MODEL, "budget": budget,
                          "spend_limit_in_config": eval_config.SPEND_LIMIT_USD, "arm_a_only": arm_a_only})
    if resume_cap is not None:
        state["meta"].setdefault("resume_caps", []).append({"cap": resume_cap, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    state.setdefault("cases", {})
    save = lambda: json.dump(state, open(state_path, "w", encoding="utf-8"), indent=1, default=str)
    con = con or duckdb.connect(core_miner.DB, read_only=True)
    start_total = None
    if arm_a_only:
        ledger = None
    else:
        limit = budget
        if resume_cap is not None:   # a tighter stop for THIS invocation: the ledger total at its start plus resume_cap, never above the budget
            start_total = Ledger(orchestrator.EVAL_LEDGER, budget).total
            limit = min(budget, start_total + resume_cap)
            log(f"This invocation may spend at most ${resume_cap:.2f}: hard limit ${limit:.4f} (ledger total at start ${start_total:.4f}; overall budget ${budget:.2f}).")
        ledger = ledger or Ledger(orchestrator.EVAL_LEDGER, limit)
    stopped = None

    # ---- Phase 1: Arm A for every case (free). It also fixes the Arm B schedule of each case before any money is spent.
    schedules = {}
    for name in order:
        case = cases[name]
        cs = state["cases"].setdefault(name, {"role": case["role"], "status": "not started"})
        arm_path = os.path.join(out_dir, f"arm_a_{name}.json")
        if os.path.exists(arm_path) and resume:
            rows = json.load(open(arm_path, encoding="utf-8"))
        else:
            log(f"\n=== {name}: Arm A (quarterly, no LLM) ===")
            rows = arm_a(name, case, con, log=log)
            json.dump(rows, open(arm_path, "w", encoding="utf-8"))
        cs["arm_a"] = arm_a_summary(case, rows)
        last_cut = cs["arm_a"]["last_cutoff_before_label"]
        by_q = {r["quarter"]: r for r in rows}
        if last_cut and last_cut in by_q:
            cs["arm_a"]["alert_burden_last_cutoff"] = by_q[last_cut]["n_evans"]
        log(f"  Arm A {name}: first flag {cs['arm_a']['first_flag']}, sustained from {cs['arm_a']['sustained_from']}, union first {cs['arm_a']['union_first_flag']}")
        schedules[name] = schedule_for(name, case, cs["arm_a"]["first_flag"], last_cut)
        save()
    if arm_a_only:
        for name in order:
            state["cases"][name]["status"] = "Arm A only"
            state["cases"][name]["planned_arm_b"] = schedules[name]
        state["meta"]["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save()
        write_results(state, cases, os.path.join(out_dir, "eval_results.md"))
        runs_max = sum(len(s) for s in schedules.values())
        log(f"\nArm A done. Arm B schedule ({eval_config.ARM_B_SCHEDULE}): " + "; ".join(f"{n}: {len(s)} run(s) {s}" for n, s in schedules.items()) +
            f"\nUpper bound if no positive case stops early: {runs_max} runs, about ${runs_max * COST_PER_RUN:.2f} at ${COST_PER_RUN} per run (budget ${budget:.2f}).")
        return state
    runs_max = sum(len(s) for s in schedules.values())
    log(f"\nArm B plan (upper bound): {runs_max} runs, about ${runs_max * COST_PER_RUN:.2f} at ${COST_PER_RUN} per run; budget ${budget:.2f}.")

    # ---- Phase 2: Arm B, in the fixed run order
    for name in order:
        case = cases[name]
        cs = state["cases"][name]
        if stopped:
            cs["status"] = "not started: spend limit reached" if cs["status"] == "not started" else cs["status"]
            continue
        schedule = schedules[name]
        cs["arm_b"] = cs.get("arm_b") or {"schedule": schedule, "runs": []}
        cs["arm_b"]["schedule"] = schedule
        done = {r["quarter"] for r in cs["arm_b"]["runs"] if r["status"] == "ok"}
        log(f"=== {name}: Arm B schedule {schedule} ===")
        cs["status"] = "running"
        save()
        for q in schedule:
            if q in done:
                continue
            log(f"--- {name} {q} ---")
            rec = arm_b_run(name, case, q, pipeline, ledger, out_dir, log)
            cs["arm_b"]["runs"] = [r for r in cs["arm_b"]["runs"] if r["quarter"] != q] + [rec]
            save()
            if rec["status"] == "stopped_spend_limit":
                stopped = {"case": name, "run_quarter": q, "message": rec["message"], "total_spent": round(ledger.total, 4), "limit": ledger.limit,
                           "stage": next((e["step"] for e in reversed(json.load(open(os.path.join(rec["run_dir"], "run.json")))["steps"]) if e["status"] != "start"), None)
                           if rec.get("run_dir") else None, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
                state["stop"] = stopped
                json.dump(stopped, open(os.path.join(out_dir, "stop_record.json"), "w", encoding="utf-8"), indent=1)
                cs["status"] = f"INCOMPLETE: spend limit stopped the run at {q}"
                break
            if rec["status"] not in ("ok", "not_enough_data", "no_label"):
                cs["status"] = f"INCOMPLETE: run {q} ended with status {rec['status']}: {rec['message']}"
                save()
                raise SystemExit(f"{name} {q}: {rec['status']}: {rec['message']} (not retried; see eval_out/state.json)")
            if case["role"] == "positive" and rec.get("found"):
                break  # detected: later runs are not made
        else:
            cs["status"] = "complete"
        if cs["status"] == "running":
            cs["status"] = "complete"
        save()
    state["meta"]["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state["meta"]["ledger_total"] = None if ledger is None else round(ledger.total, 4)
    save()
    write_results(state, cases, os.path.join(out_dir, "eval_results.md"), ledger_path=orchestrator.EVAL_LEDGER if not arm_a_only else None)
    return state


# ------------------------------------------------------------------------------------------------------------------------------ the report
def case_report(name, case, cs, spend):
    """The numbers of one case for the summary table."""
    a = cs.get("arm_a", {})
    ref_post = eval_config.quarter_end(case["posting_quarter"])
    ref_label = dt.date.fromisoformat(case["label_change_date"]) if case.get("label_change_date") else None
    b = cs.get("arm_b", {"schedule": [], "runs": []})
    out = {"role": case["role"], "status": cs.get("status"), "spend": spend}
    if a.get("first_flag"):
        d_a = eval_config.quarter_end(a["first_flag"])
        out["armA_detect"] = a["first_flag"]
        out["armA_lead_posting"] = months(d_a, ref_post)
        out["armA_lead_label"] = months(d_a, ref_label) if ref_label else None
        rounded = next((q for q in b["schedule"] if eval_config.quarter_end(q) >= d_a), None)
        out["armA_rounded"] = rounded
        if rounded:
            d_r = eval_config.quarter_end(rounded)
            out["armA_rounded_lead_posting"] = months(d_r, ref_post)
            out["armA_rounded_lead_label"] = months(d_r, ref_label) if ref_label else None
    found = next((r for r in b["runs"] if r.get("found")), None)
    if found:
        d_b = dt.date.fromisoformat(found["date"])
        out["armB_detect"] = found["quarter"]
        out["armB_lead_posting"] = months(d_b, ref_post)
        out["armB_lead_label"] = months(d_b, ref_label) if ref_label else None
    return out


def write_results(state, cases, path, ledger_path=None):
    meta = state["meta"]
    rows = []
    spend = {}
    if ledger_path and os.path.exists(ledger_path):
        for l in open(ledger_path, encoding="utf-8"):
            r = json.loads(l)
            spend[r["run"].split(" ")[0]] = spend.get(r["run"].split(" ")[0], 0.0) + r["cost"]
    L = ["# Canary evaluation results", "",
         f"- **Locked plan**: `EVALUATION_PLAN.md` v5, lock commit `{meta['eval_lock_commit']}` (tag `eval-lock`).",
         f"- **Code ran from commit** `{meta['code_commit']}`" + (" (working tree had uncommitted changes)" if meta.get("working_tree_dirty") else " (clean working tree)") + ".",
         f"- **Model**: {meta['model']} for every LLM call. Started {meta['started']}, finished {meta.get('finished', 'not finished')}.",
         f"- **Budget**: ${meta['budget']:.2f} working budget (config ceiling ${meta['spend_limit_in_config']:.2f}); spent ${meta.get('ledger_total') or 0:.2f} in this evaluation ledger."
         + (" **Arm A only: no Arm B was run.**" if meta.get("arm_a_only") else ""),
         "- **Amendments approved by the owner on 2026-10-03, before any evaluation run** (see `eval_runs.md`): spend limit $10 and a working budget of $9.50; briefs only for the target terms and the top 10 recommended terms per run; the Message Batches API for cost only (same model, prompts, schemas, inputs); "
         f"**the Arm B schedule was reduced because of budget** (setting `ARM_B_SCHEDULE = {eval_config.ARM_B_SCHEDULE!r}`): per positive case the first year-end on or after its first Arm A flag plus the last quarter-end before the label change, rivaroxaban at 2017Q4, 2018Q4, 2019Q4. Lead times for Arm B are therefore at the resolution of those runs (coarser than the locked schedule, so conservative).", ""]
    if state.get("stop"):
        s = state["stop"]
        L += [f"> **STOPPED by the spend limit** at {s['case']} {s['run_quarter']} (stage {s['stage']}); total spent ${s['total_spent']} of ${s['limit']}. "
              "Cases below are reported as incomplete. Continuing needs the owner's written approval of a higher limit.", ""]
    L += ["## Summary", "", "Lead times in months (positive = earlier than the reference). Cases never detected are misses, not zero lead time.", "",
          "| Case | Role | Status | Arm A first flag | Arm A lead vs posting / vs label | Arm A rounded to the Arm B schedule | Arm B detection (run) | Arm B lead vs posting / vs label | Spend |",
          "|---|---|---|---|---|---|---|---|---|"]
    reps = {}
    for name in [n for n in eval_config.RUN_ORDER if n in state["cases"]]:
        rep = reps[name] = case_report(name, cases[name], state["cases"][name], spend.get(name, 0.0))
        fmt = lambda v: "n/a" if v is None else f"{v:+.1f}"
        L.append(f"| {name} | {rep['role']} | {rep['status']} | {rep.get('armA_detect') or 'never'} | "
                 f"{fmt(rep.get('armA_lead_posting'))} / {fmt(rep.get('armA_lead_label'))} | "
                 f"{(rep.get('armA_rounded') or 'n/a')}: {fmt(rep.get('armA_rounded_lead_posting'))} / {fmt(rep.get('armA_rounded_lead_label'))} | "
                 f"{rep.get('armB_detect') or ('not detected' if state['cases'][name].get('arm_b') else 'not run')} | "
                 f"{fmt(rep.get('armB_lead_posting'))} / {fmt(rep.get('armB_lead_label'))} | ${rep['spend']:.2f} |")
    for name in [n for n in eval_config.RUN_ORDER if n in state["cases"]]:
        cs, case = state["cases"][name], cases[name]
        a = cs.get("arm_a", {})
        L += ["", f"## {name} ({case['role']}, case {case['number']}, {case['brand']})", "",
              f"- Target PTs: {', '.join(case['terms'])}. FDA posting {case['posting_quarter']}; label change {case.get('label_change_date') or 'none expected'}.",
              f"- **Arm A**: first quarter-end where any single target PT meets Evans: **{a.get('first_flag') or 'never'}**; the flag stays on from {a.get('sustained_from') or 'n/a'}; "
              f"union of the list (secondary) first flags at {a.get('union_first_flag') or 'never'}. Last cutoff before the label change: {a.get('last_cutoff_before_label') or 'n/a'}; "
              f"alert burden there (all Evans-flagged terms): {a.get('alert_burden_last_cutoff', 'n/a')}.",
              f"- **Status**: {cs.get('status')}."]
        b = cs.get("arm_b")
        if not b:
            continue
        L += [f"- **Arm B schedule**: {', '.join(b['schedule']) or 'none (never flagged before the label change, no LLM spend)'}.", ""]
        if b["runs"]:
            L += ["| Run | Cutoff | Status | Recommended / examined | Target PT routes | Run spend |", "|---|---|---|---|---|---|"]
            for r in sorted(b["runs"], key=lambda x: x["quarter"]):
                routes = "; ".join(f"{t.title()}: {v['route']}" for t, v in r.get("targets", {}).items()) if r["status"] in ("ok", "not_enough_data", "no_label") else r.get("message", "")
                L.append(f"| {name} {r['quarter']} | {r['date']} | {r['status']} | {r.get('n_recommended', '-')} / {r.get('n_examined', '-')} | {routes} | "
                         f"${(r.get('run_spend') or 0):.2f} |")
        if case["role"] == "negative":
            ok = all(not v["route"].startswith("recommended") for r in b["runs"] for v in r.get("targets", {}).values())
            L += ["", f"**Negative case outcome**: {'SUCCESS (no run recommended liver injury)' if ok and b['runs'] else 'FAILURE or not run (a target was recommended in at least one run)' if b['runs'] else 'not run'}. "
                  "One negative case is a sanity check, not a rate."]
    L += ["", "## Alert burden (plan section 7)", "",
          "Arm A: all Evans-flagged terms at the last cutoff before the label change. Arm B: terms recommended after triage, Label Checker and Skeptic in the case's final Arm B run "
          "(a positive case stops at its first detection, so its final run may be earlier than that cutoff). Counts only; non-target terms are not judged.", "",
          "| Case | Arm A flagged terms (last cutoff before label) | Arm B recommended in its final run (cutoff) |", "|---|---|---|"]
    for name in [n for n in eval_config.RUN_ORDER if n in state["cases"]]:
        cs = state["cases"][name]
        runs = sorted([r for r in cs.get("arm_b", {}).get("runs", []) if r["status"] == "ok"], key=lambda x: x["quarter"])
        fin = runs[-1] if runs else None
        L.append(f"| {name} | {cs.get('arm_a', {}).get('alert_burden_last_cutoff', 'n/a')} | {fin['n_recommended'] if fin else 'n/a'} ({fin['quarter'] if fin else '-'}) |")
    L += ["", "## Notes", "",
          "- Detection (Arm B) is judged on the Skeptic's recommendation before any human approval step, as in plan section 5c. Briefs were written only for the target terms and the top 10 "
          "recommended terms per run (amendment above); this does not affect detection.",
          "- Per-quarter details (every Arm A quarter, every target PT's statistics) are in `eval_out/arm_a_<case>.json`; every Arm B run's full outputs are in `eval_out/runs/`.", ""]
    open(path, "w", encoding="utf-8").write("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arm-a-only", action="store_true", help="statistics arm only: free, no LLM")
    ap.add_argument("--written-go", action="store_true", help="the owner's written go: required for Arm B (spends money)")
    ap.add_argument("--budget", type=float, default=BUDGET_DEFAULT, help="hard limit for this evaluation's LLM spend (never above eval_config.SPEND_LIMIT_USD)")
    ap.add_argument("--resume", action="store_true", help="continue from eval_out/state.json (skips finished runs)")
    ap.add_argument("--resume-cap", type=float, help="stop if THIS invocation would spend more than this many dollars (the overall budget is unchanged)")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--cases", help="comma-separated subset, in the fixed run order")
    a = ap.parse_args()
    order = [c for c in eval_config.RUN_ORDER if not a.cases or c in a.cases.split(",")]
    st = run(order=order, budget=a.budget, written_go=a.written_go, arm_a_only=a.arm_a_only, resume=a.resume, allow_dirty=a.allow_dirty, resume_cap=a.resume_cap)
    print("\nstatuses:", {k: v["status"] for k, v in st["cases"].items()}, "\nresults: eval_out/eval_results.md")
