# Canary evaluation results

- **Locked plan**: `EVALUATION_PLAN.md` v5, lock commit `425633dd8729eeac52f9e060c19eb28297f6fa24` (tag `eval-lock`).
- **Code ran from commit** `d5dcc3bdac0061413b3966862920233979d43d43` (clean working tree).
- **Model**: claude-haiku-4-5-20251001 for every LLM call. Started 2026-10-03 11:30:12, finished 2026-10-03 17:13:39.
- **Budget**: $9.50 working budget (config ceiling $10.00); spent $3.42 in this evaluation ledger.
- **Amendments approved by the owner on 2026-10-03, before any evaluation run** (see `eval_runs.md`): spend limit $10 and a working budget of $9.50; briefs only for the target terms and the top 10 recommended terms per run; the Message Batches API for cost only (same model, prompts, schemas, inputs); **the Arm B schedule was reduced because of budget** (setting `ARM_B_SCHEDULE = 'reduced'`): per positive case the first year-end on or after its first Arm A flag plus the last quarter-end before the label change, rivaroxaban at 2017Q4, 2018Q4, 2019Q4. Lead times for Arm B are therefore at the resolution of those runs (coarser than the locked schedule, so conservative).

## Summary

Lead times in months (positive = earlier than the reference). Cases never detected are misses, not zero lead time.

| Case | Role | Status | Arm A first flag | Arm A lead vs posting / vs label | Arm A rounded to the Arm B schedule | Arm B detection (run) | Arm B lead vs posting / vs label | Spend |
|---|---|---|---|---|---|---|---|---|
| rivaroxaban | negative | complete | 2012Q2 | +63.0 / n/a | 2017Q4: -3.0 / n/a | not detected | n/a / n/a | $1.58 |
| pirfenidone | positive | complete | 2015Q2 | +45.0 / +49.1 | 2015Q4: +39.0 / +43.0 | 2015Q4 | +39.0 / +43.0 | $0.28 |
| lamotrigine | positive | complete | 2004Q1 | +66.0 / +78.9 | 2004Q4: +57.0 / +69.9 | 2004Q4 | +57.0 / +69.9 | $0.38 |
| gabapentin | positive | complete | 2006Q1 | +39.0 / +64.4 | 2006Q4: +30.0 / +55.3 | not detected | n/a / n/a | $1.07 |
| dasatinib | positive | complete | 2007Q4 | +189.0 / +199.1 | 2007Q4: +189.0 / +199.1 | 2007Q4 | +189.0 / +199.1 | $0.10 |

## rivaroxaban (negative, case 5, Xarelto)

- Target PTs: DRUG-INDUCED LIVER INJURY, LIVER INJURY, HEPATOTOXICITY. FDA posting 2017Q3; label change none expected.
- **Arm A**: first quarter-end where any single target PT meets Evans: **2012Q2**; the flag stays on from n/a; union of the list (secondary) first flags at 2014Q4. Last cutoff before the label change: n/a; alert burden there (all Evans-flagged terms): n/a.
- **Status**: complete.
- **Arm B schedule**: 2017Q4, 2018Q4, 2019Q4.

| Run | Cutoff | Status | Recommended / examined | Target PT routes | Run spend |
|---|---|---|---|---|---|
| rivaroxaban 2017Q4 | 2017-12-31 | ok | 132 / 135 | Drug-Induced Liver Injury: no statistical flag; Liver Injury: no statistical flag; Hepatotoxicity: no statistical flag | $0.53 |
| rivaroxaban 2018Q4 | 2018-12-31 | ok | 128 / 135 | Drug-Induced Liver Injury: no statistical flag; Liver Injury: no statistical flag; Hepatotoxicity: no statistical flag | $0.32 |
| rivaroxaban 2019Q4 | 2019-12-31 | ok | 124 / 134 | Drug-Induced Liver Injury: no statistical flag; Liver Injury: no statistical flag; Hepatotoxicity: no statistical flag | $0.53 |

**Negative case outcome**: SUCCESS (no run recommended liver injury). One negative case is a sanity check, not a rate.

## pirfenidone (positive, case 2, Esbriet)

- Target PTs: DRUG-INDUCED LIVER INJURY, LIVER INJURY, HEPATOTOXICITY. FDA posting 2019Q1; label change 2019-08-02.
- **Arm A**: first quarter-end where any single target PT meets Evans: **2015Q2**; the flag stays on from n/a; union of the list (secondary) first flags at 2015Q2. Last cutoff before the label change: 2019Q2; alert burden there (all Evans-flagged terms): 183.
- **Status**: complete.
- **Arm B schedule**: 2015Q4, 2019Q2.

