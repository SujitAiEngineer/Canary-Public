"""Offline tests (no API, no spend) for triage part 4: prune, novelty quota, label-aware seating (post-result exploratory, opt-in, default off).
The key test: with the flag off the triage output is EXACTLY the locked output on all the saved evaluation runs. Also: the fixed non-clinical list, the three
pruning rules, the novelty formula, the selection order of each variant by hand calculation, the list size min(150, survivors), and that the Miner-store frame
used by the variants gives the same locked list as the saved runs.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_triage_prune.py"""
import glob, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd

import triage, triage_prune as T
from agents import triage_step

RUNS = [d for d in sorted(glob.glob("eval_out/runs/*")) if os.path.exists(f"{d}/miner.json") and os.path.exists(f"{d}/triage.json")]
J = lambda p: json.load(open(p, encoding="utf-8"))


def frame(rows):
    """rows: (pt, a, chi2, evans=True, novel=False, dist=0.3)"""
    out = []
    for r in rows:
        pt, a, chi2 = r[:3]
        out.append({"pt": pt, "a": a, "chi2": chi2, "evans": r[3] if len(r) > 3 else True, "novel": r[4] if len(r) > 4 else False, "dist": r[5] if len(r) > 5 else 0.3})
    return pd.DataFrame(out).sort_values("pt", kind="stable")


def test_default_reproduces_the_locked_triage_output_exactly_on_all_saved_runs():
    assert len(RUNS) >= 12, len(RUNS)
    for d in RUNS:
        m, saved = J(f"{d}/miner.json"), J(f"{d}/triage.json")
        out = triage_step.run(m, triage="locked")   # prune is off by default
        assert out["terms"] == saved["terms"], d
        assert out["counts"] == saved["counts"] and out["label_date"] == saved["label_date"] and out["cap"] == saved["cap"] == 150, d
        assert out["prune"] is None and out["triage_v2"] is False and out["stratified"] is False


def test_the_miner_store_frame_used_by_the_variants_gives_the_locked_list_on_saved_runs():
    for d in RUNS[:4] + RUNS[-2:]:
        saved = J(f"{d}/triage.json")
        df, feats = T.load(saved["drug"], saved["date"])
        _, text = triage_step.label_text(saved["drug"], saved["date"])
        locked, _ = triage.triage(df, text, min_count=3, cap=150)
        assert [t["term"] for t in saved["terms"]] == list(locked.pt), d
        assert len(feats["indications"]) == 3 and feats["indication_counts"] == sorted(feats["indication_counts"], reverse=True)
        assert 0 <= feats["win_drug"] <= feats["n_drug"] and all(v <= feats["n_drug"] for v in feats["win"].values())


def test_non_clinical_list_matches_the_fixed_examples_and_never_a_target_term():
    for t in ("DRUG INEFFECTIVE", "DRUG INEFFECTIVE FOR UNAPPROVED INDICATION", "OFF LABEL USE", "NO ADVERSE EVENT", "DRUG INTERACTION", "WRONG DRUG ADMINISTERED",
              "INCORRECT DOSE ADMINISTERED", "PRODUCT QUALITY ISSUE", "PRODUCT USE ISSUE", "MEDICATION ERROR", "DEVICE MALFUNCTION", "EXPIRED PRODUCT ADMINISTERED",
              "TREATMENT NONCOMPLIANCE", "INAPPROPRIATE SCHEDULE OF DRUG ADMINISTRATION", "INTENTIONAL PRODUCT MISUSE", "DRUG DOSE OMISSION", "UNDERDOSE", "off label use"):
        assert T.is_non_clinical(t), t
    for t in ("DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "MENINGITIS ASEPTIC", "HEPATOTOXICITY", "CHYLOTHORAX",
              "DRUG-INDUCED LIVER INJURY", "LIVER INJURY", "INAPPROPRIATE ANTIDIURETIC HORMONE SECRETION", "HYPONATRAEMIA", "GLYCOSYLATED HAEMOGLOBIN INCREASED", "OVERDOSE", "DEATH",
              "DISEASE PROGRESSION", "DRUG HYPERSENSITIVITY", "DRUG TOXICITY", "DRUG ABUSE", "PRODUCTIVE COUGH", "RENAL FAILURE"):
        assert not T.is_non_clinical(t), t


