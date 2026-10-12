"""Label RAG (Phase 2 Task 3): download historical brand label PDFs (Drugs@FDA), chunk by section and paragraph, embed
locally (fastembed, bge-small-en-v1.5, ONNX, no torch), store in a plain numpy index, and run time-aware retrieval.
Run: PYTHONIOENCODING=utf-8 python -u rag_index.py build atorvastatin gabapentin   |   ... python -u rag_index.py test
"""
import json, os, re, subprocess, sys, time
import numpy as np
from canary_utils import get_json
from test_b_historical_labels import pdf_text  # downloads the PDF (cached in data/drugsatfda) and returns layout text

RAG_DIR = "data/rag"
MODEL = "BAAI/bge-small-en-v1.5"
QPREFIX = "Represent this sentence for searching relevant passages: "
NDAS = {"atorvastatin": ["NDA020702"], "gabapentin": ["NDA020235"], "simvastatin": ["NDA019766"]}  # brand application(s)
HEAD = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2})?)\s+([A-Z][A-Za-z0-9 ,/&()\-]{3,70})\s*$")  # numbered PLR heading, e.g. "5.3 Endocrine Function"
KNOWN = {"WARNINGS", "PRECAUTIONS", "ADVERSE REACTIONS", "CONTRAINDICATIONS", "WARNINGS AND PRECAUTIONS", "DESCRIPTION",
         "INDICATIONS AND USAGE", "DOSAGE AND ADMINISTRATION", "OVERDOSAGE", "CLINICAL PHARMACOLOGY", "DRUG INTERACTIONS",
         "USE IN SPECIFIC POPULATIONS", "CLINICAL STUDIES", "HOW SUPPLIED", "BOXED WARNING"}


def label_docs(nda):
    d = get_json("https://api.fda.gov/drug/drugsfda.json", {"search": f'application_number:"{nda}"', "limit": 5})
    out = {}
    for r in d.get("results", []):
        for s in r.get("submissions", []):
            for x in s.get("application_docs", []) or []:
                if x.get("type") == "Label" and x.get("url"):
                    out[x["url"].replace("http://", "https://")] = x["date"]
    return sorted((v, u) for u, v in out.items())


def raw_text(url):
    """Reading-order text (no -layout, so two-column highlights do not interleave). Returns (text, pages)."""
    pdf = os.path.join("data/drugsatfda", url.split("/")[-1])
    pdf_text(url)  # ensures the PDF is on disk
    raw = pdf + ".raw.txt"
    if not os.path.exists(raw):
        subprocess.run(["pdftotext", pdf, raw], check=True, stderr=subprocess.DEVNULL)
    info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, errors="replace").stdout
    pages = int(re.search(r"Pages:\s+(\d+)", info).group(1)) if "Pages:" in info else 0
    return open(raw, encoding="utf-8", errors="replace").read(), pages


TOP = ("INDICATIONS AND USAGE|DOSAGE AND ADMINISTRATION|DOSAGE FORMS AND STRENGTHS|CONTRAINDICATIONS|WARNINGS AND PRECAUTIONS|"
       "ADVERSE REACTIONS|DRUG INTERACTIONS|USE IN SPECIFIC POPULATIONS|OVERDOSAGE|DESCRIPTION|CLINICAL PHARMACOLOGY|"
       "NONCLINICAL TOXICOLOGY|CLINICAL STUDIES|REFERENCES|HOW SUPPLIED|PATIENT COUNSELING INFORMATION")
OLD = "INDICATIONS AND USAGE|DOSAGE AND ADMINISTRATION|CONTRAINDICATIONS|WARNINGS|PRECAUTIONS|ADVERSE REACTIONS|OVERDOSAGE|CLINICAL PHARMACOLOGY|DESCRIPTION|DRUG INTERACTIONS"
_TITLE_WORD = r"(?:[A-Z][A-Za-z0-9()/\-]*|and|of|in|for|to|the|with|or|on|due|who|by|from|at)"