| Run | Cutoff | Status | Recommended / examined | Target PT routes | Run spend |
|---|---|---|---|---|---|
| pirfenidone 2015Q4 | 2015-12-31 | ok | 48 / 76 | Drug-Induced Liver Injury: no statistical flag; Liver Injury: no statistical flag; Hepatotoxicity: recommended | $0.28 |

## lamotrigine (positive, case 3, Lamictal)

- Target PTs: MENINGITIS ASEPTIC. FDA posting 2009Q3; label change 2010-10-28.
- **Arm A**: first quarter-end where any single target PT meets Evans: **2004Q1**; the flag stays on from 2004Q1; union of the list (secondary) first flags at 2004Q1. Last cutoff before the label change: 2010Q3; alert burden there (all Evans-flagged terms): 610.
- **Status**: complete.
- **Arm B schedule**: 2004Q4, 2010Q3.

| Run | Cutoff | Status | Recommended / examined | Target PT routes | Run spend |
|---|---|---|---|---|---|
| lamotrigine 2004Q4 | 2004-12-31 | ok | 96 / 97 | Meningitis Aseptic: recommended | $0.38 |

## gabapentin (positive, case 1, Neurontin)

- Target PTs: DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS, DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS. FDA posting 2009Q2; label change 2011-08-11.
- **Arm A**: first quarter-end where any single target PT meets Evans: **2006Q1**; the flag stays on from n/a; union of the list (secondary) first flags at 2006Q1. Last cutoff before the label change: 2011Q2; alert burden there (all Evans-flagged terms): 685.
- **Status**: complete.
- **Arm B schedule**: 2006Q4, 2011Q2.

| Run | Cutoff | Status | Recommended / examined | Target PT routes | Run spend |
|---|---|---|---|---|---|
| gabapentin 2006Q4 | 2006-12-31 | ok | 137 / 146 | Drug Reaction With Eosinophilia And Systemic Symptoms: no statistical flag; Drug Rash With Eosinophilia And Systemic Symptoms: no statistical flag | $0.54 |
| gabapentin 2011Q2 | 2011-06-30 | ok | 123 / 142 | Drug Reaction With Eosinophilia And Systemic Symptoms: no statistical flag; Drug Rash With Eosinophilia And Systemic Symptoms: no statistical flag | $0.53 |

## dasatinib (positive, case 4, Sprycel)

- Target PTs: CHYLOTHORAX. FDA posting 2023Q3; label change 2024-08-02.
- **Arm A**: first quarter-end where any single target PT meets Evans: **2007Q4**; the flag stays on from 2007Q4; union of the list (secondary) first flags at 2007Q4. Last cutoff before the label change: 2024Q2; alert burden there (all Evans-flagged terms): 398.
- **Status**: complete.
- **Arm B schedule**: 2007Q4, 2024Q2.

| Run | Cutoff | Status | Recommended / examined | Target PT routes | Run spend |
|---|---|---|---|---|---|
| dasatinib 2007Q4 | 2007-12-31 | ok | 22 / 23 | Chylothorax: recommended | $0.10 |

## Alert burden (plan section 7)

Arm A: all Evans-flagged terms at the last cutoff before the label change. Arm B: terms recommended after triage, Label Checker and Skeptic in the case's final Arm B run (a positive case stops at its first detection, so its final run may be earlier than that cutoff). Counts only; non-target terms are not judged.

| Case | Arm A flagged terms (last cutoff before label) | Arm B recommended in its final run (cutoff) |
|---|---|---|
| rivaroxaban | n/a | 124 (2019Q4) |
| pirfenidone | 183 | 48 (2015Q4) |
| lamotrigine | 610 | 96 (2004Q4) |
| gabapentin | 685 | 123 (2011Q2) |
| dasatinib | 398 | 22 (2007Q4) |

## Notes

- Detection (Arm B) is judged on the Skeptic's recommendation before any human approval step, as in plan section 5c. Briefs were written only for the target terms and the top 10 recommended terms per run (amendment above); this does not affect detection.
- Per-quarter details (every Arm A quarter, every target PT's statistics) are in `eval_out/arm_a_<case>.json`; every Arm B run's full outputs are in `eval_out/runs/`.
