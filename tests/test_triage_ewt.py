"""Offline tests (no API, no spend) for triage part 5: Evidence-Weighted Triage, EWT (post-result exploratory, opt-in, default off).
The key test: with the flag off the triage output is EXACTLY the locked output on all the saved evaluation runs. Also: the score and the selection order by
hand calculation, shared ranks for equal values, the tie-breaks, the list size min(150, survivors), the component counts on a tiny hand-built pair of
DuckDB files (health-professional codes, OT not serious, country fallback with unknown not counted, the recent window), the locked drop of on-label terms,
and that the opt-in hook changes only the selection.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_triage_ewt.py"""
import glob, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duckdb
import pandas as pd

import triage, triage_ewt as T
from agents import triage_step

RUNS = [d for d in sorted(glob.glob("eval_out/runs/*")) if os.path.exists(f"{d}/miner.json") and os.path.exists(f"{d}/triage.json")]
J = lambda p: json.load(open(p, encoding="utf-8"))


def frame(rows):
    """rows: (pt, chi2, ic025, hp, recent, countries, serious)"""
    return pd.DataFrame([dict(zip(("pt", "chi2", "ic025", "hp", "recent", "countries", "serious"), r)) for r in rows]).sort_values("pt", kind="stable")


def test_default_reproduces_the_locked_triage_output_exactly_on_all_saved_runs():
    assert len(RUNS) >= 12, len(RUNS)
    for d in RUNS:
        m, saved = J(f"{d}/miner.json"), J(f"{d}/triage.json")
        out = triage_step.run(m, triage="locked")   # ewt is off by default
        assert out["terms"] == saved["terms"], d
        assert out["counts"] == saved["counts"] and out["label_date"] == saved["label_date"] and out["cap"] == saved["cap"] == 150, d
        assert out["ewt"] is False and out["prune"] is None and out["triage_v2"] is False and out["stratified"] is False


def test_constants_are_the_fixed_design():
    assert T.COMPONENTS == ("ic025", "hp", "recent", "countries", "serious") and T.CAP == 150 and T.RECENT_QUARTERS == 4
    assert T.HP_CODES == ("MD", "HP", "PH", "RN") and T.SERIOUS_CODES == ("DE", "LT", "HO", "DS", "CA", "RI") and "OT" not in T.SERIOUS_CODES


def test_score_by_hand_and_selection_order():
    s = frame([("A", 30, 1.0, 10, 5, 3, 8), ("B", 20, 2.0, 20, 9, 6, 9), ("C", 10, 3.0, 30, 1, 9, 10), ("D", 5, 0.5, 5, 0, 1, 1)])
    sc = T.score_frame(s).set_index("pt")
    # ranks (1 = largest): ic025 A3 B2 C1 D4 | hp A3 B2 C1 D4 | recent A2 B1 C3 D4 | countries A3 B2 C1 D4 | serious A3 B2 C1 D4
    assert sc.loc["A", "score"] == (3 + 3 + 2 + 3 + 3) / 5 and sc.loc["B", "score"] == (2 + 2 + 1 + 2 + 2) / 5
    assert sc.loc["C", "score"] == (1 + 1 + 3 + 1 + 1) / 5 and sc.loc["D", "score"] == 4.0
    assert list(sc.sort_values("pos").index) == ["C", "B", "A", "D"]       # scores C 1.4, B 1.8, A 2.8, D 4.0


def test_equal_values_share_the_average_rank_and_ties_go_to_chi2_then_name():
    s = frame([("A", 30, 1.0, 5, 5, 2, 5), ("B", 20, 1.0, 5, 5, 2, 5), ("C", 20, 1.0, 5, 5, 2, 5)])   # every component equal for all three
    sc = T.score_frame(s).set_index("pt")
    assert (sc[["r_" + c for c in T.COMPONENTS]] == 2.0).all().all() and (sc.score == 2.0).all()
    assert list(sc.sort_values("pos").index) == ["A", "B", "C"]     # chi2 30 first, then B before C (equal chi2, alphabetical)


def test_list_size_is_min_cap_survivors_and_output_is_in_chi2_order():
    for n in (1, 40, 150, 151, 400):
        rows = [(f"T{i:04d}", float(1000 - i), float(i % 7), i % 11, i % 13, i % 5, i % 3) for i in range(n)]
        out, sc = T.select(frame(rows), cap=150)
        assert len(out) == min(150, n) and len(sc) == n
        assert list(out.chi2) == sorted(out.chi2, reverse=True)
        assert set(out.pt) == set(sc.sort_values("pos").head(150).pt)


def test_with_few_survivors_ewt_returns_exactly_the_locked_set():
    rows = [(f"T{i}", float(100 - i), float(i % 4), i, 0, 1, i % 2) for i in range(60)]
    out, _ = T.select(frame(rows), cap=150)
    assert set(out.pt) == {r[0] for r in rows}


