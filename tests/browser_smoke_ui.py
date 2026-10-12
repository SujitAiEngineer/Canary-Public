"""Real-browser smoke test of the UI polish: headless Chrome (or Edge) drives the actual React page against the real API with fake providers (tests/smoke_server.py).
Not part of the offline suite (it starts node, uvicorn and a browser); run it by hand:  PYTHONIOENCODING=utf-8 python -u tests/browser_smoke_ui.py
Scenarios: every model with every triage option through the page (8 runs, each ending in a brief with its provenance); the paid-model confirmation gate; the free model's
rate limit (waiting state, then the run resumes); the daily limit and a rejected key (clear message, other models offered, one click selects it); a missing key (the model
is shown as unavailable, others usable); no keys at all (demo mode replays a run). Needs Chrome or Edge, node_modules in web/, and free ports 8000, 5173 and 9333.
Screenshots of the key states are saved to the folder given as the first argument (default: the system temp folder)."""
import base64, json, os, shutil, socket, subprocess, sys, tempfile, time

import requests
import websocket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}
SHOTS = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
CHROME = next((p for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p)), None)
FREE = "openrouter:nvidia/nemotron-3-super-120b-a12b:free"
MODELS = [("claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001", "Anthropic"), ("claude-sonnet-5-5", "claude-sonnet-5-5", "Anthropic"),
          ("claude-opus-5-5", "claude-opus-5-5", "Anthropic"), (FREE, "nvidia/nemotron-3-super-120b-a12b:free", "OpenRouter")]
TRIAGES = [("ewt", "Evidence-Weighted (EWT)"), ("locked", "Locked (original)")]


PORT = 9333


class Browser:
    """A headless browser that is NOT the user's: a brand-new temporary profile (never the normal Chrome profile), extensions off, the debugging port on 127.0.0.1 only.
    close() kills the whole process tree, waits, deletes the temporary profile and checks that nothing is left (process, port, folder)."""

    def __init__(self, port=PORT):
        self.port = port
        self.dir = tempfile.mkdtemp(prefix="canary_ui_smoke_")
        self.proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={self.dir}", "--no-first-run", "--no-default-browser-check",
                                      "--disable-extensions", "--disable-component-extensions-with-background-pages", "--disable-gpu", "--remote-allow-origins=*",
                                      "--window-size=1280,2400", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws = None
        try:
            for _ in range(60):
                try:
                    pages = requests.get(f"http://127.0.0.1:{port}/json", timeout=2).json()
                    self.ws = websocket.create_connection(next(p for p in pages if p["type"] == "page")["webSocketDebuggerUrl"], timeout=60)
                    break
                except Exception:
                    time.sleep(0.5)
            else:
                raise RuntimeError("the browser did not start")
            self.n = 0
            self.check_isolation()
        except BaseException:
            self.close()
            raise

    def command_lines(self):
        """Command lines of every chrome.exe process that belongs to this test (its temporary profile is in the command line)."""
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='msedge.exe'\" | ForEach-Object { $_.CommandLine }"],
                             capture_output=True, text=True).stdout.splitlines()
        return [l for l in out if os.path.basename(self.dir) in l]

    def check_isolation(self):
        main = [l for l in self.command_lines() if "--type=" not in l]
        assert len(main) == 1, main
        c = main[0]
        normal = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data").lower()
        assert f"--user-data-dir={self.dir}" in c and self.dir.lower().startswith(tempfile.gettempdir().lower()), c           # a temporary profile
        assert normal not in c.lower() and "--disable-extensions" in c and f"--remote-debugging-port={self.port}" in c, c
        ext = [t["url"] for t in requests.get(f"http://127.0.0.1:{self.port}/json", timeout=5).json() if t["url"].startswith("chrome-extension://")]
        assert not ext, f"extensions are still loaded: {ext}"                                                                  # nothing from any extension runs
        listening = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-NetTCPConnection -LocalPort {self.port} -State Listen | Select -Expand LocalAddress) -join ','"],
                                   capture_output=True, text=True).stdout.strip()
        assert listening and set(listening.split(",")) <= {"127.0.0.1", "::1"}, listening                                       # the debugging port is local only

    def send(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m["result"]

    def js(self, expr):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", str(r["exceptionDetails"])))
        return r["result"].get("value")

    def goto(self, url):
        self.send("Page.navigate", url=url)
        self.wait("document.readyState === 'complete'", 30)

    def wait(self, expr, timeout=60, what=None):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.js(f"Boolean({expr})"):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.4)
        raise AssertionError(f"timed out after {timeout}s waiting for {what or expr}")

    def click(self, sel):
        self.wait(f"document.querySelector({json.dumps(sel)})", 20, f"element {sel}")
        self.js(f"document.querySelector({json.dumps(sel)}).click()")

    def text(self, sel):
        return self.js(f"(document.querySelector({json.dumps(sel)}) || {{}}).innerText || ''")

    def type(self, sel, value):
        self.js(f"""(() => {{ const el = document.querySelector({json.dumps(sel)});
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, {json.dumps(value)});
            el.dispatchEvent(new Event('input', {{bubbles: true}})); }})()""")

    def shot(self, name):
        data = self.send("Page.captureScreenshot", format="png")["data"]
        p = os.path.join(SHOTS, f"ui_{name}.png")
        open(p, "wb").write(base64.b64decode(data))
        return p

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)   # the browser and every process it started
        try:
            self.proc.wait(timeout=20)
        except Exception:
            pass
        for _ in range(20):                                                                           # Windows may hold files for a moment after the process ends
            shutil.rmtree(self.dir, ignore_errors=True)
            if not os.path.exists(self.dir):
                break
            time.sleep(0.5)

    def verify_gone(self):
        """After close(): no process of this test's browser, nothing listening on the debugging port, the temporary profile deleted. Returns a description."""
        left = self.command_lines()
        with socket.socket() as s:
            s.settimeout(1)
            port_open = s.connect_ex(("127.0.0.1", self.port)) == 0
        assert not left and not port_open and not os.path.exists(self.dir), (len(left), port_open, os.path.exists(self.dir))
        return f"no browser process, port {self.port} closed, temporary profile deleted"


