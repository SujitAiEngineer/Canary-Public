# Canary Evaluation Plan (v5, LOCKED at the commit recorded in section 11)

Status: **FINAL.** Term lists approved by the project owner as drafted. Nothing here was computed from FAERS for the evaluation drugs: no PRR, signal
flag, or time-machine result exists for any of them. Counts used to pick the cases (suspect report counts) and label reading are the only data touched.
Drug-name matching and all case facts are frozen in `eval_config.py` (section 8). Numbers in sections 4 and 5 were estimated on atorvastatin and simvastatin only (development drugs).
Skeptic design B (Haiku only), the Arm B year-end schedule and the $15 limit were decided by the project owner (sections 5b, 5d, 6). The class comparator lists are approved.

## 1. Cases

Four positive cases and one negative case. There is no backup case.

| # | Drug (brand) | Signal (FDA wording) | FDA posting quarter | Brand label application | Label change date (first brand-label version containing the reaction anywhere) | Role |
|---|---|---|---|---|---|---|
| 1 | gabapentin (Neurontin) | DRESS | 2009Q2 | NDA020235 (same label under NDA020882, NDA021129) | 2011-08-11 (supplement 50) | positive |
| 2 | pirfenidone (Esbriet) | Drug-induced liver injury | 2019Q1 | NDA022535 (NDA208780 tablets share the label) | 2019-08-02 (supplement 12) | positive |
| 3 | lamotrigine (Lamictal) | Aseptic meningitis | 2009Q3 | NDA020241 | 2010-10-28 (supplement 44) | positive |
| 4 | dasatinib (Sprycel) | Chylothorax | 2023Q3 | NDA021986 | 2024-08-02 (supplement 28) | positive |
| 5 | rivaroxaban (Xarelto) | Liver injury | 2017Q3 | NDA022406 | none expected (FDA: no action needed). Known caveat below. | negative |

Label change dates were found by reading every brand-label PDF in Drugs@FDA and taking the first version whose text contains the reaction (`phase2_findings.md`, Task 2;
`task2_label_scan.json`). Quoted sentences are in `eval_config.py`.

**Label change date definition (applies to every case).** The label change date is the date of the **first brand-label version in which the reaction appears anywhere in the
label**, including postmarketing (adverse reaction) lists, not only warnings or boxed warnings. The date is the Drugs@FDA document date of that version. "The reaction"
means wording that matches the case's term list in section 2 (or a clear synonym of it). This is also the definition of "on the label" used by the Label Checker.

**Known caveat for the negative case.** The Xarelto label has contained "hepatitis (including hepatocellular injury)" in its postmarketing list since the 2015-01-09
version, which is before the 2017Q3 FDA posting and unchanged in all 19 later versions through 2026-03-26. Under the definition above, the reaction was arguably already
on the label. This is recorded as a caveat, not a reason to change the case: the Label Checker may legitimately say "partial" or "yes" for rivaroxaban, and the route
record in section 5 shows when it does.

Excluded from evaluation: **atorvastatin** (development case only, section 7). No evaluation drug is on the project-policy exclusion lists (checked in code against the local exclusion file).

### Excluded during planning: methadone (hypoglycemia, posted 2022Q3)

Methadone was dropped before any signal analysis. Reason, from the label: the brand label, Dolophine (NDA006134), already listed "Metabolic: hypoglycemia, hypokalemia,
hypomagnesemia, weight gain" in its postmarketing list in the 2021-06-03 version (earlier versions do not). Under the label change date definition above, the label change
(2021-06-03) is **before** the FDA posting (2022Q3), so there is no lead time to measure. The dedicated warning ("5.17 Hypoglycemia") that came on 2023-12-18 appears only on the other
methadone applications (NDA017058, NDA017116, NDA021624); Drugs@FDA lists no Dolophine label after 2021. The other methadone products did not carry the term before 2023-12-18, but
the brand-label rule picks Dolophine. Replaced by lamotrigine as positive case 3.

## 2. What counts as the signal: MedDRA preferred terms (PTs)

Approved as drafted. Each PT is tested on its own for the primary detection; the union of the list (a report counts once if it carries any PT in the list) is a secondary result. PT names are as FAERS writes them (upper case). Both old and
new MedDRA names are listed where a term was renamed, because the bulk files span 2004 to 2026. Terms already on the label at approval are left out, so the list targets the *new*
finding. Term lists are frozen at approval. No term is added or removed after anyone sees a signal result.

