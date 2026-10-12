"""One command starts Canary: the FastAPI backend (port 8000) and the React dev server (port 5173), then opens the browser.
Run from the repo root:  python start.py          Stop with Ctrl-C.
Demo mode (replay the precomputed runs; no FAERS data and no API key needed):  python start.py --demo
First time only:  cd web && npm install"""
import os, subprocess, sys, time, webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
NPM = "npm.cmd" if os.name == "nt" else "npm"


def main():
    if not os.path.isdir(os.path.join(ROOT, "web", "node_modules")):
        sys.exit("The web app is not installed yet: run  cd web && npm install  once, then start again.")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if "--demo" in sys.argv:
        env["CANARY_DEMO"] = "1"  # the API reports default_demo, so the page opens in demo mode
    api = subprocess.Popen([sys.executable, "-u", "-m", "uvicorn", "api.main:app", "--port", "8000"], cwd=ROOT, env=env)
    web = subprocess.Popen([NPM, "run", "dev", "--", "--host", "127.0.0.1"], cwd=os.path.join(ROOT, "web"), env=env)
    time.sleep(4)
    print("\nCanary is running at http://127.0.0.1:5173  (API on :8000). Ctrl-C to stop.\n", flush=True)
    webbrowser.open("http://127.0.0.1:5173")
    try:
        while api.poll() is None and web.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in (web, api):
            if p.poll() is None:
                p.terminate()
        if os.name == "nt":  # npm leaves node children behind on Windows
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(web.pid)], capture_output=True)


if __name__ == "__main__":
    main()