class Servers:
    def __init__(self):
        npm = "npm.cmd" if os.name == "nt" else "npm"
        self.web = subprocess.Popen([npm, "run", "dev", "--", "--host", "127.0.0.1"], cwd=os.path.join(ROOT, "web"), env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.api = None
        for _ in range(60):
            try:
                requests.get("http://127.0.0.1:5173", timeout=2)
                break
            except Exception:
                time.sleep(0.5)

    def start_api(self, mode):
        self.stop_api()
        self.api = subprocess.Popen([PY, "-u", os.path.join(ROOT, "tests", "smoke_server.py"), mode], cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            try:
                requests.get("http://127.0.0.1:8000/api/mode", timeout=2)
                return
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("the API did not start")

    def stop_api(self):
        if self.api and self.api.poll() is None:
            self.api.terminate()
            self.api.wait(timeout=20)

    def stop(self):
        self.stop_api()
        if self.web.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.web.pid)], capture_output=True)


def open_page(b):
    b.goto("http://127.0.0.1:5173")
    b.wait("document.querySelector('[data-testid^=\"model-\"]')", 30, "the model options")


def choose(b, model, triage, confirm=True):
    b.click(f'[data-testid="model-{model}"] input')
    b.click(f'[data-testid="triage-{triage}"] input')
    paid = model != FREE
    if paid and confirm:
        b.js("[...document.querySelectorAll('label')].find(l => l.textContent.includes('Confirm the cost')).querySelector('input').click()")


def run_from_page(b, drug="atorvastatin", date="2011-12-31"):
    b.type('input[placeholder^="e.g. Lipitor"]', drug)
    b.type('input[type="date"]', date)
    b.js("[...document.querySelectorAll('button')].find(x => x.textContent.includes('Run Canary')).click()")


results = []


def ok(name, detail=""):
    results.append(name)
    print(f"  ok  {name}" + (f": {detail}" if detail else ""), flush=True)


