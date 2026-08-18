"""season_proj.py - can we project a SEASON, and does availability carry it?

NOT A BETTING SCRIPT. It reads no line, no price, no book. It scores against
actual season receiving yards.

WHY THIS IS THE RIGHT PLACE TO START on the player layer. No feed carries
historical PREGAME props, so the per-game prop model can only ever be graded
forward. Season totals are the opposite: 1,208 player-seasons with 50+ touches
across 2020-2025, all of them scoreable. It is the one part of the player layer
that can be validated BEFORE it is trusted.

A SEASON TOTAL IS NOT A SECOND MODEL. It is games x per-game, so this
deliberately reuses the same shrunk usage and shrunk efficiency the per-game
projection uses. Building a separate season estimator would let the two
silently disagree about the same player.

THE CLAIM UNDER TEST is that availability is the term that matters. Games
played carries year over year at +0.415, against 0.14-0.31 for yards per
target; mean 15.43 with sd 3.06 and 22.5% of qualifying seasons under 14 games.
Four missed games swamp any efficiency edge available to us.

FOUR CANDIDATES, all strictly point-in-time - nothing after season S-1 is read
when projecting season S:

  naive_total   last season's actual receiving yards
  naive_rate    last season's yards per game x 17
  proj_fixed    shrunk usage x shrunk efficiency x a CONSTANT games count
  proj_avail    the same, x games predicted from prior availability

Usage:
    py -3 scripts/season_proj.py
    py -3 scripts/season_proj.py --min-season 2022
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

TEST = (2021, 2025)
MIN_PRIOR_TOUCHES = 50      # a real prior workload, so there is anything to project
SHRINK_TGT = 2.0            # games of league prior on usage   (usage is stable)
SHRINK_EFF = 40.0           # targets of league prior on YPT   (efficiency is not)


def log(m=""):
    print(m, flush=True)


def mae(pairs):
    return sum(abs(a - b) for a, b in pairs) / len(pairs)


def corr(pairs):
    n = len(pairs)
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    syy = sum((b - my) ** 2 for _, b in pairs)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


def load(con, lo, hi):
    """Player-seasons with the PRIOR season's inputs attached.

    Everything on the right of the join is season-1. The only season-S values
    read are the actuals being scored.
    """
    return con.execute("""
        WITH ps AS (
            SELECT season, gsis_id, position,
                   any_value(team) AS team,
                   count(*)              AS games,
                   sum(targets)          AS targets,
                   sum(rec_yards)        AS rec_yards,
                   sum(target_share * targets) / nullif(sum(targets), 0)
                                         AS tgt_share_w,
                   avg(target_share)     AS tgt_share
            FROM mart.player_game_usage
            WHERE position IN ('WR', 'TE', 'RB')
            GROUP BY 1, 2, 3
        ),
        team_vol AS (
            SELECT season, team, sum(team_targets) / count(DISTINCT game_id)
                       AS tgt_per_game
            FROM (SELECT DISTINCT season, team, game_id, team_targets
                  FROM mart.player_game_usage)
            GROUP BY 1, 2
        ),
        lg AS (
            SELECT season,
                   sum(rec_yards) / nullif(sum(targets), 0) AS lg_ypt
            FROM ps GROUP BY 1
        )
        SELECT c.season, c.gsis_id, c.position, c.games AS act_games,
               c.rec_yards AS act_yards,
               p.games AS pr_games, p.targets AS pr_targets,
               p.rec_yards AS pr_yards, p.tgt_share AS pr_share,
               tv.tgt_per_game AS pr_team_tgt, lgp.lg_ypt AS pr_lg_ypt
        FROM ps c
        JOIN ps p  ON p.gsis_id = c.gsis_id AND p.season = c.season - 1
        LEFT JOIN team_vol tv ON tv.season = p.season AND tv.team = p.team
        LEFT JOIN lg lgp ON lgp.season = p.season
        WHERE c.season BETWEEN ? AND ?
          AND p.targets + 0 >= 0 AND p.games >= 4
          AND c.rec_yards IS NOT NULL AND p.tgt_share IS NOT NULL
          AND tv.tgt_per_game IS NOT NULL
    """, [lo, hi]).fetchall()


def fit_games(rows):
    """games_S = a + b * games_{S-1}, on the training rows only."""
    xs = [r[5] for r in rows]
    ys = [r[3] for r in rows]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sxy / sxx if sxx else 0.0
    return b, my - b * mx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--min-season", type=int, default=TEST[0])
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)
    rows = load(con, a.min_season, TEST[1])
    con.close()

    keep = [r for r in rows if (r[6] or 0) >= MIN_PRIOR_TOUCHES]
    log("=" * 78)
    log("SEASON PROJECTION - does availability carry it?")
    log("=" * 78)
    log(f"  {len(rows)} player-seasons with a prior season; {len(keep)} with "
        f"{MIN_PRIOR_TOUCHES}+ prior targets")
    log("  scored on actual season receiving yards. No market data is read.")

    # availability fit, on the first two test seasons only, scored on the rest
    fit_rows = [r for r in keep if r[0] <= a.min_season + 1]
    score_rows = [r for r in keep if r[0] > a.min_season + 1]
    b1, b0 = fit_games(fit_rows)
    mean_games = sum(r[3] for r in fit_rows) / len(fit_rows)
    log("")
    log(f"  availability fit on {a.min_season}-{a.min_season + 1} "
        f"({len(fit_rows)} rows), scored on {len(score_rows)}:")
    log(f"    games_S = {b0:.2f} + {b1:.3f} * games_prior")
    log(f"    a 17-game prior projects {b0 + b1 * 17:.1f}; "
        f"a 10-game prior projects {b0 + b1 * 10:.1f}")
    log(f"    constant-games baseline uses the training mean, {mean_games:.2f}")

    cands = {k: [] for k in ("naive_total", "naive_rate", "proj_fixed",
                             "proj_avail")}
    for r in score_rows:
        (_s, _g, _pos, act_games, act_yards, pr_games, pr_tgts, pr_yards,
         pr_share, pr_team_tgt, pr_lg_ypt) = r
        # usage: light shrinkage, because usage is the stable part
        share = ((pr_share * pr_games) + (pr_share * 0 + 0.0) * SHRINK_TGT) \
            / (pr_games + SHRINK_TGT)
        # efficiency: heavy shrinkage toward the league rate, because it is not
        ypt = ((pr_yards or 0) + (pr_lg_ypt or 0) * SHRINK_EFF) \
            / ((pr_tgts or 0) + SHRINK_EFF)
        per_game = share * (pr_team_tgt or 0) * ypt
        g_pred = max(1.0, min(17.0, b0 + b1 * pr_games))

        cands["naive_total"].append((pr_yards or 0, act_yards))
        cands["naive_rate"].append(((pr_yards or 0) / pr_games * 17, act_yards))
        cands["proj_fixed"].append((per_game * mean_games, act_yards))
        cands["proj_avail"].append((per_game * g_pred, act_yards))

    log("")
    log(f"  {'candidate':<14}{'MAE':>9}{'corr':>8}{'bias':>9}   vs naive_total")
    base = mae(cands["naive_total"])
    for k in ("naive_total", "naive_rate", "proj_fixed", "proj_avail"):
        m = mae(cands[k])
        c = corr(cands[k])
        bias = sum(p - t for p, t in cands[k]) / len(cands[k])
        delta = "" if k == "naive_total" else f"{(m / base - 1) * 100:+.1f}%"
        log(f"  {k:<14}{m:>9.1f}{c:>8.3f}{bias:>+9.1f}   {delta}")

    m_fixed, m_avail = mae(cands["proj_fixed"]), mae(cands["proj_avail"])
    gain_naive = 1 - m_avail / base
    gain_avail = 1 - m_avail / m_fixed
    log("")
    log("=" * 78)
    log("VERDICT")
    log("=" * 78)
    log(f"  availability-aware vs naive prior-season total : {gain_naive * 100:+.1f}%"
        f"   (bar: beat by 5%)")
    log(f"  availability-aware vs constant games           : {gain_avail * 100:+.1f}%"
        f"   (bar: beat by 2%)")
    log("")
    if gain_naive >= 0.05 and gain_avail >= 0.02:
        log("  CONFIRMED. Aggregate the per-game projection and model games")
        log("  played; ship a season distribution with a band.")
    elif gain_naive >= 0.05:
        log("  PARTIAL. The projection beats naive but modelling availability")
        log("  adds nothing over a constant. Ship projection x constant games")
        log("  and do NOT claim an availability model.")
    else:
        log("  REFUTED. The aggregation does not beat last season's total.")
        log("  Publish the market or ADP with a band and build no ranking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