def mark_headings(text):
    """pdftotext keeps headings inline ("2 DOSAGE AND ADMINISTRATION 2.1 Important ..."), so line-based detection missed them
    (every 2023 Zocor chunk was tagged FRONT). Insert blank lines around headings found inline:
    top-level PLR headings (number + known upper-case title), subsections N.k that continue the 1, 2, 3 sequence of the
    current top-level section (so table cells such as '6.6 Abdominal pain' are not taken), and old-format upper-case headings."""
    flat = re.sub(r"\s+", " ", text)
    plr = bool(re.search(r"\d{1,2} (?:%s)" % TOP, flat))
    out, pos, cur, last_k = [], 0, None, 0
    pat = (re.compile(r"(?<![\d.%%-])(\d{1,2}) (%s)(?![A-Za-z])|(?<![\d.%%-])(\d{1,2})\.(\d{1,2}) (%s(?: %s){0,6})" % (TOP, _TITLE_WORD, _TITLE_WORD)) if plr
           else re.compile(r"(?<![A-Za-z(])(%s)(?![A-Za-z])" % OLD))
    for m in pat.finditer(flat):
        if plr:
            if m.group(1):
                cur, last_k, title = m.group(1), 0, f"{m.group(1)} {m.group(2)}"
            else:
                if m.group(3) != cur or int(m.group(4)) != last_k + 1:
                    continue
                last_k = int(m.group(4))
                title = f"{m.group(3)}.{m.group(4)} {m.group(5).strip()}"
                # keep only the title words: the regex may swallow the first words of the body, which is harmless for the tag
            end = m.end() if m.group(1) else m.start(5)  # subsection: the title words stay in the body too
        else:
            title, end = m.group(1), m.end()
        out.append(flat[pos:m.start()])
        out.append("\n\n" + title + "\n\n")
        pos = end
    out.append(flat[pos:])
    return "".join(out)


def chunk_label(text, max_chars=1100):
    """Split on section headings, then paragraphs (blank lines); merge small paragraphs up to max_chars."""
    text = mark_headings(text)
    chunks, section, buf = [], "FRONT", []
    def flush():
        body = re.sub(r"\s+", " ", " ".join(buf)).strip()
        if len(body) > 60:
            chunks.append((section, body))
        buf.clear()
    for para in re.split(r"\n\s*\n", text):
        lines = [l for l in para.split("\n") if l.strip()]
        if not lines:
            continue
        first = lines[0].strip()
        m = HEAD.match(first)
        if len(lines) <= 2 and (m or first.upper() in KNOWN):
            flush(); section = first[:70]
            lines = lines[1:]
            if not lines:
                continue
        p = re.sub(r"\s+", " ", " ".join(lines))
        for piece in re.split(rf"(?<=[.;:])\s+(?=[A-Z(])", p) if len(p) > max_chars else [p]:  # long paragraph: sentence pieces
            if sum(len(b) for b in buf) + len(piece) > max_chars:
                flush()
            buf.append(piece)
    flush()
    return chunks