def test_the_three_pruning_rules_and_their_counts():
    df = frame([("HEADACHE", 50, 90.0), ("DRUG INEFFECTIVE", 40, 80.0), ("EPILEPSY", 30, 70.0), ("RASH", 20, 60.0), ("NAUSEA", 10, 50.0),   # on label / non-clinical / indication
                ("TINY", 2, 99.0), ("WEAK", 9, 5.0, False), ("CHYLOTHORAX", 5, 40.0)])
    text = "Adverse reactions: headache and nausea were reported."
    flagged, kept, c = T.prune(df, text, ["epilepsy", "PAIN", "X"])
    assert set(flagged.pt) == {"HEADACHE", "DRUG INEFFECTIVE", "EPILEPSY", "RASH", "NAUSEA", "CHYLOTHORAX"}   # Evans and a >= 3 only
    assert set(kept.pt) == {"RASH", "CHYLOTHORAX"}
    assert c["evans_a3"] == 6 and c["rule1_on_label"] == 2 and c["rule2_non_clinical"] == 1 and c["rule3_indication"] == 1
    assert c["locked_survivors"] == 4 and c["removed_rule2_of_survivors"] == 1 and c["removed_rule3_of_survivors"] == 1 and c["removed_total_of_survivors"] == 2 and c["pruned_survivors"] == 2
    # rule 1 is the locked matcher: the locked survivors are exactly the flagged terms not on the label
    locked, _ = triage.triage(df, text, min_count=3, cap=150)
    assert set(locked.pt) == set(flagged[flagged.locked_survivor].pt)


def test_rule_3_matches_the_whole_term_only():
    df = frame([("PAIN", 10, 9.0), ("PAIN IN EXTREMITY", 10, 8.0), ("BACK PAIN", 10, 7.0)])
    _, kept, _ = T.prune(df, "none", ["PAIN"])
    assert set(kept.pt) == {"PAIN IN EXTREMITY", "BACK PAIN"}


def test_novelty_formula_by_hand():
    # the drug has 1000 reports, 100 in the window: share 10 %. A term with a = 20 reports and 4 in the window has 20 % = exactly 2 x: novel (>=). 3 of 20 = 15 %: not.
    assert T.novel_flags([20, 20, 5, 0], [4, 3, 1, 0], 1000, 100) == [True, False, True, False]
    assert T.novel_flags([20], [20], 1000, 0) == [True]       # drug with no window reports: any term with window reports is novel (share >= 0)
    assert T.novel_flags([20], [0], 0, 0) == [False]          # degenerate drug: nothing is novel


def test_variant_P_is_the_chi_squared_order_with_alphabetical_ties():
    kept = frame([("B", 5, 10.0), ("A", 5, 10.0), ("C", 5, 30.0), ("D", 5, 20.0)])
    o = T.variant_order(kept, "P")
    assert list(o.pt) == ["C", "D", "A", "B"] and list(o.pos) == [1, 2, 3, 4]
    final, _ = T.select_variant(kept, "P", cap=3)
    assert list(final.pt) == ["C", "D", "A"]


def test_variant_PN_takes_the_best_novel_terms_first_then_fills_by_chi_squared():
    # chi2 order: T1..T6; T5 and T6 are novel. quota 1 -> T5 (best novel) first, then T1, T2 by chi-squared
    kept = frame([("T1", 5, 60.0), ("T2", 5, 50.0), ("T3", 5, 40.0), ("T4", 5, 30.0), ("T5", 5, 20.0, True, True), ("T6", 5, 10.0, True, True)])
    o = T.variant_order(kept, "PN", quota=1)
    assert list(o.pt) == ["T5", "T1", "T2", "T3", "T4", "T6"] and o[o.pt == "T5"].quota_slot.iloc[0] and not o.quota_slot.iloc[1:].any()
    final, _ = T.select_variant(kept, "PN", cap=3, quota=1)
    assert set(final.pt) == {"T5", "T1", "T2"} and list(final.pt) == ["T1", "T2", "T5"]            # the final list is shown in chi-squared order
    few, _ = T.select_variant(kept, "PN", cap=4, quota=40)                                          # fewer novel terms than the quota: all novel in, the rest filled
    assert set(few.pt) == {"T5", "T6", "T1", "T2"}


