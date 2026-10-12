"""Precompute the demo runs (Build Step 9) with the Sonnet brief setting and package them into demo_data/ for replay with no FAERS files and no
API key. Linezolid and lithium use the demo-only "extended review mode (top 400)"; atorvastatin uses the normal cap of 150. Needs data/ and .env.
Run: PYTHONIOENCODING=utf-8 python -u precompute_demo.py atorvastatin-2011 [linezolid-2019 lithium-2017]"""
import json, os, shutil, sys

import canary_config
import orchestrator
from spend import Ledger

STEPS = ["resolver", "miner", "triage", "label_checker", "skeptic", "writer"]


def package(run_dir, spec, result):
    """Copy the run folder into demo_data/<id>/. The Miner output is slimmed (per-term yearly counts are already in the Skeptic evidence)."""
    dest = os.path.join(canary_config.DEMO_DIR, spec["id"])
    os.makedirs(dest, exist_ok=True)
    for name in ("trace.jsonl", "run.json", "brief.md"):
        shutil.copy(os.path.join(run_dir, name), dest)
    for s in STEPS:
        d = json.load(open(os.path.join(run_dir, f"{s}.json"), encoding="utf-8"))
        if s == "miner":
            for t in d.get("terms", []):
                t.pop("yearly", None)
        json.dump(d, open(os.path.join(dest, f"{s}.json"), "w", encoding="utf-8"), separators=(",", ":"))
    json.dump({**spec, "source_run": os.path.basename(run_dir), "run_spend": result["run_spend"], "mode": result["mode"]},
              open(os.path.join(dest, "meta.json"), "w", encoding="utf-8"), indent=1)
    size = sum(os.path.getsize(os.path.join(dest, f)) for f in os.listdir(dest))
    print(f"packaged {spec['id']} -> {dest}  ({size / 1e6:.1f} MB)", flush=True)


if __name__ == "__main__":
    ids = sys.argv[1:] or [s["id"] for s in canary_config.DEMO_RUNS]
    for spec in canary_config.DEMO_RUNS:
        if spec["id"] not in ids:
            continue
        print(f"\n===== {spec['id']} =====", flush=True)
        res = orchestrator.run_pipeline(spec["drug"], spec["date"], brief_model=canary_config.DEMO_BRIEF_MODEL, review_cap=spec["review_cap"],
                                        featured=spec["featured"], triage="locked", cost_confirmed=True,
                                        ledger=Ledger(canary_config.BUILD_LEDGER, canary_config.BUILD_BUDGET_USD))   # the packaged demo runs were made with the locked triage
        print(f"status {res['status']}  run spend ${res['run_spend']:.3f}  ledger ${res['ledger_total']:.2f} of ${res['budget']:.2f}", flush=True)
        if res["status"] != "ok":
            sys.exit(f"stopped: {res.get('message')}")
        package(res["run_dir"], spec, res)
