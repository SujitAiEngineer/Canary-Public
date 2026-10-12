"""Triage v2: POST-RESULT EXPLORATORY WORK, opt-in, default OFF. Not part of the locked evaluation (see eval_runs.md, 2026-10-03). The locked triage.py
(chi-squared rank, cap 150) stays the default and is not touched; this module is only used when a caller passes the opt-in flag.

Two changes (motivated by the supplementary gabapentin run, where the older-named DRESS term met Evans at 3 of 4 year-ends but ranked 390, 181 and 271,
below the cap of 150):
  1. TERM GROUPING before ranking: renamed or synonymous MedDRA terms listed in the reviewed file reference/term_groups.csv are merged into one group.
     The group's 2x2 table counts each REPORT once (a report carrying both names is not double counted), then PRR, chi-squared and the Evans test are
     computed on the group. A group is "on the label" if any of its names is.
  2. RESERVED SLOTS for serious events: the total stays at the cap (150). Up to `reserve` (20) slots go to the best (by chi-squared) terms that meet
     the Evans criteria AND are on the EMA Important Medical Events (IME) list, taken from the official file reference/ema_ime_meddra_v29_1.csv. The
     remaining slots are filled by the existing chi-squared rank over everything not already reserved. A group is on the IME list if any member is.
"""
import csv
import re

import pandas as pd

import triage  # the locked module: fold(), on_label_exact(), MIN_COUNT

GROUPS_CSV = "reference/term_groups.csv"
IME_CSV = "reference/ema_ime_meddra_v29_1.csv"
CAP = 150
RESERVE = 20


def load_groups(path=GROUPS_CSV):
    """{group name: [member terms]} (the current name first). Every term belongs to at most one group."""
    groups, seen = {}, {}
    for r in csv.DictReader(open(path, encoding="utf-8", newline="")):
        g, m = r["group_name"].strip().upper(), r["member_term"].strip().upper()
        if m in seen and seen[m] != g:
            raise ValueError(f"{m} is in two groups: {seen[m]} and {g}")
        seen[m] = g
        groups.setdefault(g, [])
        if m not in groups[g]:
            groups[g].append(m)
    return groups


def load_ime(path=IME_CSV):
    """Upper-cased preferred terms of the official EMA IME list."""
    return {r["pt_name"].strip().upper() for r in csv.DictReader(open(path, encoding="utf-8", newline=""))}


def group_stats(con, groups, n_total, n_drug):
    """2x2 table of every group that has at least one report with the drug. Needs the temp tables `latest` and `drug_rids` that the Miner's mine()
    just built on this connection. Each report counts once per group. Returns {group: {a, b, c, d, PRR, chi2, evans, members}}."""
    gmap = [(m, g) for g, ms in groups.items() for m in ms]
    con.execute("CREATE OR REPLACE TEMP TABLE gmap(pt VARCHAR, grp VARCHAR)")
    con.executemany("INSERT INTO gmap VALUES (?, ?)", gmap)
    a = dict(con.execute("SELECT grp, count(DISTINCT rid) FROM reac JOIN gmap USING (pt) WHERE rid IN (SELECT rid FROM drug_rids) GROUP BY grp").fetchall())
    anyn = dict(con.execute("SELECT grp, count(DISTINCT rid) FROM reac JOIN gmap USING (pt) WHERE rid IN (SELECT rid FROM latest) GROUP BY grp").fetchall())
    out = {}
    for g, a_g in a.items():
        b = n_drug - a_g
        c = anyn[g] - a_g
        d = (n_total - n_drug) - c
        n = a_g + b + c + d
        prr = (a_g / (a_g + b)) / (c / (c + d)) if c > 0 and (a_g + b) > 0 and (c + d) > 0 else float("nan")
        denom = (a_g + b) * (c + d) * (a_g + c) * (b + d)
        chi2 = n * (a_g * d - b * c) ** 2 / denom if denom else float("nan")
        out[g] = {"a": int(a_g), "b": int(b), "c": int(c), "d": int(d), "PRR": prr, "chi2": chi2,
                  "evans": bool(a_g >= 3 and prr >= 2 and chi2 >= 4), "members": groups[g]}
    return out