def test_component_counts_on_a_hand_built_store():
    d = tempfile.mkdtemp()
    main, ext = os.path.join(d, "m.duckdb"), os.path.join(d, "e.duckdb")
    c = duckdb.connect(main)
    c.execute("CREATE TABLE cases(rid BIGINT, caseid BIGINT, ver INTEGER, qidx SMALLINT, fda_dt DATE)")
    c.execute("CREATE TABLE reac(rid BIGINT, pt VARCHAR)")
    c.execute("CREATE TABLE outc(rid BIGINT, outc_cod VARCHAR)")
    # qcut = 20: the window is quarters 17..20. Case 1 was first received in quarter 5 (old); report 6 is a later version of case 1 (quarter 19) and is the
    # latest one, so the case stays old. Cases 2..5 were first received in quarters 18, 19, 20, 17 (recent). Report 7 is not the drug's.
    c.execute("INSERT INTO cases VALUES (1,1,0,5,NULL),(2,2,0,18,NULL),(3,3,0,19,NULL),(4,4,0,20,NULL),(5,5,0,17,NULL),(6,1,1,19,NULL),(7,7,0,3,NULL)")
    c.execute("CREATE TEMP TABLE drug_rids AS SELECT * FROM (VALUES (6),(2),(3),(4),(5)) t(rid)")
    c.execute("INSERT INTO reac VALUES (6,'RASH'),(2,'RASH'),(3,'RASH'),(4,'RASH'),(5,'RASH'),(2,'NAUSEA'),(7,'RASH')")
    c.execute("INSERT INTO outc VALUES (6,'OT'),(2,'HO'),(3,'DE'),(3,'OT'),(4,'OT'),(5,'RI'),(7,'DE')")
    e = duckdb.connect(ext)
    e.execute("CREATE TABLE report_ext(rid BIGINT, qidx SMALLINT, occp_cod VARCHAR, occr_country VARCHAR, reporter_country VARCHAR, event_dt_raw VARCHAR, country VARCHAR)")
    e.execute("""INSERT INTO report_ext VALUES (6,19,'MD',NULL,'US',NULL,'US'),(2,18,'CN','US',NULL,NULL,'US'),(3,19,'PH','JP',NULL,NULL,'JP'),
                 (4,20,NULL,NULL,NULL,NULL,NULL),(5,17,'LW','JP',NULL,NULL,'JP'),(7,3,'MD','FR',NULL,NULL,'FR')""")
    e.close()
    got = T.component_counts(c, 20, ext_path=ext)
    # RASH over reports 6,2,3,4,5: hp = 6 (MD) and 3 (PH) -> 2; recent = cases 2,3,4,5 (report 6's case 1 is old) -> 4; countries US, JP (report 4 unknown) -> 2;
    # serious (DE LT HO DS CA RI, not OT) = 2 (HO), 3 (DE), 5 (RI) -> 3
    assert got["RASH"] == (2, 4, 2, 3), got["RASH"]
    assert got["NAUSEA"] == (0, 1, 1, 1), got["NAUSEA"]       # report 2: consumer, recent, US, HO
    assert "ext" not in [r[0] for r in c.execute("SELECT database_name FROM duckdb_databases()").fetchall()]   # detached again


def test_survivors_apply_the_locked_drop_and_carry_the_five_components():
    df = pd.DataFrame([
        {"pt": "RASH", "a": 10, "b": 90, "c": 100, "d": 9800, "chi2": 50.0, "evans": True},
        {"pt": "NAUSEA", "a": 10, "b": 90, "c": 100, "d": 9800, "chi2": 40.0, "evans": True},     # on the label -> dropped
        {"pt": "FEVER", "a": 2, "b": 98, "c": 10, "d": 9890, "chi2": 30.0, "evans": True},        # a < 3 -> dropped
        {"pt": "COUGH", "a": 10, "b": 90, "c": 100, "d": 9800, "chi2": 20.0, "evans": False},     # not Evans -> dropped
        {"pt": "PAIN", "a": 8, "b": 92, "c": 100, "d": 9800, "chi2": 10.0, "evans": True}]).sort_values("pt", kind="stable")
    feats = {"RASH": (3, 2, 4, 5), "PAIN": (1, 0, 2, 0)}
    s, counts = T.survivors(df, feats, "Adverse reactions: nausea.")
    assert list(s.sort_values("pt").pt) == ["PAIN", "RASH"] and counts == {"evans": 4, "a>=3": 3, "not_exact_on_label": 2}
    assert tuple(s[s.pt == "RASH"][["hp", "recent", "countries", "serious"]].iloc[0]) == (3, 2, 4, 5) and all(c in s.columns for c in T.COMPONENTS)
    assert float(s[s.pt == "RASH"].ic025.iloc[0]) > float(s[s.pt == "PAIN"].ic025.iloc[0])


def test_hook_runs_and_changes_only_the_selection():
    from agents import miner
    m = miner.run("atorvastatin", "2011-12-31")
    locked = triage_step.run(m, triage="locked")
    r = triage_step.run(m, ewt=True)
    assert r["ewt"] is True and r["status"] == "ok" and r["counts"]["variant"] == "EWT"
    assert len(r["terms"]) == len(locked["terms"]) == 150 and r["counts"]["not_exact_on_label"] == locked["counts"]["not_exact_on_label"]
    assert r["label_date"] == locked["label_date"] and r["cap"] == 150


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
