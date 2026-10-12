# Canary: checkpoint notes

Written for: the Carnegie Mellon (Emeritus) Agentic AI program, Research Assistant option; one section per course checkpoint (1.1 scoping, 2.1 agent design, 3.1 RAG and retrieval, 4.1 Tree-of-Thought, 5.1 multi-agent roles and coordination, 6.1 safety and evaluation, 7.1 final report). Facts only: what was built, the design choices and why, and the evidence from tests and runs, with file names and commit hashes. Numbers come from `eval_runs.md`, the `analysis/` findings files, `docs/evaluation_study.md` and the test suites. Commit hashes are short (first seven characters); the evaluation-plan lock is `425633dd8729eeac52f9e060c19eb28297f6fa24` (tag `eval-lock`), and work done before it (Phases 0 to 2: data feasibility, label RAG prototype, Miner store, evaluation plan) is in that commit and in the files under `analysis/`.

Canary in one sentence: a user types a drug name and a "time machine" date; Canary finds side effects reported to the FDA before that date that are not on the drug's label, judges whether each looks like a real signal or noise, and writes a short cited brief that a human approves or rejects.

---

## 1.1 Scoping

**Problem.** Reaction reports in the FDA Adverse Event Reporting System (FAERS) are numerous (24,812,425 reports across 90 quarterly files, 2004Q1 to 2026Q2, in our DuckDB store) and a drug can show hundreds of statistically flagged reaction terms (alert counts at the last cutoff before the label change: 183 for pirfenidone, 610 lamotrigine, 685 gabapentin, 398 dasatinib; `eval_out/eval_results.md`). Most flagged terms are noise, already on the label, or confounded; a few later become label warnings. The task is to surface the unlabeled ones worth a human's attention, early, with evidence that can be checked.

**User.** A drug-safety reviewer or research assistant who types a drug name, a date, optionally "serious reports only", and approves or rejects each finding. Every output carries "research prototype, not medical advice".

**Why one prompt is not enough** (design reasoning, with the evidence that supports it):
- The statistics must come from code, not from a model: the disproportionality numbers (PRR, chi-squared, Evans criteria) are computed over millions of reports by the Miner (`miner.py`, `agents/miner.py`), and the openFDA API cannot be used for any reported number because its suspect filter overcounts about 2.7 times (`CLAUDE.md`, Phase 1 Test C finding in `analysis/phase1_findings.md`).
- The date cutoff has to be enforced by data filtering, not by asking a model to ignore the future: the time machine counts only reports loaded in a quarter up to the cutoff, the label version in force at that date, and PubMed papers before it.
- The label must be read from the right historical version: retrieval over dated label chunks (`rag_index.py`, `rag_chroma.py`) feeds the judgment; a prompt alone has no access to the label in force in 2011.
- Judging "is this term on the label" and "real effect, reporting spike or confounding" needs language understanding, but each judgment is small, structured and checkable, so it is done term by term with forced tool calls and evidence IDs (`label_checker.py`, `agents/skeptic.py`) rather than in one long free-form answer.
- A run touches 150 terms per cutoff; the work is parallel and cacheable per term (`agents/parallel.py`, on-disk caches).

**Environment** (public data only, no proprietary or personal data): FAERS quarterly bulk files (drug-level counts, role PS or SS, latest case version per case); Drugs@FDA brand-label PDFs (historical versions); RxNorm (drug names); PubMed (paper counts and titles); the EMA Important Medical Events list (seriousness, post-result work only); openFDA for the live demo only. Excluded drugs: products excluded by project policy and their close competitors (loaded from `config/exclusions.local.txt`). Feasibility was tested before building: Phase 0 verdict "GO, with conditions" (`analysis/phase0_findings.md`) and Phase 1 tests A to F, each "GO WITH CONDITIONS" (`analysis/phase1_findings.md`).

**Actions the system can take:** resolve a drug name, query the local store, retrieve label passages, look up PubMed, call a language model with a forced tool schema, write a brief, record a human decision. It cannot execute code, write to the FAERS store, or send anything outside the project (the evaluation and the app only read public sources).

**Feedback.** Each recommended signal gets approve or reject buttons in the UI; a rejection requires a reason; decisions go to `feedback_log.jsonl` (`feedback.py`, commit `339d740`; `tests/test_guardrails.py`, `tests/test_demo_mode.py` for demo mode). The evaluation's own feedback loop is the locked plan and its log (`EVALUATION_PLAN.md`, `eval_runs.md`).

