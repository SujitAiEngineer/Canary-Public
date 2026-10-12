"""Build Step 1 test: the Drug Resolver. Run: PYTHONIOENCODING=utf-8 python -u tests/test_resolver.py (or pytest)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.resolver import resolve


def test_brand():
    r = resolve("Lipitor")
    assert r["status"] == "resolved" and r["generic_name"] == "atorvastatin" and r["rxcui"] == "83367"


def test_brand_with_dose():
    r = resolve("lipitor 20mg")
    assert r["status"] == "resolved" and r["generic_name"] == "atorvastatin"


def test_generic_with_form():
    r = resolve("Linezolid 600 mg tablets")
    assert r["status"] == "resolved" and r["generic_name"] == "linezolid"


def test_typo_asks_did_you_mean():
    r = resolve("atorvastatn")
    assert r["status"] == "needs_confirmation" and r["suggestions"][0]["generic_name"] == "atorvastatin"
    assert r["generic_name"] is None  # never chosen silently


def test_zyvox():
    r = resolve("Zyvox")
    assert r["status"] == "resolved" and r["generic_name"] == "linezolid"


def test_nonsense_not_found():
    for word in ("xyzzyflorp", "asdfgh", "qwertzupi"):
        r = resolve(word)
        assert r["status"] == "not_found", (word, r)


def test_empty():
    assert resolve("  ")["status"] == "not_found"


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); n += 1; print("pass", name, flush=True)
    print(f"{n} tests passed")