def merge_terms(terms, gstats):
    """Replace every member term of a grouped concept by ONE row named after the group (the current name). Terms outside any group are unchanged.
    `terms` is the Miner agent's list of dicts."""
    members = {m for g in gstats.values() for m in g["members"]}
    kept = [t for t in terms if t["term"] not in members]
    for name, g in gstats.items():
        kept.append({"term": name, "a": g["a"], "b": g["b"], "c": g["c"], "d": g["d"],
                     "PRR": None if g["PRR"] != g["PRR"] else float(g["PRR"]), "chi2": None if g["chi2"] != g["chi2"] else float(g["chi2"]),
                     "evans": g["evans"], "yearly": {}, "members": g["members"], "grouped": True})
    return kept


def names_of(row):
    m = row.get("members") if hasattr(row, "get") else None
    return list(m) if isinstance(m, (list, tuple)) else [row["pt"]]


def select(df, label_text, min_count=triage.MIN_COUNT, cap=CAP, reserve=RESERVE, ime=None, mode="reserve_first"):
    """The v2 selection. df: one row per term (columns pt, a, chi2, evans, optional members), sorted by pt for deterministic ties.
    mode "reserve_first" (the rule as specified): the `reserve` best serious terms are taken first, the other slots are filled by chi-squared rank over
        everything else. NOTE: when at least `reserve` serious terms already rank inside the top `cap`, the reserved set is a subset of the plain list and
        the result equals the plain list (the rule changes nothing).
    mode "reserve_beyond": the plain chi-squared fill is limited to cap - reserve slots; the `reserve` reserved slots go to the best serious terms NOT
        already in that plain fill; unused reserved slots are given back to the plain fill, so the total is still `cap`.
    Returns (kept DataFrame sorted by chi-squared descending with a bool column `reserved_ime`, step counts)."""
    ime = load_ime() if ime is None else ime
    ev = df[df.evans]
    s1 = ev[ev.a >= min_count]
    fl = triage.fold(label_text)
    s2 = s1[[not any(triage.on_label_exact(n, fl) for n in names_of(r)) for r in s1.to_dict("records")]].copy()
    s2["ime"] = [any(n in ime for n in names_of(r)) for r in s2.to_dict("records")]
    by_chi2 = s2.sort_values("chi2", ascending=False, kind="stable")
    if mode == "reserve_first":
        reserved = s2[s2.ime].sort_values("chi2", ascending=False, kind="stable").head(reserve)
        rest = s2.drop(reserved.index).sort_values("chi2", ascending=False, kind="stable").head(max(cap - len(reserved), 0))
    elif mode == "reserve_beyond":
        plain = by_chi2.head(max(cap - reserve, 0))
        reserved = by_chi2.drop(plain.index)
        reserved = reserved[reserved.ime].head(reserve)
        rest = by_chi2.drop(reserved.index).head(max(cap - len(reserved), 0))   # unused reserved slots go back to the plain fill; the total stays at the cap
    else:
        raise ValueError(mode)
    kept = s2.loc[list(reserved.index) + list(rest.index)].copy()
    kept["reserved_ime"] = [i in set(reserved.index) for i in kept.index]
    kept = kept.sort_values("chi2", ascending=False, kind="stable")
    return kept, {"evans": len(ev), f"a>={min_count}": len(s1), "not_exact_on_label": len(s2), "ime_eligible": int(s2.ime.sum()),
                  "reserved": len(reserved), f"cap{cap}": len(kept)}