**Scope limits.** About 24 hours of build time, a working end-to-end MVP and a small, honest, locked evaluation (`BUILD_PLAN.md`); everything else went to a future-work list.

---

## 2.1 Agent design: reasoning, memory, tools, recovery from missteps

**Reasoning.** Each agent is one narrow, structured decision.
- Label Checker: a forced tool call returns `on_label` (yes, partial, no), a verbatim quote of at most 200 characters, and a confidence; the prompt and schema are locked (`label_checker.py`; `EVALUATION_PLAN.md` section 10). Rule: yes drops the term; partial and no pass to the Skeptic, partial marked "partially labeled".
- Skeptic: a forced tool call scores three branches and names a winner; **code**, not the model, sets the verdict (section 4.1).
- Writer: the model writes short claims; **code** removes any claim without a valid citation and writes the fixed parts itself (`agents/writer.py`).

**Memory.**
- Per-call cache on disk, keyed by term, model, prompt version and (for the Skeptic and Writer) a hash of the evidence packet, so an unchanged call is never paid for twice (`cache/`, gitignored).
- Per-run folder `runs/<run_id>/` with `trace.jsonl` (every step), each step's JSON output, `run.json` and `brief.md` (`orchestrator.py`).
- The label index: 18,856 chunks of brand-label history in the local store at the time of the evaluation work (35,072 in the Chroma collection after later additions), tagged by drug, label date and section.
- The spend ledger (`spend.py`; `eval_spend.jsonl`, `run_spend.jsonl`, gitignored) and the human feedback log (`feedback_log.jsonl`).
- Long-term record of decisions: `eval_runs.md`, written before a run when the run is paid or post-result.

**Tools.** RxNorm for names (`agents/resolver.py`, commit `de0a2f3`); DuckDB store of FAERS (`miner_store.py`, `miner.py`, `agents/miner.py`, commit `62a854f`); Drugs@FDA and local embeddings plus Chroma for labels (`label_index.py`, `rag_chroma.py`, commit `328d02e`); PubMed (inside `agents/skeptic.py`); the Anthropic API through the spend ledger (`spend.py`); optionally one OpenRouter free model through `model_router.py` (commit `1943474`).

**Recovery from missteps** (each is a tested behavior):
- The Resolver never guesses silently: only an exact RxNorm match is "resolved"; a spelling-corrected or approximate match asks "did you mean?" (`tests/test_resolver.py`, 7 tests; `tests/test_guardrails.py` test 1).
- An invalid or missing tool call is never a drop: the Label Checker passes the term on with the flag `invalid_decision` (`agents/label_checker.py`); the Skeptic retries once on an incomplete verdict and never caches an incomplete verdict, and an invalid output never dismisses (`tests/test_skeptic_rule.py` test 6). Strict tool use fixed intermittent single-branch outputs (`6fd2e25`).
- A dropped network connection is retried at most 3 times with the same request, then the run stops; nothing is skipped silently (`net_retry.py`, `tests/test_network_retry.py`, 9 tests; commit `c614ba8`; this fixed the crash on rivaroxaban 2018Q4).
- Rate limits (HTTP 429) are retried with backoff, a permanent one is raised (`tests/test_spend_concurrency.py`, 5 tests).
- A stopped batch run resumes from the cache without paying twice: 150 of 150 Label Checker answers reused at no cost in the resume check (`eval_runs.md`, "documented bug fix"), `tests/test_batching.py` test "interrupted batch is resumed, not resubmitted".
- The spend limit is a hard stop: checked before each call (and before each batch, with worst-case cost) and recorded after (`spend.py`, `batching.py`; commit `932970b`).
- Determinism: exact ties are broken alphabetically so the evidence packet, cache key and output do not change between runs (`919be29`).

---

## 3.1 RAG and retrieval

**What was built.** Time-aware retrieval of label passages. Brand-label PDFs from Drugs@FDA (historical versions) are converted with `pdftotext`, chunked by section and paragraph (about 1,100 characters), tagged with drug, label date, file and section, and embedded locally with `BAAI/bge-small-en-v1.5` through `fastembed` (no torch). Retrieval is hybrid (dense cosine plus BM25, merged by reciprocal rank fusion, k=60) and restricted to the **label version in force at the cutoff date**; the top 5 chunks go to the Label Checker (`rag_index.py` numpy index, `rag_chroma.py` Chroma store, `label_index.py` to add a drug).

