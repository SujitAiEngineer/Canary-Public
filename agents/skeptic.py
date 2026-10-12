"""Skeptic agent (Build Step 5): Tree-of-Thought, Design B exactly as locked in EVALUATION_PLAN.md section 5b.
1. CODE computes the evidence (no LLM) for three branches, each item with an evidence ID, plus a neutral context field:
     R  real effect         R1 statistics, R2 label status + quote, R3 PubMed count, R4-R6 top 3 titles (adverse-effect wording, before the cutoff)
     S  reporting spike     S1 drug cases by year, S2 term cases by year, S3 peak-year concentration
     C  confounding         C1 case count, C2.. indications listed for any drug in the drug+term cases, with the baseline share
     X  context (neutral)   X1.. the same term's PRR for the class comparators at the same cutoff (visible to all branches)
2. ONE forced-tool Haiku call per term scores each branch separately (0-1, one-sentence reason, cited IDs) and picks a winner.
3. CODE sets the verdict: only a "real_effect" winner is recommended. A dismissal (winner reporting_spike or confounding) is valid only if it
   cites at least one item from the winning branch's OWN block; otherwise the term is NOT dismissed and is recommended with the flag
   "uncertain: comparator context only". Comparator context alone can never dismiss a signal.
Every call is cached by term, model, prompt version and a hash of the evidence packet.
Run: PYTHONIOENCODING=utf-8 python -u agents/skeptic.py atorvastatin 2011-12-31   (development drugs only)
"""
import hashlib, json, os, re, sys, threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anthropic
import duckdb
import eval_config
import miner as core_miner
from agents import miner as miner_agent
from agents import parallel
from canary_utils import CACHE_DIR, MODEL_CHEAP, get_json, is_fresh, load_env
from net_retry import retry_connection
from label_checker import PRICES
from triage import fold

load_env()
PROMPT_VERSION = "skeptic-v2"
_PM_LOCK = threading.Lock()
EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BRANCHES = {"real_effect": "R", "reporting_spike": "S", "confounding": "C"}
UNKNOWN_INDICATION = r"UNKNOWN|NOT REPORTED|NO INDICATION|INDICATION NOT"
N_INDICATIONS = 8

# Class comparators. Evaluation drugs: eval_config.COMPARATORS (locked). Development and demo drugs: chosen by class from names only,
# no counts looked at, none of them an evaluation drug.
COMPARATORS = {**eval_config.COMPARATORS, **eval_config.DEV_COMPARATORS,
               "simvastatin": {"atorvastatin": r"atorva|lipitor", "rosuvastatin": r"rosuvastatin|crestor", "pravastatin": r"pravastatin|pravachol"},
               "linezolid": {"vancomycin": r"vancomy|vancocin", "daptomycin": r"daptomycin|cubicin", "tedizolid": r"tedizolid|sivextro"},
               "lithium": {"valproate": r"valpro|depakote|divalproex", "carbamazepine": r"carbamazepine|tegretol"},
               "ixazomib": {"bortezomib": r"bortezomib|velcade", "carfilzomib": r"carfilzomib|kyprolis"}}

SYSTEM = ("You are a skeptical pharmacovigilance scientist. A drug-reaction pair is reported to FDA more often than expected. You must decide "
          "which of three explanations best accounts for it, using only the numbered evidence items you are given. "
          "Record your decision by calling the record_skeptic_verdict tool, and always fill in all four fields: real_effect, reporting_spike, confounding and winner. Before you write any reason, decide which explanation wins and which losing explanation scores highest; "
          "the reason for the WINNING explanation must then say, in its own sentence, what you weighed in that losing explanation's block and why it accounts for the signal less well.")

INSTRUCTIONS = """Drug: {drug}. Reaction (MedDRA preferred term): {term}. Data known before {date}.

Three explanations, each with its own evidence block (R, S, C), plus a neutral CONTEXT block (X) that every explanation may refer to:
- real_effect: the drug really causes this reaction (block R).
- reporting_spike: the reports come from a burst of reporting in particular years, not a steady rate (block S).
- confounding: the reaction is due to the patients' underlying disease or indication, not the drug (block C).

For EACH explanation give a score from 0 to 1 (how well that explanation alone accounts for the signal), a one-sentence reason, and the IDs of the
evidence items you rely on. Cite only IDs that exist in the blocks above. Cite IDs from an explanation's own block for that explanation;
context items (X) may be cited as extra support, but an explanation that is rejected must be rejected on the strength of its own block.
Then name the winner: the explanation that best accounts for the signal. If the evidence does not clearly support a spike or confounding,
the winner is real_effect. Do not use outside knowledge about the drug as evidence.

Two further rules:
1. The reason you give for the winner must explicitly address the evidence of the highest-scoring losing explanation: say what in that block you weighed and why it does not account for the signal better. If two explanations tie, address the other one.
2. A PubMed paper (items R4 onward) may be described as "supporting" only if its title is about this drug, or its drug class, causing this reaction. A paper on something else (another drug, animals, a disease population, efficacy) must be described neutrally, for example "a paper on X", and must not be counted as support."""

