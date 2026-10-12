"""Offline tests (no API, no spend) for the alternative ranking scores (post-result exploratory part 2, opt-in, default off): PRR lower bound, IC025 and
best-of-both. Formulas against hand calculations, the small-count property, the selection (cap, filters, ties, best-of ordering), and that the default
triage still reproduces the locked output on all saved runs.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_triage_rankings.py"""
import glob, json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd

import triage_v2
from agents import triage_step

RUNS = [d for d in sorted(glob.glob("eval_out/runs/*")) if os.path.exists(f"{d}/miner.json") and os.path.exists(f"{d}/triage.json")]


def test_prr_lower_bound_matches_the_hand_calculation():
    # a=10 b=90 c=100 d=9800: PRR = 9.9, SE = sqrt(0.1 - 0.01 + 0.01 - 1/9900) = 0.31606, lower bound = exp(ln 9.9 - 1.96 x 0.31606) = 5.328
    assert abs(triage_v2.prr_lower_bound(10, 90, 100, 9800) - 5.328) < 0.005
    assert triage_v2.prr_lower_bound(10, 90, 100, 9800) < (10 / 100) / (100 / 9900)    # always below the point estimate
    assert triage_v2.prr_lower_bound(0, 90, 100, 9800) == float("-inf") and triage_v2.prr_lower_bound(5, 90, 0, 9800) == float("-inf")


def test_ic025_matches_the_hand_calculation():
    # N = 10000, E = 100 x 110 / 10000 = 1.1, IC = log2(10.5 / 1.6) = 2.7141, IC025 = 2.7141 - 3.3/sqrt(10.5) - 2/10.5^1.5 = 1.637
    assert abs(triage_v2.ic025(10, 90, 100, 9800) - 1.637) < 0.005
    e = 100 * 110 / 10000
    assert abs(triage_v2.ic025(10, 90, 100, 9800) - (math.log2(10.5 / (e + 0.5)) - 3.3 / math.sqrt(10.5) - 2 / 10.5 ** 1.5)) < 1e-12


def test_both_scores_punish_small_counts_with_the_same_ratio():
    small = (3, 297, 100, 99600)         # a rare term
    big = (30, 2970, 1000, 996000)       # the same proportions, ten times the counts
    prr = lambda a, b, c, d: (a / (a + b)) / (c / (c + d))
    assert abs(prr(*small) - prr(*big)) < 1e-9                                   # identical PRR...
    assert triage_v2.prr_lower_bound(*small) < triage_v2.prr_lower_bound(*big)   # ...but the small-count one gets a lower lower bound
    assert triage_v2.ic025(*small) < triage_v2.ic025(*big)


def df_of(rows):
    d = pd.DataFrame(rows)
    d["evans"] = d.get("evans", True)
    return d.sort_values("pt", kind="stable")


def table(a, b=1000, c=500, d=500000):
    return {"a": a, "b": b, "c": c, "d": d}


def test_select_ranked_keeps_the_cap_and_the_locked_filters():
    rows = [{"pt": f"T{i:03d}", **table(5 + i), "evans": True} for i in range(200)]
    rows.append({"pt": "NOT EVANS", **table(500), "evans": False})
    rows.append({"pt": "TOO FEW", **table(2), "evans": True})
    rows.append({"pt": "ON LABEL", **table(300), "evans": True})
    for how in triage_v2.RANKINGS:
        kept, c, ranked = triage_v2.select_ranked(df_of(rows), "the label mentions on label somewhere", how, cap=150)
        got = set(kept.pt)
        assert len(kept) == 150 and not ({"NOT EVANS", "TOO FEW", "ON LABEL"} & got), how        # the Evans flag, a >= 3 and the exact on-label drop are unchanged
        assert c["not_exact_on_label"] == 200 and len(ranked) == 200 and list(ranked.new_rank) == list(range(1, 201))


def test_with_the_same_ratio_the_term_with_more_reports_ranks_first_under_every_score():
    rare = {"pt": "A RARE", "a": 3, "b": 297, "c": 100, "d": 99600, "evans": True}                 # a name that sorts first, so a tie could not explain the result
    common = {"pt": "B COMMON", "a": 300, "b": 29700, "c": 10000, "d": 9960000, "evans": True}      # the same PRR with ten times... a hundred times the counts
    for how in ("prr_lb", "ic025", "best_of"):
        kept, c, ranked = triage_v2.select_ranked(df_of([rare, common]), "x", how, cap=150)
        assert list(ranked.pt) == ["B COMMON", "A RARE"], how


def test_best_of_orders_by_the_better_rank_then_the_worse_then_the_name():
    rows = [{"pt": "A", **table(10), "evans": True}, {"pt": "B", **table(10), "evans": True}, {"pt": "C", **table(60), "evans": True}, {"pt": "D", **table(30), "evans": True}]
    kept, c, ranked = triage_v2.select_ranked(df_of(rows), "x", "best_of", cap=150)
    assert set(ranked.pt) == {"A", "B", "C", "D"} and list(ranked.new_rank) == [1, 2, 3, 4]
    r = ranked.set_index("pt")
    assert all(min(r.rank_prr_lb[x], r.rank_ic025[x]) <= min(r.rank_prr_lb[y], r.rank_ic025[y]) for x, y in zip(ranked.pt, ranked.pt[1:]))   # sorted by the better rank
    assert list(ranked.pt).index("A") < list(ranked.pt).index("B")                                                                         # equal terms: alphabetical, deterministic
    again = triage_v2.select_ranked(df_of(rows[::-1]), "x", "best_of", cap=150)[2]
    assert list(again.pt) == list(ranked.pt)                                                                                               # input order does not matter


def test_unknown_ranking_is_refused():
    try:
        triage_v2.select_ranked(df_of([{"pt": "A", **table(10), "evans": True}]), "x", "chi2", cap=150)
    except ValueError:
        return
    raise AssertionError("an unknown ranking was accepted")


def test_default_still_reproduces_the_locked_triage_exactly_and_the_flag_is_off_by_default():
    assert len(RUNS) >= 12
    for d in RUNS:
        m, saved = json.load(open(f"{d}/miner.json", encoding="utf-8")), json.load(open(f"{d}/triage.json", encoding="utf-8"))
        out = triage_step.run(m, triage="locked")
        assert out["terms"] == saved["terms"] and out["counts"] == saved["counts"] and out["rank_by"] is None and out["triage_v2"] is False, d


def test_the_new_ranking_changes_a_real_list_but_never_its_size_or_filters():
    d = next(x for x in RUNS if "gabapentin" in json.load(open(f"{x}/miner.json", encoding="utf-8"))["drug"])
    m = json.load(open(f"{d}/miner.json", encoding="utf-8"))
    locked = triage_step.run(m, triage="locked")
    for how in triage_v2.RANKINGS:
        r = triage_step.run(m, rank_by=how)
        assert r["rank_by"] == how and len(r["terms"]) == len(locked["terms"]) <= 150
        assert r["counts"]["not_exact_on_label"] == locked["counts"]["not_exact_on_label"]    # the same survivors, only the order and the cut differ


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
