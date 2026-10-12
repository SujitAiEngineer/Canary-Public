"""Exclusion lists for case and demo choices, loaded from a LOCAL, untracked file (config/exclusions.local.txt, one name per line, '#' starts a comment).
The template config/exclusions.example.txt is empty and is what is used when the local file is missing. A name matches when it is a substring of the lower-cased text."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL = os.path.join(HERE, "config", "exclusions.local.txt")
EXAMPLE = os.path.join(HERE, "config", "exclusions.example.txt")


def load(path=None):
    path = path or (LOCAL if os.path.exists(LOCAL) else EXAMPLE)
    names = set()
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip().lower()
        if line:
            names.add(line)
    return names


EXCLUDED = load()


def is_excluded(text):
    t = str(text).lower()
    return any(n in t for n in EXCLUDED)


def assert_not_excluded(names):
    bad = [n for n in names if is_excluded(n)]
    if bad:
        raise ValueError(f"excluded by project policy: {bad}")
