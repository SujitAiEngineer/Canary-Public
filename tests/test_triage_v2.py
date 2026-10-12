"""Offline tests (no API, no spend) for triage v2 (post-result exploratory, opt-in, default off). The key test: with the flag off the output is EXACTLY the
locked triage output on all the saved evaluation runs. Also: the mapping file, the official IME list, report-level group counts, the reserved-slot
selection, and the effect on the gabapentin DRESS case.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_triage_v2.py"""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duckdb
import pandas as pd

import triage_v2
from agents import miner as miner_agent, triage_step

RUNS = [d for d in sorted(glob.glob("eval_out/runs/*")) if os.path.exists(f"{d}/miner.json") and os.path.exists(f"{d}/triage.json")]
J = lambda p: json.load(open(p, encoding="utf-8"))


def test_default_reproduces_the_locked_triage_output_exactly_on_existing_data():
    assert len(RUNS) >= 12, len(RUNS)   # rivaroxaban 3, pirfenidone, lamotrigine, gabapentin 2 + 4 supplementary, dasatinib
    for d in RUNS:
        m, saved = J(f"{d}/miner.json"), J(f"{d}/triage.json")
        out = triage_step.run(m, triage="locked")   # v2 is off by default
        assert out["terms"] == saved["terms"], d                       # the same terms, order and numbers
        assert out["counts"] == saved["counts"] and out["label_date"] == saved["label_date"] and out["cap"] == saved["cap"] == 150, d
        assert out["triage_v2"] is False


def test_default_miner_agent_still_reproduces_a_saved_miner_output():
    for d in RUNS[:3]:
        saved = J(f"{d}/miner.json")
        new = miner_agent.run(saved["drug"], saved["date"], allow_evaluation=True)   # term_groups not passed: the locked behaviour
        assert json.loads(json.dumps(new["terms"])) == saved["terms"] and new["n_drug"] == saved["n_drug"] and "term_groups" not in new, d   # JSON turns the int year keys into strings


def test_the_mapping_file_is_consistent_and_complete_for_the_gabapentin_pair():
    g = triage_v2.load_groups()
    assert len(g) == 14
    dress = g["DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"]
    assert dress == ["DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"]
    members = [m for ms in g.values() for m in ms]
    assert len(members) == len(set(members)) == 28 and all(len(ms) == 2 for ms in g.values())   # one current and one retired name each, no term in two groups


