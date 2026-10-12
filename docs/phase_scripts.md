# Canary: Phase 0 data feasibility test

Checks whether public FDA/NLM data can support Canary, using atorvastatin as the test drug.
Data only: no agents, no LLM calls. Not medical advice.

## Run

```
pip install -r requirements.txt
python phase0_feasibility.py
```

- Prints results for all 8 steps and saves the raw numbers to `phase0_results.json`.
- Responses are cached in `./cache`, so re-runs are fast and polite to the APIs. Delete the folder to refresh.
- No key is needed. openFDA rejects `limit` above 100 without a key, and the script stays within that.
  For higher rate limits, put `OPENFDA_API_KEY=...` in a `.env` file (git-ignored).
- The first run takes a few minutes (about 150 calls at 0.5 s spacing).

Results and the GO/NO-GO verdict are in `phase0_findings.md`.


## Phase 1 tests

Setup: `pip install -r requirements.txt`, and put `OPENFDA_API_KEY` and `ANTHROPIC_API_KEY` in `.env` (git-ignored).
Test B step 3 needs `pdftotext` (poppler) on the PATH. On Windows set `PYTHONIOENCODING=utf-8` and use `python -u` for progress output.
All calls are cached in `./cache`, so re-runs are free. Results and verdicts are in `phase1_findings.md`.

| Test | Command | Output |
|---|---|---|
| Keys check | `python check_keys.py` | OK or error per service |
| A: answer key | `python test_a_answer_key.py` (first run about 10 min: Wayback needs 6 s between calls) | `answer_key.csv`, `test_a_candidates.csv` |
| B: historical labels | `python test_b_historical_labels.py` (steps: no env var = Lipitor set search; `STEP=2` DailyMed versions; `STEP=3` Drugs@FDA label PDFs) | `test_b_brand_versions.json`, `test_b_drugsatfda.json` |
| C: case study, API version (report-level, superseded) | `python test_c_case_study.py` (about 12 min) | `test_c_results.json`, `case_study_atorvastatin_api_report_level.csv` |
| C: case study, drug-level from bulk files | `STAGE=parse python test_c_drug_level.py` (downloads 52 quarters, about 1.1 GB, resumable, about 45 min) then `STAGE=agg python test_c_drug_level.py` | `case_study_atorvastatin.csv`, `case_study_yearly.csv`, `test_c_drug_level_results.json` |
| D: LLM eval | `python build_label_text.py` then `python test_d_llm_eval.py` (about $0.40 with the cache empty) | `label_gold_set_DRAFT.csv`, `llm_eval_results.csv` |
| E: speed and calls | `python test_e_product_feasibility.py` (uses its own `cache_test_e` folder so the run is cold) | `test_e_results.json` |
| F: bulk files | `STEP=1b python test_f_bulk_files.py` (probe), `STEP=2` (download 2011Q1, 21 MB), `STEP=3` (analyze) | `test_f_results.json`, `data/faers/` |
| Case study candidate selection (data preparation only, no signal analysis) | `STAGE=universe`, then `STAGE=parse` (all 90 quarterly files; about 15 min once the zips are on disk, about 1 hour with downloads), `STAGE=count`, `STAGE=labels`, `STAGE=write`, each as `python -u case_study_candidates.py` | `case_study_candidates_for_review.md`, `.csv`, `case_study_candidates_near_misses.csv` |

Shared helpers (caching, retries, delays, PRR, chi-squared, model names) are in `canary_utils.py`.
Throwaway probe scripts are in `scratch/` (`probe_terms.py` imports from the parent folder, so run it from the project root with `PYTHONPATH=.`). Console logs are `test_*_output.txt`.


## Phase 2 tasks

Setup: `pip install -r requirements.txt fastembed duckdb pyarrow`. Set `PYTHONIOENCODING=utf-8`. Results and verdicts are in `phase2_findings.md`; the draft evaluation plan is `EVALUATION_PLAN.md`.

| Task | Command | Output |
|---|---|---|
| 0: case picks | Edit already applied (the exclusion list is applied there); delete `data/candidates` and `data/candidates_v2` by hand | |
| 1: evaluation plan | Read `EVALUATION_PLAN.md` | draft, not locked |
| 2: label change dates | `python -u task2_label_dates.py` (downloads brand label PDFs, a few minutes) | `task2_label_scan.json` |
| 3: label RAG | `python -u rag_index.py build atorvastatin gabapentin` (about 13 min), `python -u rag_index.py add simvastatin`, `python -u rag_index.py test` | `data/rag/` (index, `retrieval_test.json`) |
| 4: Miner store and run | `python -u miner_store.py build` (about 11 min, 1.7 GB, needs the zips in `data/faers`), then `python -u miner.py atorvastatin 2011` | `data/canary.duckdb` |
| 5: Skeptic data checks | `python -u skeptic_checks.py` | console |
| 6: PubMed check | `python -u pubmed_check.py` | console |
| 7: LLM label checker | `python -u task7_label_checker.py` (needs a valid `ANTHROPIC_API_KEY`; about 10 Haiku calls) | `simvastatin_gold_set_DRAFT.csv`, `task7_results.json` |
