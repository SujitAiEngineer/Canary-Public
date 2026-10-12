# Canary: an agentic early-warning system for unlabeled drug safety signals

> **About this export.** A public export of the Canary research prototype: product code, tests, documentation, and the evaluation plan, results and log. Not included: the FAERS and label data stores, the precomputed demo runs, the saved evaluation run outputs, the FDA-posting answer key and candidate lists, the reference tables, and the Phase 0 to 2 scripts, so some commands and links in the documentation refer to files that exist only in the original repository. Products excluded by project policy are loaded from `config/exclusions.local.txt` (not included; copy `config/exclusions.example.txt` and list one name per line).

Capstone for the Carnegie Mellon (Emeritus) Agentic AI program, Research Assistant option. **Research prototype, not medical advice.**

## The problem

The FDA receives reports of side effects (FAERS). Some reactions are reported often but are not on the drug's label yet; some of those later become label warnings. Canary takes a **drug name and a "time machine" date**, sees only data from before that date, finds reported side effects that are **not on the label in force at that date**, judges whether each looks like a real signal or noise, and writes a short **brief with citations**. A human approves or rejects every finding.

Honest status: a working end-to-end MVP and a small, locked evaluation (3 of 4 positive cases detected before their label change, the negative control passed, the one miss traced to the triage cap). The evaluation is a sanity check, not a validated detection rate. See `docs/evaluation_study.md`.

## Architecture

```
text -> Resolver -> Miner -> Triage -> Label Checker -> Skeptic -> Writer -> human approves or rejects
         RxNorm     FAERS    top 150    label RAG       3 branches   cited brief   (feedback log)
                    PRR/Evans (EWT or    yes/partial/no  with code-checked
                    as of date locked)                    evidence
```

1. **Drug Resolver** (`agents/resolver.py`): text to generic name via RxNorm; asks "did you mean?" when unsure.
2. **Miner** (`miner.py`, `agents/miner.py`): drug-level counts (role PS or SS, latest case version) and PRR, chi-squared and the Evans flag (at least 3 reports, PRR 2 or more, chi-squared 4 or more) for every reaction, as of the date, from the local DuckDB store.
3. **Triage** (`agents/triage_step.py`): drops terms whose exact name is on the label in force and keeps the top 150. **Default: Evidence-Weighted Triage (EWT, `triage_ewt.py`)**, which ranks by the plain average of five evidence ranks (IC025, health-professional reports, recent reports, distinct countries, serious-outcome reports); **the locked chi-squared triage (`triage.py`) is an explicit option and is what the evaluation uses.**
4. **Label Checker** (`agents/label_checker.py`, `label_checker.py`, `mesh_synonyms.py`): retrieves passages from the label version in force at the date (hybrid dense plus BM25 retrieval over locally embedded label chunks, Chroma) and an LLM answers yes, partial or no with a quoted sentence. **MeSH synonyms (add-only, on by default in the app, see below)** let it find a label that names the same condition under another name.
5. **Skeptic** (`agents/skeptic.py`): Tree-of-Thought with three branches, each backed by a real data check computed by code: real effect (statistics, label, PubMed), reporting spike (reports by year), confounding (indications, class comparators). One structured LLM call scores the branches; code sets the verdict.
6. **Writer** (`agents/writer.py`): short factual claims; code removes any claim without a valid citation or with a number not in the evidence.
7. **Orchestrator** (`orchestrator.py`): passes structured JSON between agents, logs every step (`runs/<id>/trace.jsonl`), tracks spend.
8. **UI**: FastAPI (`api/main.py`) plus a React page (`web/`): drug name, date, serious-only, model and triage selectors, each agent ticking off live, the brief with approve and reject buttons (`feedback.py` writes `feedback_log.jsonl`).
9. **Demo mode**: precomputed runs in `demo_data/` replay with no FAERS data and no API key. The public repository does not ship `demo_data/`; make your own with `python precompute_demo.py <demo id>` (needs the data stores and a key; ids are in `canary_config.DEMO_RUNS`).

