"""Offline tests (no model, no network, no spend; a temporary cache folder and a temporary MeSH cache) for the MeSH synonym expansion of the Label Checker's retrieval.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_mesh_synonyms.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace

import canary_config, label_checker as locked, mesh_synonyms as ms, orchestrator
from agents import label_checker as agent
from spend import Ledger

ROWS = {"churg": [{"resource": "http://id.nlm.nih.gov/mesh/T1", "label": "x"}]}
SPARQL = [{"dl": {"value": "Churg-Strauss Syndrome"}, "tl": {"value": "Churg-Strauss Syndrome"}},
          {"dl": {"value": "Churg-Strauss Syndrome"}, "tl": {"value": "Allergic Angiitis"}},                      # no new word: dropped
          {"dl": {"value": "Churg-Strauss Syndrome"}, "tl": {"value": "Angiitis, Allergic Granulomatous"}},       # comma-inverted: dropped
          {"dl": {"value": "Churg-Strauss Syndrome"}, "tl": {"value": "Eosinophilic Granulomatous Vasculitis"}},
          {"dl": {"value": "Churg-Strauss Syndrome"}, "tl": {"value": "Allergic Granulomatous Angiitis"}}]       # the original itself: dropped


class Fetch:
    def __init__(self, fail=False):
        self.n, self.fail = 0, fail

    def __call__(self, what, *a):
        self.n += 1
        if self.fail:
            raise ConnectionError("no network")
        if what == "lookup":
            kind, label = a
            return ROWS["churg"] if kind == "term" and label == "allergic granulomatous angiitis" else []
        return SPARQL


def tmp_cache():
    return os.path.join(tempfile.mkdtemp(), "mesh.json")


def test_descriptor_and_entry_terms_are_collected_and_filtered():
    f = Fetch()
    r = ms.lookup("ALLERGIC GRANULOMATOUS ANGIITIS", cache_path=tmp_cache(), fetch=f)
    assert r["status"] == "found" and r["descriptor"] == ["Churg-Strauss Syndrome"]
    assert r["synonyms"] == ["Churg-Strauss Syndrome", "Eosinophilic Granulomatous Vasculitis"], r["synonyms"]


def test_a_repeat_lookup_comes_from_the_local_file_with_no_request():
    path, f = tmp_cache(), Fetch()
    ms.lookup("ALLERGIC GRANULOMATOUS ANGIITIS", cache_path=path, fetch=f)
    n = f.n
    r = ms.lookup("allergic granulomatous angiitis", cache_path=path, fetch=f)
    assert f.n == n and r["source"] == "cache" and r["synonyms"], (f.n, n, r)
    assert json.load(open(path, encoding="utf-8"))["allergic granulomatous angiitis"]["status"] == "found"


def test_nothing_found_is_cached_but_a_failure_is_not():
    path = tmp_cache()
    f = Fetch()
    r = ms.lookup("SOMETHING MEDDRA ONLY", cache_path=path, fetch=f)
    assert r["status"] == "none" and r["synonyms"] == []
    n = f.n
    assert ms.lookup("something meddra only", cache_path=path, fetch=f)["source"] == "cache" and f.n == n
    bad = ms.lookup("ANOTHER TERM", cache_path=path, fetch=Fetch(fail=True))
    assert bad["status"] == "error" and bad["synonyms"] == [] and "ConnectionError" in bad["error"]
    assert "another term" not in json.load(open(path, encoding="utf-8"))          # a failed lookup is retried next time


def test_plural_and_singular_variants_are_tried():
    seen = []

    def fetch(what, *a):
        if what == "lookup":
            seen.append(a)
            return [{"resource": "http://id.nlm.nih.gov/mesh/T9"}] if a == ("term", "nightmares") else []
        return [{"dl": {"value": "Dreams"}, "tl": {"value": "Dreams"}}, {"dl": {"value": "Dreams"}, "tl": {"value": "Nightmares"}}]
    r = ms.lookup("NIGHTMARE", cache_path=tmp_cache(), fetch=fetch)
    assert ("term", "nightmares") in seen and r["synonyms"] == ["Dreams"], r      # "Nightmares" is the original's plural, not a new synonym


def test_retrieval_keeps_the_original_top_5_and_adds_at_most_two_synonym_passages():
    calls = []
    chunk = lambda t: {"file": "f", "section": "s", "text": t}
    ORIG = [chunk(f"o{i}") for i in range(30)]

    def fake_search(drug, query, cutoff, k):
        calls.append((query, k))
        if query.startswith("churg"):
            lst = [chunk("o2"), chunk("churg-strauss text"), chunk("c3"), chunk("c4")]       # o2 is already in the original top 5
        elif query.startswith("vasculitis"):
            lst = [chunk("v1"), chunk("v2"), chunk("churg-strauss text")]
        else:
            lst = ORIG
        return "20090915", [(1.0 / (1 + i), c) for i, c in enumerate(lst)][:k], "chroma"
    old = agent._search
    agent._search = fake_search
    try:
        plain = agent.retrieve("montelukast", "ALLERGIC GRANULOMATOUS ANGIITIS", "2009-12-31")
        assert calls == [("allergic granulomatous angiitis adverse reaction", agent.TOP_K)]       # no synonyms: the original single search, unchanged
        calls.clear()
        in_force, hits, backend = agent.retrieve("montelukast", "ALLERGIC GRANULOMATOUS ANGIITIS", "2009-12-31", synonyms=["Churg-Strauss Syndrome", "Vasculitis X"])
        assert [q for q, _ in calls][0] == "allergic granulomatous angiitis adverse reaction" and calls[0][1] == agent.TOP_K and all(k == agent.POOL for _, k in calls[1:])
        texts = [h["text"] for _, h in hits]
        assert texts[:agent.TOP_K] == [h["text"] for _, h in plain[1]], texts                    # the original top 5, exactly, in the same order
        assert len(texts) == agent.TOP_K + agent.EXTRA == 7 and in_force == "20090915"
        assert texts[5:] == ["churg-strauss text", "v1"] or texts[5:] == ["v1", "churg-strauss text"], texts   # best-ranked synonym hits not already in the top 5 (o2 is skipped)
        assert len(set(texts)) == 7
    finally:
        agent._search = old


def test_no_label_in_force_stays_no_label_with_synonyms():
    old = agent._search
    agent._search = lambda drug, query, cutoff, k: (None, [], "chroma")
    try:
        assert agent.retrieve("gabapentin", "X", "1990-01-01", synonyms=["Y"]) == (None, [], "chroma")
    finally:
        agent._search = old


def test_the_user_message_gets_one_line_only_when_synonyms_were_found_and_the_locked_prompt_is_untouched():
    olds = (locked.CACHE_DIR,)
    locked.CACHE_DIR = tempfile.mkdtemp()
    sent = []

    class Client:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            sent.append(kw)
            call = SimpleNamespace(type="tool_use", input={"on_label": "yes", "evidence": "q", "confidence": 0.9})
            return SimpleNamespace(content=[call], usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    hits = [(0.1, {"section": "S", "text": "t", "file": "f"})]
    try:
        r1 = agent.judge_term(Client(), "montelukast", "NIGHTMARE", "20080605", hits, "chroma")
        r2 = agent.judge_term(Client(), "montelukast", "NIGHTMARE", "20080605", hits, "chroma", synonyms=["Dreams", "Night Terrors"])
    finally:
        locked.CACHE_DIR, = olds
    p1, p2 = sent[0]["messages"][0]["content"], sent[1]["messages"][0]["content"]
    assert "Also known as" not in p1 and "(MedDRA preferred term): NIGHTMARE\n\nDecide" in p1 and "mesh_synonyms" not in r1
    assert "(MedDRA preferred term): NIGHTMARE\nAlso known as: Dreams; Night Terrors\n\nDecide" in p2 and r2["mesh_synonyms"] == ["Dreams", "Night Terrors"]
    assert sent[0]["tools"] == sent[1]["tools"] == [locked.TOOL] and sent[0]["tool_choice"] == sent[1]["tool_choice"] == {"type": "tool", "name": locked.TOOL["name"]}
    assert p1.replace("\nAlso known as: Dreams; Night Terrors", "") == p2.replace("\nAlso known as: Dreams; Night Terrors", "")


def test_default_on_in_the_app_off_and_refused_in_the_locked_evaluation():
    assert canary_config.MESH_SYNONYMS is True                            # ON again with design 2 (gold set 28 of 32 vs 27)
    seen = {}
    olds = (orchestrator.resolver.resolve, orchestrator.miner.run, orchestrator.triage_step.run, orchestrator.label_checker.run, orchestrator.skeptic.run, orchestrator.writer.run)
    orchestrator.resolver.resolve = lambda t: {"status": "resolved", "generic_name": "montelukast", "rxcui": "1", "confidence": 1.0, "message": "ok"}
    orchestrator.miner.run = lambda *a, **k: {"status": "ok", "drug": "montelukast", "date": "d", "n_drug": 1, "n_terms": 1, "n_evans": 1, "elapsed_s": 0, "terms": []}
    orchestrator.triage_step.run = lambda m, **k: {"status": "ok", "label_date": "x", "counts": {"evans": 0, "not_exact_on_label": 0}, "cap": 150, "terms": []}

    def lc(tr, client=None, model=None, **k):
        seen["kwargs"] = k
        return {"status": "ok", "spend": 0, "n_checked": 0, "decisions": {"yes": 0, "partial": 0, "no": 0}, "label_date": "x", "n_to_skeptic": 0, "results": []}
    orchestrator.label_checker.run = lc
    orchestrator.skeptic.run = lambda m, lab, client=None, **k: {"status": "ok", "spend": 0, "n_evaluated": 0, "n_recommended": 0, "n_dismissed": 0, "results": []}
    orchestrator.writer.run = lambda sk, lab, m, client=None, **k: {"status": "ok", "spend": 0, "brief_markdown": "# t\n*n*\n", "n_written": 0, "n_recommended": 0, "removed_uncited": 0}
    try:
        def go(**kw):
            tmp = tempfile.mkdtemp()
            return orchestrator.run_pipeline("montelukast", "2008-06-30", runs_dir=tmp, run_id="r", ledger=Ledger(os.path.join(tmp, "l.jsonl"), 3.5), client=object(), **kw)
        go()
        assert seen["kwargs"] == {"mesh": True}                               # the app default
        go(mesh_synonyms=False)
        assert seen["kwargs"] == {}                                           # off: the call is exactly the locked one
        try:
            orchestrator.run_pipeline("gabapentin", "2008-12-31", evaluation_mode=True, mesh_synonyms=True, runs_dir=tempfile.mkdtemp())
            raise AssertionError("evaluation mode accepted MeSH synonyms")
        except ValueError as e:
            assert "MeSH" in str(e)
    finally:
        (orchestrator.resolver.resolve, orchestrator.miner.run, orchestrator.triage_step.run, orchestrator.label_checker.run, orchestrator.skeptic.run, orchestrator.writer.run) = olds


def test_a_fresh_lookup_does_not_read_the_mesh_cache_but_still_writes_it():
    path, f = tmp_cache(), Fetch()
    ms.lookup("ALLERGIC GRANULOMATOUS ANGIITIS", cache_path=path, fetch=f)
    n = f.n
    r = ms.lookup("ALLERGIC GRANULOMATOUS ANGIITIS", cache_path=path, fetch=f, fresh=True)
    assert f.n > n and r["source"] == "api", (f.n, n, r)                                  # live although the answer is cached
    data = json.load(open(path, encoding="utf-8"))
    assert data["allergic granulomatous angiitis"]["status"] == "found"                    # and written
    n = f.n
    assert ms.lookup("allergic granulomatous angiitis", cache_path=path, fetch=f)["source"] == "cache" and f.n == n   # a normal run afterwards reads it


def test_a_fresh_run_makes_the_mesh_lookups_and_every_label_checker_call_live():
    seen, calls = [], []
    old_lookup, old_search, olds = ms.lookup_many, agent._search, locked.CACHE_DIR
    ms.lookup_many = lambda terms, **k: seen.append(k.get("fresh")) or [{"term": t, "synonyms": [], "status": "none"} for t in terms]
    agent._search = lambda drug, query, cutoff, k: ("20090915", [(0.1, {"section": "S", "text": "t", "file": "f"})], "chroma")
    locked.CACHE_DIR = tempfile.mkdtemp()

    class Client:
        def __init__(self, fresh):
            self.fresh, self.messages = fresh, self

        def create(self, **kw):
            calls.append(1)
            call = SimpleNamespace(type="tool_use", input={"on_label": "no", "evidence": "", "confidence": 0.5})
            return SimpleNamespace(content=[call], usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    tr = {"status": "ok", "drug": "montelukast", "date": "2009-12-31", "terms": [{"term": "NIGHTMARE", "a": 1, "b": 1, "c": 1, "d": 1, "PRR": 1.0, "chi2": 1.0}]}
    try:
        agent.run(tr, client=Client(False), mesh=True)
        assert (seen, len(calls)) == ([False], 1)
        agent.run(tr, client=Client(False), mesh=True)
        assert (seen, len(calls)) == ([False, False], 1)                                      # normal run: answer cache hit, no call
        agent.run(tr, client=Client(True), mesh=True)
        assert (seen, len(calls)) == ([False, False, True], 2)                                 # fresh run: live MeSH lookup AND a live model call
    finally:
        ms.lookup_many, agent._search, locked.CACHE_DIR = old_lookup, old_search, olds


class Slow:
    """A fake MeSH service with latency, a different answer per term, and a call log with thread ids."""

    def __init__(self, latency=0.02):
        self.latency, self.calls, self.lock = latency, [], __import__("threading").Lock()

    def __call__(self, what, *a):
        import time
        time.sleep(self.latency)
        with self.lock:
            self.calls.append((what, a))
        if what == "lookup":
            kind, label = a
            return [{"resource": "http://id.nlm.nih.gov/mesh/T" + label.replace(" ", "_")}] if kind == "term" and not label.startswith("none") else []
        iri = [w for w in a[0].split() if w.startswith("<http")][-1].strip("<>")
        name = iri.rsplit("/T", 1)[1].replace("_", " ")
        return [{"dl": {"value": "Descriptor " + name}, "tl": {"value": "Descriptor " + name}}, {"dl": {"value": "Descriptor " + name}, "tl": {"value": "Entry word " + name}}]


def test_many_terms_concurrently_give_exactly_the_sequential_answers_in_order():
    terms = ["alpha beta", "none here", "gamma", "alpha beta", "delta epsilon", "none again", "zeta"]    # a repeated term and some terms MeSH does not know
    seq = [ms.lookup(t, cache_path=tmp_cache(), fetch=Slow(0)) for t in terms]
    path, f = tmp_cache(), Slow()
    par = ms.lookup_many(terms, cache_path=path, fetch=f, workers=8)
    assert [r["term"] for r in par] == terms
    strip = lambda rs: [{k: v for k, v in r.items() if k != "source"} for r in rs]
    assert strip(par) == strip(seq)
    assert [r["status"] for r in par] == ["found", "none", "found", "found", "found", "none", "found"]
    n_unique = len({t for t in terms})
    assert len([c for c in f.calls if c[0] == "lookup" and c[1][0] == "descriptor" and c[1][1] in ("alpha beta",)]) == 1, "a repeated term is looked up once"
    cache = json.load(open(path, encoding="utf-8"))
    assert len(cache) == n_unique and cache["alpha beta"]["synonyms"] == seq[0]["synonyms"]      # one write, every answer in it
    again = ms.lookup_many(terms, cache_path=path, fetch=Slow(), workers=8)
    assert all(r["source"] == "cache" for r in again)                                             # a normal run afterwards makes no request


def test_the_workers_really_overlap_and_a_fresh_batch_is_live_and_written():
    import time
    terms = [f"term{i} word" for i in range(16)]
    f1 = Slow(0.05)
    t0 = time.time(); ms.lookup_many(terms, cache_path=tmp_cache(), fetch=f1, workers=1); t_one = time.time() - t0
    path, f8 = tmp_cache(), Slow(0.05)
    t0 = time.time(); ms.lookup_many(terms, cache_path=path, fetch=f8, workers=8); t_eight = time.time() - t0
    assert t_eight < t_one / 3, (t_one, t_eight)
    f = Slow()
    ms.lookup_many(terms[:3], cache_path=path, fetch=f, fresh=True, workers=8)
    assert len([c for c in f.calls if c[0] == "lookup"]) >= 3                                      # fresh: live although cached


def test_requests_share_one_pace_and_are_retried_on_429_and_5xx_but_not_on_other_errors():
    import time
    from types import SimpleNamespace
    saved = (ms._pace, ms.requests.get, time.sleep)
    ms._pace = ms._Pace(0.05)
    starts, answers = [], []

    def fake_get(url, params=None, timeout=None, headers=None):
        starts.append(time.monotonic())
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(status_code=a, headers={"Retry-After": "0"} if a == 429 else {}, json=lambda: ["ok"], raise_for_status=lambda: (_ for _ in ()).throw(ms.requests.HTTPError(str(a))) if a >= 400 else None)
    ms.requests.get = fake_get
    ms.time.sleep = lambda s: None if s >= 0.5 else saved[2](s)                                     # skip the long back-off waits, keep the short pacing waits
    try:
        answers[:] = [429, 503, 200]
        assert ms._get("u") == ["ok"] and len(starts) == 3                                         # two retries, then success
        answers[:] = [ms.requests.ConnectionError("x"), 200]
        assert ms._get("u") == ["ok"]
        answers[:] = [404]
        try:
            ms._get("u"); raise AssertionError("a 404 must raise")
        except ms.requests.HTTPError:
            pass
        answers[:] = [503] * 4
        try:
            ms._get("u"); raise AssertionError("retries must run out")
        except ms.requests.HTTPError:
            pass
        starts.clear(); answers[:] = [200] * 6
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(6) as pool:
            list(pool.map(lambda _: ms._get("u"), range(6)))
        gaps = sorted(starts)
        assert gaps[-1] - gaps[0] >= 0.2 and all(b - a >= 0.02 for a, b in zip(gaps, gaps[1:])), gaps   # six threads, one pace of 0.05 s (thread timing jitter allowed)
    finally:
        ms._pace, ms.requests.get, ms.time.sleep = saved


def test_the_evaluation_runner_never_asks_for_synonyms():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run_evaluation.py"), encoding="utf-8").read()
    assert "mesh" not in src.lower()
    import inspect
    assert inspect.signature(agent.run).parameters["mesh"].default is False and inspect.signature(agent.retrieve).parameters["synonyms"].default == ()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