def build(drugs):
    from fastembed import TextEmbedding
    os.makedirs(RAG_DIR, exist_ok=True)
    rows, report = [], []
    for drug in drugs:
        for nda in NDAS[drug]:
            for date, url in label_docs(nda):
                text, pages = raw_text(url)
                ch = chunk_label(text)
                cpp = len(text) / max(pages, 1)
                report.append({"drug": drug, "date": date, "file": url.split("/")[-1], "pages": pages, "chars": len(text),
                               "chars_per_page": round(cpp), "scanned_suspect": cpp < 300, "chunks": len(ch)})
                print(drug, date, url.split("/")[-1], "pages", pages, "chars/page", round(cpp), "chunks", len(ch), flush=True)
                for sec, body in ch:
                    rows.append({"drug": drug, "label_date": date, "file": url.split("/")[-1], "section": sec, "text": body})
    t0 = time.time()
    model = TextEmbedding(MODEL)
    t_load = time.time() - t0
    t0 = time.time()
    docs = [f"{r['section']}. {r['text']}" for r in rows]
    uniq = sorted(set(docs))  # identical paragraphs recur across label versions: embed each once
    print(f"{len(rows)} chunks, {len(uniq)} unique", flush=True)
    uvec = np.array(list(model.embed(uniq, batch_size=32)), dtype="float32")
    pos = {d: i for i, d in enumerate(uniq)}
    vecs = uvec[[pos[d] for d in docs]]
    t_embed = time.time() - t0
    np.save(f"{RAG_DIR}/vectors.npy", vecs)
    with open(f"{RAG_DIR}/chunks.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    json.dump(report, open(f"{RAG_DIR}/label_report.json", "w"), indent=1)
    size = sum(os.path.getsize(f"{RAG_DIR}/{n}") for n in ("vectors.npy", "chunks.jsonl"))
    print(f"\n{len(rows)} chunks from {len(report)} labels; model load {t_load:.1f}s, embed {t_embed:.1f}s "
          f"({1000 * t_embed / len(uniq):.1f} ms/unique chunk); index {size / 1e6:.1f} MB", flush=True)
    json.dump({"chunks": len(rows), "labels": len(report), "model_load_s": t_load, "embed_s": t_embed, "unique_chunks": len(uniq), "index_mb": size / 1e6},
              open(f"{RAG_DIR}/build_stats.json", "w"))


def add_latest(drug):
    """Append only the newest label of `drug` to the existing index (cheap way to add a drug for a quick test)."""
    from fastembed import TextEmbedding
    date, url = label_docs(NDAS[drug][0])[-1]
    text, pages = raw_text(url)
    new = [{"drug": drug, "label_date": date, "file": url.split("/")[-1], "section": sec, "text": body} for sec, body in chunk_label(text)]
    t0 = time.time()
    vecs = np.array(list(TextEmbedding(MODEL).embed([f"{r['section']}. {r['text']}" for r in new], batch_size=32)), dtype="float32")
    old = np.load(f"{RAG_DIR}/vectors.npy")
    np.save(f"{RAG_DIR}/vectors.npy", np.vstack([old, vecs]))
    with open(f"{RAG_DIR}/chunks.jsonl", "a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r) + "\n")
    print(f"added {len(new)} chunks of {drug} label {date} in {time.time() - t0:.0f}s")


_cache = {}
def load():
    if not _cache:
        from fastembed import TextEmbedding
        _cache["model"] = TextEmbedding(MODEL)
        _cache["vecs"] = np.load(f"{RAG_DIR}/vectors.npy")
        _cache["rows"] = [json.loads(l) for l in open(f"{RAG_DIR}/chunks.jsonl", encoding="utf-8")]
    return _cache


def _tok(t):
    return re.findall(r"[a-z0-9]+", t.lower())


def _bm25(texts, query, k1=1.5, b=0.75):
    """Plain BM25 over the candidate chunks (one label version, so it is small)."""
    docs = [_tok(t) for t in texts]
    avg = sum(len(d) for d in docs) / max(len(docs), 1)
    n = len(docs)
    scores = np.zeros(n)
    for term in set(_tok(query)):
        df = sum(1 for d in docs if term in d)
        if not df:
            continue
        idf = np.log(1 + (n - df + 0.5) / (df + 0.5))
        for i, d in enumerate(docs):
            f = d.count(term)
            if f:
                scores[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg))
    return scores


def search(drug, query, cutoff, k=5):
    """Time-aware hybrid retrieval: only the label version in force at `cutoff` (latest label_date <= cutoff, YYYY-MM-DD).
    Dense (bge-small cosine) and BM25 rankings are fused with reciprocal rank fusion. Returns (label_date, [(score, chunk)])."""
    c = load()
    cut = cutoff.replace("-", "")
    dates = sorted({r["label_date"] for r in c["rows"] if r["drug"] == drug and r["label_date"] <= cut})
    if not dates:
        return None, []
    in_force = dates[-1]
    idx = [i for i, r in enumerate(c["rows"]) if r["drug"] == drug and r["label_date"] == in_force]
    q = np.array(list(c["model"].embed([QPREFIX + query])), dtype="float32")[0]
    sims = c["vecs"][idx] @ q / (np.linalg.norm(c["vecs"][idx], axis=1) * np.linalg.norm(q))
    lex = _bm25([c["rows"][i]["section"] + " " + c["rows"][i]["text"] for i in idx], query)
    rank_d = np.empty(len(idx)); rank_d[np.argsort(-sims)] = np.arange(len(idx))
    rank_l = np.empty(len(idx)); rank_l[np.argsort(-lex)] = np.arange(len(idx))
    fused = 1 / (60 + rank_d) + 1 / (60 + rank_l)
    order = np.argsort(-fused)[:k]
    return in_force, [(float(fused[o]), c["rows"][idx[o]]) for o in order]


if __name__ == "__main__":
    if sys.argv[1] == "build":
        build(sys.argv[2:])
    elif sys.argv[1] == "add":
        add_latest(sys.argv[2])


def test():
    """Task 3 step 4: time-aware retrieval checks. Returns list of result dicts and prints them."""
    out = []
    cases = [("atorvastatin", "diabetes, increased blood glucose, HbA1c", r"increases in hba1c and fasting serum glucose",
              [("2011-12-31", "must NOT retrieve the glucose warning"), ("2013-12-31", "glucose warning should be rank 1")]),
             ("gabapentin", "drug reaction with eosinophilia and systemic symptoms, DRESS, multiorgan hypersensitivity",
              r"\bDRESS\b|eosinophilia and systemic symptoms",
              [("2010-12-31", "must NOT retrieve DRESS"), ("2011-12-31", "DRESS should be rank 1")])]
    for drug, query, rx, cutoffs in cases:
        for cutoff, expect in cutoffs:
            in_force, hits = search(drug, query, cutoff, k=5)
            ranks = [i + 1 for i, (_, r) in enumerate(hits) if re.search(rx, r["text"], re.I)]
            print(f"\n{drug} cutoff {cutoff}: label in force {in_force}; target ranks in top 5: {ranks}  ({expect})")
            for i, (sc, r) in enumerate(hits, 1):
                print(f"  {i}. {sc:.3f} [{r['section'][:40]}] {r['text'][:130]}")
            out.append({"drug": drug, "cutoff": cutoff, "label_in_force": in_force, "target_ranks_top5": ranks, "expect": expect})
    json.dump(out, open(f"{RAG_DIR}/retrieval_test.json", "w"), indent=1)


if __name__ == "__main__" and sys.argv[1] == "test":
    test()
