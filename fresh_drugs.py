"""The five fresh drugs for the clean EWT test (part 5, Step 2) with their FAERS name patterns. List, selection rule and patterns were committed in
eval_runs.md (commit bd88b30) BEFORE any ranking was computed for them. Patterns are written from names only. No exclusion pattern.
`register()` adds them to the Miner agent's pattern table in this process only (agents/miner.py and eval_config.py are not edited)."""

FRESH = {
    "montelukast": {"match": r"montelukast|singulair", "nda": "NDA020829", "signal": "Stevens-Johnson syndrome (posted 2012Q1; label updated Sep 2012)"},
    "voriconazole": {"match": r"voriconazol|vfend", "nda": "NDA021266", "signal": "photosensitivity reaction (posted 2021Q4; label updated Aug 2022)"},
    "docetaxel": {"match": r"docetaxel|taxotere|docefrez|docivyx", "nda": "NDA020449", "signal": "neutropenic enterocolitis (posted 2017Q2; label updated Sep/Oct 2018)"},
    "bosentan": {"match": r"bosentan|tracleer", "nda": "NDA021290", "signal": "anaphylaxis and DRESS (posted 2016Q2; label updated Oct 2016)"},
    "enzalutamide": {"match": r"enzalutamide|xtandi", "nda": "NDA203415", "signal": "severe cutaneous adverse reactions (posted 2020Q2; label updated Oct 2020)"},
}


def register():
    import exclusions
    from agents import miner
    exclusions.assert_not_excluded(FRESH)   # the exclusion lists come from the local, untracked config file
    for name, c in FRESH.items():
        miner.PATTERNS[name] = (c["match"], None)