TOOL = {
    "name": "record_skeptic_verdict",
    "description": "Record the score, reason and cited evidence IDs for each of the three explanations, and the winner.",
    "input_schema": {"type": "object", "properties": {
        **{b: {"type": "object", "properties": {
            "score": {"type": "number", "description": "0 to 1"},
            "reason": {"type": "string", "description": "One sentence."},
            "cited_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["score", "reason", "cited_ids"]} for b in BRANCHES},
        "winner": {"type": "string", "enum": list(BRANCHES)}},
        "required": [*BRANCHES, "winner"]}}
# Strict tool use guarantees all four fields are returned (without it, Haiku sometimes returned only the first branch).
# Strict mode does not support minimum/maximum, so the 0-1 score range is checked in code (_complete).
TOOL["strict"] = True
def _closed(o):
    if o.get("type") == "object":
        o["additionalProperties"] = False
        for v in o["properties"].values():
            _closed(v)
_closed(TOOL["input_schema"])


_US = [("haem", "hem"), ("aem", "em"), ("oed", "ed"), ("oea", "ea")]  # UK to US spelling (the MedDRA PTs are UK spelled)


def pubmed_query(drug, term):
    phrase = term.lower().replace("'", " ").strip()
    us = phrase
    for a, b in _US:
        us = us.replace(a, b)
    phrases = [phrase] + ([us] if us != phrase else [])
    return (f'{drug}[Title/Abstract] AND (' + " OR ".join(f'"{p}"[Title/Abstract]' for p in phrases) + ') AND '
            '(adverse[Title/Abstract] OR toxicity[Title/Abstract] OR induced[Title/Abstract] OR "adverse effects"[Subheading])')

# ---------------------------------------------------------------- Step 1: evidence (code only)

class Evidence:
    """Opens the store once per (drug, cutoff, serious flag) and builds each term's evidence packet."""

    def __init__(self, miner_out, label_by_term, db=core_miner.DB):
        self.m, self.labels = miner_out, label_by_term
        self.drug, self.date = miner_out["drug"], miner_out["date"]
        self.qcut, self.serious = miner_out["quarter_index"], miner_out["serious_only"]
        self.cutoff_year = int(self.date[:4])
        self.con = duckdb.connect(db, read_only=True)
        regex, exclude, _ = miner_agent.drug_pattern(self.drug)
        core_miner.mine(self.con, regex, self.qcut, serious_only=self.serious, min_a=10 ** 9, exclude=exclude)  # builds temp tables latest, drug_rids
        self.con.execute("CREATE TEMP TABLE drug_reac AS SELECT DISTINCT rid, pt FROM reac WHERE rid IN (SELECT rid FROM drug_rids)")
        self.con.execute("CREATE TEMP TABLE drug_indi AS SELECT DISTINCT rid, indi_pt FROM indi WHERE rid IN (SELECT rid FROM drug_rids)")
        self.n_drug = self.con.execute("SELECT count(*) FROM drug_rids").fetchone()[0]
        self.base_indi = dict(self.con.execute(f"SELECT indi_pt, count(*) FROM drug_indi WHERE NOT regexp_matches(indi_pt, '{UNKNOWN_INDICATION}') GROUP BY indi_pt").fetchall())
        self.by_term = {t["term"]: t for t in miner_out["terms"]}
        self.context = self._comparators(regex_only=exclude)

    def _comparators(self, regex_only):
        """Neutral context: per comparator drug, the PRR table of every term at the same cutoff (mined once, looked up per term)."""
        out = {}
        for name, rx in COMPARATORS.get(self.drug, {}).items():
            df, n_total, n = core_miner.mine(self.con, rx, self.qcut, serious_only=self.serious, min_a=1, exclude=regex_only)
            out[name] = (n, df.set_index("pt"))
        # the comparators' mine() calls replaced the temp tables: rebuild the focus drug's
        regex, exclude, _ = miner_agent.drug_pattern(self.drug)
        core_miner.mine(self.con, regex, self.qcut, serious_only=self.serious, min_a=10 ** 9, exclude=exclude)
        return out

    def pubmed(self, term):
        """Count of papers on drug + the reaction term as an EXACT PHRASE (US and UK spellings), with adverse-effect wording, published on or
        before the cutoff date, and the top 3 titles. (The first version ANDed the words of the term, which matched unrelated papers.)"""
        q = pubmed_query(self.drug, term)
        p = {"db": "pubmed", "term": q, "retmode": "json", "retmax": 3, "datetype": "pdat", "maxdate": self.date.replace("-", "/"),
             "mindate": "1900/01/01", "sort": "relevance"}
        with _PM_LOCK:  # one PubMed request at a time (get_json adds its polite delay), whatever the number of workers
            # a dropped connection is retried (3 attempts, short backoff) with the SAME request; if it still fails it raises and the run stops
            r = retry_connection(get_json, f"{EU}/esearch.fcgi", p, what="PubMed esearch").get("esearchresult", {})
            ids = r.get("idlist", [])
            docs = retry_connection(get_json, f"{EU}/esummary.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
                                    what="PubMed esummary").get("result", {}) if ids else {}
        return int(r.get("count", 0)), [(i, ("e-published " + docs[i]["epubdate"]) if docs[i].get("epubdate") else docs[i]["pubdate"], docs[i]["title"]) for i in ids if i in docs], q

    def packet(self, term, pm=None):
        """Returns {"items": {id: text}, "blocks": {"R": [...ids], "S": [...], "C": [...], "X": [...]}} for one term."""
        t, lab = self.by_term[term], self.labels[term]
        items, blocks = {}, {"R": [], "S": [], "C": [], "X": []}

        def add(block, text):
            i = f"{block}{len(blocks[block]) + 1}"
            items[i] = text
            blocks[block].append(i)

        # R: real effect
        add("R", f"Disproportionality for {self.drug} at {self.date}: {t['a']} reports with this term out of {self.n_drug} {self.drug} reports; "
                 f"PRR {t['PRR']:.2f}, chi-squared {t['chi2']:.1f}, Evans criteria {'met' if t['evans'] else 'not met'}.")
        quote = f' Quoted label sentence: "{lab["evidence"]}"' if lab.get("evidence") else ""
        note = "partially labeled (a related, narrower or broader reaction is on the label)" if lab["decision"] == "partial" else "not on the label"
        aka = f" Retrieval also searched the MeSH synonyms: {'; '.join(lab['mesh_synonyms'])}." if lab.get("mesh_synonyms") else ""
        add("R", f"Label check against the label in force ({lab['label_date']}): {note}.{quote}{aka}")
        n, top, query = pm if pm is not None else self.pubmed(term)
        add("R", f"PubMed: {n} papers on {self.drug} with this reaction and adverse-effect wording, published before {self.date}.")
        for pmid, pdate, title in top:
            add("R", f"PubMed paper PMID {pmid} ({pdate}): {title}")
        # S: reporting spike
        by_year, ty = self.m["drug_cases_by_year"], t["yearly"]
        years = sorted(by_year, key=int)
        add("S", f"{self.drug} cases by year of first receipt: " + ", ".join(f"{y}: {by_year[y]}" for y in years))
        add("S", "Cases with this reaction by year: " + (", ".join(f"{y}: {ty.get(y, 0)}" for y in years) or "none"))
        if ty:
            peak = max(sorted(ty, key=int), key=lambda y: ty[y])  # ties go to the earliest year, so the packet is deterministic
            tot_t, tot_d = sum(ty.values()), sum(by_year.values())
            add("S", f"Peak year {peak}: {ty[peak]} of the {tot_t} reaction cases ({100 * ty[peak] / tot_t:.0f}%) arrived that year, "
                     f"while that year holds {100 * by_year[peak] / tot_d:.0f}% of all {self.drug} cases.")
        # C: confounding by indication
        ind = self.con.execute(f"""
            WITH tc AS (SELECT rid FROM drug_reac WHERE pt = ?)
            SELECT indi_pt, count(DISTINCT rid) FROM drug_indi WHERE rid IN (SELECT rid FROM tc)
              AND NOT regexp_matches(indi_pt, '{UNKNOWN_INDICATION}') GROUP BY indi_pt ORDER BY 2 DESC, 1 LIMIT {N_INDICATIONS}""", [term]).fetchall()
        add("C", f"{t['a']} {self.drug} cases carry this reaction. Indications listed for any drug in those cases (n cases, share of the {t['a']} cases, "
                 f"and the share of all {self.n_drug} {self.drug} cases for comparison) follow.")
        for name, n_ind in ind:
            add("C", f"Indication {name}: {n_ind} cases ({100 * n_ind / t['a']:.0f}% of reaction cases); baseline {100 * self.base_indi.get(name, 0) / self.n_drug:.0f}% of all {self.drug} cases.")
        if not ind:
            add("C", "No indication is recorded for these cases.")
        # X: neutral context
        for name, (n, df) in self.context.items():
            if term in df.index:
                x = df.loc[term]
                add("X", f"Class comparator {name} at the same cutoff: {int(x.a)} of {n} reports with this reaction, PRR {x.PRR:.2f}, chi-squared {x.chi2:.1f}, Evans {'met' if x.evans else 'not met'}.")
            else:
                add("X", f"Class comparator {name} at the same cutoff: no reports with this reaction among {n} reports.")
        if not self.context:
            add("X", "No class comparators are defined for this drug.")
        return {"items": items, "blocks": blocks}


def render(packet):
    names = {"R": "BLOCK R: real effect", "S": "BLOCK S: reporting spike", "C": "BLOCK C: confounding by indication", "X": "CONTEXT X (neutral, for all explanations)"}
    return "\n\n".join(f"{names[b]}\n" + "\n".join(f"[{i}] {packet['items'][i]}" for i in ids) for b, ids in packet["blocks"].items())


# ---------------------------------------------------------------- Step 2: one forced-tool call per term (cached)

def _complete(v):
    return isinstance(v, dict) and v.get("winner") in BRANCHES and all(isinstance(v.get(b), dict) and {"score", "reason", "cited_ids"} <= v[b].keys() and isinstance(v[b]["score"], (int, float)) and 0 <= v[b]["score"] <= 1 for b in BRANCHES)


def judge(client, drug, term, date, packet, model=MODEL_CHEAP):
    prompt = render(packet) + "\n\n" + INSTRUCTIONS.format(drug=drug, term=term, date=date)
    ehash = hashlib.md5(json.dumps(packet, sort_keys=True).encode()).hexdigest()
    key = hashlib.md5(json.dumps([PROMPT_VERSION, model, term, ehash, SYSTEM, prompt, TOOL], sort_keys=True).encode()).hexdigest()
    path = os.path.join(CACHE_DIR, f"sk_{key}.json")
    if os.path.exists(path) and not is_fresh(client):
        hit = json.load(open(path, encoding="utf-8"))
        if _complete(hit["verdict"]):  # an incomplete (truncated) verdict from an earlier call is never reused
            return {**hit, "cached": True, "cost": 0.0, "evidence_hash": ehash}
    kw = dict(model=model, max_tokens=1800, system=SYSTEM, tools=[TOOL], tool_choice={"type": "tool", "name": TOOL["name"]},
              messages=[{"role": "user", "content": prompt}])
    if model == MODEL_CHEAP:
        kw["extra_body"] = {"temperature": 0}
    in_tok = out_tok = 0
    for attempt in (1, 2):  # one retry if the forced tool call comes back incomplete; the raw output is kept in the trace
        r = client.messages.create(**kw)
        in_tok += r.usage.input_tokens; out_tok += r.usage.output_tokens
        call = next((b for b in r.content if b.type == "tool_use"), None)
        verdict = call.input if call is not None else None
        if _complete(verdict):
            break
    out = {"verdict": verdict, "in_tok": in_tok, "out_tok": out_tok, "model": model, "attempts": attempt}
    out["stop_reason"] = r.stop_reason
    if _complete(out["verdict"]):
        os.makedirs(CACHE_DIR, exist_ok=True)
        json.dump(out, open(path, "w", encoding="utf-8"))
    pin, pout = PRICES[model]
    return {**out, "cached": False, "cost": (out["in_tok"] * pin + out["out_tok"] * pout) / 1e6, "evidence_hash": ehash}


# ---------------------------------------------------------------- Step 3 (code): the verdict rule

FLAG = "uncertain: comparator context only"


def decide(verdict, packet):
    """The locked rule. Returns (decision, flag, reason, cited_items). decision is 'recommended' or 'dismissed'."""
    valid = verdict is not None and verdict.get("winner") in BRANCHES and all(b in verdict for b in BRANCHES)
    if not valid:
        return "recommended", "uncertain: invalid Skeptic output", "The Skeptic call returned no valid verdict.", []
    w = verdict["winner"]
    if w == "real_effect":
        return "recommended", None, verdict[w]["reason"], [i for i in verdict[w]["cited_ids"] if i in packet["items"]]
    own = set(packet["blocks"][BRANCHES[w]])
    cited_own = [i for i in verdict[w]["cited_ids"] if i in own]
    if not cited_own:
        why = "cited only context items" if any(i in packet["blocks"]["X"] for i in verdict[w]["cited_ids"]) else "cited no valid item from its own block"
        return "recommended", FLAG, f"Dismissal as {w} not accepted ({why}). Skeptic said: {verdict[w]['reason']}", []
    return "dismissed", None, verdict[w]["reason"], cited_own


def run(miner_out, label_out, only_terms=None, client=None, model=MODEL_CHEAP, workers=parallel.WORKERS, allow_evaluation=False):
    """label_out: agents.label_checker.run output. Evaluates every term that route == to_skeptic (or only_terms, for development tests).
    Evidence is built by code first (PubMed lookups in parallel under one rate limit, the store sequentially), then the Haiku calls run
    `workers` at a time. Same prompts and inputs as a sequential run; results are in input order. The spend limit is enforced by the client."""
    drug, date = miner_out["drug"], miner_out["date"]
    miner_agent.guard(drug, allow_evaluation)
    out = {"agent": "skeptic", "drug": drug, "date": date, "model": model, "prompt_version": PROMPT_VERSION}
    todo = [r for r in label_out.get("results", []) if r["route"] == "to_skeptic" and (only_terms is None or r["term"] in only_terms)]
    if not todo:
        return {**out, "status": "skipped", "results": [], "message": "No terms reached the Skeptic."}
    client = client or anthropic.Anthropic()
    terms = [lab["term"] for lab in todo]
    ev = Evidence(miner_out, {r["term"]: r for r in todo})
    try:
        pms = parallel.pmap(ev.pubmed, terms, workers, on_done=lambda n, tot: print(f"  skeptic evidence (PubMed) {n}/{tot}", flush=True) if n % 20 == 0 or n == tot else None)
        packets = [ev.packet(t, pm) for t, pm in zip(terms, pms)]
    finally:
        ev.con.close()

    def judge_one(i, cl):
        if hasattr(cl, "set_context"):
            cl.set_context(term=terms[i])
        return judge(cl, drug, terms[i], date, packets[i], model)

    if hasattr(client, "map"):  # the evaluation sends these calls as batches (batching.BatchRunner)
        js = client.map(judge_one, range(len(todo)))
    else:
        js = parallel.pmap(lambda i: judge_one(i, client), range(len(todo)), workers,
                           on_done=lambda n, tot: print(f"  skeptic {n}/{tot}", flush=True) if n % 10 == 0 or n == tot else None)
    results = []
    for lab, pk, j in zip(todo, packets, js):
        term, ok = lab["term"], _complete(j["verdict"])
        decision, flag, reason, cited = decide(j["verdict"] if ok else None, pk)
        results.append({"term": term, "decision": decision, "flag": flag, "reason": reason,
                        "cited": [{"id": c, "text": pk["items"][c]} for c in cited], "winner": j["verdict"]["winner"] if ok else None,
                        "branches": {b: j["verdict"][b] for b in BRANCHES} if ok else None,
                        "label_decision": lab["decision"], "label_evidence": lab["evidence"], "label_date": lab["label_date"],
                        "evidence": pk, "evidence_hash": j["evidence_hash"], "cost": j["cost"], "cached": j["cached"],
                        "raw_invalid": None if ok else j["verdict"]})
    spend = sum(r["cost"] for r in results)
    return {**out, "status": "ok", "n_evaluated": len(results), "n_recommended": sum(r["decision"] == "recommended" for r in results),
            "n_dismissed": sum(r["decision"] == "dismissed" for r in results), "spend": round(spend, 4), "results": results}

def show(res):
    print(f"\n=== {res['term']}: {res['decision'].upper()}{' [' + res['flag'] + ']' if res['flag'] else ''}; winner {res['winner']}")
    for b, v in (res["branches"] or {}).items():
        print(f"  {b:16s} score {v['score']:.2f}  cites {v['cited_ids']}  {v['reason']}")
    print(f"  reason: {res['reason']}")
    print("  evidence packet:")
    for i, tx in res["evidence"]["items"].items():
        print(f"    [{i}] {tx[:230]}")


if __name__ == "__main__":
    from agents import label_checker, miner, triage_step
    m = miner.run(sys.argv[1], sys.argv[2])
    only = sys.argv[3].split(",") if len(sys.argv) > 3 else None
    tr = triage_step.run(m)
    if only:
        tr["terms"] = [t for t in tr["terms"] if t["term"] in only]
    lab = label_checker.run(tr)
    sk = run(m, lab, only)
    for r in sk["results"]:
        show(r)
