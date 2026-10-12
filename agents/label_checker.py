"""Label Checker agent (Build Step 4). Uses the LOCKED prompt and forced tool schema in label_checker.py (not modified), with Chroma
hybrid retrieval of the top 5 chunks from the label version in force at the cutoff (numpy index as fallback).
Per term: yes / partial / no, the quoted evidence sentence and the label date.
Rule (EVALUATION_PLAN.md 5a): yes drops the term; partial and no pass on to the Skeptic, partial marked "partially labeled".
An invalid tool call is never a drop: it passes on with the flag "invalid_decision".
Run: PYTHONIOENCODING=utf-8 python -u agents/label_checker.py atorvastatin 2011-12-31 5
"""
import collections, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anthropic
import label_checker as locked
from agents import parallel
import mesh_synonyms
import rag_index
from canary_utils import MODEL_CHEAP, is_fresh, load_env

load_env()
TOP_K = 5
POOL = 30         # how deep each synonym's ranking is read when looking for extra passages
EXTRA = 2         # at most this many synonym passages are added to the original top 5


def _search(drug, query, cutoff, k):
    try:
        import rag_chroma
        return rag_chroma.search(drug, query, cutoff, k=k) + ("chroma",)
    except Exception as e:  # missing or broken Chroma store: use the numpy fallback and say so
        print(f"  (chroma unavailable: {type(e).__name__}; using numpy index)", flush=True)
        return rag_index.search(drug, query, cutoff, k=k) + ("numpy",)


def retrieve(drug, reaction, cutoff, k=TOP_K, synonyms=()):
    """Hybrid (dense + BM25, reciprocal rank fusion) top k chunks of the label version in force. Chroma, falling back to numpy.
    synonyms (MeSH, product only): the original query's top k stay exactly as they are (no fusion with the synonyms). The hybrid search is then run for each synonym
    separately, and up to EXTRA passages that are not already among the k are added, taking the best-ranked synonym hits (rank 1 of every synonym first, in the
    synonyms' order, then rank 2, and so on). The Label Checker sees at most k + EXTRA passages. The label version in force is the same for every query.
    No synonyms = exactly the original single search (what the locked evaluation uses)."""
    query = f"{reaction.lower()} adverse reaction"  # the query form used for the locked 27 of 32 result
    in_force, hits, backend = _search(drug, query, cutoff, k)
    if not synonyms or not hits:
        return in_force, hits, backend
    have = {(c["file"], c["section"], c["text"]) for _, c in hits}
    lists = [_search(drug, f"{s.lower()} adverse reaction", cutoff, POOL)[1] for s in synonyms]
    extra = []
    for rank in range(POOL):
        for lst in lists:
            if rank < len(lst) and len(extra) < EXTRA:
                score, chunk = lst[rank]
                key = (chunk["file"], chunk["section"], chunk["text"])
                if key not in have:
                    have.add(key)
                    extra.append((score, chunk))
    return in_force, list(hits) + extra, backend


def judge_term(client, drug, reaction, in_force, hits, backend, model=MODEL_CHEAP, synonyms=()):
    """The LLM step for one term, given retrieval results. Safe to run in a thread. synonyms: MeSH synonyms found for the term (product only); when there are any,
    one line "Also known as: ..." follows the reaction in the user message. The locked prompt and schema are not changed."""
    if not hits:
        return {"term": reaction, "decision": None, "valid": False, "label_date": None, "evidence": "", "cost": 0.0,
                "note": "no label version in force", "chunks": [], "backend": backend}
    if hasattr(client, "set_context"):
        client.set_context(term=reaction)
    passages = "\n\n".join(f"[{h['section']}] {h['text']}" for _, h in hits)
    asked = reaction + (f"\nAlso known as: {'; '.join(synonyms)}" if synonyms else "")
    res = locked.check(client, asked, passages, f"{drug} label of {in_force}", model=model)
    return {"term": reaction, "decision": res["on_label"], "valid": res["valid"], "confidence": res["confidence"],
            "evidence": res["evidence"] or "", "label_date": in_force, "cost": res["cost"], "cached": res["cached"],
            "chunks": [{"section": h["section"], "text": h["text"], "file": h["file"]} for _, h in hits], "backend": backend,
            **({"mesh_synonyms": list(synonyms)} if synonyms else {})}


