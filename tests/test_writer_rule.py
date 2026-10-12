"""Offline tests (no API) for the brief writer's citation rule: uncited claims are removed.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_writer_rule.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.writer import validate_claims, DISCLAIMER

PK = {"items": {"R1": "a", "S1": "b"}, "blocks": {}}
LABEL = ["Hyperglycemia;  Nervous system: amnesia"]


def test_valid_id_kept():
    kept, removed = validate_claims([{"text": "x", "cited_ids": ["R1"], "label_quote": ""}], PK, LABEL)
    assert len(kept) == 1 and not removed


def test_uncited_claim_removed():
    kept, removed = validate_claims([{"text": "x", "cited_ids": [], "label_quote": ""}], PK, LABEL)
    assert not kept and len(removed) == 1


def test_invented_id_removed_but_real_ids_kept():
    kept, removed = validate_claims([{"text": "x", "cited_ids": ["R9"], "label_quote": ""},
                                     {"text": "y", "cited_ids": ["R9", "S1"], "label_quote": ""}], PK, LABEL)
    assert [k["text"] for k in kept] == ["y"] and kept[0]["cited_ids"] == ["S1"] and len(removed) == 1


def test_label_quote_must_be_verbatim():
    ok, _ = validate_claims([{"text": "x", "cited_ids": [], "label_quote": "hyperglycemia; nervous system"}], PK, LABEL)
    bad, rem = validate_claims([{"text": "x", "cited_ids": [], "label_quote": "causes diabetes"}], PK, LABEL)
    assert len(ok) == 1 and not bad and len(rem) == 1


def test_number_not_in_evidence_removes_sentence():
    pk = {"items": {"R1": "132 reports out of 24849; PRR 13.19, chi-squared 1324.2. Peak year 2005: 20% arrived."}}
    ok = {"text": "There were 132 of 24,849 reports (PRR 13.19) in 2005 [R1].", "cited_ids": ["R1"], "label_quote": ""}
    made_up = {"text": "About 31-10% of cases had a lipid indication.", "cited_ids": ["R1"], "label_quote": ""}
    kept, removed = validate_claims([ok, made_up], pk, [""])
    assert [k["text"] for k in kept] == [ok["text"]] and removed[0]["removed_because"] == "number not in the evidence"
    kept, _ = validate_claims([{"text": "No papers were found.", "cited_ids": ["R1"], "label_quote": ""}], pk, [""])
    assert len(kept) == 1  # a sentence with no numbers is judged by the citation rule only


def test_disclaimer_present():
    assert "not medical advice" in DISCLAIMER


class _Block:
    type = "tool_use"
    def __init__(self, inp): self.input = inp


class _Resp:
    class usage:
        input_tokens, output_tokens = 100, 50
    def __init__(self, inp): self.content = [_Block(inp)]


class StubClient:
    """Returns one cited claim for any request; counts calls."""
    calls = 0
    class messages:
        @staticmethod
        def create(**kw):
            StubClient.calls += 1
            return _Resp({"claims": [{"text": "Stub claim with 12 reports.", "cited_ids": ["R1"], "label_quote": ""}]})


def _fake_run(n=6):
    items = {"R1": "12 reports out of 100; PRR 3.00, chi-squared 50.0.", "R2": "label", "S1": "s", "C1": "c"}
    results, labs, terms = [], [], []
    for i in range(n):
        t = f"TERM {i}"
        br = {k: {"score": 0.5, "reason": "r", "cited_ids": ["R1"]} for k in ("real_effect", "reporting_spike", "confounding")}
        results.append({"term": t, "decision": "recommended", "flag": None, "winner": "real_effect", "branches": br, "label_decision": "no",
                        "label_evidence": "", "label_date": "20200101", "evidence": {"items": items, "blocks": {}}, "evidence_hash": f"h{i}", "cached": True, "reason": "r", "cited": []})
        labs.append({"term": t, "route": "to_skeptic", "chunks": [], "evidence": ""})
        terms.append({"term": t, "chi2": 1000.0 - i})  # TERM 0 has the highest chi-squared
    return {"drug": "d", "date": "2011-12-31", "results": results}, {"results": labs}, {"terms": terms}


def test_featured_term_keeps_its_real_rank():
    from agents.writer import run as write_brief
    sk, lab, m = _fake_run(6)
    out = write_brief(sk, lab, m, client=StubClient, max_briefs=2, featured=["TERM 4"], mode_note="extended review mode (top 400)")
    assert [t["term"] for t in out["terms"]] == ["TERM 0", "TERM 1", "TERM 4"]  # the highlight is added, in its real position, not promoted
    assert {t["term"]: t["rank"] for t in out["terms"]}["TERM 4"] == 5
    assert out["terms"][2]["featured"] and not out["terms"][0]["featured"]
    md = out["brief_markdown"]
    assert "(demo highlight)" in md and "extended review mode (top 400)" in md and "Term 5" in md.split("Also recommended")[1]


def test_review_cap_is_demo_only():
    import orchestrator
    try:
        orchestrator.run_pipeline("gabapentin", "2008-12-31", evaluation_mode=True, review_cap=400, client=StubClient)
    except ValueError as e:
        assert "demo-only" in str(e)
        return
    raise AssertionError("evaluation mode accepted an extended review cap")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
