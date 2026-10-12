"""Brief writer agent (Build Step 6). Turns the recommended signals into a short brief for a human reviewer.
The LLM only writes short factual claims (one forced, strict tool call per recommended term). CODE then:
  - removes every claim that has no valid citation: a citation is an evidence ID that exists in that term's packet, or a label quote that
    is verbatim in the retrieved label passages. IDs that do not exist are dropped from a claim; a claim left with none is removed;
  - writes the fixed parts itself (headline numbers, label status with its quote, Skeptic scores and flags, the disclaimer).
Model: PIPELINE_MODEL (Haiku) by default; the demo setting passes canary_config.DEMO_BRIEF_MODEL (Sonnet). Cached by term, model,
prompt version and the evidence hash. Every brief says "research prototype, not medical advice".
"""
import hashlib, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anthropic
import canary_config
from agents import parallel
from agents.skeptic import render, BRANCHES
from canary_utils import CACHE_DIR, is_fresh, load_env
from label_checker import PRICES

load_env()
PROMPT_VERSION = "writer-v1"
DISCLAIMER = ("Research prototype, not medical advice. These are unverified statistical signals from public FDA adverse event reports. "
              "They show reporting patterns, not proven causes, and every finding needs human review.")

SYSTEM = ("You write short, factual evidence summaries for a drug safety reviewer. You use only the numbered evidence items you are given. "
          "Record the summary by calling the record_brief tool.")

INSTRUCTIONS = """Drug: {drug}. Reaction: {term}. Data known before {date}.

The Skeptic's judgement (for context): winner {winner}; scores real_effect {s_real:.2f}, reporting_spike {s_spike:.2f}, confounding {s_conf:.2f}.

Write 3 to 5 short factual claims for the reviewer. Rules:
- Every claim must cite at least one evidence ID from the blocks above in cited_ids (for example "R1", "S3"), or quote the label verbatim in label_quote (the quote must appear in the evidence). A claim with no valid citation will be deleted.
- Use only the evidence items. No outside knowledge, no advice about treatment, and no recommendation to change a label.
- Include the strongest argument against a real effect (the explanation with the highest losing score), and say plainly what is uncertain.
- A PubMed paper may be described as supporting only if its title is about this drug, or its class, causing this reaction; otherwise describe it neutrally.
- One sentence per claim. Leave label_quote as an empty string when you do not quote the label."""

TOOL = {"name": "record_brief", "description": "Record the short list of cited claims.", "strict": True,
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["claims"], "properties": {
            "claims": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                  "required": ["text", "cited_ids", "label_quote"],
                                                  "properties": {"text": {"type": "string"},
                                                                 "cited_ids": {"type": "array", "items": {"type": "string"}},
                                                                 "label_quote": {"type": "string"}}}}}}}


def _norm(t):
    return re.sub(r"\s+", " ", t.lower()).strip()


_NUM = re.compile(r"\d+(?:\.\d+)?")
_ID = re.compile(r"\b[RSCX]\d+\b")


def numbers(text):
    """Numeric tokens of a text, ignoring evidence IDs (R1, S3) and thousands separators (24,849 = 24849)."""
    return set(_NUM.findall(re.sub(r"(?<=\d),(?=\d{3})", "", _ID.sub(" ", text))))


def validate_claims(claims, packet, label_texts):
    """The writer rules. Returns (kept, removed). A claim is kept only if (1) it has at least one real evidence ID or a verbatim label quote,
    and (2) every number in its text appears in the evidence packet (so the model cannot write a figure that is not in the evidence)."""
    kept, removed = [], []
    corpus = _norm(" ".join(label_texts))
    allowed = numbers(" ".join(packet["items"].values()))
    for c in claims or []:
        ids = [i for i in c.get("cited_ids", []) if i in packet["items"]]
        quote = (c.get("label_quote") or "").strip()
        quote_ok = bool(quote) and _norm(quote) in corpus
        if not numbers(c.get("text", "")) <= allowed:
            removed.append({**c, "removed_because": "number not in the evidence"})
            continue
        if (ids or quote_ok) and c.get("text", "").strip():
            kept.append({"text": c["text"].strip(), "cited_ids": ids, "label_quote": quote if quote_ok else ""})
        else:
            removed.append(c)
    return kept, removed


