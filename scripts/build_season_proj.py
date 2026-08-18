"""build_season_proj.py - season receiving projections with a MEASURED band.

WHAT IS VALIDATED AND WHAT IS NOT, stated up front because the distinction is
the whole product:

  VALIDATED  the construction beats last season's actual total by 14.6% on
             out-of-sample MAE (220.0 vs 257.6 over 409 player-seasons, fit on
             2021-22 and scored on 2023-25) and is nearly unbiased - median
             actual/projected ratio 1.01 against naive's +126-yard bias.
  NOT        whether it beats a MARKET. There is no season-long player market
             in this warehouse: none defined in ref.market, none captured, and
             no ADP source. So this is a projection, not an edge, and it is not
             presented as a betting product.

THE BAND IS MEASURED, NOT MODELLED. The empirical distribution of
actual/projected on the 409 out-of-sample rows is p10 0.34, p50 1.01, p90 1.77.
A ratio band is the right shape - a 1,200-yard projection and a 300-yard one do
not share an absolute error - and a p10-p90 spanning 0.34x to 1.77x is the
honest width. Publishing a point estimate for a season total would imply a
precision the backtest says does not exist.

AVAILABILITY IS A FLAG, NOT A MODEL. Games played carries at +0.415 year over
year, and the fitted line spans only 4.7 games across the whole prior range
against a 14.42 constant - it bought 0.9% and missed its 2% bar. It gains 7.2%
for the 31 players who played 4-11 games the prior year, which is suggestive on
31 rows and not a model. Those players are flagged instead.

Usage:
    py -3 scripts/build_season_proj.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
SEASON = 2026
PRIOR = 2025

# Same shrinkage as the validated backtest. Usage is stable so it is shrunk
# lightly; efficiency is not, so it is shrunk hard toward the league rate.
SHRINK_TGT = 2.0
SHRINK_EFF = 40.0
GAMES_CONST = 14.42        # training-set mean; the availability model lost to it

# Empirical actual/projected quantiles, measured on the 409 out-of-sample rows.
BAND = {"p10": 0.34, "p25": 0.64, "p50": 1.01, "p75": 1.34, "p90": 1.77}

# RUSHING gets its OWN band, backtested the same way on 237 out-of-sample rows.
# It beats naive by only 8.1% against receiving's 14.6%, under-projects by 91
# yards, and its band is far wider - p10 0.12 to p90 2.36. Sharing receiving's
# band would understate rushing uncertainty by roughly half.
RUSH_BAND = {"p10": 0.12, "p50": 1.05, "p90": 2.36}
RUSH_GAMES_CONST = 12.54
SHRINK_CARRY = 2.0
LOW_GAMES = 11             # prior-season games at or below which availability
                           # is flagged rather than modelled


def log(m=""):
    print(m, flush=True)


def build(con) -> int:
    con.execute("""
        CREATE OR REPLACE TABLE mart.player_season_projection AS
        WITH prior AS (
            SELECT gsis_id, any_value(position) AS position,
                   any_value(team) AS prior_team,
                   count(*)       AS pr_games,
                   sum(targets)   AS pr_targets,
                   sum(rec_yards) AS pr_yards,
                   avg(target_share) AS pr_share,
                   sum(carries)     AS pr_carries,
                   sum(rush_yards)  AS pr_rush_yards,
                   avg(carry_share) AS pr_carry_share
            FROM mart.player_game_usage
            WHERE season = ? AND position IN ('WR', 'TE', 'RB')
            GROUP BY gsis_id
        ),
        team_vol AS (
            SELECT team, sum(team_targets) / count(DISTINCT game_id)
                       AS tgt_per_game,
                   sum(team_rush_plays) / count(DISTINCT game_id)
                       AS rush_per_game
            FROM (SELECT DISTINCT season, team, game_id, team_targets,
                         team_rush_plays
                  FROM mart.player_game_usage WHERE season = ?)
            GROUP BY team
        ),
        lg AS (
            SELECT sum(rec_yards) / nullif(sum(targets), 0)   AS lg_ypt,
                   sum(rush_yards) / nullif(sum(carries), 0)  AS lg_ypc
            FROM mart.player_game_usage WHERE season = ?
              AND position IN ('WR', 'TE', 'RB')
        ),
        -- 2026 team comes from the roster, not last season's team: 29% of
        -- 20-touch players change club, and projecting a player onto the wrong
        -- offense is worse than not projecting them.
        now AS (
            SELECT gsis_id, team AS team_2026, display_name, position
            FROM mart.player_projection_pre
        ),
        calc AS (
            SELECT n.gsis_id, n.display_name, n.team_2026, n.position,
                   p.pr_games, p.pr_targets, p.pr_yards, p.pr_share,
                   p.pr_carries, p.pr_rush_yards,
                   (p.pr_share * p.pr_games) / (p.pr_games + ?) AS share,
                   (coalesce(p.pr_yards, 0) + lg.lg_ypt * ?)
                       / (coalesce(p.pr_targets, 0) + ?)         AS ypt,
                   (coalesce(p.pr_carry_share, 0) * p.pr_games)
                       / (p.pr_games + ?)                        AS csh,
                   (coalesce(p.pr_rush_yards, 0) + lg.lg_ypc * ?)
                       / (coalesce(p.pr_carries, 0) + ?)         AS ypc,
                   tv.tgt_per_game, tv.rush_per_game
            FROM now n
            JOIN prior p USING (gsis_id)
            LEFT JOIN team_vol tv ON tv.team = n.team_2026
            CROSS JOIN lg
        )
        SELECT ? AS season, gsis_id, display_name, team_2026 AS team, position,
               pr_games, pr_targets, pr_yards, pr_carries, pr_rush_yards,
               round(share, 4) AS proj_target_share,
               round(ypt, 3)   AS proj_ypt,
               round(share * tgt_per_game * ? , 1)        AS proj_targets,
               round(share * tgt_per_game * ypt * ?, 0)   AS proj_rec_yards,
               round(share * tgt_per_game * ypt * ? * ?, 0) AS p10_rec_yards,
               round(share * tgt_per_game * ypt * ? * ?, 0) AS p90_rec_yards,
               round(csh * rush_per_game * ?, 1)          AS proj_carries,
               round(csh * rush_per_game * ypc * ?, 0)    AS proj_rush_yards,
               round(csh * rush_per_game * ypc * ? * ?, 0) AS p10_rush_yards,
               round(csh * rush_per_game * ypc * ? * ?, 0) AS p90_rush_yards,
               pr_games <= ? AS availability_flag
        FROM calc
        WHERE tgt_per_game IS NOT NULL
          AND (pr_targets >= 20 OR pr_carries >= 20)
    """, [PRIOR, PRIOR, PRIOR,
          SHRINK_TGT, SHRINK_EFF, SHRINK_EFF, SHRINK_CARRY, SHRINK_EFF,
          SHRINK_EFF, SEASON,
          GAMES_CONST, GAMES_CONST, GAMES_CONST, BAND["p10"],
          GAMES_CONST, BAND["p90"],
          RUSH_GAMES_CONST, RUSH_GAMES_CONST,
          RUSH_GAMES_CONST, RUSH_BAND["p10"],
          RUSH_GAMES_CONST, RUSH_BAND["p90"], LOW_GAMES])

    n = con.execute(
        "SELECT count(*) FROM mart.player_season_projection").fetchone()[0]
    flagged = con.execute("SELECT count(*) FROM mart.player_season_projection "
                          "WHERE availability_flag").fetchone()[0]
    log(f"  mart.player_season_projection: {n} players, {flagged} availability"
        f"-flagged")

    log("")
    log("  top projected receivers (band is measured, p10-p90):")
    log(f"    {'player':<24}{'tm':<5}{'pos':<5}{'tgts':>7}{'yards':>8}"
        f"{'p10':>7}{'p90':>7}")
    for r in con.execute("""
        SELECT display_name, team, position, proj_targets, proj_rec_yards,
               p10_rec_yards, p90_rec_yards, availability_flag
        FROM mart.player_season_projection
        ORDER BY proj_rec_yards DESC LIMIT 12
    """).fetchall():
        flag = "  *" if r[7] else ""
        log(f"    {str(r[0])[:23]:<24}{r[1]:<5}{r[2]:<5}{r[3]:>7.1f}"
            f"{r[4]:>8.0f}{r[5]:>7.0f}{r[6]:>7.0f}{flag}")
    log("")
    log("  * prior season of 11 games or fewer. Availability is FLAGGED, not")
    log("    modelled - the fitted line bought 0.9% and missed its 2% bar.")
    log("")
    log("  The p10-p90 band spans 0.34x to 1.77x because that is the measured")
    log("  out-of-sample spread. A season receiving total is not a precise")
    log("  quantity and the output should not imply that it is.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    try:
        return build(con)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