## Setup

Requirements: Python 3.12, `pdftotext` and `pdfinfo` (poppler) on the PATH for label PDFs, Node 20 or newer for the web page. On Windows always set `PYTHONIOENCODING=utf-8` and run long scripts with `python -u`.

```
pip install -r requirements.txt fastembed duckdb pyarrow
cp .env.example .env        # then fill in the keys you use (see "Keys")
cd web && npm install && cd ..
```

**Just look at it (no data, no key):** `python start.py --demo` replays the precomputed runs in `demo_data/` (present in the original working copy; not in the public repository, where you build them with `precompute_demo.py` after setting up the data below).

**Build the data stores from public FDA files (needed for live runs; about 3.5 GB of downloads, about 3 GB on disk):**

1. FAERS quarterly files (public, `fis.fda.gov`): `python -u download_faers.py` (polite, resumable; about an hour; `--check` only tests the file names). They land in `data/faers/` (files before 2012Q4 are named `aers_`, later ones `faers_`).
2. DuckDB store of drug-level reports: `python -u miner_store.py build` (about 11 minutes, 1.7 GB, resumable) creates `data/canary.duckdb`.
3. Extra report fields used by Evidence-Weighted Triage (occupation, countries): `python -u miner_store_ext.py build` then `python -u miner_store_ext.py country` creates `data/canary_ext.duckdb` (about 2.5 minutes). `data/canary.duckdb` is only ever opened read-only.
4. Label history (Drugs@FDA brand labels, local embedding with `BAAI/bge-small-en-v1.5`): `python -u rag_index.py build atorvastatin` builds the first index, `python -u rag_chroma.py build` creates the Chroma store, and `python -u label_index.py <drug> <NDA number> [<NDA number>...]` adds any other drug (the brand application number is on its Drugs@FDA page; the evaluation drugs' numbers are in `eval_config.py`). Each drug takes a few minutes of polite downloads.

Everything downloaded or built lives in `data/` and `cache/`, which are not in git.

## Usage

```
python start.py                       # backend on :8000, web page on :5173, opens the browser
python start.py --demo                # replay precomputed runs only
python orchestrator.py --drug Lipitor --date 2011-12-31 [--serious] [--model <id>] [--triage ewt|locked] [--fresh] [--no-mesh] [--yes]
```

The page and the command line offer the same two choices. On the page they sit under the drug name and date.

**Model selector (one model for all agents).** Each option shows its provider, its model ID and what one run is estimated to cost (nothing cached; a rerun of the same drug and date is cheaper):

| Option | Provider | Model ID | Estimated cost of one run |
|---|---|---|---|
| Claude Haiku 4.5 (default; the model of the locked evaluation) | Anthropic | `claude-haiku-4-5-20251001` | about $1.07 |
| Claude Sonnet 5.5 | Anthropic | `claude-sonnet-5-5` | about $2.57 |
| Claude Opus 5.5 | Anthropic | `claude-opus-5-5` | about $7.58, which does not fit under the $6.00 limit, so the page refuses it |
| Nemotron 3 Super | OpenRouter, labelled **Free** | `nvidia/nemotron-3-super-120b-a12b:free` | $0 (the free tier allows 50 requests per day) |

- **Paid models need an explicit confirmation**: tick "Confirm the cost" (page), pass `--yes` or type a yes (command line), or send `confirm_cost` (API). The hard limit for all paid runs outside the locked evaluation is **$6.00** (`run_spend.jsonl`).
- **The free model shows how it was tested**, in its own card: "tested on the 32-row gold set: 81.3% vs Haiku 84.4%", plus the caveat that three other candidates were blocked by the free-tier daily cap. The wording is read from `model_portability.md`, which also has the full table.
- **A missing or rejected key** is shown before the run starts: the model's card says which key is missing in `.env` and Run stays blocked; a key the provider refuses stops the run with a clear message. Either way the page offers the other models that can be used, one click selects one. Demo mode needs no key at all.
- **The free model's rate limit** shows a waiting state ("the run resumes by itself") and then continues. If the daily limit is used up the run stops with a message and offers other models; the answers already obtained are cached, so a rerun resumes.
- The evaluation runner never uses these choices: it refuses any model except Haiku and always uses the locked triage.

**Triage selector.**
- **Evidence-Weighted (EWT), the default**: ranks flagged terms by the average of five evidence ranks (statistics, health-professional reports, recent reports, countries, serious outcomes), so well-evidenced rarer terms are cut less often by the cap of 150.
- **Locked (original)**: the chi-squared ranking of the locked evaluation, the 150 flagged terms with the largest chi-squared, which favours terms with many reports.

**Every brief says what produced it**: a line at the top of the brief (page, Markdown download and `brief.md`) names the model and provider, the triage and the "research prototype, not medical advice" notice; the run record `runs/<id>/run.json` keeps the model, the triage and, when a run stops, the models offered instead. Runs made before the triage choice existed (the packaged demos) are labelled as using the locked triage.

**Fresh run (off by default; a checkbox on the page, `--fresh` on the command line).** A normal rerun of a drug and date reuses saved answers (instant, free). A fresh run ignores the saved answers and the saved MeSH lookups for that run, so every Label Checker, Skeptic and Writer call and every MeSH lookup is made live (a paid model costs money again); new answers are still saved. PubMed and RxNorm lookups keep their own cache. The brief's provenance line says "fresh run". The locked evaluation refuses it.

On the command line: `--model <id>`, `--triage ewt|locked`, `--fresh` and `--no-mesh`; the API takes `model`, `triage`, `fresh`, `mesh_synonyms` and `confirm_cost` on `POST /api/runs`, and `GET /api/models` lists the options with provider, model ID, estimated cost, free label, whether the key is present, and the triage explanations.

## MeSH synonyms (Label Checker retrieval)

MedDRA, the vocabulary of FAERS, and a label's own wording often differ: the FAERS term "allergic granulomatous angiitis" is the label's "Churg-Strauss syndrome". Without help the label passage can rank outside the top 5 and the Label Checker answers "not on label".

- **How it works** (`mesh_synonyms.py`): the reaction term is looked up in the free NLM MeSH service (`id.nlm.nih.gov/mesh`) as a descriptor name and as an entry term; the descriptor name and up to 5 entry terms that add vocabulary become synonyms. The original query's top 5 passages are kept **exactly as they are**; the hybrid search is then run for each synonym and **up to 2 extra passages** that are not already there are added (at most 7 passages), and one line "Also known as: ..." is added to the Label Checker's message. The locked prompt, the tool schema and the forced tool call are not changed.
- **Add-only, so it cannot lose a passage.** On the 32-row Label Checker gold set, synonyms improved the score from 27 of 32 to 28 of 32, with no row getting worse. A first design that fused the synonym rankings into one list scored 24 of 32 and was replaced (`eval_runs.md`).
- **On by default in the app** (`canary_config.MESH_SYNONYMS`); switch it off with `--no-mesh` (command line) or `"mesh_synonyms": false` (API). **Always off in the locked evaluation.**
- **Known limitation**: a "yes" drops a term before the Skeptic sees it, and a synonym can push a related-but-broader label phrase to "yes" (for example EPILEPSY matched to "seizures"); three such cases are logged in `eval_runs.md`.
- **Cache and speed**: answers are cached in `data/mesh_cache.json`, so a repeat run makes no request. Lookups run 8 at a time under one shared pace of at most 5 requests a second, with retries on timeouts, 429 and 5xx: 150 terms took **138 s instead of 636 s** with identical answers. A **fresh run** skips reading the cache (live lookups) but still writes it. If the service fails, the run falls back to the original query and says so in its log.

## Keys (`.env`, never committed)

`.env` is in `.gitignore`. Copy `.env.example` and fill in: `ANTHROPIC_API_KEY` (the paid models), `OPENROUTER_API_KEY` (the free model, Nemotron 3 Super; the free tier allows 50 requests per day, which is enough for roughly one full test or a few small runs), `OPENFDA_API_KEY` (optional, only for the Phase 0 to 2 scripts and the live demo). No key is ever sent anywhere except its own provider.

## Tests

Offline, no API spend (a stub client fails a test if it makes a call). Run any file, for example `python -u tests/test_guardrails.py`, or all of them:

```
for t in tests/test_*.py; do python -u $t; done
```

Some tests need the local data stores (`data/canary.duckdb`, `data/canary_ext.duckdb`, the label index) and the saved evaluation runs in `eval_out/`; `tests/test_resolver.py` calls RxNorm (free).

UI and selector checks, all with fake providers (no model call, no spend, and the real answer cache is never touched): `tests/test_mesh_synonyms.py` (MeSH lookups, caching, concurrency, retries, the add-only retrieval), `tests/test_fresh_run.py` (the fresh-run option: off by default, ignores the answer cache but still writes it, shown in the provenance line; one notice per brief), `tests/test_ui_polish.py` (options, provenance, key errors, waiting state, demo without keys), `tests/test_api_smoke.py` (every model with every triage option, 8 full runs through the API calls the page makes), and, run by hand because it starts node and a browser, `python -u tests/browser_smoke_ui.py` (headless Chrome or Edge drives the real page through every option, the paid-model confirmation, the rate-limit waiting state, the daily-limit and rejected-key messages, a missing key, and demo mode with no keys).

## The evaluation model, re-checking the results, and a known test failure in the public export

- **Model and its retirement floor.** The locked evaluation used `claude-haiku-4-5-20251001` for every model call. Anthropic lists Claude Haiku 4.5's retirement as **not sooner than October 15, 2026** (a floor, not a scheduled date; models overview, checked 2026-10-04). After it retires the evaluation cannot be re-run live on that model; running it on another model would be a new, separately labelled evaluation, not a re-run. The model setting is one value, `MODEL` in `eval_config.py`.
- **Results can be re-checked from the cached answers, with no new spend.** Every model call in the evaluation is cached on disk in `cache/` (keyed by model, prompt and evidence hash), and each run's outputs are in `eval_out/runs/`. Re-running a saved case with a client that fails on any call reuses the cached answers: checked on 2026-10-04 for pirfenidone 2015Q4 and dasatinib 2007Q4, with zero model calls and identical Label Checker decisions, Skeptic decisions and winners (48 of 76 and 22 of 23 recommended), and the same reuse was seen earlier for rivaroxaban 2018Q4 (150 of 150 answers cached; `eval_runs.md`). `cache/` and `eval_out/` are not in git; keep a private copy (`eval_runs.md` and `eval_spend.jsonl` too). **The public export does not contain them**, so the re-check works only in a copy that has them.
- **Expected failure in the public export.** In the sanitized public export, `tests/test_run_evaluation.py` is expected to fail one test, `test_results_name_the_lock_commit_and_the_code_commit_and_the_amendments`, because it looks up the git tag `eval-lock` and the lock commit `425633dd8729eeac52f9e060c19eb28297f6fa24`, which exist only in the original repository's history (the export is a single commit). That test file stops at its first failure, so the tests after it do not run in the export; all 14 pass in the original repository. In the export's own sandbox run, every other test file passed (175 tests), with the data stores linked in. Some tests need the local data stores and saved runs, which the export does not carry.

## Where the evidence lives

| What | Where |
|---|---|
| The locked evaluation plan | `EVALUATION_PLAN.md` (git tag `eval-lock`, commit `425633dd8729eeac52f9e060c19eb28297f6fa24`) |
| Locked results, supplementary gabapentin run | `eval_out/eval_results.md`, `eval_out/eval_supplementary_gabapentin.md`, `eval_out/` (per-quarter data, every run's outputs) |
| The evaluation log: every run, bug fix, amendment, post-result analysis, in order | `eval_runs.md` |
| The whole study in plain language, every number traced to a log entry and commit | `docs/evaluation_study.md` |
| Checkpoint notes (course checkpoints 1.1 to 7.1) | `docs/checkpoint_notes.md` |
| Model choices, prices, limits | `model_portability.md` |
| Data feasibility and design evidence (Phases 0 to 2) | `analysis/` (`analysis/README.md`, `phase0_findings.md`, `phase1_findings.md`, `phase2_findings.md`); the Phase 0 to 2 commands are in `docs/phase_scripts.md` |
| Post-result triage experiments and EWT | `triage_v2.py`, `triage_prune.py`, `triage_ewt.py`, `analysis/*_check.py`, results JSON in `analysis/` |
| EWT confirmation run on gabapentin | `ewt_confirmation/` |
| Spend | ledgers `eval_spend.jsonl` (evaluation, $5.5558 of $9.50), `run_spend.jsonl` (product runs, limit $6.00), `build_spend.jsonl` (build), all gitignored |

## Checkpoint map (which files implement which course checkpoint)

| Checkpoint | What | Files |
|---|---|---|
| 1.1 Scoping | problem, user, environment, actions, feedback | `CLAUDE.md`, `BUILD_PLAN.md`, `analysis/phase0_findings.md`, `analysis/phase1_findings.md`, `feedback.py` |
| 2.1 Agent design: tools and memory | tools, caches, run folders, recovery | `agents/`, `canary_utils.py`, `miner_store.py`, `rag_chroma.py`, `net_retry.py`, `spend.py`, `orchestrator.py` |
| 3.1 RAG | time-aware label retrieval | `rag_index.py`, `rag_chroma.py`, `label_index.py`, `label_checker.py`, `agents/label_checker.py` |
| 4.1 Tree-of-Thought | three-branch Skeptic with code-checked evidence | `agents/skeptic.py`, `tests/test_skeptic_rule.py` |
| 5.1 Multi-agent | roles and coordination | `orchestrator.py`, `agents/`, `batching.py`, `agents/parallel.py`, `api/main.py`, `web/` |
| 6.1 Safety and evaluation | guardrails, logging, success criteria, human approval | `tests/test_guardrails.py`, `agents/writer.py`, `feedback.py`, `EVALUATION_PLAN.md`, `run_evaluation.py`, `eval_runs.md`, `docs/evaluation_study.md` |
| 7.1 Final report | the whole study | `docs/evaluation_study.md`, `docs/checkpoint_notes.md` |

## Hard rules this project follows

Public data only. Products excluded by project policy, and their close competitors, are never used as cases (the lists are loaded from the local, untracked file `config/exclusions.local.txt`; `config/exclusions.example.txt` is an empty template). All numbers that could go in the paper use drug-level suspect counts from the FAERS bulk files (the openFDA API overcounts about 2.7 times and is for the live demo only). For label history the brand label from Drugs@FDA is used. Be polite to APIs (delays, local caching).

## Future work (not part of the MVP)

- A larger cap or a staged review for terms that meet the Evans criteria but rank below the triage cap (the cause of the gabapentin miss).
- Validating Evidence-Weighted Triage with a different proxy than later labels, and checking what the rare serious terms it drops are worth.
- Full PubMed retrieval with abstract ranking and a looser, term-aware search (lab terms ending in "increased" currently return 0 papers).
- OCR for the few scanned label PDFs.
- Live testing of Sonnet 5.5 and Opus 5.5 through the model router, and of a free OpenRouter model once a working key is available.
- Moving off Claude Haiku 4.5 before its retirement (Anthropic lists it as not sooner than 2026-10-15); the evaluation is locked to that model, so a different model means a new, separately labelled evaluation.
- A polished UI, and a 10 to 15 drug backtest with a larger set of cases.
