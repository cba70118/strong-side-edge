"""ngs_stability.py - does Next Gen Stats carry, or is it just descriptive?

NOT A BETTING SCRIPT. It reads no line, no price, no book.

WHY THIS EXISTS. raw.ngs_passing / ngs_receiving / ngs_rushing have been in the
warehouse since the first load and NOTHING has ever read them. Before wiring
them into the prop model they have to answer the same question every other
input answered: does a player's value this season tell you anything about the
same player next season?

The bar is set by what we already know. usage_stability.py measured target
share at 0.748 year over year and yards per target at 0.136 - which is why
prop_model shrinks efficiency at k=40 and usage at k=2. NGS metrics are
CONSTRUCTED to separate skill from outcome: completion percentage above
expectation, yards after catch above expectation, rush yards over expected. If
that construction works they should sit well above their raw counterparts. If
they do not, they are description dressed as measurement and they get no place
in the model.

METHOD. week=0 rows are the league's own season aggregates (mean 407 pass
attempts against 33 for a weekly row), so no re-aggregation is needed and none
is done - re-deriving a season number from weekly rates would reintroduce the
unweighted-mean error that once put a 3-14 team at +0.170 net EPA.

Usage:
    py -3 scripts/ngs_stability.py
    py -3 scripts/ngs_stability.py --min-season 2018
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

# (table, volume column, volume floor, [(label, column, kind, raw counterpart)])
# kind: over_exp = built to strip luck | process = how they play
#       usage = how much they are used | outcome = what happened
GROUPS = [
    ("ngs_passing", "attempts", 200, [
        ("CPOE", "completion_percentage_above_expectation",
         "over_exp", "completion_percentage"),
        ("completion %", "completion_percentage", "outcome", None),
        ("expected completion %", "expected_completion_percentage",
         "process", None),
        ("time to throw", "avg_time_to_throw", "process", None),
        ("aggressiveness", "aggressiveness", "process", None),
        ("intended air yards", "avg_intended_air_yards", "process", None),
        ("air yards to sticks", "avg_air_yards_to_sticks", "process", None),
        ("passer rating", "passer_rating", "outcome", None),
    ]),
    ("ngs_receiving", "targets", 50, [
        ("YAC above expected", "avg_yac_above_expectation", "over_exp",
         "avg_yac"),
        ("avg YAC", "avg_yac", "outcome", None),
        ("avg separation", "avg_separation", "process", None),
        ("avg cushion", "avg_cushion", "process", None),
        ("air yards share", "percent_share_of_intended_air_yards",
         "usage", None),
        ("intended air yards", "avg_intended_air_yards", "process", None),
        ("catch %", "catch_percentage", "outcome", None),
    ]),
    ("ngs_rushing", "rush_attempts", 80, [
        ("RYOE per att", "rush_yards_over_expected_per_att", "over_exp",
         "avg_rush_yards"),
        ("avg rush yards", "avg_rush_yards", "outcome", None),
        ("rush pct over exp", "rush_pct_over_expected", "over_exp", None),
        ("efficiency", "efficiency", "process", None),
        ("time to LOS", "avg_time_to_los", "process", None),
        ("8+ defender rate", "percent_attempts_gte_eight_defenders",
         "process", None),
    ]),
]

# The bar, from usage_stability.py. Printed so no result is read in a vacuum.
BASELINE_USAGE = 0.748
BASELINE_EFFICIENCY = 0.136


def log(m=""):
    print(m, flush=True)


def corr(pairs):
    n = len(pairs)
    if n < 15:
        return None, n
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    syy = sum((b - my) ** 2 for _, b in pairs)
    if sxx <= 0 or syy <= 0:
        return None, n
    return sxy / math.sqrt(sxx * syy), n


def series(con, table, col, vol, floor, min_season):
    rows = con.execute(
        "SELECT season, player_gsis_id, " + col + " FROM raw." + table +
        " WHERE week = 0 AND season_type = 'REG' AND season >= ?"
        "   AND " + col + " IS NOT NULL AND " + vol + " >= ?",
        [min_season, floor]).fetchall()
    return {(s, p): v for s, p, v in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--min-season", type=int, default=2016)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)

    log("=" * 78)
    log("NEXT GEN STATS - does any of it carry year to year?")
    log("=" * 78)
    log("seasons %d+, regular season, the league's own season aggregates"
        % a.min_season)
    log("(week=0). No market data is read anywhere.")
    log("")
    log("  THE BAR: target share carries at %.3f, yards per target at %.3f."
        % (BASELINE_USAGE, BASELINE_EFFICIENCY))
    log("  An efficiency-family metric is only worth modeling if it lands far")
    log("  above that second number.")

    results = {}
    for table, vol, floor, metrics in GROUPS:
        log("")
        log("  %s  (min %d %s)" % (table, floor, vol))
        log("    %-22s%-10s%8s%6s   verdict" % ("metric", "kind", "yr/yr", "n"))
        log("    " + "-" * 62)
        for label, col, kind, raw in metrics:
            cur = series(con, table, col, vol, floor, a.min_season)
            pairs = [(v, cur[(s + 1, p)]) for (s, p), v in cur.items()
                     if (s + 1, p) in cur]
            r, n = corr(pairs)
            results[label] = (r, kind, raw, table, col)
            if r is None:
                verdict = "insufficient"
            elif r >= 0.40:
                verdict = "CARRIES"
            elif r >= 0.25:
                verdict = "weak"
            else:
                verdict = "NOISE"
            rs = ("%+.3f" % r) if r is not None else "  --"
            log("    %-22s%-10s%8s%6d   %s" % (label, kind, rs, n, verdict))

    log("")
    log("=" * 78)
    log("DOES 'OVER EXPECTED' BEAT THE RAW NUMBER IT REFINES?")
    log("=" * 78)
    log("This is the whole claim. If stripping the luck out does not make a")
    log("metric more stable, the construction is not doing anything.")
    log("")
    log("  %-24s%8s   %-24s%8s%8s"
        % ("over-expected metric", "r", "raw counterpart", "r", "gain"))
    gains = []
    for label, (r, kind, raw, table, col) in results.items():
        if kind != "over_exp" or not raw:
            continue
        rawlab = None
        for l2, (r2, k2, rc2, t2, c2) in results.items():
            if c2 == raw and t2 == table:
                rawlab = l2
                break
        if rawlab is None or r is None or results[rawlab][0] is None:
            continue
        rr = results[rawlab][0]
        gains.append((label, r, rawlab, rr, r - rr))
        log("  %-24s%+8.3f   %-24s%+8.3f%+8.3f"
            % (label, r, rawlab, rr, r - rr))

    carries = [l for l, v in results.items() if v[0] is not None and v[0] >= 0.40]
    log("")
    log("=" * 78)
    log("VERDICT")
    log("=" * 78)
    log("  clears 0.40: %s" % (", ".join(carries) if carries else "NOTHING"))
    best = max([g for _, _, _, _, g in gains], default=None)
    if best is not None:
        log("  best over-expected gain over its raw counterpart: %+.3f" % best)
    if carries and best is not None and best >= 0.10:
        log("  NGS earns a place. Wire what cleared into the prop model as an")
        log("  efficiency prior, shrunk by its own measured r.")
    elif carries:
        log("  Some NGS metrics are stable, but the over-expected construction")
        log("  is NOT what makes them stable. Use the stable ones on their own")
        log("  terms and leave the rest as description.")
    else:
        log("  NGS is descriptive only. It gets no place in the prop model.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