def test_official_ime_list_has_our_targets_and_not_ordinary_terms():
    ime = triage_v2.load_ime()
    assert len(ime) == 7846
    for t in ("DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "MENINGITIS ASEPTIC", "HEPATOTOXICITY", "CHYLOTHORAX", "INAPPROPRIATE ANTIDIURETIC HORMONE SECRETION",
              "RHABDOMYOLYSIS", "STEVENS-JOHNSON SYNDROME"):
        assert t in ime, t
    for t in ("HEADACHE", "NAUSEA", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"):   # the retired DRESS name is not on the current list: the group supplies it
        assert t not in ime, t


def test_group_counts_use_reports_not_names():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE reac(rid BIGINT, pt VARCHAR)")
    con.execute("CREATE TEMP TABLE latest(rid BIGINT)"); con.execute("CREATE TEMP TABLE drug_rids(rid BIGINT)")
    new, old = "DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS", "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"
    reac = [(1, new), (1, old),          # one drug report carrying BOTH names: counted once
            (2, old), (3, new),          # one drug report each
            (4, old), (5, new)]          # two non-drug reports
    con.executemany("INSERT INTO reac VALUES (?, ?)", reac)
    con.executemany("INSERT INTO latest VALUES (?)", [(i,) for i in range(1, 21)])
    con.executemany("INSERT INTO drug_rids VALUES (?)", [(i,) for i in (1, 2, 3, 6, 7)])
    g = triage_v2.group_stats(con, {"DRESS": [new, old]}, n_total=20, n_drug=5)["DRESS"]
    assert (g["a"], g["b"], g["c"], g["d"]) == (3, 2, 2, 13)   # a: reports 1, 2, 3 (not 4); c: reports 4, 5; every report once
    assert g["a"] + g["b"] + g["c"] + g["d"] == 20


def df_of(rows):
    return pd.DataFrame(rows).sort_values("pt", kind="stable")


def fake_terms(n=200, ime_at=(), non_evans=(), members=None):
    rows = []
    for i in range(n):
        rows.append({"pt": f"T{i:03d}", "a": 10, "chi2": 1000.0 - i, "evans": i not in non_evans, "ime_name": i in ime_at})
    return rows


def test_select_reserves_up_to_20_slots_and_keeps_the_total_at_150():
    rows = fake_terms(200, ime_at=range(160, 200))                       # 40 serious terms, all far below the plain cut (rank 150)
    ime = {f"T{i:03d}" for i in range(160, 200)}
    kept, c = triage_v2.select(df_of(rows), "no label text", cap=150, reserve=20, ime=ime)
    assert len(kept) == 150 and c["reserved"] == 20 and c["ime_eligible"] == 40
    got = set(kept.pt)
    assert {f"T{i:03d}" for i in range(160, 180)} <= got                    # the 20 BEST serious terms by chi-squared are in
    assert not ({f"T{i:03d}" for i in range(180, 200)} & got)               # the rest of the serious terms are not
    assert {f"T{i:03d}" for i in range(0, 130)} <= got and not ({f"T{i:03d}" for i in range(130, 160)} & got)   # the other 130 slots go by chi-squared rank
    assert list(kept.reserved_ime).count(True) == 20 and list(kept.chi2) == sorted(kept.chi2, reverse=True)


def test_reserve_first_is_a_no_op_when_20_serious_terms_already_rank_in_the_top_150_but_reserve_beyond_is_not():
    rows = fake_terms(200)
    ime = {f"T{i:03d}" for i in list(range(0, 50)) + list(range(160, 171))}      # 50 serious terms near the top, 11 more far below the plain cut
    plain = {f"T{i:03d}" for i in range(150)}
    first, c1 = triage_v2.select(df_of(rows), "no label text", cap=150, reserve=20, ime=ime, mode="reserve_first")
    assert set(first.pt) == plain and c1["reserved"] == 20                        # the rule as specified changes nothing here
    beyond, c2 = triage_v2.select(df_of(rows), "no label text", cap=150, reserve=20, ime=ime, mode="reserve_beyond")
    got = set(beyond.pt)
    assert len(beyond) == 150 and {f"T{i:03d}" for i in range(160, 171)} <= got   # the 11 serious terms outside the cut now get in
    assert c2["reserved"] == 11 and {f"T{i:03d}" for i in range(0, 139)} <= got   # the 9 unused reserved slots went back to the plain fill: ranks 0..138 stay
    assert list(beyond.reserved_ime).count(True) == 11


def test_reserve_beyond_keeps_the_total_at_the_cap_when_reserved_terms_sit_just_outside_the_plain_fill():
    rows = fake_terms(160)                                    # only 160 survivors: serious terms at ranks 131-139 lie inside the first cap - reserved positions
    ime = {f"T{i:03d}" for i in range(131, 140)}
    kept, c = triage_v2.select(df_of(rows), "no label text", cap=150, reserve=20, ime=ime, mode="reserve_beyond")
    assert len(kept) == 150 and c["reserved"] == 9 and ime <= set(kept.pt) and {f"T{i:03d}" for i in range(0, 130)} <= set(kept.pt)   # 141 plain + 9 reserved


def test_select_with_few_serious_terms_gives_the_slots_back():
    rows = fake_terms(200, ime_at=(190, 191, 192))
    ime = {"T190", "T191", "T192"}
    kept, c = triage_v2.select(df_of(rows), "no label text", cap=150, reserve=20, ime=ime)
    assert len(kept) == 150 and c["reserved"] == 3 and {"T190", "T191", "T192"} <= set(kept.pt) and "T146" in set(kept.pt) and "T147" not in set(kept.pt)  # 147 chi-squared slots + 3


def test_select_only_reserves_for_terms_that_meet_evans_and_are_not_on_the_label():
    rows = fake_terms(200, ime_at=(170, 171, 172, 173), non_evans=(170,))
    ime = {"T170", "T171", "T172", "T173"}
    kept, c = triage_v2.select(df_of(rows), "label mentions t172 clearly", cap=150, reserve=20, ime=ime)   # T170 fails Evans; T172's exact name is on the label
    got = set(kept.pt)
    assert "T170" not in got and "T172" not in got and {"T171", "T173"} <= got and c["ime_eligible"] == 2


def test_select_treats_a_group_as_serious_and_as_on_the_label_if_any_member_is():
    rows = fake_terms(200)
    for r in rows:
        r["members"] = None
    rows[180]["pt"], rows[180]["members"] = "GROUP CURRENT", ["GROUP CURRENT", "GROUP OLD"]
    rows[181]["pt"], rows[181]["members"] = "OTHER CURRENT", ["OTHER CURRENT", "OTHER OLD"]
    kept, c = triage_v2.select(df_of(rows), "label text with other old inside", cap=150, reserve=20, ime={"GROUP CURRENT"})
    got = set(kept.pt)
    assert "GROUP CURRENT" in got and "OTHER CURRENT" not in got       # serious through the current name; the other is dropped as on the label via its OLD name


def test_gabapentin_dress_is_grouped_and_v2_changes_the_list_without_assuming_the_outcome():
    """Real data (statistics only): gabapentin 2008Q4. Grouping merges the two DRESS names (structure checked here). Whether the term then makes the v2 cut is a
    RESULT reported by analysis/triage_v2_check.py, not something a test may assume."""
    groups = triage_v2.load_groups()
    default_m = miner_agent.run("gabapentin", "2008-12-31", allow_evaluation=True)
    v2_m = miner_agent.run("gabapentin", "2008-12-31", allow_evaluation=True, term_groups=groups)
    old_name = "DRUG RASH WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"
    dress = "DRUG REACTION WITH EOSINOPHILIA AND SYSTEMIC SYMPTOMS"
    assert old_name in {t["term"] for t in default_m["terms"]} and old_name not in {t["term"] for t in v2_m["terms"]}   # merged away
    row = next(t for t in v2_m["terms"] if t["term"] == dress)
    assert row["members"] == [dress, old_name] and row["a"] == 20 and row["evans"] and row["a"] == next(t for t in default_m["terms"] if t["term"] == old_name)["a"]
    assert v2_m["n_terms"] < default_m["n_terms"] and v2_m["term_groups"][dress] == [dress, old_name]
    d_names = {t["term"] for t in triage_step.run(default_m, triage="locked")["terms"]}
    v = triage_step.run(v2_m, v2=True)
    assert old_name not in d_names and dress not in d_names                    # the locked rule never sends it to the Label Checker
    names = [t["term"] for t in v["terms"]]
    assert v["triage_v2"] is True and len(names) <= 150 and len(names) == len(set(names)) and old_name not in names   # the retired name never appears on its own
    assert v["counts"]["reserved"] == min(20, v["counts"]["ime_eligible"]) and sum(t["reserved_ime"] for t in v["terms"]) == v["counts"]["reserved"]
    b = triage_step.run(v2_m, v2="beyond")                                      # the variant: reserved slots for serious terms OUTSIDE the plain top 130
    assert b["triage_v2"] == "beyond" and len(b["terms"]) <= 150 and b["counts"]["reserved"] <= 20 and old_name not in {t["term"] for t in b["terms"]}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