def scenario_options(b, srv):
    srv.start_api("ok")
    open_page(b)
    cards = b.js("[...document.querySelectorAll('[data-testid^=\"model-\"]')].map(e => e.innerText)")
    assert len(cards) == 4
    for (mid, model_id, prov), text in zip(MODELS, cards):
        assert prov in text and model_id in text, text
        assert ("Free" in text) == (mid == FREE) and (("per run" in text) != (mid == FREE)), text
    free_text = cards[3]
    assert "tested on the 32-row gold set: 81.3% vs Haiku 84.4%" in free_text, free_text
    tri = b.js("[...document.querySelectorAll('[data-testid^=\"triage-\"]')].map(e => e.innerText)")
    assert len(tri) == 2 and "Evidence-Weighted" in tri[0] and "default" in tri[0] and "Locked (original)" in tri[1] and all(len(t.split("\n")) >= 2 for t in tri), tri
    ok("selectors show provider, model ID, cost or Free, the tested label, and a triage explanation each")
    b.shot("selectors")
    # a paid model keeps Run disabled until the cost is confirmed
    b.click('[data-testid="model-claude-sonnet-5-5"] input')
    b.type('input[placeholder^="e.g. Lipitor"]', "atorvastatin")
    assert b.js("[...document.querySelectorAll('button')].find(x => x.textContent.includes('Run Canary')).disabled") is True
    b.js("[...document.querySelectorAll('label')].find(l => l.textContent.includes('Confirm the cost')).querySelector('input').click()")
    assert b.js("[...document.querySelectorAll('button')].find(x => x.textContent.includes('Run Canary')).disabled") is False
    ok("a paid model needs an explicit cost confirmation before Run is enabled")
    for mid, model_id, prov in MODELS:
        for tid, tlabel in TRIAGES:
            open_page(b)
            choose(b, mid, tid)
            run_from_page(b)
            b.wait("document.querySelector('[data-testid=\"provenance\"]') || document.querySelector('[data-testid=\"failure\"]')", 180, f"the brief of {mid} {tid}")
            assert b.js("Boolean(document.querySelector('[data-testid=\"failure\"]'))") is False, b.text('[data-testid="failure"]')
            prov_text = b.text('[data-testid="provenance"]')
            assert "Produced by" in prov_text and model_id in prov_text and prov in prov_text and tlabel in prov_text and "not medical advice" in prov_text, prov_text
            ok(f"{prov} {model_id} + {tlabel}", "brief shows its model, triage and the notice")
    b.shot("brief_provenance")


def scenario_waiting(b, srv):
    srv.start_api("waiting")
    open_page(b)
    choose(b, FREE, "ewt")
    run_from_page(b)
    b.wait("document.querySelector('[data-testid=\"waiting\"]')", 120, "the waiting state")
    assert "resumes by itself" in b.text('[data-testid="waiting"]')
    b.shot("waiting")
    b.wait("document.querySelector('[data-testid=\"provenance\"]')", 180, "the run to resume and finish")
    assert b.js("Boolean(document.querySelector('[data-testid=\"waiting\"]'))") is False
    ok("the free model's rate limit shows a waiting state, then the run resumes and finishes")


def scenario_failure(b, srv, mode, expect_title, expect_text):
    srv.start_api(mode)
    open_page(b)
    choose(b, FREE, "ewt")
    run_from_page(b)
    b.wait("document.querySelector('[data-testid=\"failure\"]')", 120, f"the {mode} failure panel")
    t = b.text('[data-testid="failure"]')
    assert expect_title in t and expect_text in t and "Try another model" in t, t
    use = b.js("[...document.querySelectorAll('[data-testid^=\"use-\"]')].map(e => e.dataset.testid)")
    assert use and all(not u.startswith("use-openrouter") for u in use), use
    b.shot(f"failure_{mode}")
    b.click(f'[data-testid="{use[0]}"]')
    b.wait("!document.querySelector('[data-testid=\"failure\"]')", 10, "the panel to close")
    picked = b.js("document.querySelector('[data-testid^=\"model-\"] input:checked').closest('label').dataset.testid")
    assert picked == "model-" + use[0][len("use-"):], (picked, use)
    ok(f"{mode}: clear message, other models offered, one click selects it", t.split("\n")[0])


