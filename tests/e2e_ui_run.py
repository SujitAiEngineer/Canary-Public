"""End-to-end check of the Step 8 done-when through the live HTTP API, the same calls the React app makes (needs `python start.py` or uvicorn
running on :8000 and API credit; a full run costs about $1). Not part of the offline test suite.
Run: PYTHONIOENCODING=utf-8 python -u tests/e2e_ui_run.py Zyvox 2019-12-31"""
import json, sys, time
import requests

BASE = "http://127.0.0.1:5173"  # through the Vite proxy, exactly as the browser does
drug, date = sys.argv[1], sys.argv[2]
r = requests.post(f"{BASE}/api/resolve", json={"text": drug}).json()
print("resolve:", r["status"], r.get("generic_name"))
assert r["status"] == "resolved"
run = requests.post(f"{BASE}/api/runs", json={"drug": r["generic_name"], "date": date, "serious_only": False}).json()["run_id"]
print("run", run)
t0 = time.time()
with requests.get(f"{BASE}/api/runs/{run}/events", stream=True) as s:
    ev_name = None
    for line in s.iter_lines(decode_unicode=True):
        if line.startswith("event:"):
            ev_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            if ev_name == "end":
                break
            e = json.loads(line[5:])
            if e["status"] != "start":
                print(f"  +{time.time() - t0:5.0f}s {e['step']:14s} {e['status']:8s} {e['summary'][:110]}", flush=True)
info = requests.get(f"{BASE}/api/runs/{run}").json()
print("status:", info["status"], "spend", info["run_spend"], "ledger", info["ledger_total"])
sig = requests.get(f"{BASE}/api/runs/{run}/signals").json()
print("counts:", sig["counts"])
s0 = sig["signals"][0]
print("first signal:", s0["term"], "|", s0["claims"][0]["text"][:120])
fb = requests.post(f"{BASE}/api/runs/{run}/feedback", json={"term": s0["term"], "decision": "approve", "reason": "e2e check"})
print("feedback:", fb.status_code, fb.json().get("decision"))