# ======================================================================================================================================
# Part 2 (post-result exploratory, opt-in, default off): alternative RANKING scores in place of chi-squared. Pre-specified in eval_runs.md
# (2026-10-03, "part 2") BEFORE any result; no threshold is tuned and no other score is tried. Only the ranking step changes: the Evans flag, a >= 3,
# the exact on-label drop and the cap of 150 are those of the locked triage.
# ======================================================================================================================================
import math

RANKINGS = ("prr_lb", "ic025", "best_of")


def prr_lower_bound(a, b, c, d):
    """Lower bound of the 95% confidence interval of the PRR: exp(ln PRR - 1.96 SE), SE = sqrt(1/a - 1/(a+b) + 1/c - 1/(c+d))."""
    if a <= 0 or c <= 0 or a + b <= 0 or c + d <= 0:
        return float("-inf")
    prr = (a / (a + b)) / (c / (c + d))
    se = math.sqrt(max(1 / a - 1 / (a + b) + 1 / c - 1 / (c + d), 0.0))
    return math.exp(math.log(prr) - 1.96 * se)


def ic025(a, b, c, d):
    """Lower bound of the information component (BCPNN): E = (a+b)(a+c)/N; IC = log2((a+0.5)/(E+0.5));
    IC025 = IC - 3.3 (a+0.5)^(-1/2) - 2 (a+0.5)^(-3/2)."""
    n = a + b + c + d
    e = (a + b) * (a + c) / n
    ic = math.log2((a + 0.5) / (e + 0.5))
    return ic - 3.3 * (a + 0.5) ** -0.5 - 2 * (a + 0.5) ** -1.5


def _positions(series):
    """1 = best (largest). Ties go to the term that comes first in the frame, which is alphabetical order (the frame is sorted by term name)."""
    order = series.sort_values(ascending=False, kind="stable").index
    return pd.Series({idx: i + 1 for i, idx in enumerate(order)})


def select_ranked(df, label_text, how, min_count=triage.MIN_COUNT, cap=CAP):
    """Triage with the ranking replaced by `how` in RANKINGS. df: one row per term (pt, a, b, c, d, evans), sorted by term name.
    Returns (kept DataFrame in the new order, counts, ranked DataFrame of ALL survivors with columns prr_lb, ic025, rank_prr_lb, rank_ic025, new_rank)."""
    if how not in RANKINGS:
        raise ValueError(how)
    ev = df[df.evans]
    s1 = ev[ev.a >= min_count]
    fl = triage.fold(label_text)
    s2 = s1[[not any(triage.on_label_exact(n, fl) for n in names_of(r)) for r in s1.to_dict("records")]].copy()
    s2["prr_lb"] = [prr_lower_bound(r.a, r.b, r.c, r.d) for r in s2.itertuples()]
    s2["ic025"] = [ic025(r.a, r.b, r.c, r.d) for r in s2.itertuples()]
    s2["rank_prr_lb"], s2["rank_ic025"] = _positions(s2.prr_lb), _positions(s2.ic025)
    if how == "prr_lb":
        key = list(zip(s2.rank_prr_lb, s2.index))
    elif how == "ic025":
        key = list(zip(s2.rank_ic025, s2.index))
    else:  # best_of: the better rank, then the worse rank, then (via the frame order) the term name
        key = [(min(r1, r2), max(r1, r2), idx) for r1, r2, idx in zip(s2.rank_prr_lb, s2.rank_ic025, s2.index)]
    order = [idx for _, idx in sorted(zip(key, s2.index), key=lambda z: z[0])]
    s2["new_rank"] = pd.Series({idx: i + 1 for i, idx in enumerate(order)})
    ranked = s2.loc[order]
    kept = ranked.head(cap)
    return kept, {"evans": len(ev), f"a>={min_count}": len(s1), "not_exact_on_label": len(s2), f"cap{cap}": len(kept)}, ranked


