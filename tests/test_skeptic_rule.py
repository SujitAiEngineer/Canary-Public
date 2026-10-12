"""Offline tests (no API) for the Skeptic's code-enforced verdict rule, EVALUATION_PLAN.md 5b.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_skeptic_rule.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.skeptic import decide, FLAG

PK = {"items": {"R1": "r", "S1": "s", "S2": "s", "C1": "c", "X1": "x"}, "blocks": {"R": ["R1"], "S": ["S1", "S2"], "C": ["C1"], "X": ["X1"]}}


def v(winner, cited):
    b = lambda ids: {"score": 0.5, "reason": "because", "cited_ids": ids}
    out = {"real_effect": b(["R1"]), "reporting_spike": b(["S1"]), "confounding": b(["C1"]), "winner": winner}
    out[winner] = b(cited)
    return out


def test_real_effect_is_recommended():
    assert decide(v("real_effect", ["R1"]), PK)[:2] == ("recommended", None)


def test_spike_with_own_evidence_is_dismissed():
    d, flag, reason, cited = decide(v("reporting_spike", ["S1", "X1"]), PK)
    assert d == "dismissed" and flag is None and cited == ["S1"]  # the context item is not counted


def test_confounding_with_own_evidence_is_dismissed():
    assert decide(v("confounding", ["C1"]), PK)[0] == "dismissed"


def test_dismissal_on_context_only_is_not_dismissal():
    d, flag, *_ = decide(v("reporting_spike", ["X1"]), PK)
    assert (d, flag) == ("recommended", FLAG)


def test_dismissal_citing_another_branch_or_nothing_is_not_dismissal():
    assert decide(v("reporting_spike", ["C1"]), PK)[:2] == ("recommended", FLAG)  # a confounding item cannot dismiss a spike
    assert decide(v("confounding", []), PK)[:2] == ("recommended", FLAG)
    assert decide(v("confounding", ["Z9"]), PK)[:2] == ("recommended", FLAG)  # invented ID


def test_invalid_output_never_dismisses():
    assert decide(None, PK)[0] == "recommended"
    assert decide({"winner": "nonsense"}, PK)[0] == "recommended"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
