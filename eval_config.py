"""Evaluation configuration (Phase 2). Frozen at plan approval: drug-name matching, MedDRA term lists, cutoff grid, case facts.
Nothing here was tuned on FAERS counts for the evaluation drugs: name patterns come from the generic and brand names, plus
misspelling stems seen in the distinct DRUGNAME strings (names only, no counts, no PRR).

Matching rule (applied to the lower-cased text  DRUGNAME + " " + PROD_AI  of every PS or SS drug row; regular expressions,
searched anywhere in the string):  a row belongs to the drug if `match` finds a hit AND `exclude` finds none.
`exclude` removes compounded mixtures (pain creams that list several ingredients) and other molecules that share a stem.
Foreign brands are covered through PROD_AI (the active ingredient field, present from 2012Q4) rather than listed by name.
"""
import datetime as dt

COMPOUNDED = r"lidocaine|prilocaine|baclofen|diclofenac|cyclobenzaprine|bupivacaine|amitriptyline|flurbiprofen|ketamine|ketoprofen|meloxicam|fluticasone|clonidine|nortriptyline|orphenadrine|cream|lotion|\\"  # last "\\" = a literal backslash, used by FAERS to join ingredients

CASES = {
    "gabapentin": dict(
        number=1, role="positive", brand="Neurontin", brand_nda="NDA020235", posting_quarter="2009Q2",
        label_change_date="2011-08-11",
        label_quote="Drug Reaction with Eosinophilia and Systemic Symptoms (DRESS)/Multiorgan hypersensitivity",
        match=r"gabap|gabaentin|neurontin|neurotin|gralise",
        exclude=r"enacarbil|horizant|" + COMPOUNDED,
        terms=["DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"]),
    "pirfenidone": dict(
        number=2, role="positive", brand="Esbriet", brand_nda="NDA022535", posting_quarter="2019Q1",
        label_change_date="2019-08-02",
        label_quote="Elevated liver enzymes and drug-induced liver injury: ALT, AST, and bilirubin elevations have occurred with ESBRIET including cases of drug-induced liver injury",
        match=r"pirf|pirespa|piresspa|esbriet|pirefenidone|perfenidone|pifenidone|pirenidone|s-7701",
        exclude=COMPOUNDED,
        terms=["DRUG-INDUCED LIVER INJURY", "LIVER INJURY", "HEPATOTOXICITY"]),
    "lamotrigine": dict(
        number=3, role="positive", brand="Lamictal", brand_nda="NDA020241", posting_quarter="2009Q3",
        label_change_date="2010-10-28",
        label_quote="Aseptic meningitis reported in pediatric and adult patients. (5.7)",
        match=r"lamotri|lamatrig|lamict|lamicital|lamicstart|subvenite|epitrigin",
        exclude=COMPOUNDED,
        terms=["MENINGITIS ASEPTIC"]),
    "dasatinib": dict(
        number=4, role="positive", brand="Sprycel", brand_nda="NDA021986", posting_quarter="2023Q3",
        label_change_date="2024-08-02",
        label_quote="Respiratory, thoracic, and mediastinal disorders: interstitial lung disease, chylothorax",
        match=r"dasat|dasaitinib|dastinib|sprycel|phyrago",
        exclude=COMPOUNDED,
        terms=["CHYLOTHORAX"]),
    "rivaroxaban": dict(
        number=5, role="negative", brand="Xarelto", brand_nda="NDA022406", posting_quarter="2017Q3",
        label_change_date=None,   # FDA: no action needed. Known caveat: "hepatitis (including hepatocellular injury)" is in the
        existing_text_first_seen="2015-01-09",  # postmarketing list from this label version on, before the posting.
        existing_text="hepatitis (including hepatocellular injury)",
        match=r"rivarox|xarelto",
        exclude=COMPOUNDED,
        terms=["DRUG-INDUCED LIVER INJURY", "LIVER INJURY", "HEPATOTOXICITY"]),
}

# Development case: not part of the evaluation, used to build and debug.
DEV = {"atorvastatin": dict(match=r"atorva|lipitor|torvast|sortis", exclude=COMPOUNDED,
                            terms=["TYPE 2 DIABETES MELLITUS", "DIABETES MELLITUS", "BLOOD GLUCOSE INCREASED", "HYPERGLYCAEMIA",
                                   "GLYCOSYLATED HAEMOGLOBIN INCREASED"], label_change_date="2012-02-28")}

# Excluded during planning (never run): methadone. See EVALUATION_PLAN.md.
EXCLUDED_DURING_PLANNING = ["methadone"]

