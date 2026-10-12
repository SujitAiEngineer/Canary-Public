"""Offline tests of the exclusion loader. They need config/exclusions.local.txt (not in git); without it they say so and check nothing.
Run: PYTHONIOENCODING=utf-8 python -u tests/test_exclusions.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import exclusions, eval_config, fresh_drugs, canary_config


def have_local():
    return os.path.exists(exclusions.LOCAL)


def test_the_example_template_is_empty_and_the_local_file_is_ignored_by_git():
    assert exclusions.load(exclusions.EXAMPLE) == set()
    assert "config/exclusions.local.txt" in open(os.path.join(exclusions.HERE, ".gitignore"), encoding="utf-8").read()


def test_local_list_is_loaded_and_matches_case_insensitively_by_substring():
    if not have_local():
        print("skipped: no config/exclusions.local.txt"); return
    names = sorted(exclusions.EXCLUDED)
    assert len(names) >= 50
    n = names[0]
    assert exclusions.is_excluded(n.upper()) and exclusions.is_excluded("brand of " + n + " tablets") and not exclusions.is_excluded("")


def test_no_case_in_the_project_is_on_the_exclusion_lists():
    if not have_local():
        print("skipped: no config/exclusions.local.txt"); return
    used = list(eval_config.CASES) + list(eval_config.DEV) + list(fresh_drugs.FRESH) + [d["drug"] for d in canary_config.DEMO_RUNS]
    exclusions.assert_not_excluded(used)
    for c in list(eval_config.CASES.values()) + list(eval_config.DEV.values()):
        exclusions.assert_not_excluded(c["match"].split("|"))


def test_assert_not_excluded_raises_for_an_excluded_name():
    if not have_local():
        print("skipped: no config/exclusions.local.txt"); return
    try:
        exclusions.assert_not_excluded(["aspirin", sorted(exclusions.EXCLUDED)[0]])
    except ValueError as e:
        assert "project policy" in str(e)
    else:
        raise AssertionError("an excluded name was accepted")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("pass", name, flush=True)
