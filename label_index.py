"""Add a drug's brand-label history to the RAG index without touching existing rows or the locked rag_index.py.
Appends chunks and vectors to the numpy index (data/rag) and to the Chroma collection (data/rag_chroma), same chunking, model and
metadata as rag_index.build. Run: PYTHONIOENCODING=utf-8 python -u label_index.py linezolid NDA021130 [NDA...]"""
import json, sys, time
import numpy as np
import rag_index
from rag_index import RAG_DIR, MODEL, label_docs, raw_text, chunk_label


def add_drug(drug, ndas):
    import rag_chroma
    from fastembed import TextEmbedding
    existing = {json.loads(l)["drug"] for l in open(f"{RAG_DIR}/chunks.jsonl", encoding="utf-8")}
    assert drug not in existing, f"{drug} is already in the index"
    rows, seen = [], set()
    for nda in ndas:
        for date, url in label_docs(nda):
            if (date, url) in seen:
                continue
            seen.add((date, url))
            try:
                text, pages = raw_text(url)
            except Exception as e:  # a listed PDF can be missing (HTTP 404): skip it and say so
                print(f"  skipped {date} {url.split('/')[-1]}: {type(e).__name__}", flush=True)
                continue
            ch = chunk_label(text)
            print(drug, date, url.split("/")[-1], "pages", pages, "chunks", len(ch), flush=True)
            rows += [{"drug": drug, "label_date": date, "file": url.split("/")[-1], "section": s, "text": b} for s, b in ch]
    docs = [f"{r['section']}. {r['text']}" for r in rows]
    uniq = sorted(set(docs))
    t0 = time.time()
    uvec = np.array(list(TextEmbedding(MODEL).embed(uniq, batch_size=32)), dtype="float32")
    pos = {d: i for i, d in enumerate(uniq)}
    vecs = uvec[[pos[d] for d in docs]]
    print(f"{len(rows)} chunks ({len(uniq)} unique) embedded in {time.time() - t0:.0f}s", flush=True)
    old = np.load(f"{RAG_DIR}/vectors.npy")
    start = len(old)
    np.save(f"{RAG_DIR}/vectors.npy", np.vstack([old, vecs]))
    with open(f"{RAG_DIR}/chunks.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    import chromadb
    col = chromadb.PersistentClient(rag_chroma.CHROMA_DIR).get_collection(rag_chroma.COLLECTION)
    for s in range(0, len(rows), 2000):
        e = min(s + 2000, len(rows))
        col.add(ids=[str(start + i) for i in range(s, e)], embeddings=vecs[s:e].tolist(), documents=[rows[i]["text"] for i in range(s, e)],
                metadatas=[{k: rows[i][k] for k in ("drug", "label_date", "file", "section")} for i in range(s, e)])
    print(f"added {len(rows)} chunks of {drug}; chroma now {col.count()}", flush=True)


if __name__ == "__main__":
    add_drug(sys.argv[1], sys.argv[2:])