# Arm B (full Canary) schedule. Arm A (statistics) runs every quarter-end.
# Positive cases: each YEAR-END (Q4) on or after the first statistical flag and before the label change, plus the last quarter-end
# before the label change. Negative case: year-ends 2017, 2018, 2019.
NEGATIVE_RUNS = ["2017Q4", "2018Q4", "2019Q4"]
# Run order is fixed: smallest possible schedules first, so that a spend stop leaves the fewest cases incomplete.
RUN_ORDER = ["rivaroxaban", "pirfenidone", "lamotrigine", "gabapentin", "dasatinib"]
SPEND_LIMIT_USD = 10.00  # was 15.00; lowered to 10.00 by the project owner on 2026-10-03, before any evaluation run (eval_runs.md)
BRIEF_TOP_N = 10        # briefs are written only for the target terms and the top BRIEF_TOP_N recommended terms per run (project owner, 2026-10-03; eval_runs.md)
ARM_B_SCHEDULE = "reduced"  # "reduced" (approved 2026-10-03, before any run, because of budget): per positive case the first year-end on or after its first Arm A flag plus the last quarter-end before the label change; rivaroxaban keeps NEGATIVE_RUNS. "locked" = arm_b_quarters() as written in the plan. The functions below are unchanged.
USE_BATCHES = True      # Label Checker, Skeptic and brief calls go through the Message Batches API (50% price); same model, prompts, schemas and inputs (2026-10-03; eval_runs.md)
MODEL = "claude-haiku-4-5-20251001"  # Haiku for every LLM call, including the final brief. No Sonnet escalation.

# Quarter-end cutoffs: the data known on the last day of the quarter (quarter loaded <= that quarter).
def quarter_end(q):
    y, n = int(q[:4]), int(q[-1])
    return dt.date(y, 3 * n, 1).replace(day={1: 31, 2: 30, 3: 30, 4: 31}[n])

def qidx(q):
    """Index used by the Miner store: 2004Q1 = 0."""
    return (int(q[:4]) - 2004) * 4 + int(q[-1]) - 1

def quarters(first, last):
    y, n = int(first[:4]), int(first[-1])
    while (y, n) <= (int(last[:4]), int(last[-1])):
        yield f"{y}Q{n}"
        y, n = (y, n + 1) if n < 4 else (y + 1, 1)

def last_cutoff_before(date_iso):
    """Last quarter-end strictly before a label change date (positive cases stop here)."""
    d = dt.date.fromisoformat(date_iso)
    y, n = d.year, (d.month - 1) // 3 + 1
    q = f"{y}Q{n}"
    if quarter_end(q) >= d:
        y, n = (y, n - 1) if n > 1 else (y - 1, 4)
        q = f"{y}Q{n}"
    return q

# Class comparators for the Skeptic's class-effect check (same therapeutic class, same cutoff). Names only: no counts or PRRs were
# looked at. Patterns use the same match rule as CASES (lower-cased DRUGNAME + " " + PROD_AI, exclude compounded creams).
COMPARATORS = {
    "gabapentin": {"pregabalin": r"pregabalin|lyrica", "topiramate": r"topiramate|topamax", "levetiracetam": r"levetiracetam|keppra"},
    "pirfenidone": {"nintedanib": r"nintedanib|ofev"},
    "lamotrigine": {"carbamazepine": r"carbamazepine|tegretol", "oxcarbazepine": r"oxcarbazepine|trileptal",
                    "levetiracetam": r"levetiracetam|keppra", "topiramate": r"topiramate|topamax"},
    "dasatinib": {"imatinib": r"imatinib|gleevec|glivec", "nilotinib": r"nilotinib|tasigna", "bosutinib": r"bosutinib|bosulif",
                  "ponatinib": r"ponatinib|iclusig"},
    "rivaroxaban": {"apixaban": r"apixaban|eliquis", "dabigatran": r"dabigatran|pradaxa", "edoxaban": r"edoxaban|savaysa|lixiana"},
}
DEV_COMPARATORS = {"atorvastatin": {"rosuvastatin": r"rosuvastatin|crestor", "simvastatin": r"simvastatin|zocor", "pravastatin": r"pravastatin|pravachol"}}


def arm_b_quarters(case, first_flag_quarter):
    """Quarter-ends at which full Canary runs for a case (before it is stopped by a recommendation or by the spend limit)."""
    c = CASES[case]
    if c["role"] == "negative":
        return list(NEGATIVE_RUNS)
    if first_flag_quarter is None:
        return []
    last = last_cutoff_before(c["label_change_date"])
    first_year = int(first_flag_quarter[:4])
    out = [f"{y}Q4" for y in range(first_year, int(last[:4]) + 1) if quarter_end(f"{y}Q4") >= quarter_end(first_flag_quarter)
           and f"{y}Q4" <= last]
    if last not in out:
        out.append(last)
    return out
