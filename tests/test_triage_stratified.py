"""Offline tests (no API, no spend) for stratified triage (post-result exploratory part 3, opt-in, default off): the quota algorithm against hand calculations
and invariants, the band boundaries, the selection inside bands, and that the default triage still reproduces the locked output exactly.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_triage_stratified.py"""
import glob, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd

import triage_v2
from triage_v2 import stratified_quotas as quotas, band_of, select_stratified
from agents import triage_step

RUNS = [d for d in sorted(glob.glob("eval_out/runs/*")) if os.path.exists(f"{d}/miner.json") and os.path.exists(f"{d}/triage.json")]


def test_quotas_match_the_hand_calculation_proportional_case():
    # n = (300, 150, 90, 60), N = 600: raw 75, 37.5, 22.5, 15 -> floors 75, 37, 22, 15 (149); the spare slot goes to the larger remainder, a tie, so to the rarer band B2
    assert quotas([300, 150, 90, 60]) == [75, 38, 22, 15]


def test_quotas_match_the_hand_calculation_when_the_rare_minimum_binds_and_bands_run_short():
    # n = (10, 5, 300, 600): proportional (2, 1, 49, 98); rare minimum 38 -> 35 slots taken from B3 and B4 (12 and 23) and split 25:13 over B1:B2; then
    # B1 and B2 are limited to the 10 and 5 they have, and the 23 unused slots go to B3 and B4 in proportion to what they have left (8 and 15)
    assert quotas([10, 5, 300, 600]) == [10, 5, 45, 90]


def test_all_survivors_are_kept_when_there_are_not_more_than_the_cap():
    assert quotas([5, 5, 5, 5]) == [5, 5, 5, 5] and quotas([0, 0, 0, 150]) == [0, 0, 0, 150] and quotas([40, 40, 40, 30]) == [40, 40, 40, 30]


def test_quota_invariants_over_many_random_cases():
    rnd = random.Random(2026)
    for _ in range(2000):
        n = [rnd.choice([0, 0, 1, 3, 10, 40, 120, 400]) * rnd.randint(1, 3) for _ in range(4)]
        q = quotas(n)
        assert sum(q) == min(150, sum(n)), (n, q)             # the list size is always min(150, survivors)
        assert all(0 <= a <= b for a, b in zip(q, n)), (n, q)  # never more than a band has, never negative
        if sum(n) > 150 and n[0] + n[1] >= 38:
            assert q[0] + q[1] >= 38, (n, q)                   # the rare minimum is honoured whenever the rare bands have enough terms
        assert quotas(n) == q                                  # deterministic


def test_the_rare_minimum_is_not_a_cap_for_the_rare_bands():
    q = quotas([1000, 1000, 10, 10])      # proportional already gives the rare bands almost everything: the minimum does not bind
    assert sum(q) == 150 and q[0] + q[1] >= 147


def test_band_boundaries_are_inclusive():
    assert [band_of(a) for a in (3, 10, 11, 30, 31, 100, 101, 5000)] == [0, 0, 1, 1, 2, 2, 3, 3]
    try:
        band_of(2)
    except ValueError:
        return
    raise AssertionError("a < 3 must be refused")


def frame(rows):
    d = pd.DataFrame(rows)
    d["evans"] = d["evans"] if "evans" in d else True
    return d.sort_values("pt", kind="stable")