def test_variant_PL_average_rank_by_hand_and_nothing_is_dropped_for_being_close():
    # chi2 ranks: A1 B2 C3 D4; distance ranks (largest distance first): D1 C2 B3 A4 -> combined A 2.5, B 2.5, C 2.5, D 2.5: all tie -> chi-squared rank decides: A B C D
    kept = frame([("A", 5, 40.0, True, False, 0.10), ("B", 5, 30.0, True, False, 0.20), ("C", 5, 20.0, True, False, 0.30), ("D", 5, 10.0, True, False, 0.40)])
    o = T.variant_order(kept, "PL")
    assert list(o.pt) == ["A", "B", "C", "D"] and set(o.combined) == {2.5}
    # a term with the smallest distance (closest to the label) and the best chi-squared rank is only lowered, never dropped
    kept = frame([("A", 5, 90.0, True, False, 0.01), ("B", 5, 80.0, True, False, 0.50), ("C", 5, 70.0, True, False, 0.40), ("D", 5, 60.0, True, False, 0.30)])
    o = T.variant_order(kept, "PL")
    # A: chi 1, dist rank 4 -> 2.5; B: 2,1 -> 1.5; C: 3,2 -> 2.5; D: 4,3 -> 3.5
    assert list(o.pt) == ["B", "A", "C", "D"] and list(o.combined) == [1.5, 2.5, 2.5, 3.5]
    assert len(o) == 4 and set(T.select_variant(kept, "PL", cap=150)[0].pt) == {"A", "B", "C", "D"}
    final, _ = T.select_variant(kept, "PL", cap=3)
    assert set(final.pt) == {"A", "B", "C"}


def test_variant_PLN_quota_by_chi_squared_and_the_fill_in_PL_order():
    kept = frame([("A", 5, 90.0, True, False, 0.01), ("B", 5, 80.0, True, False, 0.50), ("C", 5, 70.0, True, True, 0.40), ("D", 5, 60.0, True, True, 0.30)])
    o = T.variant_order(kept, "PLN", quota=1)
    assert list(o.pt) == ["C", "B", "A", "D"]   # quota: C (best novel by chi-squared); then PL order B (1.5), A (2.5), D (3.5), with C removed from it
    assert o.quota_slot.iloc[0] and not o.quota_slot.iloc[1:].any()


def test_list_size_is_min_cap_survivors_for_every_variant():
    rnd = random.Random(7)
    for n in (0, 1, 5, 149, 150, 151, 400):
        rows = [(f"T{i:04d}", 5, rnd.random() * 100, True, rnd.random() < 0.3, rnd.random()) for i in range(n)]
        kept = frame(rows) if rows else pd.DataFrame({"pt": [], "a": [], "chi2": [], "evans": [], "novel": [], "dist": []})
        for v in T.VARIANTS:
            final, order = T.select_variant(kept, v)
            assert len(final) == min(150, n) and len(order) == n and len(set(final.pt)) == len(final), (n, v)
            if v in ("PN", "PLN"):
                assert int(final.quota_slot.sum()) == min(40, int(kept.novel.sum())) if n else True


def test_prune_variants_equal_the_locked_list_when_nothing_is_pruned():
    d = next(x for x in RUNS if J(f"{x}/triage.json")["counts"]["not_exact_on_label"] <= 150)   # a run below the cap: every variant keeps every survivor
    saved = J(f"{d}/triage.json")
    df, feats = T.load(saved["drug"], saved["date"])
    date, text = triage_step.label_text(saved["drug"], saved["date"])
    for v in ("P", "PN"):
        kept, counts = T.run_variant(df, feats, text, saved["drug"], date, v)
        pruned = counts["removed_total_of_survivors"]
        assert len(kept) == len(saved["terms"]) - pruned and set(kept.pt) <= {t["term"] for t in saved["terms"]}, v


def test_the_opt_in_hook_runs_and_changes_only_the_selection():
    d = next(x for x in RUNS if "gabapentin" in J(f"{x}/miner.json")["drug"])
    m = J(f"{d}/miner.json")
    locked = triage_step.run(m, triage="locked")
    for v in T.VARIANTS:
        r = triage_step.run(m, prune=v)
        assert r["prune"] == v and len(r["terms"]) == len(locked["terms"]) == 150
        assert r["counts"]["locked_survivors"] == locked["counts"]["not_exact_on_label"] and r["counts"]["variant"] == v
        assert not any(T.is_non_clinical(t["term"]) for t in r["terms"])   # no non-clinical term survives


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