**1. gabapentin, DRESS**
- `DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS`: the current PT for DRESS.
- `DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS`: the older PT name for the same concept, likely the one used in 2008 to 2010 data.
- Not included: `DRUG HYPERSENSITIVITY`, `HYPERSENSITIVITY`, `RASH`, `EOSINOPHILIA` (too broad or only one component; gabapentin already carries hypersensitivity wording).

**2. pirfenidone, drug-induced liver injury**
- `DRUG-INDUCED LIVER INJURY`: the FDA wording.
- `LIVER INJURY`: the generic PT reporters use when the cause is not coded as drug-induced.
- `HEPATOTOXICITY`: the usual PT for a reported hepatic toxic effect.
- Not included: `ALANINE AMINOTRANSFERASE INCREASED`, `ASPARTATE AMINOTRANSFERASE INCREASED`, `LIVER FUNCTION TEST ABNORMAL`, `HEPATIC ENZYME INCREASED` (enzyme elevations were already on the Esbriet label at approval, so these are not the new signal).

**3. lamotrigine, aseptic meningitis**
- `MENINGITIS ASEPTIC`: the FDA wording and the MedDRA PT.
- Not included: `MENINGITIS` (includes infectious), `CENTRAL NERVOUS SYSTEM INFECTION` (FDA grouped it, but it is infectious and would add noise).

**4. dasatinib, chylothorax**
- `CHYLOTHORAX`: the FDA wording and the only PT for the concept.
- Not included: `PLEURAL EFFUSION` (already on the Sprycel label; including it would hide the new finding), `CHYLOUS ASCITES` (different site).

**5. rivaroxaban, liver injury (negative case)**
- Same three PTs as case 2: `DRUG-INDUCED LIVER INJURY`, `LIVER INJURY`, `HEPATOTOXICITY`. Same concept, so the same list.
- Not included: `HEPATIC ENZYME INCREASED` and other enzyme terms, for the same reason as case 2. The list is kept identical to case 2 on purpose, so terms are not tuned per drug.

## 3. Signal rule

- Data: FAERS quarterly bulk files (ASCII), 2004Q1 to the cutoff, loaded in `data/canary.duckdb`. Drug-level counts only: the drug is Primary or Secondary Suspect (role code PS or SS).
  Latest case version per case known at the cutoff. Drug matching uses the name patterns in `eval_config.py` (section 8). The openFDA API is not used for any evaluation number.
- Cutoffs: cumulative, at each calendar **quarter end** (03-31, 06-30, 09-30, 12-31). Only reports loaded in a quarter up to and including the cutoff quarter count.
- 2x2 table per drug and signal concept: a = drug and signal, b = drug without signal, c = other drugs with signal, d = other drugs without signal. The comparator is every other drug in the database at that cutoff.
- **Evans criteria: PRR >= 2, chi-squared >= 4 (no Yates correction), and a >= 3.** The 2x2 table is built **per term** (each PT on its own). **Primary detection:** a quarter-end where **any single target PT individually** meets Evans. **Secondary result:** the same test on the union of the term list (a report counts once if it carries any PT in the list).
- Miner check done on atorvastatin: the store reproduces `case_study_atorvastatin.csv` exactly (10 of 10 checks).

## 4. Triage between the Miner and the Label Checker

The Miner flags hundreds of terms per drug. A cheap, deterministic filter decides which go to the LLM Label Checker. Implemented in `triage.py`; **fixed at lock**.

1. **Minimum count: a >= 3** (the Evans minimum, so no extra count filter beyond Evans). Tested against a >= 5 on atorvastatin, see below.
2. **Cheap first pass, exact or synonym:** drop a term if its exact PT phrase appears anywhere in the label version in force at the cutoff. US/UK spelling is folded (haem/hem, aemia/emia, oedema/edema, diarrhoea/diarrhea), and increased/increase/elevated inflections are accepted. No further synonym list. "Anywhere" matches the label change date definition. A term dropped here is on the label by definition and never reaches the LLM.
3. **Cap:** rank the remaining terms by chi-squared, descending, and send the **top 150** to the LLM Label Checker. Terms below the cap are never examined (counted as not recommended).

**Estimates, atorvastatin only (development case, year-end cutoffs 2008 to 2013):**