def check_term(client, drug, reaction, cutoff, model=MODEL_CHEAP, mesh=False):
    syn = mesh_synonyms.lookup(reaction, fresh=is_fresh(client))["synonyms"] if mesh else []
    in_force, hits, backend = retrieve(drug, reaction, cutoff, synonyms=syn)
    return judge_term(client, drug, reaction, in_force, hits, backend, model, synonyms=syn)


def route(r, t):
    r["stats"] = {k: t[k] for k in ("a", "b", "c", "d", "PRR", "chi2")}
    if r["decision"] == "yes" and r["valid"]:
        r["route"] = "dropped_on_label"
    else:
        r["route"] = "to_skeptic"
        r["note"] = ("partially labeled: " + r["evidence"]) if r["decision"] == "partial" else (
            "invalid_decision" if not r["valid"] else "not on label")
    return r


def run(triage_out, client=None, model=MODEL_CHEAP, workers=parallel.WORKERS, mesh=False):
    """triage_out: output of agents.triage_step.run. Retrieval is sequential; the LLM calls run `workers` at a time (same prompts and
    inputs as the sequential version, results in input order). The spend limit is enforced by the client (spend.LedgerClient).
    mesh=True (the app's default, set by the orchestrator; the locked evaluation never sets it): MeSH synonyms widen the retrieval and add an "Also known as" line."""
    drug, cutoff = triage_out["drug"], triage_out["date"]
    out = {"agent": "label_checker", "drug": drug, "date": cutoff, "model": model}
    if triage_out.get("status") != "ok":
        return {**out, "status": "skipped", "message": "Triage returned no terms."}
    client = client or anthropic.Anthropic()
    terms = triage_out["terms"]
    fresh = is_fresh(client)   # a fresh run does live MeSH lookups too (the file is not read, but written)
    t_mesh = time.time()
    lookups = mesh_synonyms.lookup_many([t["term"] for t in terms], fresh=fresh) if mesh else [{"synonyms": [], "status": "off"} for t in terms]
    if mesh:
        n = collections.Counter(l["status"] for l in lookups)
        print(f"  label checker: MeSH lookups for {len(terms)} terms: {dict(n)} (cache hits {mesh_synonyms.stats['cache_hits']}, live requests {mesh_synonyms.stats['live_requests']}, {time.time() - t_mesh:.0f} s)", flush=True)
        for t, l in zip(terms, lookups):
            if l["status"] == "error":
                print(f"  MeSH lookup failed for {t['term']!r}, original query only: {l.get('error')}", flush=True)
    retrieved = [retrieve(drug, t["term"], cutoff, synonyms=l["synonyms"]) for t, l in zip(terms, lookups)]
    print(f"  label checker: retrieved passages for {len(terms)} terms", flush=True)
    def work(i, cl):
        return judge_term(cl, drug, terms[i]["term"], *retrieved[i], model=model, synonyms=lookups[i]["synonyms"])

    if hasattr(client, "map"):  # the evaluation sends these calls as one batch (batching.BatchRunner)
        results = client.map(work, range(len(terms)))
    else:
        results = parallel.pmap(lambda i: work(i, client), range(len(terms)), workers,
                                on_done=lambda n, tot: print(f"  label checker {n}/{tot}", flush=True) if n % 10 == 0 or n == tot else None)
    results = [route(r, t) for r, t in zip(results, terms)]
    spend = sum(r["cost"] for r in results)
    dist = {k: sum(1 for r in results if r["decision"] == k) for k in ("yes", "partial", "no")}
    if mesh:
        out["mesh_status"] = dict(collections.Counter(l["status"] for l in lookups))
    return {**out, "status": "ok", "label_date": results[0]["label_date"] if results else None, "decisions": dist,
            "n_checked": len(results), "n_dropped": dist["yes"], "n_to_skeptic": len(results) - sum(r["route"] == "dropped_on_label" for r in results),
            "spend": round(spend, 4), "results": results}

if __name__ == "__main__":
    from agents import miner, triage_step
    tr = triage_step.run(miner.run(sys.argv[1], sys.argv[2]))
    tr["terms"] = tr["terms"][: int(sys.argv[3])] if len(sys.argv) > 3 else tr["terms"]
    r = run(tr)
    print(json.dumps({k: v for k, v in r.items() if k != "results"}, indent=1))
    for x in r["results"]:
        print(x["term"], x["decision"], x["route"], "|", x["evidence"][:100])
