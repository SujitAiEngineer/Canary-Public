"""Which model and which triage produced a run (shown with every brief), and the not-medical-advice notice. Used by the orchestrator (written into brief.md and run.json)
and by the API (added to the brief of runs made before this existed, such as the packaged demos). No model, no network."""
import canary_config
from agents.writer import DISCLAIMER

PROVIDER_LABELS = {"anthropic": "Anthropic", "openrouter": "OpenRouter"}
MARK = "**Produced by**"


def provider_of(model):
    return "openrouter" if str(model).startswith("openrouter:") else "anthropic"


def model_id(model):
    """The provider's own ID (the OpenRouter models are stored with an 'openrouter:' prefix so that cache keys carry the provider)."""
    return model[len("openrouter:"):] if str(model).startswith("openrouter:") else model


def triage_option(triage):
    return next((o for o in canary_config.TRIAGE_OPTIONS if o["id"] == triage), {"id": triage, "label": str(triage), "explanation": ""})


def of_run(run):
    """Provenance from a run.json dict. Runs made before the triage choice existed (the packaged demos) carry no triage key: they used the locked triage."""
    models = run.get("models") or {}
    pipeline = models.get("pipeline") or canary_config.PIPELINE_MODEL
    brief = models.get("brief") or pipeline
    t = run.get("triage")
    opt = triage_option(t or "locked")
    return {"pipeline_model": pipeline, "pipeline_provider": provider_of(pipeline), "brief_model": brief, "brief_provider": provider_of(brief),
            "triage": opt["id"], "triage_label": opt["label"], "triage_explanation": opt["explanation"], "triage_inferred": t is None, "fresh": bool(run.get("fresh")),
            "notice": DISCLAIMER}


def header_markdown(p):
    """Two blockquote lines for the top of a brief: what produced it, and the notice."""
    def who(model):
        return f"`{model_id(model)}` ({PROVIDER_LABELS[provider_of(model)]})"
    models = who(p["pipeline_model"]) if p["pipeline_model"] == p["brief_model"] else f"{who(p['pipeline_model'])} for the Label Checker and Skeptic, {who(p['brief_model'])} for this brief"
    tri = p["triage_label"] + (" (this run predates the triage choice; it used the locked triage)" if p["triage_inferred"] else "")
    fresh = "; fresh run (answer cache ignored)" if p.get("fresh") else ""
    return f"> {MARK}: model {models}; triage: {tri}{fresh}.\n> *{p['notice']}*\n"


def with_header(markdown, run):
    """The brief with its provenance header, added once (a brief that already has it is returned unchanged)."""
    notice = f"*{DISCLAIMER}*\n"
    if MARK in markdown.split("\n\n", 2)[0] + markdown[:600]:
        # the header already carries the notice: drop the writer's own copy that sits below it (briefs made before this fix have both)
        head, sep, rest = markdown.partition(MARK)
        i = rest.find(notice, rest.find(DISCLAIMER) + 1)
        return markdown if i < 0 else head + sep + rest[:i].rstrip("\n") + "\n\n" + rest[i + len(notice):].lstrip("\n")
    markdown = markdown.replace(notice, "", 1).replace("\n\n\n", "\n\n", 1) if notice in markdown[:900] else markdown   # the writer puts the notice under the title; the header replaces it
    first_nl = markdown.index("\n") + 1 if "\n" in markdown else len(markdown)
    return markdown[:first_nl] + "\n" + header_markdown(of_run(run)) + "\n" + markdown[first_nl:]
