"""adot_prior.py - should the efficiency baseline know a receiver's role?

THE GAP. mart.usage_baseline has exactly THREE rows - one per position. Every
WR in the league shrinks toward the same `base_ypt`, so a 15-air-yard deep
threat and a 6-air-yard slot receiver are pulled toward an identical efficiency
prior at k=40 targets. That is the crudest possible role model, and it was
right to start there while nothing better had been measured.

WHAT CHANGED. ngs_stability.py measured aDOT (NGS avg_intended_air_yards) at
0.781 year over year - the most stable player metric anywhere in this project,
above target share at 0.748 - and it carries INCREMENTAL signal about next
season's yards per target at partial +0.208, controlling for the player's own
prior YPT. The relationship is monotone across every bucket:

    prior aDOT   <7.5   7.5-10   10-12.5   12.5-15   15+
    actual YPT   7.29     7.99      8.10      8.40   8.79

So the baseline can be a function of role instead of a single position mean.

WHAT THIS DELIBERATELY DOES NOT USE. avg_separation is MORE stable than aDOT's
raw counterpart and it is still excluded, because its partial correlation with
next-season YPT is NEGATIVE (-0.136): separation measures assignment, not
quality, and slot receivers separate most while gaining least per target.
Stability alone never earned an input a place here.

POINT IN TIME. The aDOT used for season S is the week=0 aggregate for season
S-1. Never the current season - that is the same leak the ratings assert
against.

HONEST FITTING. The slope is fit on the training seasons only and scored on
seasons it never saw. A role prior tuned and graded on the same rows would
always look like it helped.

Usage:
    py -3 scripts/adot_prior.py                 # test only
    py -3 scripts/adot_prior.py --build         # write mart.adot_ypt_prior
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

TRAIN = (2016, 2022)
TEST = (2023, 2025)
SHRINK_TARGETS = 40.0     # matches sql/12_prop_projection.sql
MIN_TARGETS_NGS = 50      # NGS publishes a season row above this


def log(m=""):
    print(m, flush=True)


def fit_slope(con, lo, hi):
    """YPT as a function of PRIOR-season aDOT, target-weighted OLS."""
    rows = con.execute("""
        SELECT p.avg_intended_air_yards AS adot,
               c.yards * 1.0 / nullif(c.targets, 0) AS ypt,
               c.targets AS w
        FROM raw.ngs_receiving c
        JOIN raw.ngs_receiving p
          ON p.player_gsis_id = c.player_gsis_id AND p.season = c.season - 1
         AND p.week = 0 AND p.season_type = 'REG' AND p.targets >= ?
        WHERE c.week = 0 AND c.season_type = 'REG' AND c.targets >= ?
          AND c.season BETWEEN ? AND ?
          AND p.avg_intended_air_yards IS NOT NULL AND c.yards IS NOT NULL
    """, [MIN_TARGETS_NGS, MIN_TARGETS_NGS, lo, hi]).fetchall()
    W = sum(r[2] for r in rows)
    mx = sum(r[0] * r[2] for r in rows) / W
    my = sum(r[1] * r[2] for r in rows) / W
    sxy = sum(r[2] * (r[0] - mx) * (r[1] - my) for r in rows)
    sxx = sum(r[2] * (r[0] - mx) ** 2 for r in rows)
    b1 = sxy / sxx
    return b1, my - b1 * mx, len(rows)


def evaluate(con, b1, b0, lo, hi):
    """Out-of-sample: does a role-aware baseline beat the position baseline?

    Replicates sql/12_prop_projection.sql EXACTLY - the same 16-game trailing
    sums, the same k=40 - and changes only what the shrinkage pulls toward.
    An earlier version of this used the game's actual targets where the formula
    uses the player's PRIOR cumulative targets, which measured nothing.
    """
    rows = con.execute("""
        WITH lagged AS (
            SELECT u.gsis_id, u.season, u.week, u.position, u.rec_yards,
                   sum(u.rec_yards) OVER we AS p_rec_yds_sum,
                   sum(u.targets)   OVER we AS p_tgt_sum
            FROM mart.player_game_usage u
            WHERE u.position IN ('WR', 'TE', 'RB')
            WINDOW we AS (PARTITION BY u.gsis_id ORDER BY u.season, u.week
                          ROWS BETWEEN 16 PRECEDING AND 1 PRECEDING)
        )
        SELECT l.rec_yards, pj.proj_targets, b.base_ypt,
               n.avg_intended_air_yards AS prior_adot,
               coalesce(l.p_rec_yds_sum, 0) AS pry, coalesce(l.p_tgt_sum, 0) AS ptg
        FROM lagged l
        JOIN mart.player_usage_projection pj
          ON pj.gsis_id = l.gsis_id AND pj.season = l.season AND pj.week = l.week
        JOIN mart.usage_baseline b ON b.position = l.position
        LEFT JOIN raw.ngs_receiving n
               ON n.player_gsis_id = l.gsis_id AND n.season = l.season - 1
              AND n.week = 0 AND n.season_type = 'REG' AND n.targets >= ?
        WHERE l.season BETWEEN ? AND ? AND l.rec_yards IS NOT NULL
          AND pj.proj_targets IS NOT NULL
    """, [MIN_TARGETS_NGS, lo, hi]).fetchall()

    K = SHRINK_TARGETS
    n_cov = 0
    err_cur = err_new = 0.0
    err_cur_cov = err_new_cov = 0.0
    for actual, ptgt, base, adot, pry, ptg in rows:
        cur_ypt = (pry + base * K) / (ptg + K)
        cur = cur_ypt * ptgt
        if adot is None:
            new = cur
        else:
            n_cov += 1
            role = b0 + b1 * adot
            new = ((pry + role * K) / (ptg + K)) * ptgt
            err_cur_cov += abs(actual - cur)
            err_new_cov += abs(actual - new)
        err_cur += abs(actual - cur)
        err_new += abs(actual - new)
    n = len(rows)
    cov = (err_cur_cov / n_cov, err_new_cov / n_cov) if n_cov else (0, 0)
    return err_cur / n, err_new / n, n, n_cov, cov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--build", action="store_true")
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=not a.build)

    log("=" * 78)
    log("aDOT AS AN EFFICIENCY PRIOR - does role beat position?")
    log("=" * 78)

    b1, b0, n_fit = fit_slope(con, *TRAIN)
    log("  fit on %d-%d (%d player-seasons, target-weighted):"
        % (TRAIN[0], TRAIN[1], n_fit))
    log("    YPT = %.3f + %.4f * prior aDOT" % (b0, b1))
    log("    a 15-air-yard receiver starts at %.2f, a 6-air-yard one at %.2f"
        % (b0 + b1 * 15, b0 + b1 * 6))
    log("    the single position baseline treats those as identical")

    mae_cur, mae_new, n, n_cov, cov = evaluate(con, b1, b0, *TEST)
    log("")
    log("  scored on %d-%d, never seen by the fit:" % (TEST[0], TEST[1]))
    log("    %d player-games, %d with a prior-season aDOT (%.1f%%)"
        % (n, n_cov, 100.0 * n_cov / n))
    log("    receiving yards MAE, position baseline : %.4f" % mae_cur)
    log("    receiving yards MAE, aDOT role baseline: %.4f" % mae_new)
    log("    change: %+.4f yards" % (mae_new - mae_cur))
    log("")
    log("  restricted to the %d rows that HAVE an aDOT:" % n_cov)
    log("    position baseline %.4f  ->  role baseline %.4f  (%+.4f)"
        % (cov[0], cov[1], cov[1] - cov[0]))

    log("")
    log("=" * 78)
    log("VERDICT")
    log("=" * 78)
    if mae_new < mae_cur - 0.005:
        log("  The role prior helps. Wire it into sql/12_prop_projection.sql in")
        log("  place of the flat position base_ypt.")
        ok = True
    elif mae_new > mae_cur + 0.005:
        log("  The role prior HURTS out of sample. Do not ship it. aDOT is")
        log("  stable and incrementally informative about YPT, and that still")
        log("  does not survive contact with the full projection.")
        ok = False
    else:
        log("  No measurable difference. The k=40 shrinkage already pulls so")
        log("  hard that what it shrinks TOWARD barely matters for a player")
        log("  with real target volume. Not worth the complexity.")
        ok = False

    if a.build and ok:
        con.execute("""
            CREATE OR REPLACE TABLE mart.adot_ypt_prior AS
            SELECT ? AS intercept, ? AS slope, ? AS fit_lo, ? AS fit_hi,
                   ? AS min_targets
        """, [b0, b1, TRAIN[0], TRAIN[1], MIN_TARGETS_NGS])
        log("\n  wrote mart.adot_ypt_prior")
    elif a.build:
        log("\n  --build given but the test did not clear; nothing written.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