| Cutoff | Label in force | Evans flagged | After exact on-label drop (a>=3 / a>=5) | Best-ranked flagged target PT by chi-squared, a>=3 | a>=5 |
|---|---|---|---|---|---|
| 2008 | 2007-09-26 | 688 | 688 / 535 | DIABETES MELLITUS, 126 (HbA1c 178) | 124 (HbA1c 172) |
| 2009 | 2009-06-22 | 713 | 674 / 515 | HbA1c increased, 114 | 112 |
| 2010 | 2009-06-22 | 619 | 584 / 443 | HbA1c increased, 107 | 105 |
| 2011 | 2009-06-22 | 615 | 580 / 445 | HbA1c increased, 89 | 86 |
| 2012 | 2012-11-02 | 578 | 540 / 405 | DIABETES MELLITUS, 58 (HbA1c 70) | 58 (70) |
| 2013 | 2012-11-02 | 597 | 558 / 421 | DIABETES MELLITUS, 30 (T2DM 58, HbA1c 82) | 30 (58, 82) |

- **Minimum count test (a >= 3 vs a >= 5).** In both settings the best-ranked target PT is inside the top 150 at **6 of 6** cutoffs, so by the pre-agreed rule (same or more cutoffs) the plan uses **a >= 3**. The ranks barely moved (at most 6 places), so the choice costs nothing on this drug and lets small early counts through, which matters for rare reactions. It rests on one drug.
- The exact first pass removed 0 to 39 terms per cutoff (up to about 6%) and removed no target PT at any cutoff.
- Cap 25 would have lost the target at every cutoff (best rank 30 to 126), which is why the cap is 150. Ranking by count alone put the best target at 6 to 64 and by PRR at 189 to 320, so chi-squared is kept.
- **Limits, stated up front:** one drug and six dependent cutoffs, so the cap is a development estimate, not a tested property. For every case we record at each quarter whether the target PTs passed or were removed by triage.
- **Label Checker cost, measured:** 32 forced-tool-call Haiku calls with five retrieved chunks cost $0.0867 in total, about **$0.0027 per term**. A full 150-term run is about **$0.41**, before caching (consecutive quarters with the same label version repeat many term prompts, so the cached cost is lower).

## 5. Label Checker rule, Skeptic, success definitions, and cost limit

### 5a. The global Label Checker rule (all cases)

- **yes** (same concept or a clear synonym on the label): the term is **dropped**. It is not a signal.
- **partial** (a related, narrower or broader concept): the term is **passed to the Skeptic with a "partially labeled" note**, quoting the label sentence.
- **no**: the term is **passed to the Skeptic**.

The final brief recommends a term only if it passes the Skeptic. This rule applies identically to the positive cases and to rivaroxaban.

**What does not count as "on the label".** Mentions of the reaction in the indications, in efficacy results, or as a trial endpoint (for example "reduce the risk of myocardial infarction"), and findings in animals or other nonclinical studies, are not adverse reactions on the label. This is written into the Label Checker prompt (section 10).

