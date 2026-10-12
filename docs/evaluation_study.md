# Canary: the evaluation study, from the locked plan to the EWT confirmation

Written for: a reader who wants the whole evaluation story in order (a paper reviewer, a program grader, the project owner). Plain language, facts only, no new analysis. Every number below comes from one of two places: a results file (`eval_out/eval_results.md`, `eval_out/eval_supplementary_gabapentin.md`, `ewt_confirmation/ewt_confirmation_gabapentin.md`) or an entry of the evaluation log `eval_runs.md`. Each section says which entry and which git commit. Short hashes are the first seven characters of the commit; where a hash is the lock or a result commit the full hash is given.

**How to read the evidence classes.** *Locked* = fixed before any result was seen and run once under `EVALUATION_PLAN.md`. *Post-result* = chosen after seeing the locked results; never changes the official result. *Tuned* = the idea was built from looking at that very case, so a good result there proves little. *Clean* = a test whose rules and cases were fixed before any ranking was computed on it.

**The official headline never changed: 3 of 4 positive cases detected, the negative control passed, gabapentin missed.**

---

## 1. The locked plan

Source: `EVALUATION_PLAN.md` v5, lock commit **`425633dd8729eeac52f9e060c19eb28297f6fa24`** (git tag `eval-lock`; message "Lock evaluation plan"; the only later change to the plan file is the line recording that hash, commit `2ea7427`). Log entry: "Approvals by the project owner, 2026-10-03" and "The evaluation run, 2026-10-03".

**What Canary does.** A user names a drug and a "time machine" date. Canary uses only FAERS reports received before that date, finds reaction terms that look disproportionately reported but are not on the drug's label, judges each, and writes a short cited brief. Pipeline: Resolver, Miner (drug-level PRR and chi-squared; a term is flagged when it meets the Evans criteria: at least 3 reports, PRR at least 2, chi-squared at least 4), Triage (drops terms whose exact name is on the label in force, ranks the rest by chi-squared, keeps the top 150), Label Checker (retrieval from the label version in force, then a yes / partial / no decision with a quoted sentence), Skeptic (three branches: real effect, reporting spike, confounding by indication; the code enforces when a term may be dismissed), Writer.

**Cases (section 1 of the plan).** Four positive cases and one negative case, no backup case.

| # | Drug | Signal | FDA posting | Label change (first brand-label version naming the reaction) | Role |
|---|---|---|---|---|---|
| 1 | gabapentin (Neurontin) | DRESS | 2009Q2 | 2011-08-11 | positive |
| 2 | pirfenidone (Esbriet) | drug-induced liver injury | 2019Q1 | 2019-08-02 | positive |
| 3 | lamotrigine (Lamictal) | aseptic meningitis | 2009Q3 | 2010-10-28 | positive |
| 4 | dasatinib (Sprycel) | chylothorax | 2023Q3 | 2024-08-02 | positive |
| 5 | rivaroxaban (Xarelto) | liver injury | 2017Q3 | none expected (FDA: no action) | negative |

Methadone (hypoglycemia) was dropped during planning, before any signal analysis, because the brand label already named the reaction before the FDA posting; lamotrigine replaced it. Atorvastatin is a development case only and is excluded from evaluation. No evaluation drug is on the project-policy exclusion lists. The target terms per case are fixed in plan section 2 and `eval_config.py` (for gabapentin both the current and the older MedDRA name of DRESS).

**Two arms (plan sections 3, 5, 6).** Arm A is statistics alone, quarterly: detection is the first quarter-end where any single target term meets the Evans criteria. Arm B is full Canary, at year-ends: detection is the first run where a target term is *recommended* by the Skeptic, judged before any human approval. Lead time (positive cases only) is measured in months against the FDA posting quarter and against the label change date; a case never detected is a miss, not zero lead time.

**Success definitions.** A positive case counts as found if any of its terms ends as a recommended signal. The negative case succeeds if Canary does not recommend a label update for liver injury; the route (no statistical flag, removed by triage, Label Checker yes, or Skeptic dismissal) is recorded each run. One negative case is a sanity check, not a rate. Known caveat written into the plan: the Xarelto label has carried "hepatitis (including hepatocellular injury)" since 2015-01-09, so the reaction was arguably already on the label.