def test_selection_takes_the_top_chi_squared_inside_each_band_and_applies_the_locked_filters():
    rows = []
    for i in range(300):   # 300 survivors in B1 (a = 3..10), chi-squared falling with i
        rows.append({"pt": f"R{i:03d}", "a": 3 + i % 8, "chi2": 1000.0 - i, "evans": True})
    for i in range(150):   # 150 in B2
        rows.append({"pt": f"M{i:03d}", "a": 11 + i % 20, "chi2": 900.0 - i, "evans": True})
    for i in range(90):    # 90 in B3
        rows.append({"pt": f"C{i:03d}", "a": 31 + i % 70, "chi2": 800.0 - i, "evans": True})
    for i in range(60):    # 60 in B4
        rows.append({"pt": f"V{i:03d}", "a": 101 + i, "chi2": 700.0 - i, "evans": True})
    rows += [{"pt": "NOT EVANS", "a": 50, "chi2": 5000.0, "evans": False}, {"pt": "TOO FEW", "a": 2, "chi2": 5000.0, "evans": True},
             {"pt": "ON LABEL", "a": 50, "chi2": 5000.0, "evans": True}]
    kept, c, allsurv = select_stratified(frame(rows), "the label names on label here")
    assert c["band_survivors"] == [300, 150, 90, 60] and c["band_quotas"] == [75, 38, 22, 15] and len(kept) == 150
    assert not ({"NOT EVANS", "TOO FEW", "ON LABEL"} & set(kept.pt))
    got = set(kept.pt)
    assert {f"R{i:03d}" for i in range(75)} <= got and "R075" not in got                    # the 75 best of band 1 by chi-squared
    assert {f"M{i:03d}" for i in range(38)} <= got and "M038" not in got
    assert {f"C{i:03d}" for i in range(22)} <= got and {f"V{i:03d}" for i in range(15)} <= got and "V015" not in got
    assert list(kept.chi2) == sorted(kept.chi2, reverse=True) and set(kept.band) == {1, 2, 3, 4}


def test_a_rare_term_that_loses_the_global_ranking_wins_a_slot_in_its_own_band():
    # 400 very common terms beat any rare term on chi-squared, so the locked cap of 150 would hold none of the rare ones; the bands give them their share
    rows = [{"pt": f"V{i:03d}", "a": 200 + i, "chi2": 5000.0 - i, "evans": True} for i in range(400)]
    rows += [{"pt": f"R{i:03d}", "a": 3 + i % 8, "chi2": 60.0 - i * 0.01, "evans": True} for i in range(100)]
    kept, c, _ = select_stratified(frame(rows), "x")
    locked_top = set(frame(rows).sort_values("chi2", ascending=False, kind="stable").head(150).pt)
    assert not any(p.startswith("R") for p in locked_top)                       # the locked rule keeps none of the rare terms
    # proportional share would be 150 x 100 / 500 = 30, below the rare minimum of 38, so band 1 gets 38 (the 8 extra slots come from the common band)
    assert sum(p.startswith("R") for p in kept.pt) == c["band_quotas"][0] == 38 and c["band_quotas"] == [38, 0, 0, 112] and len(kept) == 150


def test_stratified_is_deterministic_and_independent_of_input_order():
    rows = [{"pt": f"T{i:03d}", "a": 3 + (i * 7) % 150, "chi2": 100.0 - (i % 9), "evans": True} for i in range(400)]
    a = select_stratified(frame(rows), "x")[0]
    b = select_stratified(frame(rows[::-1]), "x")[0]
    assert list(a.pt) == list(b.pt)


def test_default_reproduces_the_locked_output_exactly_and_the_flag_is_off_by_default():
    assert len(RUNS) >= 12
    for d in RUNS:
        m, saved = json.load(open(f"{d}/miner.json", encoding="utf-8")), json.load(open(f"{d}/triage.json", encoding="utf-8"))
        out = triage_step.run(m, triage="locked")
        assert out["terms"] == saved["terms"] and out["counts"] == saved["counts"] and out["stratified"] is False and out["rank_by"] is None, d


def test_stratified_on_a_real_capped_run_keeps_the_size_the_survivors_and_the_filters():
    d = next(x for x in RUNS if "gabapentin" in json.load(open(f"{x}/miner.json", encoding="utf-8"))["drug"])
    m = json.load(open(f"{d}/miner.json", encoding="utf-8"))
    locked, s = triage_step.run(m, triage="locked"), triage_step.run(m, stratified=True)
    assert s["stratified"] is True and len(s["terms"]) == len(locked["terms"]) == 150
    assert s["counts"]["not_exact_on_label"] == locked["counts"]["not_exact_on_label"] and sum(s["counts"]["band_survivors"]) == s["counts"]["not_exact_on_label"]
    assert sum(s["counts"]["band_quotas"]) == 150 and set(t["term"] for t in s["terms"]) != set(t["term"] for t in locked["terms"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
