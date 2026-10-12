"""Chroma backend for the label RAG index (Build Step 0). Like for like with rag_index.py (numpy): same embedding model
(bge-small via fastembed), same chunks and metadata, same hybrid retrieval (dense + BM25 merged by reciprocal rank), same
label-version filter. The numpy index stays as the fallback. Vectors are copied from the numpy index, not re-embedded.
Run: PYTHONIOENCODING=utf-8 python -u rag_chroma.py build   |   python -u rag_chroma.py compare
"""
import json, re, sys
import numpy as np
import rag_index
from rag_index import RAG_DIR, MODEL, QPREFIX, _bm25

CHROMA_DIR = "data/rag_chroma"
COLLECTION = "labels"
_cache = {}


def build():
    import chromadb
    c = rag_index.load()
    client = chromadb.PersistentClient(CHROMA_DIR)
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    col = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    rows, vecs = c["rows"], c["vecs"]
    for s in range(0, len(rows), 2000):
        e = min(s + 2000, len(rows))
        col.add(ids=[str(i) for i in range(s, e)], embeddings=vecs[s:e].tolist(),
                documents=[rows[i]["text"] for i in range(s, e)],
                metadatas=[{k: rows[i][k] for k in ("drug", "label_date", "file", "section")} for i in range(s, e)])
    print(f"chroma collection '{COLLECTION}': {col.count()} chunks in {CHROMA_DIR}", flush=True)


def _load():
    if not _cache:
        import chromadb
        from fastembed import TextEmbedding
        _cache["model"] = TextEmbedding(MODEL)
        _cache["col"] = chromadb.PersistentClient(CHROMA_DIR).get_collection(COLLECTION)
    return _cache


def search(drug, query, cutoff, k=5):
    """Same contract as rag_index.search: returns (label_date in force, [(score, chunk dict)])."""
    c = _load()
    col, cut = c["col"], cutoff.replace("-", "")
    metas = col.get(where={"drug": drug}, include=["metadatas"])["metadatas"]
    dates = sorted({m["label_date"] for m in metas if m["label_date"] <= cut})
    if not dates:
        return None, []
    in_force = dates[-1]
    got = col.get(where={"$and": [{"drug": drug}, {"label_date": in_force}]}, include=["documents", "metadatas"])
    order = sorted(range(len(got["ids"])), key=lambda i: int(got["ids"][i]))  # original row order, so ties break as in numpy
    ids = [got["ids"][i] for i in order]
    rows = [{**got["metadatas"][i], "text": got["documents"][i]} for i in order]
    n = len(ids)
    q = np.array(list(c["model"].embed([QPREFIX + query])), dtype="float32")[0]
    res = col.query(query_embeddings=[q.tolist()], n_results=n, where={"$and": [{"drug": drug}, {"label_date": in_force}]})
    assert len(res["ids"][0]) == n, "dense search must rank every chunk of the version"
    pos = {i: p for p, i in enumerate(ids)}
    rank_d = np.empty(n)
    for r, i in enumerate(res["ids"][0]):
        rank_d[pos[i]] = r
    lex = _bm25([r["section"] + " " + r["text"] for r in rows], query)
    rank_l = np.empty(n); rank_l[np.argsort(-lex)] = np.arange(n)
    fused = 1 / (60 + rank_d) + 1 / (60 + rank_l)
    top = np.argsort(-fused)[:k]
    return in_force, [(float(fused[o]), rows[o]) for o in top]


def compare():
    """Top 5 from Chroma must equal top 5 from numpy on the four Task 3 retrieval tests and the 32-row gold set."""
    import pandas as pd
    jobs = [("atorvastatin", "diabetes, increased blood glucose, HbA1c", "2011-12-31"),
            ("atorvastatin", "diabetes, increased blood glucose, HbA1c", "2013-12-31"),
            ("gabapentin", "drug reaction with eosinophilia and systemic symptoms, DRESS, multiorgan hypersensitivity", "2010-12-31"),
            ("gabapentin", "drug reaction with eosinophilia and systemic symptoms, DRESS, multiorgan hypersensitivity", "2011-12-31")]
    gold = pd.read_csv("label_gold_set_v2_DRAFT.csv", comment="#")
    cutoff = "2026-10-02"  # the gold set was scored against the current label
    jobs += [(g.drug, f"{g.reaction.lower()} adverse reaction", cutoff) for g in gold.itertuples()]
    bad = 0
    for drug, query, cut in jobs:
        d1, a = rag_index.search(drug, query, cut, k=5)
        d2, b = search(drug, query, cut, k=5)
        ka = [(r["file"], r["section"], r["text"]) for _, r in a]
        kb = [(r["file"], r["section"], r["text"]) for _, r in b]
        ok = d1 == d2 and ka == kb
        bad += not ok
        print("OK  " if ok else "DIFF", drug, cut, query[:50], d1, d2, flush=True)
    print(f"\n{len(jobs) - bad} of {len(jobs)} identical top 5 (4 retrieval tests + {len(jobs) - 4} gold rows)")
    return bad


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build()
    elif sys.argv[1] == "compare":
        sys.exit(1 if compare() else 0)