**Gold set and final test of the Label Checker** (32 rows: 20 atorvastatin, 12 simvastatin, `label_gold_set_v2_DRAFT.csv`; all rows approved as final by the project owner; the owner's decisions: myocardial infarction = no, weight decreased = no, vomiting = partial). Haiku, forced tool call, hybrid retrieval top 5, run **once** with the final prompt:

- Accuracy **27 of 32 (84%)**; **32 of 32 valid tool calls**; spend $0.0889. The prompt is **frozen at this result, whatever the score**.
- Confusion table (rows gold, columns predicted):

| gold \ predicted | yes | partial | no |
|---|---|---|---|
| yes | 13 | 2 | 0 |
| partial | 1 | 5 | 1 |
| no | 0 | 1 | 9 |

- Atorvastatin 16 of 20, simvastatin 11 of 12. Effect of the two new rules: myocardial infarction is now no (it was yes), and vomiting, whose gold became partial, is predicted partial. Total accuracy is unchanged at 27 of 32, with a different set of misses.
- The five misses: LIVER INJURY (gold partial, said yes because of "hepatic failure": still the hepatitis-vs-liver-injury case the prompt names); TYPE 2 DIABETES MELLITUS (gold yes, said partial: the new trial-results rule pushed down a label that reports diabetes only as a CARDS trial result and in a W&P on HbA1c and glucose, a side effect of the rules); MALAISE (gold yes, said partial: the postmarketing list was not among the five retrieved chunks, a retrieval miss); ASTHENIA (gold partial, said no); WEIGHT DECREASED (gold no, said partial).
- **Yes versus partial**, the boundary that matters for section 5a, has three errors in 22 rows with a gold of yes or partial (two yes called partial, one partial called yes). This is the Label Checker's known weak spot and is carried into the paper as a limitation. Because a "partial" goes on to the Skeptic instead of being dropped, the cheaper error is the more common one.
- Section tagging bug fixed (pdftotext leaves headings inline, so every 2023 Zocor chunk had been tagged "FRONT"). Retrieval improved: the atorvastatin glucose warning is now the **top** result at the 2013 cutoff (it was 4th).

### 5b. The Skeptic: Tree-of-Thought, design B, Haiku only

Run only on terms that reach it under section 5a. Three steps, per term. **Every LLM call in the evaluation uses Haiku, including the final brief. There is no Sonnet escalation.**

**Step 1, code computes the evidence (no LLM)**, from the DuckDB store, the label check and PubMed. The evidence is split into three explicit branches and one shared context field. Each evidence item has an ID (for example `S2` for the second item in the spike block) so that a citation can be checked by code.
- **Branch 1, real effect:** the drug's own disproportionality numbers (a, PRR, chi-squared) at the cutoff, the Label Checker's decision and quoted sentence (including any "partially labeled" note), and the **PubMed count** of papers on drug plus the term, with adverse-effect wording, published before the cutoff, with the top 3 titles and PMIDs.
- **Branch 2, reporting spike:** drug cases and cases with the term, by year of first receipt, up to the cutoff.
- **Branch 3, indication-based confounding:** the share of the drug's cases with the term that list a related indication for any drug in the case (INDI).
- **Context field (neutral, visible to all three branches):** the same term's PRR for the **class comparators** at the same cutoff (lists in `eval_config.py`, section 8). Comparator evidence can mean a class effect (supports a real effect) or a shared confounder (supports confounding), so it does not belong to either branch.

**Step 2, one structured Haiku call per term.** A forced tool call whose schema has, for each of the three branches separately, a score from 0 to 1 (how well that branch explains the signal), a one-sentence reason, and the IDs of the evidence items it cites; then the **winner**: `real_effect`, `reporting_spike`, or `confounding`. Rules enforced by **code**:
- A term whose winner is `real_effect` is **recommended**.
- A term is **dismissed** only if the winner is `reporting_spike` or `confounding` **and** the winning branch's cited IDs are items from that branch's own evidence (a spike item or an indication item). **The Skeptic may not dismiss a signal on comparator evidence alone.** A dismissal that cites only context (comparator) items, or no valid item, is invalid: the term is **not dismissed** and is recommended with the flag "uncertain: comparator context only".
- The stated dismissal reason is the winning branch's sentence plus its cited items.

**Step 3, the brief and caching.** For each recommended term, one more Haiku call writes the short brief with citations. Every Skeptic call is cached by term, model, prompt version and a **hash of the evidence packet**, so an unchanged packet in a later run is not paid for again.

### 5c. Success definitions

**Detection (primary).** A case is detected at the **first scheduled run (section 6) where any single target PT individually meets Evans** (Arm A, quarterly) or **is recommended** in the final brief (Arm B, at the Arm B schedule). The union of the term list is reported as a secondary result, with its own first detection.

**Positive cases.** The target counts as **found** if **any of its terms** reaches the final brief as a **recommended signal**. It is judged before the human approval step. Not found = a miss, reported as a miss.

**Negative case (rivaroxaban).** Success = Canary does **not** end up recommending a label update for liver injury. For every run, **the route is recorded**:
1. **no statistical flag:** no single target PT meets Evans; or
2. **removed by triage:** flagged but dropped by the exact on-label match, or below the cap; or
3. **Label Checker "yes":** judged already on the label and dropped (under the global rule only "yes" drops a term). The existing "hepatitis (including hepatocellular injury)" postmarketing text is the known caveat; the Label Checker's quoted sentence is recorded. If it says "partial", the term goes to the Skeptic with the "partially labeled" note and the route is then 4; or
4. **Skeptic dismissal:** a valid dismissal under section 5b (winner reporting spike or confounding, citing that branch's own evidence), with the stated reason.

Failure = recommended in any run, including "uncertain: comparator context only". A dismissal with no valid reason does not count. One negative case is a sanity check, not a rate, and is reported as such.

### 5d. Cost estimate (Haiku only) and the hard spend limit

The Skeptic is not built yet, so its cost is an estimate from the token counts of the real data it would read (about 2.3 thousand input tokens per term: three evidence blocks, the comparator context, up to five label chunks, three PubMed titles; about 0.5 thousand output tokens). Haiku is $1 per million input tokens and $5 per million output.

| Item | Cost |
|---|---|
| Label Checker, measured (32 calls, $0.0889) | about $0.0028 per term; 150 terms about **$0.42 per run** |
| Skeptic Haiku call, estimated | about $0.005 per term; 120 terms (assumes 80% of the 150 reach the Skeptic) about **$0.58 per run** |
| Briefs for recommended terms, estimated | about $0.006 each; assume 10 per run, about **$0.06** |
| **Total per run** | about **$1.06**; about **$1.25** if every term reaches the Skeptic. Before caching. |

**Number of Arm B runs (section 6).** The maximum for each case if its first flag were as early as the data allows (the drug's first year on the market, or 2004) and it were never recommended before the label change:

| Case | Maximum runs | Basis |
|---|---|---|
| rivaroxaban (negative) | 3 | year-ends 2017, 2018, 2019 |
| pirfenidone | 6 | year-ends 2014 to 2018, plus 2019Q2 |
| lamotrigine | 7 | year-ends 2004 to 2009, plus 2010Q3 |
| gabapentin | 8 | year-ends 2004 to 2010, plus 2011Q2 |
| dasatinib | 19 | year-ends 2006 to 2023, plus 2024Q2 |
| **All cases** | **43** | about **$46** before caching |

The expected number is much smaller, because each positive case stops at its first recommendation and, if flagged near the FDA posting, has few year-ends before its label change; a rough guess is 14 to 20 runs, **about $15 to $25 before caching**. Caching saves part of the Label Checker cost (the label version in force rarely changes, so many term prompts repeat) but not the Skeptic cost, because the evidence changes each year. **At about $1.06 per run, $15 funds about 14 runs. The limit may therefore stop the evaluation before the last case. This is accepted:** the stop is automatic, logged, and reported as incomplete, and the **run order is fixed so that a stop leaves the fewest cases incomplete: rivaroxaban, pirfenidone, lamotrigine, gabapentin, dasatinib** (smallest possible schedules first). The pass-through rate is an assumption; I did not spend money to measure it.

**Hard spend limit: $15 for the whole evaluation.** Implemented in code: every Anthropic call goes through one function that adds its cost to a ledger file (`eval_spend.jsonl`: time, case, run quarter, stage, term, model, tokens, cost) and checks the running total **before** the call, including that call's projected cost. When the total would pass $15 the evaluation stops automatically, writes a stop record (case, run quarter, stage, term, total spent), and reports the results so far as **incomplete**. It does not continue without the owner's written approval of a higher limit. A stop is not a re-run and does not count under the one-shot rule; continuing from the stop point with the same locked code is allowed once that approval is given.

## 6. Arm schedule and lead time

**Statistics-alone arm (Arm A): quarterly.** The Miner runs at every quarter-end. Output per quarter: the ranked Evans-flagged terms, no label check, no Skeptic, no brief. Detection = the first quarter-end where **any single target PT** meets Evans.

**Full Canary arm (Arm B): year-ends, to cut cost.** Triage, Label Checker, Skeptic, brief, run at:
- **Positive cases:** each **year-end (Q4 cutoff) on or after the first statistical flag** (Arm A's first detection quarter), **plus the last quarter-end before the label change**, in that order, until Canary recommends the target or the label changes. The last cutoff is the last quarter-end strictly before the label change date (gabapentin 2011Q2, pirfenidone 2019Q2, lamotrigine 2010Q3, dasatinib 2024Q2). If the target is never flagged by then, the case is a miss and no LLM spend occurs. The first run at which Canary recommends the target is the Full Canary detection, and later runs are not made.
- **Negative case:** the year-ends **2017Q4, 2018Q4, 2019Q4** (3 runs), regardless of flags, with the route (section 5c) recorded each time.
- **Fixed run order across cases:** rivaroxaban, pirfenidone, lamotrigine, gabapentin, dasatinib.

**Lead time is reported at this resolution, and the plan says so.** Arm B can only detect at its scheduled runs, so its detection date can be up to a year later than a quarterly schedule would give, and its lead times are correspondingly shorter (conservative). Lead time, for both arms (positive cases only; positive = earlier than the reference):
1. vs the **FDA posting quarter**: posting-quarter end date minus the detection date, in months.
2. vs the **label change date** (definition in section 1): label change date minus the detection date, in months.

Arm A is reported at its own quarterly resolution **and also rounded to the Arm B schedule** (the first Arm B run date on or after its detection quarter), so the two arms can be compared like for like. We also report the first run after which the flag stays on, and the secondary result for the union of the term list. Cases never detected are misses, not zero lead time. If the label change date is at or before the first flag, lead time is not positive and the case is reported that way.

## 7. Ablation: statistics alone vs full Canary

Compared on, per case: (a) was the target found, and when (lead time both ways); (b) **alert burden**: how many flagged terms a reviewer would have to read for the drug at the last cutoff before the label change (Arm A: all Evans-flagged terms; Arm B: terms that reach the brief after triage, Label Checker and Skeptic), counted only, with no judgment of the non-target terms because there is no answer key beyond the target; (c) rivaroxaban outcome and route; (d) LLM spend. Canary's own recommendation is scored before the human approval step.

## 8. Atorvastatin and the configuration

**Atorvastatin** is the **development case only** and is **excluded from evaluation**. At drug level its diabetes signal was not flagged before the 2012 label change; the early API-based signal came from other drugs' reports. It is used to build and debug the Miner, triage, Label Checker, Skeptic, and retrieval, and to run the Phase 2 tests. All triage numbers above come from it. Simvastatin is a second development drug, used only for the Label Checker gold set.

**`eval_config.py`** (written, frozen at lock) holds, for each case: the case number and role, brand, brand application, posting quarter, label change date and quoted sentence, the MedDRA term list from section 2, and the drug-name matching rule.

Drug-name matching rule: a PS or SS drug row belongs to the drug if the lower-cased text `DRUGNAME + " " + PROD_AI` matches the case's `match` pattern and does not match its `exclude` pattern. `exclude` removes compounded pain creams that list many ingredients (lidocaine, baclofen, ketamine, cream, a literal backslash and similar) and other molecules that share a stem (gabapentin enacarbil, Horizant, Regnite). Patterns come from generic and brand names plus misspelling stems read from the distinct drug-name strings (names only, no counts, no PRR); foreign brands are covered through PROD_AI.

| Drug | match | notable exclusions |
|---|---|---|
| gabapentin | `gabap`, `gabaentin`, `neurontin`, `neurotin`, `gralise` | gabapentin enacarbil, Horizant, compounded creams |
| pirfenidone | `pirf`, `pirespa`, `piresspa`, `esbriet`, and the misspellings `pirefenidone`, `perfenidone`, `pifenidone`, `pirenidone`, `s-7701` | compounded creams |
| lamotrigine | `lamotri`, `lamatrig`, `lamict`, `lamicital`, `lamicstart`, `subvenite`, `epitrigin` | compounded creams |
| dasatinib | `dasat`, `dasaitinib`, `dastinib`, `sprycel`, `phyrago` | compounded creams |
| rivaroxaban | `rivarox`, `xarelto` | compounded creams |

**Class comparators for the Skeptic** (`COMPARATORS` in `eval_config.py`, chosen from therapeutic class by name only, no counts): gabapentin: pregabalin, topiramate, levetiracetam; pirfenidone: nintedanib; lamotrigine: carbamazepine, oxcarbazepine, levetiracetam, topiramate; dasatinib: imatinib, nilotinib, bosutinib, ponatinib; rivaroxaban: apixaban, dabigatran, edoxaban. Atorvastatin (development): rosuvastatin, simvastatin, pravastatin. **Approved by the owner; ponatinib added for dasatinib in v5.**

Checked without any counts: the five patterns share no drug-name string with each other, and none of the five drugs is on the project-policy exclusion lists. Also fixed in the file: the negative-case window (2017Q3 to 2019Q4), the quarter-end grid, and the excluded methadone note. `miner.mine()` accepts the `exclude` pattern; on atorvastatin it gives the same 24,849 drug cases as before.

## 9. One-shot rule

The evaluation **runs once**. The plan, `eval_config.py`, `triage.py`, `label_checker.py` (prompt and tool schema), the Skeptic prompts, the model setting, and the term lists are locked at the "Lock evaluation plan" commit, and the commit hash is recorded with the results. A re-run is allowed **only for a documented bug fix** (a crash, a data-loading error, a wrong date or name pattern that is shown to be a mistake), with the bug, the fix, and the before/after recorded in the evaluation log (`eval_runs.md`) and **never to tune** thresholds, caps, terms, prompts, or models after seeing any result. If a re-run happens, both runs are reported. Anything learned from the results that would change the method goes on the future-work list, not into a second run. The spend limit in section 5d is a stop, not a re-run.

## 10. Locked items

At the lock commit, these are frozen:
- The five cases, the term lists in section 2, the Evans rule and primary detection rule (sections 3 and 5c), the cutoff grid, the triage rule (section 4), the global Label Checker rule and success definitions (section 5), the Skeptic design in section 5b (three branches plus a neutral context field, evidence computed by code, one structured Haiku call, dismissal rule enforced by code, Haiku brief, caching by evidence hash), the Arm A and Arm B schedules and the run order (section 6), the class comparator lists in `eval_config.py`, and the **$15** spend limit (section 5d).
- **Models: Haiku (`claude-haiku-4-5-20251001`) for every LLM call, including the final brief. No Sonnet.** Set in one config value (`MODEL` in `eval_config.py`).
- **The final Label Checker prompt and tool schema**, exactly as in `label_checker.py` and reproduced here. Frozen at the 27 of 32 result in section 5a. Temperature 0, forced tool call, retrieval = hybrid (dense + BM25), top 5 chunks from the label version in force at the cutoff.
- The Skeptic prompt and its tool schema are written and frozen before any evaluation run, and are not tuned on evaluation drugs.

System prompt:

> You are a careful pharmacovigilance reviewer. You decide whether a reaction reported to FDA is described in the label passages you are given. Record your decision by calling the record_label_decision tool.

User prompt (the passages block, then):

> Reaction reported to FDA (MedDRA preferred term): {reaction}
>
> Decide whether the label passages above describe this reaction. Use only the passages.
> - "yes": ONLY if the label names the same concept or a clear synonym (including US/UK spelling and clinically equivalent wording).
> - "partial": the label names a related, narrower, or broader concept but not this one. Examples: hepatitis vs liver injury; hypersensitivity vs DRESS; muscle pain vs pain; a reaction named only as the outcome of another reaction.
> - "no": the passages do not describe it or anything related.
> Do not count as on-label adverse reactions: mentions of the reaction in the indications, in efficacy results, or as a trial endpoint (for example "reduce the risk of ..."); and findings in animals or other nonclinical studies. Such mentions are not "yes". If nothing else in the passages describes the reaction, answer "no", or "partial" if a related clinical adverse reaction is listed.
> When you are unsure between yes and partial, choose partial.
> The evidence must be a short verbatim quote (at most 200 characters) from the passages, or an empty string for "no".

Tool schema (`tool_choice` forced to this tool):

```json
{"name": "record_label_decision",
 "description": "Record whether the reaction is described in the label passages.",
 "input_schema": {"type": "object",
   "properties": {"on_label": {"type": "string", "enum": ["yes", "partial", "no"]},
                  "evidence": {"type": "string", "description": "Short verbatim quote from the passages, or empty for no."},
                  "confidence": {"type": "number", "minimum": 0, "maximum": 1}},
   "required": ["on_label", "evidence", "confidence"]}}
```

- Everything else (UI, extra drugs, a 10 to 15 drug backtest) goes on the future-work list. Every call is cached and spend is logged.

## 11. Lock record

This plan, `eval_config.py`, `triage.py`, `label_checker.py`, `label_gold_set_v2_DRAFT.csv` and the Miner, retrieval and data-check code are committed as **"Lock evaluation plan"** (git tag `eval-lock`).

Lock commit hash: **425633dd8729eeac52f9e060c19eb28297f6fa24** (tag `eval-lock`). The only change after this commit is this line, recorded in a follow-up commit, so the locked content is the tagged one.
