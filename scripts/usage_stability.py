"""
usage_stability.py - measure what is actually predictable, before modeling it.

The prop engine's founding claim is that USAGE (target share, route share,
carry share) is more stable week to week than PRODUCTION (yards, receptions),
and that projecting usage then converting to yards therefore beats projecting
yards directly.

That is a testable claim and it decides the whole architecture, so it gets
tested before a line of the model is written.

Method: for each player-game, take the mean of the previous N games (strictly
prior - no leakage) and correlate it with the current game's value. Higher
correlation = more predictable from recent history. Compared across metrics on
the SAME player-games, so the comparison is like for like.

Usage:
    py -3 scripts/usage_stability.py
    py -3 scripts/usage_stability.py --window 4 --min-snap-pct 0.25
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

# (column, label, family)
METRICS = [
    ("offense_pct",      "snap %",           "usage"),
    ("route_proxy_pct",  "route share",      "usage"),
    ("target_share",     "target share",     "usage"),
    ("air_yards_share",  "air-yard share",   "usage"),
    ("carry_share",      "carry share",      "usage"),
    ("wopr",             "WOPR",             "usage"),
    ("targets",          "targets",          "volume"),
    ("carries",          "carries",          "volume"),
    ("receptions",       "receptions",       "production"),
    ("rec_yards",        "receiving yards",  "production"),
    ("rush_yards",       "rushing yards",    "production"),
    ("rz_touches",       "red-zone touches", "production"),
    ("racr",             "RACR (efficiency)", "efficiency"),
]


def log(m: str = "") -> None:
    print(m, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--window", type=int, default=4)
    ap.add_argument("--min-snap-pct", type=float, default=0.25)
    ap.add_argument("--positions", default="WR,TE,RB")
    a = ap.parse_args()

    con = duckdb.connect(str(a.db), read_only=True)
    pos = "','".join(p.strip() for p in a.positions.split(","))

    prior = ",\n".join(
        f"""avg({c}) OVER (PARTITION BY gsis_id ORDER BY season, week
              ROWS BETWEEN {a.window} PRECEDING AND 1 PRECEDING) AS prior_{c}"""
        for c, _, _ in METRICS)

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE lagged AS
        WITH base AS (
            SELECT gsis_id, season, week, position, offense_pct,
                   {', '.join(c for c, _, _ in METRICS if c != 'offense_pct')}
            FROM mart.player_game_usage
            WHERE position IN ('{pos}')
        )
        SELECT *, {prior},
               count(*) OVER (PARTITION BY gsis_id ORDER BY season, week
                    ROWS BETWEEN {a.window} PRECEDING AND 1 PRECEDING) AS n_prior
        FROM base
    """)

    # One common sample for every metric, so the comparison is like for like.
    where = (f"n_prior >= {a.window} AND offense_pct >= {a.min_snap_pct} "
             + " AND ".join([""] + [f"prior_{c} IS NOT NULL AND {c} IS NOT NULL"
                                    for c, _, _ in METRICS]))
    n = con.execute(f"SELECT count(*) FROM lagged WHERE {where}").fetchone()[0]

    log("=" * 78)
    log("WEEK-TO-WEEK PREDICTABILITY")
    log("=" * 78)
    log(f"positions {a.positions} | prior window {a.window} games | "
        f"snap% >= {a.min_snap_pct}")
    log(f"common sample: {n:,} player-games (2020-2025)\n")
    if n < 500:
        log("sample too small to conclude")
        return 1

    log(f"  {'metric':<20}{'family':<12}{'corr':>8}{'R^2':>8}"
        f"{'mean':>10}{'CV':>8}")
    results = []
    for c, label, fam in METRICS:
        r = con.execute(f"""
            SELECT corr({c}, prior_{c}), avg({c}), stddev_samp({c})
            FROM lagged WHERE {where}
        """).fetchone()
        if r[0] is None:
            continue
        cv = (r[2] / r[1]) if r[1] else float("nan")
        results.append((label, fam, r[0], r[0] ** 2, r[1], cv))
    for label, fam, corr, r2, mean, cv in sorted(
            results, key=lambda x: -x[2]):
        log(f"  {label:<20}{fam:<12}{corr:>8.3f}{r2:>8.3f}{mean:>10.3f}{cv:>8.2f}")

    # definitional A/B: does the corrected denominator predict better?
    ab = con.execute(f"""
        WITH b AS (
          SELECT target_share, pbp_target_share, offense_pct,
                 avg(target_share) OVER w AS p_can,
                 avg(pbp_target_share) OVER w AS p_pbp,
                 count(*) OVER w AS np
          FROM mart.player_game_usage WHERE position IN ('{pos}')
          WINDOW w AS (PARTITION BY gsis_id ORDER BY season, week
                       ROWS BETWEEN {a.window} PRECEDING AND 1 PRECEDING))
        SELECT count(*), corr(target_share,p_can), corr(pbp_target_share,p_pbp)
        FROM b WHERE np >= {a.window} AND offense_pct >= {a.min_snap_pct}
          AND p_can IS NOT NULL AND p_pbp IS NOT NULL
    """).fetchone()
    log("")
    log(f"  DEFINITIONAL A/B on target share (n={ab[0]:,}):")
    log(f"    nflverse denominator (team targets)    corr {ab[1]:.4f}")
    log(f"    our old denominator (team pass plays)  corr {ab[2]:.4f}")
    log(f"    difference {ab[1]-ab[2]:+.4f}")

    usage = [x for x in results if x[1] == "usage"]
    prod = [x for x in results if x[1] == "production"]
    mu = sum(x[2] for x in usage) / len(usage)
    mp = sum(x[2] for x in prod) / len(prod)
    log(f"\n  mean corr - usage:      {mu:.3f}")
    log(f"  mean corr - production: {mp:.3f}")
    log(f"  advantage:              {mu - mp:+.3f}")
    verdict = ("CONFIRMED" if mu - mp > 0.10 else
               "WEAK" if mu - mp > 0.03 else "NOT SUPPORTED")
    log(f"\n  PREMISE ({verdict}): project usage, then convert to production.")

    log("\n" + "=" * 78)
    log("HOW FAST DOES IT STABILISE?  (target share, by window length)")
    log("=" * 78)
    log(f"  {'window':>8}{'n':>9}{'corr':>8}")
    for w in (1, 2, 3, 4, 6, 8):
        r = con.execute(f"""
            WITH b AS (
                SELECT target_share,
                       avg(target_share) OVER (PARTITION BY gsis_id
                            ORDER BY season, week
                            ROWS BETWEEN {w} PRECEDING AND 1 PRECEDING) AS p,
                       count(*) OVER (PARTITION BY gsis_id ORDER BY season, week
                            ROWS BETWEEN {w} PRECEDING AND 1 PRECEDING) AS np,
                       offense_pct
                FROM mart.player_game_usage WHERE position IN ('{pos}')
            )
            SELECT count(*), corr(target_share, p) FROM b
            WHERE np >= {w} AND p IS NOT NULL AND offense_pct >= {a.min_snap_pct}
        """).fetchone()
        if r[1] is not None:
            log(f"  {w:>8}{r[0]:>9,}{r[1]:>8.3f}")

    log("\n" + "=" * 78)
    log("YARDS PER TARGET - is efficiency predictable at all?")
    log("=" * 78)
    log("If usage is predictable but efficiency is not, then the right model")
    log("is projected-usage x LEAGUE/ROLE-baseline efficiency, not projected")
    log("usage x the player's own past efficiency.\n")
    r = con.execute(f"""
        WITH b AS (
            SELECT gsis_id, season, week,
                   CASE WHEN targets >= 2 THEN rec_yards*1.0/targets END AS ypt,
                   offense_pct
            FROM mart.player_game_usage WHERE position IN ('{pos}')
        ), l AS (
            SELECT *, avg(ypt) OVER (PARTITION BY gsis_id ORDER BY season, week
                        ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING) AS prior_ypt,
                   count(ypt) OVER (PARTITION BY gsis_id ORDER BY season, week
                        ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING) AS np
            FROM b
        )
        SELECT count(*), corr(ypt, prior_ypt), avg(ypt), stddev_samp(ypt)
        FROM l WHERE np >= 8 AND ypt IS NOT NULL AND prior_ypt IS NOT NULL
          AND offense_pct >= {a.min_snap_pct}
    """).fetchone()
    log(f"  yards per target, 8-game prior: n={r[0]:,}  corr={r[1]:.3f}  "
        f"mean={r[2]:.2f}  sd={r[3]:.2f}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