**Design choices and why.**
- Brand/reference label from Drugs@FDA, because DailyMed brand history for Lipitor only starts in 2024 and Drugs@FDA has the older labels (`analysis/phase1_findings.md` Test B).
- Hybrid retrieval, because dense-only ranked the real atorvastatin glucose warning 14th for a literal query; hybrid moved it to 4th, so the Label Checker is given the top 5 chunks, not the top 1 (`analysis/phase2_findings.md`, Task 3). A later section-tagging fix made that warning the top result at the 2013 cutoff (`EVALUATION_PLAN.md` section 5a).
- Chroma instead of the plain numpy index, like for like (`328d02e`): vectors were copied, not re-embedded.

**Evidence.**
- Time-aware retrieval tests (`analysis/phase2_findings.md`): at cutoffs before a warning existed the target was correctly not retrieved (atorvastatin 2011 and gabapentin 2010: pass); at cutoffs after it was added it was retrieved (gabapentin 2011 rank 1; atorvastatin 2013 rank 4 before the fixes).
- The Chroma swap returns the same top 5 chunks (same label date, files, sections, text, order) on 36 of 36 checks (`eval_runs.md`, first entry; `python rag_chroma.py compare`).
- The Label Checker on the locked 32-row gold set (20 atorvastatin, 12 simvastatin rows): **27 of 32 correct (84%)**, 32 of 32 valid tool calls, $0.0889; the yes-versus-partial boundary has 3 errors in 22 rows (`EVALUATION_PLAN.md` section 5a; `analysis/task7_results_v2.csv`; `tests/test_label_checker_agent.py`, `becd916`).
- Retrieval cost and limits recorded: 41 label PDFs scanned for a text layer (2 scanned images without one, no OCR installed); section tags are weak before about 2006 (`analysis/phase2_findings.md`).
- Data preparation for the evaluation drugs: label histories of 17 gabapentin, 6 pirfenidone, 27 lamotrigine, 17 dasatinib, 26 rivaroxaban versions indexed, the label-change version of each positive case present (`eval_runs.md`, "data preparation, brand-label indexing").

---

## 4.1 Tree-of-Thought

**What was built.** The Skeptic (`agents/skeptic.py`, commits `da0b3e9`, `6fd2e25`), Design B as locked in `EVALUATION_PLAN.md` section 5b. For each term that passes the Label Checker:
1. **Code computes the evidence, no model**, split into three branches with an evidence ID on every item, plus a neutral context field. Branch R (real effect): the drug's own disproportionality numbers, the label status and quote, and a PubMed count with the top 3 titles before the cutoff. Branch S (reporting spike): the drug's cases by year of first receipt, the term's cases by year, peak-year concentration. Branch C (confounding by indication): the share of the term's cases that list a related indication. Context X: the same term's PRR for class comparators at the same cutoff (visible to all branches, owned by none).
2. **One forced tool call per term** scores each branch from 0 to 1 with a one-sentence reason and the IDs it cites, then names the winner.
3. **Code sets the verdict.** Only a `real_effect` winner is recommended. A dismissal (winner spike or confounding) is valid only if the winning branch cites at least one item from its *own* block; a dismissal that cites only context, another branch, or nothing is not a dismissal: the term is recommended with the flag "uncertain: comparator context only". The Skeptic may not dismiss a signal on comparator evidence alone.

**Design choices and why.** Each branch is backed by a real data check, not by the model's recall; the verdict rule is in code so the model cannot dismiss without checkable evidence; the winning branch must address the strongest losing branch (prompt rule added after development runs, `eval_runs.md`); a PubMed paper may be called supporting only if its title is about the drug or class causing the reaction. A PubMed bug (words ANDed instead of an exact phrase) was found and fixed before the evaluation (`analysis/pubmed_fix_before_after.py`).

