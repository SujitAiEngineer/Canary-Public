"""Run-time settings for the pipeline (not the locked evaluation config; that is eval_config.py).
The evaluation reads only eval_config.MODEL and never reads DEMO_BRIEF_MODEL or the build budget."""
import eval_config

PIPELINE_MODEL = eval_config.MODEL            # Label Checker, Skeptic and (by default) the brief writer: Haiku
DEMO_BRIEF_MODEL = "claude-sonnet-5-5"        # demo brief writer only (Build Step 9); the evaluation never reads this
BUILD_BUDGET_USD = 8.00                       # API spend for the whole build (raised from 3.00 to 6.00, then to 8.00, by the project owner, for the demo runs)
BUILD_LEDGER = "build_spend.jsonl"            # one JSON line per Anthropic call made while building (gitignored)
DEFAULT_TRIAGE = "ewt"                        # product default: Evidence-Weighted Triage (triage_ewt.py). The locked chi-squared triage stays an explicit option ("locked")
TRIAGE_CHOICES = ("ewt", "locked")            # the evaluation runner always uses "locked" (orchestrator.run_pipeline evaluation_mode)
RUN_LEDGER = "run_spend.jsonl"                # ledger for normal (non-evaluation) paid runs from the model-selector work on (gitignored); the build ledger is used up
RUN_LIMIT_USD = 6.00                          # hard limit for ALL paid work outside the locked evaluation (owner: $3.50 on 2026-10-04, raised to $6.00 on 2026-10-12); the free model costs $0
OPENROUTER_FREE_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"   # the one free OpenRouter model offered; chosen by test (model_portability.md)
OPENROUTER_FREE_MODEL_NOTE = "tested on the 32-row gold set: 81.3% vs Haiku 84.4%; 3 other candidates blocked by the free-tier daily cap"   # shown next to the model (model_portability.md)
TRIAGE_OPTIONS = [   # shown in the UI and returned by /api/models, one line each
    {"id": "ewt", "label": "Evidence-Weighted (EWT)", "default": True,
     "explanation": "Ranks flagged terms by the average of five evidence ranks (statistics, health-professional reports, recent reports, countries, serious outcomes), so well-evidenced rarer terms are cut less often by the cap of 150."},
    {"id": "locked", "label": "Locked (original)", "default": False,
     "explanation": "The chi-squared ranking of the locked evaluation: the 150 flagged terms with the largest chi-squared, which favours terms with many reports."}]
MESH_SYNONYMS = True                          # MeSH synonyms for the Label Checker's retrieval (mesh_synonyms.py): ON in the app since 2026-10-12 (design 2: original top 5 kept, up to 2 synonym passages added; gold set 28 of 32 vs 27 of 32 without). The locked evaluation always runs with them OFF (orchestrator evaluation_mode)
MAX_BRIEFS_DEFAULT = 15                       # the interactive brief writes the top N recommended terms in full (the evaluation driver passes None = all)
RUNS_DIR = "runs"                             # one folder per orchestrator run: trace.jsonl, step outputs, run.json (gitignored)

# ---- Demo-only settings (Build Step 9). The evaluation never reads anything below: it uses the locked cap of 150 and no featured terms.
DEMO_REVIEW_CAP = 400                                    # "extended review: top 400" for the linezolid and lithium demos
DEMO_REVIEW_LABEL = "extended review mode (top 400)"     # printed on every output made with DEMO_REVIEW_CAP
DEMO_RUNS = [  # id, drug, time machine date, review cap (None = the locked cap of 150), demo-highlight terms (kept at their real rank)
    {"id": "atorvastatin-2011", "drug": "atorvastatin", "date": "2011-12-31", "review_cap": None,
     "featured": ["GLYCOSYLATED HAEMOGLOBIN INCREASED"], "title": "Atorvastatin (Lipitor), before 2011-12-31: the fake signal"},
    {"id": "linezolid-2019", "drug": "linezolid", "date": "2019-12-31", "review_cap": DEMO_REVIEW_CAP,
     "featured": ["INAPPROPRIATE ANTIDIURETIC HORMONE SECRETION"], "title": "Linezolid (Zyvox), before 2019-12-31: SIADH"},
    {"id": "lithium-2017", "drug": "lithium", "date": "2017-12-31", "review_cap": DEMO_REVIEW_CAP,
     "featured": ["DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"], "title": "Lithium, before 2017-12-31: DRESS"},
]
DEMO_DIR = "demo_data"                                   # precomputed demo runs, replayed with no FAERS files and no API key