def write_claims(client, drug, date, sk, label_chunks, model):
    packet = sk["evidence"]
    prompt = render(packet) + "\n\n" + INSTRUCTIONS.format(
        drug=drug, term=sk["term"], date=date, winner=sk["winner"], s_real=sk["branches"]["real_effect"]["score"],
        s_spike=sk["branches"]["reporting_spike"]["score"], s_conf=sk["branches"]["confounding"]["score"])
    key = hashlib.md5(json.dumps([PROMPT_VERSION, model, sk["term"], sk["evidence_hash"], SYSTEM, prompt, TOOL], sort_keys=True).encode()).hexdigest()
    path = os.path.join(CACHE_DIR, f"wr_{key}.json")
    if os.path.exists(path) and not is_fresh(client):
        return {**json.load(open(path, encoding="utf-8")), "cached": True, "cost": 0.0}
    kw = dict(model=model, max_tokens=1500, system=SYSTEM, tools=[TOOL], tool_choice={"type": "tool", "name": TOOL["name"]},
              messages=[{"role": "user", "content": prompt}])
    if model == canary_config.PIPELINE_MODEL:
        kw["extra_body"] = {"temperature": 0}  # Haiku accepts it; Sonnet 5.5 rejects non-default sampling
    else:  # Sonnet 5.5 also rejects a forced tool_choice: ask for the tool in words and retry once if it is not called
        kw["tool_choice"] = {"type": "auto"}
        kw["messages"] = [{"role": "user", "content": prompt + "\n\nRespond only by calling the record_brief tool."}]
    in_tok = out_tok = 0
    call = None
    for attempt in (1, 2):
        r = client.messages.create(**kw)
        in_tok += r.usage.input_tokens; out_tok += r.usage.output_tokens
        call = next((b for b in r.content if b.type == "tool_use"), None)
        if call is not None:
            break
    out = {"claims": (call.input or {}).get("claims", []) if call is not None else [], "model": model}
    os.makedirs(CACHE_DIR, exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"))
    pin, pout = PRICES[model]
    return {**out, "cached": False, "cost": (in_tok * pin + out_tok * pout) / 1e6}


def cite(ids):
    return " [" + ", ".join(ids) + "]" if ids else ""


def term_section(sk, claims, lab, rank=None, n_rec=None, featured=False, tag="demo highlight"):
    """The fixed, code-written parts plus the validated claims for one recommended term."""
    ev, b = sk["evidence"]["items"], sk["branches"]
    quote = f' "{lab["evidence"]}"' if lab.get("evidence") else ""
    status = ("partially labeled" if sk["label_decision"] == "partial" else "not on the label") + f" (label of {sk['label_date']}){quote}"
    lines = [f"### {sk['term'].title()}" + (f" ({tag})" if featured else "")]
    if rank:
        lines.append(f"- Rank: #{rank} of {n_rec} recommended terms by chi-squared" + (f". Written up as a {tag}; its position is not promoted." if featured else ""))
    lines += [f"- Data: {ev['R1']} [R1]", f"- Label check: {status} [R2]",
             f"- Skeptic: winner {sk['winner'].replace('_', ' ')} (real effect {b['real_effect']['score']:.2f}, "
             f"reporting spike {b['reporting_spike']['score']:.2f}, confounding {b['confounding']['score']:.2f})"
             + (f". Flag: {sk['flag']}" if sk["flag"] else "")]
    lines += [f"- {c['text']}{cite(c['cited_ids'])}" + (f' Label quote: "{c["label_quote"]}"' if c["label_quote"] else "") for c in claims]
    return "\n".join(lines)


def run(skeptic_out, label_out, miner_out, client=None, model=canary_config.PIPELINE_MODEL, max_briefs=None, mode_note="", featured=(), tag="demo highlight"):
    """featured: terms written up in full IN ADDITION to the top max_briefs, keeping their real chi-squared rank (not moved up the list).
    tag names them in the brief: "demo highlight" in the demos, "target term" in the evaluation (the case's own term list)."""
    drug, date = skeptic_out["drug"], skeptic_out["date"]
    out = {"agent": "writer", "drug": drug, "date": date, "model": model, "disclaimer": DISCLAIMER, "prompt_version": PROMPT_VERSION}
    results = skeptic_out.get("results", [])
    rec = [r for r in results if r["decision"] == "recommended"]
    chi = {t["term"]: t["chi2"] or 0 for t in miner_out["terms"]}
    rec.sort(key=lambda r: -chi.get(r["term"], 0))
    dismissed = [r for r in results if r["decision"] == "dismissed"]
    labs = {r["term"]: r for r in label_out["results"]}
    client = client or anthropic.Anthropic()
    feat = set(featured)
    rank = {r["term"]: i + 1 for i, r in enumerate(rec)}
    if max_briefs:
        full = [r for i, r in enumerate(rec) if i < max_briefs or r["term"] in feat]  # real order (chi-squared); highlights are not promoted
        rest = [r for r in rec if r not in full]
    else:
        full, rest = rec, []
    sections, term_out, spend = [], [], 0.0
    writable = [sk for sk in full if sk["branches"]]  # an invalid Skeptic output gets no claims; the reviewer is told

    def write_one(i, cl):
        sk = writable[i]
        if hasattr(cl, "set_context"):
            cl.set_context(stage="writer", term=sk["term"])
        return write_claims(cl, drug, date, sk, labs[sk["term"]]["chunks"], model)

    if hasattr(client, "map"):  # the evaluation sends these calls as one batch (batching.BatchRunner)
        written = client.map(write_one, range(len(writable)))
    else:
        written = parallel.pmap(lambda i: write_one(i, client), range(len(writable)))
    by_term = {sk["term"]: w for sk, w in zip(writable, written)}
    for sk in full:
        lab = labs[sk["term"]]
        is_feat = sk["term"] in feat
        if not sk["branches"]:
            sections.append(f"### {sk['term'].title()}\n- Data: {sk['evidence']['items']['R1']} [R1]\n- Skeptic output was invalid, so this term needs manual review. Flag: {sk['flag']}")
            term_out.append({"term": sk["term"], "claims": [], "removed_uncited": 0, "cost": 0.0, "cached": True, "rank": rank[sk["term"]], "featured": is_feat})
            continue
        w = by_term[sk["term"]]
        spend += w["cost"]
        texts = [c["text"] for c in lab["chunks"]] + [lab.get("evidence", "")]
        kept, removed = validate_claims(w["claims"], sk["evidence"], texts)
        sections.append(term_section(sk, kept, lab, rank[sk["term"]], len(rec), is_feat, tag))
        term_out.append({"term": sk["term"], "claims": kept, "removed_uncited": len(removed), "cost": w["cost"], "cached": w["cached"],
                         "rank": rank[sk["term"]], "featured": is_feat})
    head = [f"# Canary brief: {drug}, data before {date}" + (f" ({mode_note})" if mode_note else ""),
            f"*{DISCLAIMER}*", "",
            f"Terms examined by the Skeptic: {len(results)}. Recommended for human review: {len(rec)}. Dismissed with a cited reason: {len(dismissed)}.", ""]
    if not rec:
        head.append("No term was recommended.")
    body = "\n\n".join(sections)
    tail = []
    if rest:
        tail += ["", f"## Also recommended, not written up ({len(rest)})", ", ".join(r["term"].title() for r in rest)]
    if dismissed:
        tail += ["", f"## Dismissed by the Skeptic ({len(dismissed)})"] + [
            f"- {r['term'].title()}{f' ({tag})' if r['term'] in feat else ''}: {r['winner'].replace('_', ' ')}. {r['reason']}" + cite([c['id'] for c in r['cited']]) for r in dismissed]
    not_examined = [t for t in feat if t not in {r["term"] for r in results}]
    for t in not_examined:  # a highlighted or target term that never reached the Skeptic is said so plainly
        lab = labs.get(t)
        tail += ["", f"{tag.capitalize()} {t.title()} was not weighed by the Skeptic: " + (
            "the Label Checker judged it already on the label." if lab and lab["route"] == "dropped_on_label" else "it was outside the review cap or not flagged.")]
    tail += ["", f"*{DISCLAIMER}*"]
    md = "\n".join(head) + "\n" + body + "\n".join(tail)
    return {**out, "status": "ok", "brief_markdown": md, "terms": term_out, "n_recommended": len(rec), "n_written": len(full),
            "mode": mode_note, "featured": sorted(feat), "n_dismissed": len(dismissed), "removed_uncited": sum(t["removed_uncited"] for t in term_out), "spend": round(spend, 4)}