**Evidence.**
- `tests/test_skeptic_rule.py` (6 tests): a real-effect winner is recommended; a spike or confounding dismissal with its own evidence is dismissed; a dismissal on context only, or citing another branch or nothing, is not a dismissal; an invalid output never dismisses.
- Observed behavior, recorded plainly: the Skeptic recommends most of what it examines (Lipitor 2011: 127 to 128 of 141, about 90%; evaluation runs: rivaroxaban 132, 128 and 124 of 135, 135 and 134; gabapentin 137 of 146 and 123 of 142; lamotrigine 96 of 97; dasatinib 22 of 23; pirfenidone 48 of 76, the lowest at 63%); in a batch-versus-sequential rehearsal 35 of 36 terms with identical evidence had the same decision and winner and the mean difference in branch scores was 0.045, with one borderline term flipping (`eval_runs.md`, batch rehearsal). The lithium demo's DRESS term was dismissed as a reporting spike (real effect 0.65, spike 0.70, confounding 0.45) and reported as it came out.
- The earlier note that the rules are followed unevenly (the winner's reason does not always address the higher-scoring losing branch) was recorded, not tuned away (`eval_runs.md`, 2026-10-02).

---

## 5.1 Multi-agent roles and coordination

**Roles** (`orchestrator.py` passes plain JSON between them and logs every step):
| Role | File | Job |
|---|---|---|
| Drug Resolver | `agents/resolver.py` | free text to generic name, "did you mean?" when unsure |
| Miner | `agents/miner.py`, `miner.py` | drug-level PRR, chi-squared, Evans flag as of the date |
| Triage (code step) | `agents/triage_step.py`, `triage.py`, `triage_ewt.py` | drop on-label terms, rank, keep the top 150 (default Evidence-Weighted Triage; the locked chi-squared triage is an option and is what the evaluation uses) |
| Label Checker | `agents/label_checker.py`, `label_checker.py` | retrieve label passages, yes / partial / no with a quote |
| Skeptic | `agents/skeptic.py` | three-branch Tree-of-Thought, code-enforced verdict |
| Writer | `agents/writer.py` | short brief with citations; human approves or rejects |
| Orchestrator | `orchestrator.py` | order, JSON hand-over, trace, spend ledger, model and triage choice |

**Coordination design.** Agents hold no orchestration logic; `orchestrator.run_pipeline` calls them in order (Resolver, Miner, Triage, Label Checker, Skeptic, Writer), writes `runs/<id>/<step>.json` and an event to `trace.jsonl` after each step, and stops early with a clear status when a step returns no data. The Label Checker and Skeptic call the model about 5 terms at a time (`agents/parallel.py`; results come back in input order); evaluation runs send them as Message Batches (`batching.py`, half price, same model, prompts and inputs). The API layer (`api/main.py`, FastAPI) only starts runs and relays the events; the React UI (`web/`) shows each agent ticking off live (commit `1ff1dff`).

**Evidence.**
- A full Lipitor 2011-12-31 run completed end to end at $1.12 (`b72bf02`); a Zyvox 2019-12-31 run completed through the API and UI (`1ff1dff`).
- Concurrent runs are identical to sequential ones except for the two logged determinism fixes (`eval_runs.md`, "concurrency and determinism"): 1,119 s sequential versus 311 s concurrent for a fresh-Skeptic run.
- Orchestration and the evaluation runner are covered by `tests/test_run_evaluation.py` (14 tests: written go required, positive case stops at first detection, negative case runs all three, spend limit stops everything, resume skips finished runs, the reduced schedule matches the approved list), `tests/test_batching.py` (9), `tests/test_spend_concurrency.py` (5), `tests/test_demo_mode.py` (4), `tests/test_api.py` (5).
- The locked evaluation ran this pipeline on five drugs: 3 of 4 positive cases detected, the negative control passed (`docs/evaluation_study.md` section 2).

---

## 6.1 Safety, guardrails, logging, success criteria, human intervention, unintended actions

**Guardrails, each triggered once in `tests/test_guardrails.py` (9 tests, no API call is made; a stub client fails the test if used; commit `339d740`).**
1. Did-you-mean: an approximate drug name asks, never guesses.
2. Not enough data: fewer than 50 suspect reports stops with a message instead of a result.
3. Citation rule: a brief claim without a valid evidence ID or a verbatim label quote is removed (`tests/test_writer_rule.py`, 8 tests, also removes a sentence whose number is not in the term's evidence packet).
4. Spend stop: the ledger refuses a call that would pass the limit; the real cost of each call is recorded (4b).
5. Every brief carries "research prototype, not medical advice".
6. Feedback log: a human approves or rejects each recommended signal; a rejection needs a reason; the term must be one the run actually recommended.
7. The five evaluation drugs are blocked everywhere (Miner, Skeptic, orchestrator) except in the explicit evaluation mode.
8. Evaluation mode is a parameter of `orchestrator.run_pipeline`, with no command-line flag.

**Further controls added later and tested:** the evaluation refuses any model but the evaluation model (`tests/test_model_router.py`, 17 tests; `tests/test_triage_default.py`, 8); paid models show a cost estimate and need confirmation, with a $3.50 hard limit for remaining paid work (`model_portability.md`); `.env` (keys) and `data/` are not tracked by git (`tests/test_model_router.py` checks `.env`); public data only; the project-policy exclusion lists are applied to case choices (`EVALUATION_PLAN.md` section 1; `fresh_drugs.py` selection log).

**Logging.** Every run writes `trace.jsonl`; every paid call is a row in a ledger (time, run, stage, term, model, tokens, cost, batch id); every evaluation run, bug fix, amendment and post-result analysis is an entry in `eval_runs.md`, written *before* the run when it is paid or post-result (the supplementary gabapentin run, the EWT confirmation). One unauthorized spend ($0.0576, a batch submitted before the owner's rejection took effect) is recorded in the log in plain words.

**Success criteria** (locked, `EVALUATION_PLAN.md` section 5c): a positive case is found if any of its terms is recommended at a scheduled run; the negative case succeeds if liver injury is never recommended, with the route recorded each run (no statistical flag, removed by triage, Label Checker yes, or valid Skeptic dismissal); lead times in months against the FDA posting and the label change; misses are misses, not zero lead time; the one-shot rule forbids tuning after any result (section 9). Outcome: 3 of 4 detected, negative control passed, $3.416268 (`eval_out/eval_results.md`, `d0cb878`).

**Human intervention.** A human approves or rejects every finding (UI buttons write to `feedback_log.jsonl`); the evaluation scores Canary's recommendation *before* the human approval step, by design; the owner approved every amendment, every post-result run and every spend in writing before it ran (`eval_runs.md`).

**Unintended actions and how they were handled.** The unauthorized batch above; the dropped-connection crash (fixed, documented, not repeated); a bug in the triage-step argument that shadowed a module name, found by the full test suite and fixed (`eval_runs.md`, product change entry). The limits of what the system can do are structural: it reads public data, makes read-only database queries, calls a model through one function that checks a spend limit, and writes only inside the project folder.

---

## 7.1 Final report

**What the final report rests on.** The full study is in `docs/evaluation_study.md` (locked plan, locked results, the gabapentin supplementary run and its root cause, the retention benchmark, the ten failed variants, Evidence-Weighted Triage and its confirmation, and the limitations), with every number traceable to an `eval_runs.md` entry and commit. The model choices and their limits are in `model_portability.md`. Setup and use are in `README.md`.

**Headline results, as recorded.**
- Locked evaluation: 3 of 4 positive cases detected before the label change (pirfenidone +39.0 months against the FDA posting, lamotrigine +57.0, dasatinib +189.0 at the Arm B resolution), gabapentin missed under the budget-reduced schedule, the negative control (rivaroxaban) passed, spend $3.416268 (`d0cb878`).
- The gabapentin miss has a measured cause: terms meeting the Evans criteria ranked 390, 181 and 271 below the triage cap of 150 (supplementary run, $1.787278, `bf59496`).
- The locked triage keeps 56.8% (held-out) to 67.8% (development) of the terms that later labels add (`8f8d84c`, `a168791`).
- Ten earlier variants did not improve on the locked triage; Evidence-Weighted Triage, with rules fixed in advance, kept 78.8% versus 62.4% on five fresh drugs (bootstrap 95% CI for the difference +10.8 to +21.6 points) and the gabapentin term was recommended at both confirmation cutoffs, as tuned evidence (`10df6a1`, `f899901`, `186d2d9`).

**What the final report must say it does not show** (stated in `docs/evaluation_study.md` section 7): the benchmark is a label proxy biased towards common terms; the rare serious terms EWT drops are not shown to be harmless; EWT did not help on the development drugs; one negative control and four positive cases are not rates; the Skeptic recommends most terms it sees; all work after the locked evaluation is post-result and exploratory.

**Deliverable map.**
| Course item | Where |
|---|---|
| Problem, user, environment | section 1.1 above; `CLAUDE.md`; `analysis/phase0_findings.md`, `analysis/phase1_findings.md` |
| Evaluation plan | `EVALUATION_PLAN.md` (locked, tag `eval-lock`) |
| Evaluation results and log | `eval_out/eval_results.md`, `eval_runs.md`, `docs/evaluation_study.md` |
| Code and tests | `agents/`, `orchestrator.py`, `model_router.py`, `tests/` (run each file with `python -u tests/<name>.py`) |
| Demo | `python start.py --demo` (precomputed runs in `demo_data/`) |
