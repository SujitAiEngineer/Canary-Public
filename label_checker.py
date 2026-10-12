"""Label Checker: decides whether a reaction is described in retrieved label passages. Forced Anthropic tool call with a schema
(no free-text JSON). Prompt and schema below are LOCKED with the evaluation plan. Every call is cached; spend is returned.
Rule applied downstream (plan section 5): yes = drop; partial = pass to the Skeptic with a 'partially labeled' note; no = pass.
"""
import hashlib, json, os
from canary_utils import CACHE_DIR, MODEL_CHEAP, is_fresh

PRICES = {"claude-haiku-4-5-20251001": (1.00, 5.00), "claude-sonnet-5-5": (2.00, 10.00)}  # $/1M tokens (input, output)

SYSTEM = ("You are a careful pharmacovigilance reviewer. You decide whether a reaction reported to FDA is described in "
          "the label passages you are given. Record your decision by calling the record_label_decision tool.")

INSTRUCTIONS = """Reaction reported to FDA (MedDRA preferred term): {reaction}

Decide whether the label passages above describe this reaction. Use only the passages.
- "yes": ONLY if the label names the same concept or a clear synonym (including US/UK spelling and clinically equivalent wording).
- "partial": the label names a related, narrower, or broader concept but not this one. Examples: hepatitis vs liver injury; hypersensitivity vs DRESS; muscle pain vs pain; a reaction named only as the outcome of another reaction.
- "no": the passages do not describe it or anything related.
Do not count as on-label adverse reactions: mentions of the reaction in the indications, in efficacy results, or as a trial endpoint (for example "reduce the risk of ..."); and findings in animals or other nonclinical studies. Such mentions are not "yes". If nothing else in the passages describes the reaction, answer "no", or "partial" if a related clinical adverse reaction is listed.
When you are unsure between yes and partial, choose partial.
The evidence must be a short verbatim quote (at most 200 characters) from the passages, or an empty string for "no"."""

TOOL = {
    "name": "record_label_decision",
    "description": "Record whether the reaction is described in the label passages.",
    "input_schema": {
        "type": "object",
        "properties": {
            "on_label": {"type": "string", "enum": ["yes", "partial", "no"]},
            "evidence": {"type": "string", "description": "Short verbatim quote from the passages, or empty for no."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["on_label", "evidence", "confidence"],
    },
}


def check(client, reaction, passages, label_desc, model=MODEL_CHEAP):
    """Returns dict(on_label, evidence, confidence, in_tok, out_tok, cost, cached, valid). `valid` = a tool call with a schema-conforming value."""
    prompt = f"LABEL PASSAGES ({label_desc}; retrieved excerpts, not the whole label):\n{passages}\n\n" + INSTRUCTIONS.format(reaction=reaction)
    key = hashlib.md5(json.dumps([model, SYSTEM, prompt, TOOL], sort_keys=True).encode()).hexdigest()
    path = os.path.join(CACHE_DIR, f"lc_{key}.json")
    if os.path.exists(path) and not is_fresh(client):
        return {**json.load(open(path, encoding="utf-8")), "cached": True, "cost": 0.0}
    kw = dict(model=model, max_tokens=300, system=SYSTEM, tools=[TOOL], tool_choice={"type": "tool", "name": TOOL["name"]},
              messages=[{"role": "user", "content": prompt}])
    if model == MODEL_CHEAP:
        kw["extra_body"] = {"temperature": 0}  # Haiku accepts it; Sonnet 5.5 rejects non-default sampling
    r = client.messages.create(**kw)
    call = next((b for b in r.content if b.type == "tool_use"), None)
    inp = call.input if call is not None else {}
    out = {"on_label": inp.get("on_label"), "evidence": inp.get("evidence", ""), "confidence": inp.get("confidence"),
           "valid": call is not None and inp.get("on_label") in ("yes", "partial", "no"),
           "in_tok": r.usage.input_tokens, "out_tok": r.usage.output_tokens, "model": model}
    json.dump(out, open(path, "w", encoding="utf-8"))
    pin, pout = PRICES[model]
    return {**out, "cached": False, "cost": (out["in_tok"] * pin + out["out_tok"] * pout) / 1e6}