# ======================================================================================================================================
# Part 3 (post-result exploratory, opt-in, default off): STRATIFIED triage. Pre-specified, with these exact numbers, in eval_runs.md (2026-10-03,
# "part 3") BEFORE any result. Terms compete only against terms with a similar number of reports; the cap stays 150. Not tuned.
# ======================================================================================================================================
BANDS = ((3, 10), (11, 30), (31, 100), (101, 10 ** 9))   # report-count bands B1..B4 by a
RARE_MIN_FRAC = 0.25                                       # at least ceil(0.25 x cap) = 38 slots for B1 and B2 combined


def band_of(a):
    for i, (lo, hi) in enumerate(BANDS):
        if lo <= a <= hi:
            return i
    raise ValueError(f"a={a} is below the minimum count of 3")


def _largest_remainder(total, weights):
    """Split `total` whole slots in proportion to integer weights (largest remainder; ties go to the earlier, i.e. rarer, band). Integer arithmetic only."""
    s = sum(weights)
    if s == 0 or total <= 0:
        return [0] * len(weights)
    base = [total * w // s for w in weights]
    rem = [total * w % s for w in weights]
    for i in sorted(range(len(weights)), key=lambda i: (-rem[i], i))[: total - sum(base)]:
        base[i] += 1
    return base


def stratified_quotas(n, cap=CAP, rare_min_frac=RARE_MIN_FRAC):
    """n = survivors per band (B1..B4). Returns the slots per band: proportional (largest remainder); at least ceil(rare_min_frac x cap) for B1 and B2 combined
    (taken from B3 and B4 in proportion to their quotas); limited by availability; unused slots given to bands with survivors left, in proportion to what they
    have left, until all `cap` slots are used. The total is always min(cap, sum(n))."""
    n = list(n)
    if sum(n) <= cap:
        return n
    q = _largest_remainder(cap, n)
    need = -(-int(rare_min_frac * 1000) * cap // 1000)             # ceil(0.25 x 150) = 38, exactly (integers)
    if q[0] + q[1] < need and n[0] + n[1] > 0:
        taken = min(need - q[0] - q[1], q[2] + q[3])
        rare = _largest_remainder(q[0] + q[1] + taken, [n[0], n[1]])
        cut = _largest_remainder(taken, [q[2], q[3]])
        q = [rare[0], rare[1], q[2] - cut[0], q[3] - cut[1]]
    q = [min(a, b) for a, b in zip(q, n)]
    while True:
        unused = cap - sum(q)
        spare = [b - a for a, b in zip(q, n)]
        if unused <= 0 or sum(spare) == 0:
            break
        give = _largest_remainder(min(unused, sum(spare)), spare)
        q = [a + g for a, g in zip(q, give)]
    return q


def select_stratified(df, label_text, min_count=triage.MIN_COUNT, cap=CAP):
    """Stratified triage. df: one row per term (pt, a, chi2, evans), sorted by term name. Same survivors as the locked triage (Evans, a >= min_count, not named on
    the label); then the quota per report-count band, chi-squared within the band. Returns (kept sorted by chi-squared with columns band 1..4 and rank_in_band,
    counts incl. survivors and quota per band, all survivors with band and rank_in_band)."""
    ev = df[df.evans]
    s1 = ev[ev.a >= min_count]
    fl = triage.fold(label_text)
    s2 = s1[[not any(triage.on_label_exact(n, fl) for n in names_of(r)) for r in s1.to_dict("records")]].copy()
    s2["band"] = [band_of(int(a)) + 1 for a in s2.a]
    s2 = s2.sort_values("chi2", ascending=False, kind="stable")
    s2["rank_in_band"] = s2.groupby("band").cumcount() + 1
    n = [int((s2.band == b).sum()) for b in (1, 2, 3, 4)]
    q = stratified_quotas(n, cap)
    kept = s2[[rk <= q[b - 1] for b, rk in zip(s2.band, s2.rank_in_band)]]
    return kept, {"evans": len(ev), f"a>={min_count}": len(s1), "not_exact_on_label": len(s2), "band_survivors": n, "band_quotas": q, f"cap{cap}": len(kept)}, s2