**Locked pieces.** Term lists, Evans rule, triage rule (cap 150, minimum count 3, exact on-label drop), the Label Checker prompt and tool schema (frozen at its gold-set result, plan section 5a: 27 of 32 correct, 32 of 32 valid tool calls, spend $0.0889), the Skeptic design, the schedules, the model (Haiku, `claude-haiku-4-5-20251001`, for every call, no Sonnet), and the one-shot rule (plan section 9: a re-run only for a documented bug fix, never to tune).

**Amendments approved by the owner before any evaluation run** (log: "Approvals by the project owner, 2026-10-03", commits `63685ce`, `0968f6b`; the plan text itself was not edited):
1. Spend limit lowered from $15 to $10; working budget $9.50 (the whole Anthropic balance was $10.00).
2. Briefs are written only for the target terms and the top 10 recommended terms per run (detection does not depend on the brief).
3. The Message Batches API is used for cost only (half price): same model, prompts, schemas and inputs. Prompt caching was tested and does not apply (the shared prefix of each call was below Haiku's minimum cacheable size).
4. **The Arm B schedule was reduced because of budget** (setting `ARM_B_SCHEDULE = "reduced"`): per positive case, the first year-end on or after its first Arm A flag plus the last quarter-end before the label change; rivaroxaban 2017Q4, 2018Q4, 2019Q4. The locked schedule would have been 39 runs (about $21.06 at the measured $0.54 per run) against $9.50. Arm B lead times are therefore at the resolution of those runs, which is coarser and conservative.

**Infrastructure and bug fixes made before and during the run, all logged** (`eval_runs.md`, "Infrastructure changes" and the evaluation-run entries): RAG backend moved from numpy to Chroma with identical top-5 results on 36 of 36 checks (`328d02e`); the triage cap is passed as 150 explicitly because the locked file's default constant is 25 (`945ecc1`); Skeptic prompt rules and strict tool use (`6fd2e25`); concurrency and determinism fixes (`919be29`); a PubMed query bug fix (words were ANDed instead of searched as an exact phrase); spend-ledger race fix and strict batch limit check (`932970b`); a documented bug fix for a dropped network connection that crashed the rivaroxaban 2018Q4 run (retry of at most 3 attempts, `c614ba8`; the finished 2017Q4 run was not repeated); the `--resume-cap` option (`13116fa`). **One unauthorized spend is recorded plainly**: a rehearsal script submitted a Label Checker batch costing $0.0576 before the owner's rejection of the tool call took effect (`f46511c`); the approved rehearsal then resumed that batch for free. The rehearsal on a development drug cost $0.146721 in total.

## 2. The locked results

Source: `eval_out/eval_results.md`; log entry "The evaluation run, 2026-10-03". Results committed as **`d0cb878fe59eb60b9813b681f56e53d264f79611`** (rivaroxaban `d5dcc3b`). Code ran from `d5dcc3bdac0061413b3966862920233979d43d43` for the positive cases (rivaroxaban 2017Q4 from `26ae305`, its 2018Q4 and 2019Q4 from `13116fa`). Run on 2026-10-03, 11:30 to 17:13.

**Detection (Arm B, judged before human approval): 3 of 4 positive cases detected.**

| Case | Arm A first flag | Arm A lead vs posting / vs label (months) | Arm B detection | Arm B lead vs posting / vs label (months) | Spend |
|---|---|---|---|---|---|
| pirfenidone | 2015Q2 | +45.0 / +49.1 | **2015Q4** (HEPATOTOXICITY only) | +39.0 / +43.0 | $0.28 |
| lamotrigine | 2004Q1 | +66.0 / +78.9 | **2004Q4** | +57.0 / +69.9 | $0.38 |
| dasatinib | 2007Q4 | +189.0 / +199.1 | **2007Q4** | +189.0 / +199.1 | $0.10 |
| gabapentin | 2006Q1 | +39.0 / +64.4 | **not detected** (2006Q4 and 2011Q2: no statistical flag) | n/a | $1.07 |
| rivaroxaban (negative) | 2012Q2 | +63.0 / n/a | not recommended in any run | n/a | $1.58 |

Positive cases stop at their first detection, so pirfenidone, lamotrigine and dasatinib ran one Arm B run each; gabapentin ran its two scheduled runs; rivaroxaban ran three. Arm A rounded to the Arm B schedule gives the same detection runs for the three detected cases (2015Q4, 2004Q4, 2007Q4) with leads of +39.0, +57.0 and +189.0 months against the posting.

**Negative control: passed.** Rivaroxaban 2017Q4, 2018Q4 and 2019Q4: all three target terms had no statistical flag in every run (route 1 of the plan), and no run recommended liver injury. The Skeptic recommended 132 of 135, 128 of 135 and 124 of 134 examined terms in those runs. One negative case is a sanity check, not a rate.

**Alert burden (counts only; non-target terms are not judged).** Arm A: all Evans-flagged terms at the last cutoff before the label change. Arm B: terms recommended in the case's final Arm B run.

| Case | Arm A flagged (last cutoff before label) | Arm B recommended in its final run |
|---|---|---|
| rivaroxaban | n/a | 124 (2019Q4) |
| pirfenidone | 183 | 48 (2015Q4) |
| lamotrigine | 610 | 96 (2004Q4) |
| gabapentin | 685 | 123 (2011Q2) |
| dasatinib | 398 | 22 (2007Q4) |

**Routes recorded.** Pirfenidone 2015Q4: HEPATOTOXICITY recommended; DRUG-INDUCED LIVER INJURY and LIVER INJURY had no statistical flag. Lamotrigine 2004Q4: MENINGITIS ASEPTIC recommended. Dasatinib 2007Q4: CHYLOTHORAX recommended. Gabapentin 2006Q4 and 2011Q2: no statistical flag for either DRESS name.

**Cost.** Total **$3.416268** of the $9.50 working budget (1,944 calls in 25 batches), per case: rivaroxaban $1.5833, pirfenidone $0.2787, lamotrigine $0.3835, gabapentin $1.0704, dasatinib $0.1003. The limit was never reached; no stop record; no incomplete case.

**Caveats recorded with the results.**
- The gabapentin miss is a consequence of the budget-reduced schedule: Arm A flags the target term from 2007Q2 through 2010Q3, but the reduced schedule ran only 2006Q4 and 2011Q2, where it is not flagged.
- Arm B lead times are coarser than under the locked schedule (conservative).
- The Skeptic recommends most terms it examines (87% to 99% of the terms it examined in every evaluation run except pirfenidone, where it was 48 of 76, 63%; about 90% in the Lipitor development run), so detection mostly tracks whether the target is flagged and survives triage, not the Skeptic's discrimination. Alert burden is high.
- Pirfenidone was detected through HEPATOTOXICITY only; the other two target terms had no flag.
- One negative case; the Xarelto label caveat above.
- Detection is judged before any human approval step; briefs were written only for target terms and the top 10 recommended terms (this does not affect detection).
- Counts for the rare target terms are tiny.

## 3. The gabapentin supplementary run and its root cause

Source: log entries "Supplementary run, gabapentin: a POST-RESULT addition" (written before the run, commit `1cb59f0`; script `952dd28`) and "result of the supplementary gabapentin run" (results `bf59496`, log `bb63d71`); results file `eval_out/eval_supplementary_gabapentin.md`.

**What and why.** After the main results the owner and I saw that Arm A flags gabapentin's target term from 2007Q2 through 2010Q3, while the reduced schedule had skipped those years. A post-result run covered the four year-ends of the original locked gabapentin schedule that the reduced schedule skipped: 2007Q4, 2008Q4, 2009Q4, 2010Q4. Same model, prompts, schemas, batching and ledger; stop at first detection; hard stop $2.50 of additional spend. It was announced before it ran and it does not change the official result.

**Outcome: not detected at any of the four.** All four ran. At 2007Q4, 2008Q4 and 2009Q4 the older-named term (DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS) **did meet the Evans criteria** (a=8, PRR 2.90, chi-squared 9.8; a=20, PRR 4.03, chi-squared 44.6; a=22, PRR 3.00, chi-squared 28.9) but ranked **390 of 523, 181 of 580 and 271 of 633** by chi-squared among the triage survivors, **below the locked cap of 150**. Triage removed it before the Label Checker saw it (route 2: removed by triage, below the cap). At 2010Q4 it no longer met Evans (a=22, PRR 1.95). The current-named term had no reports at any cutoff.

**Root cause: the triage cap.** The miss was not a Skeptic dismissal; the Skeptic never saw the term. The same effect had been recorded on the demo drugs before the evaluation (linezolid SIADH ranked 288 of 622, lithium DRESS 391 of 799; log entry "known limitation found on a demo drug"). Under plan section 9 this goes to the future-work list; no re-run and no change to the cap of 150.

**Spend.** $1.787278 (959 calls in 12 batches): 2007Q4 $0.3598, 2008Q4 $0.3607, 2009Q4 $0.5336, 2010Q4 $0.5332. Whole evaluation ledger after this run: **$5.203546** of $9.50. Code ran from `952dd28`.

## 4. The retention benchmark and the locked triage's baseline

Source: log entries "Post-result exploratory work, part 3" (design fixed before any result, commit `c068243`) and "results of part 3" (development results `1fdc4c5`, held-out result `8f8d84c`, log `a168791`). Branch `triage-v2`. Free and offline; no paid call.

**What it measures.** For each drug and each year-end cutoff from 2004 to 2025 (at least 50 reports and a label in force), the *benchmark* is the set of Evans-flagged, not-on-label terms (the triage survivors) that a **later** label version (within 5 years) names in an adverse-reactions or warnings section, by an exact-phrase match (one-word terms under 6 letters excluded; ambiguous cases A1 and A2 reported separately). **Retention** is the share of those terms that are still in the final list of 150, pooled over the cutoffs where the cap binds (survivors above 150). Stated caveat: a label is a lagging, imperfect stand-in for "a real signal", and the benchmark is biased towards common terms.

**Split, fixed before any result.** Development: gabapentin, linezolid, lithium. Held-out: pirfenidone, lamotrigine, dasatinib, rivaroxaban, atorvastatin, ixazomib (ixazomib's brand labels, NDA208462, were indexed first, free data preparation).

**Baseline of the locked triage** (retention, benchmark terms n):

| Drug | n | Locked triage keeps |
|---|---|---|
| gabapentin | 49 | 39 (79.6%) |
| linezolid | 78 | 50 (64.1%) |
| lithium | 53 | 33 (62.3%) |
| **development pooled** | **180** | **122 (67.8%)** |
| pirfenidone | 1 | 1 |
| lamotrigine | 105 | 24 (22.9%) |
| dasatinib | 48 | 45 (93.8%) |
| rivaroxaban | 32 | 20 (62.5%) |
| atorvastatin | 153 | 99 (64.7%) |
| ixazomib | 13 | 11 (84.6%) |
| **held-out pooled** | **352** | **200 (56.8%)** |

The locked triage therefore keeps only 56.8% (held-out) to 67.8% (development) of the terms that labels later add, at the cutoffs where the cap binds; for lamotrigine only 22.9%. By report-count band (3 to 10, 11 to 30, 31 to 100, over 100) it keeps 40.8%, 41.5%, 61.1% and 81.3% (part-3 entry).

## 5. The seven failed remedies in eleven variants

As the owner described them: seven ideas (grouping, reserved slots, three rankings, stratification, pruning, novelty quota, label-aware seating) in eleven variants. The log reports the variants below; term grouping was run inside both triage-v2 modes and its own effect is noted separately ("grouping changes nothing" for gabapentin). All five target cases below are on the development drugs, so any rescue is illustrative. None was a clean test. Every one is branch `triage-v2`, free, offline, opt-in and default off, and the default still reproduces the locked output on all 12 saved runs (tested each time).

**Targets** used throughout: gabapentin older-name DRESS at 2007Q4 (locked rank 390), 2008Q4 (181), 2009Q4 (271); linezolid SIADH 2019Q4 (288); lithium DRESS 2017Q4 (391; older name 566). **Caught targets that must stay in:** pirfenidone hepatotoxicity 2015Q4, lamotrigine aseptic meningitis 2004Q4, dasatinib chylothorax 2007Q4.

| # | Idea | What it changed | Result | Commits |
|---|---|---|---|---|
| 1 | Term grouping + reserved serious slots, as specified (`reserve_first`) | Merged renamed MedDRA terms (14 groups); reserved up to 20 of the 150 slots for serious (EMA IME list) terms | 0 of 3 gabapentin targets; linezolid and lithium not rescued; a no-op whenever at least 20 serious terms already rank inside the top 150 | pre-log `87cd0ac`; code `388d751`; log `2dc8325` |
| 2 | Same, variant `reserve_beyond` | The plain fill limited to 130 slots; 20 reserved slots to the best serious terms outside it | 1 of 3 gabapentin (2008Q4 only); linezolid and lithium not rescued | `388d751`; bug fix `a6cbd5b` (list size), log `b891215` |
| 3 | Rank by PRR lower bound | Replaced chi-squared in the ranking step | Failed: ranked every target lower | pre-spec `e435198`; code `aa0e722`; results `db06945` |
| 4 | Rank by IC025 | Same | 1 of 5 targets (gabapentin 2008Q4, rank 143, inside the cap by 7 places); others moved up but not in | same |
| 5 | Rank by best of both | Better of the two ranks | 0 of 5 | same |
| 6 | Stratified triage (report-count bands with quotas) | Terms compete only against terms of similar report counts | **Failed**: held-out retention 27.3% vs 56.8% locked; development 42.8% vs 67.8%; no target rescued; no caught target lost | pre-spec `c068243`; development `1fdc4c5`; held-out `8f8d84c`; log `a168791` |
| 7 | Variant P: prune administrative terms, then cap | Removed non-clinical terms (fixed list) and the drug's top-3 indications | Held-out 55.7% vs 56.8%; development 66.7% vs 67.8%; targets moved 5 to 9 places, none in; 8 later-labeled benchmark terms pruned (indications and "drug interaction") | pre-spec `ab86ec7`; results `989894e` |
| 8 | Variant PN: P plus a 40-slot novelty quota | Up to 40 slots for terms whose last-4-quarter share is at least twice the drug's | Held-out 54.5%; development 66.7%; **one rescue** (gabapentin 2008Q4, via the quota), 2007Q4 and 2009Q4 not rescued | same |
| 9 | Variant PL: P plus label-aware seating | Ranked by the average of the chi-squared rank and the distance from the label | Held-out 42.0%; development 46.7% (sharply lower); no target rescued | same |
| 10 | Variant PLN: all three | PL ranking plus the novelty quota | Held-out 41.2%; development 46.7%; one rescue (gabapentin 2008Q4, quota) | same |

Retention numbers are pooled over capped cutoffs; the locked baselines are those in section 4 (200 of 352 held-out, 122 of 180 development). Part 4 explicitly labelled development results as tuned evidence and the held-out results as a *second look*, because those drugs had been used once in part 3. Conclusions recorded in the log: part 3, "the stratified design FAILED"; part 4, "none of the four variants improved on the locked triage"; the cap of 150 stays the binding constraint; everything goes to the future-work list under plan section 9.

## 6. Evidence-Weighted Triage (EWT)

**Inspiration and credit.** The WHO Uppsala Monitoring Centre's vigiRank (Caster et al., *Drug Safety* 2014; *Pharmacoepidemiology and Drug Safety* 2017) ranks signals by strength of evidence, not volume alone. EWT is Canary's own adaptation with FAERS fields and equal weights; it is not vigiRank and is not called that in any file.

**Feasibility and infrastructure.** The main DuckDB store held only 3 of the 6 candidate components. The owner chose to extend from the raw FAERS zips into a *separate* file `data/canary_ext.duckdb` (log "Infrastructure change for exploratory work only, part 5", commits `16f7585`, `2317789`): 24,812,425 reports, same report ids, 0 missing; `data/canary.duckdb` SHA-256 `f979187d8f544d589e8963d91e55b377e47f2a45442485e374e1bd17f33cd1f3` unchanged. Dechallenge and rechallenge were dropped as too patchy. Coverage gaps recorded: 2004 has no country data, 2005 half; occupation is 75 to 85% filled up to 2010.

**The design, fixed before any ranking** (log "part 5, Step 3", commit **`10df6a1`**). Survivors are the locked ones (Evans-flagged, at least 3 reports, exact name not on the label). Five components: (1) IC025; (2) reports from health professionals (occupation MD, HP, PH, RN); (3) reports first received in the last 4 quarters; (4) distinct countries (occurrence country, else reporter country; blanks are unknown); (5) serious-outcome reports (outcome code DE, LT, HO, DS, CA or RI; OT excluded). Each becomes a rank (ties share the average rank); the score is the plain average of the five ranks (lower is better); ties by chi-squared rank, then name; keep the top 150, always min(150, survivors). No weights fitted, no tuning after results.

**Win criterion fixed in advance.** On the five fresh drugs, EWT's pooled capped retention at least 3 percentage points higher than the locked triage's *and* no caught target lost.

**Fresh-drug selection** (log "part 5, Step 2", commit **`bd88b30`**, owner decisions `9e8279a`). Chosen mechanically from the FDA postings and Drugs@FDA label histories, without looking at FAERS rankings: section A of the candidate file (a dated label change after the posting), minus drugs already used or examined in the project, minus the project-policy exclusion lists; brand NDA with a first label by 2012-12-31; the five with the most brand-label versions. Montelukast (Singulair NDA020829, SJS), voriconazole (Vfend NDA021266, photosensitivity), docetaxel (Taxotere NDA020449, neutropenic enterocolitis), bosentan (Tracleer NDA021290, anaphylaxis and DRESS), enzalutamide (Xtandi NDA203415, severe cutaneous reactions). Ticagrelor and quetiapine were skipped as close competitors of products excluded by project policy; bosentan stays in (owner decision). Before the single fresh run, all five drugs were confirmed in the label index (36, 34, 26, 22 and 18 distinct label dates) with no skipped cutoffs.

**Results** (log "results of part 5", commit **`f899901`**; retention of benchmark terms, capped cutoffs):

| Split | n | Locked | EWT |
|---|---|---|---|
| **Fresh (clean test, run once)** | **274** | **171 (62.4%)** | **216 (78.8%)** |
| montelukast / voriconazole / docetaxel / bosentan / enzalutamide | 43 / 109 / 78 / 29 / 15 | 65.1 / 56.9 / 68.0 / 58.6 / 73.3% | 90.7 / 70.6 / 80.8 / 86.2 / 80.0% |
| Held-out (third look) | 352 | 200 (56.8%) | 250 (71.0%) |
| Development (tuned evidence) | 180 | 122 (67.8%) | 119 (66.1%) |

**Statistics (post-hoc, not part of the pre-registered design).** Headline: the **bootstrap 95% confidence interval for the retention difference on the fresh drugs, resampling the 56 drug-cutoffs with benchmark terms (10,000 resamples), is +10.8 to +21.6 points** (point estimate +16.4). McNemar's exact test on the 274 paired decisions (57 kept only by EWT, 12 only by the locked triage) gives p = 3.7e-8, but that p-value is overstated because the same terms repeat across year-end cutoffs; the bootstrap by drug-cutoff is the more honest interval. All five fresh drugs improved (+6.7 to +27.6 points); leaving one drug out keeps the pooled difference between +14.7 and +18.2 points.

**Composition check (post-hoc).** Terms EWT swapped in (4,804 term-instances): median 35 reports, median chi-squared 61, 36.9% on the EMA IME seriousness list. Terms swapped out (4,804): median 8 reports, median chi-squared 343, 32.6% on the list. Retention by report count, fresh: fewer than 20 reports 42.3% (locked) vs 50.0% (EWT), n=104; 20 or more 74.7% vs 96.5%, n=170. Development, under 20: 50.8% vs 34.4% (n=61). Held-out, under 20: 34.9% vs 39.5% (n=86). So the gain is mostly from common terms; for rare terms it is small on the fresh and held-out drugs and negative on the development drugs, and the benchmark cannot show what is lost when a rare serious term is dropped.

**Targets under EWT (tuned evidence)** (EWT position; in if 150 or better; locked rank): gabapentin older-name DRESS 2007Q4 173 out (390); 2008Q4 71 **in** (181); 2009Q4 103 **in** (271); linezolid SIADH 135 **in** (288); lithium DRESS current name 121 **in** (391); lithium older name 352 out (566). **Caught targets stay in:** pirfenidone 2015Q4 34 (locked 61), lamotrigine 2004Q4 23 (24), dasatinib 2007Q4 15 (1); later cutoffs lamotrigine 2010Q3 16 (20), dasatinib 2024Q2 1 (2). Final list sizes are exactly min(150, survivors) at every cutoff; EWT reshuffles roughly a third of a capped list (for example 18 to 75 terms swapped per capped cutoff on the fresh drugs).

**Conclusion recorded.** By the criterion fixed in advance, EWT beat the locked triage on the fresh drugs (+16.4 points, no caught target lost).

**The Step 1 confirmation: gabapentin under EWT, paid and illustrative** (log entries before the run `01829fc`, code `be3b23d`, results `186d2d9`; results file `ewt_confirmation/ewt_confirmation_gabapentin.md`). EWT was designed after seeing gabapentin, so this is tuned evidence and does not change the official 3 of 4. It ran gabapentin 2008Q4 and 2009Q4 through EWT triage and the same Label Checker, Skeptic and Writer (same model, prompts, batching and ledger). **The older-name DRESS term was recommended at both cutoffs**: 2008Q4 (a=20, PRR 4.03, chi-squared 44.6; EWT position 71; Label Checker no; Skeptic recommended, real effect 0.75, spike 0.65, confounding 0.35; Skeptic recommended 139 of 143 examined terms; batches `msgbatch_01PWsR1NgUDGSYwABfQfc7CP`, `msgbatch_01LcbH91eWyumEjKBth9hvvi`, `msgbatch_01HEkiqrp3FDaNSBkiTGxaTw`) and 2009Q4 (a=22, PRR 3.00, chi-squared 28.9; EWT position 103; Label Checker no; Skeptic recommended, 0.75 / 0.65 / 0.40; 130 of 139; batches `msgbatch_01CerMadmeksyoNPqtq62m4i`, `msgbatch_015ggyY3tjLqzC2hhafvi2V3`, `msgbatch_01HtUbpUJ5uJbSNFiw3iSeev`). Cost **$0.3522** ($0.1630 and $0.1892) against a $1.00 cap; evaluation ledger now $5.5558. Caveats recorded: tuned evidence; the Skeptic recommends about 97% and 94% of the terms it examines; its own reasoning says the effect "may be newly recognized or rare".

**Product changes after the study** (not evaluation changes; log "Product change" entries): EWT became the default triage for normal app runs, the locked triage stays an explicit option and the evaluation runner keeps the locked triage (`3b9adce`); a model selector (Haiku, Sonnet 5.5, Opus 5.5, one OpenRouter free model) with cost confirmation and a $3.50 hard limit for remaining paid work, where the evaluation refuses any model but Haiku (`1943474`); the free option is Nemotron 3 Super, tested live on the 32-row Label Checker gold set (26 of 32, 81.3%, against Haiku's 27 of 32, 84.4%; all outputs valid), while the three other candidates were blocked by the free tier's daily cap (`model_portability.md`).

## 7. Limitations: what the study does and does not show

**What it shows.**
- Under the locked plan, Canary detected 3 of 4 positive cases before the label change, with Arm B lead times of +39.0 months (pirfenidone), +57.0 (lamotrigine) and +189.0 (dasatinib) against the FDA posting, and passed its one negative control, at a total cost of $3.416268.
- The gabapentin miss has an identified cause: terms that meet the Evans criteria but rank below the triage cap of 150 never reach the Label Checker. That cause was measured on three gabapentin year-ends and seen on two demo drugs.
- The locked triage retains 56.8% to 67.8% of the terms that later labels add, at capped cutoffs.
- Ten triage variants built before EWT (grouping with reserved slots in two modes, three alternative rankings, stratification, and four prune and seating variants) did not improve retention on the held-out drugs; the only target rescues were marginal or came from a quota tuned on gabapentin.
- EWT, with rules fixed in advance, kept more later-labeled terms on five fresh drugs: +16.4 points, bootstrap 95% CI +10.8 to +21.6, all five drugs better, no caught target lost; it also did so on the held-out drugs (third look).

**What it does not show.**
- It does not show that the extra terms EWT keeps are real signals. The benchmark is a label proxy: labels lag, and the benchmark counts only terms a later label names, which are mostly common terms.
- It does not show that the rare serious terms EWT drops are harmless. The retention measure cannot reward keeping a rare serious term or penalise dropping one; the composition check shows EWT swaps out terms with a median of 8 reports and high chi-squared.
- It does not show that EWT works on the development drugs: there it did not help overall (66.1% vs 67.8%), and it hurt rare terms.
- It is not a validation of Canary as a detector. One negative control is a sanity check, not a rate; four positive cases are not a detection rate; the reduced schedule made Arm B lead times coarse; detection is judged on the Skeptic's recommendation before human approval, and the Skeptic recommends most of what it examines (87% to 99% in all evaluation runs but pirfenidone's 63%), so detection mostly tracks triage and the statistical flag.
- The gabapentin confirmation under EWT is tuned evidence on the case that motivated the idea; it is an illustration, not a validation.
- The statistical test: McNemar's p-value assumes independent instances while terms repeat across cutoffs, so the bootstrap interval is the headline; with five fresh drugs there are only five drug-level comparisons.
- Data limits: FAERS reports are spontaneous and uncounted for exposure; counts for the rare target terms are tiny; early quarters lack country and some occupation data; the openFDA API is not used for any reported number (drug-level counts come from the bulk files).
- Everything after the locked evaluation is post-result and exploratory (plan section 9); none of it changes the official result.

## Traceability index

| Topic | Log entry in `eval_runs.md` | Commit |
|---|---|---|
| Plan lock | Lock record (`EVALUATION_PLAN.md` section 11) | `425633d` (full hash above), tag `eval-lock` |
| Amendments, reduced schedule | "Approvals by the project owner, 2026-10-03" (both sets) | `63685ce`, `0968f6b` |
| Locked results | "The evaluation run, 2026-10-03" | `d5dcc3b`, `d0cb878`, log `d6e5d7a`, `0197a69` |
| Supplementary gabapentin | "Supplementary run, gabapentin" and its result | `1cb59f0`, `952dd28`, `bf59496`, `bb63d71` |
| Triage v2 (variants 1 and 2) | "triage v2" and "results of the FREE statistics-only check" | `87cd0ac`, `388d751`, `2dc8325`, `a6cbd5b`, `b891215` |
| Rankings (variants 3 to 5) | "part 2" and "results of the part-2 free check" | `e435198`, `aa0e722`, `db06945` |
| Stratified triage, retention benchmark | "part 3" and "results of part 3" | `c068243`, `1fdc4c5`, `8f8d84c`, `a168791` |
| Prune, novelty, seating (variants 7 to 10) | "part 4" and "results of part 4" | `ab86ec7`, `989894e` |
| EWT infrastructure, fresh drugs, design, results | "part 5" entries | `16f7585`, `2317789`, `bd88b30`, `9e8279a`, `10df6a1`, `0d7f638`, `f899901` |
| Gabapentin confirmation under EWT | "Post-result illustrative confirmation" and its results | `01829fc`, `be3b23d`, `186d2d9` |
| Product changes | "Product change" entries | `3b9adce`, `1943474` |