def scenario_limit(b, srv):
    srv.start_api("limit")      # the real $6.00 limit for paid runs: Opus (about $7.58 a run) is refused even when the cost is confirmed
    open_page(b)
    choose(b, "claude-opus-5-5", "ewt")
    run_from_page(b)
    b.wait("document.querySelector('[data-testid=\"failure\"]')", 60, "the limit message")
    t = b.text('[data-testid="failure"]')
    assert "This run would pass the spending limit" in t and "hard limit" in t and "Try another model" in t, t
    use = b.js("[...document.querySelectorAll('[data-testid^=\"use-\"]')].map(e => e.dataset.testid)")
    assert use and "use-claude-opus-5-5" not in use, use
    b.click(f'[data-testid="{use[0]}"]')
    b.wait("!document.querySelector('[data-testid=\"failure\"]')", 10, "the panel to close")
    ok("the spending limit: Opus is refused with a clear message and other models are offered", t.split("\n")[0])
    b.shot("failure_limit")


def scenario_missing_key(b, srv):
    srv.start_api("nokey")
    open_page(b)
    haiku = b.text('[data-testid="model-claude-haiku-4-5-20251001"]')
    assert "ANTHROPIC_API_KEY is not set in .env" in haiku and "Pick another model" in haiku, haiku
    assert b.js("document.querySelector('[data-testid=\"model-claude-haiku-4-5-20251001\"] input').disabled") is True
    assert b.js("document.querySelector('[data-testid=\"model-" + FREE + "\"] input').disabled") is False
    b.type('input[placeholder^="e.g. Lipitor"]', "atorvastatin")
    assert b.js("[...document.querySelectorAll('button')].find(x => x.textContent.includes('Run Canary')).disabled") is True   # the default model has no key
    b.click(f'[data-testid="model-{FREE}"] input')
    assert b.js("[...document.querySelectorAll('button')].find(x => x.textContent.includes('Run Canary')).disabled") is False
    ok("a missing key: the model is shown as unavailable with the reason, Run is blocked, another model unblocks it")
    b.shot("missing_key")


def scenario_no_keys_demo(b, srv):
    srv.start_api("nokeys")
    b.goto("http://127.0.0.1:5173")
    b.wait("[...document.querySelectorAll('button')].some(x => x.textContent.includes('Replay demo'))", 30, "demo mode to open by itself")
    b.js("[...document.querySelectorAll('button')].find(x => x.textContent.includes('Replay demo')).click()")
    b.wait("document.querySelector('[data-testid=\"provenance\"]')", 120, "the demo brief")
    t = b.text('[data-testid="provenance"]')
    assert "Produced by" in t and "Locked (original)" in t and "not medical advice" in t, t
    ok("demo mode needs no keys: the page opens in demo mode, replays a run, and the brief says what produced it")
    b.shot("demo_no_keys")


def main():
    if not CHROME:
        sys.exit("No Chrome or Edge found.")
    os.makedirs(SHOTS, exist_ok=True)
    srv, b = Servers(), None
    try:
        b = Browser()
        scenario_options(b, srv)
        scenario_limit(b, srv)
        scenario_waiting(b, srv)
        scenario_failure(b, srv, "daily", "The free model's daily limit is used up", "cached")
        scenario_failure(b, srv, "rejected", "The provider rejected the API key", "rejected the API key")
        scenario_missing_key(b, srv)
        scenario_no_keys_demo(b, srv)
        ok("browser isolation: temporary profile (not the normal Chrome profile), extensions off, no extension targets, debugging port on 127.0.0.1 only")
    finally:
        if b:
            b.close()
        srv.stop()
    if b:
        print("  ok  teardown:", b.verify_gone())
    print(f"\nbrowser smoke test: {len(results)} checks passed; screenshots in {SHOTS}")


main()
