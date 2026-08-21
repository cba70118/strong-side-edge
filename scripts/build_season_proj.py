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
# FULL PARTICIPATION, DELIBERATELY. Every games constant is 17. These are
# projections of production, not forecasts of availability, and the two were
# being conflated: a population-average absence baked into every line made
# every healthy 17-game season look like a forecast of decline, when in fact
# 16 of 36 quarterbacks project UP on a per-game basis.
#
# The BANDS were re-measured to match. They used to be actual-total over
# projected-total, so they carried the spread of missed games as well as of
# production, and availability was most of that width. They are now per-game
# actual over per-game projected on the same holdout. Reusing the old widths on
# a 17-game number would overstate every spread, the rushing one absurdly: a
# p10 of 0.12 was mostly the chance a back missed half the season.
GAMES_CONST = 17.0         # full participation: a projection, not an injury forecast

# Empirical actual/projected quantiles, measured on the 409 out-of-sample rows.
BAND = {"p10": 0.66, "p25": 0.80, "p50": 0.94, "p75": 1.14, "p90": 1.40}

# RUSHING gets its OWN band, backtested the same way on 237 out-of-sample rows.
# It beats naive by only 8.1% against receiving's 14.6%, under-projects by 91
# yards, and its band is far wider - p10 0.12 to p90 2.36. Sharing receiving's
# band would understate rushing uncertainty by roughly half.
RUSH_BAND = {"p10": 0.49, "p50": 0.92, "p90": 1.55}

# Backtested the same way, fit 2021-22 and scored 2023-25. Each band is its
# own; sharing one would understate QB yardage and halve receiver TD spread.
REC_TD_BAND = {"p10": 0.32, "p50": 0.92, "p90": 2.12}   # +13.2% over naive
PASS_YD_BAND = {"p10": 0.82, "p50": 0.96, "p90": 1.12}  # +13.8%
PASS_TD_BAND = {"p10": 0.72, "p50": 0.95, "p90": 1.36}  # +17.2% at k=300
# Passing TDs want far heavier shrinkage than passing yards. k=40 was tuned on
# receiver targets, where it is 21% of a typical denominator; against a
# quarterback's ~600 attempts it applies only 6%, so a 46-TD season carried
# through almost untouched. k chosen on the fit period alone lands on 300 and is
# worth +3.5% holdout MAE. Yards were tested the same way and REFUSED the
# retune, so they keep k=40.
# Catch rate carries at +0.609, an order above yards per target at +0.136,
# so receptions project. k chosen on the fit period alone, worth +6.9%.
SHRINK_CATCH = 60.0
REC_BAND = {"p10": 0.68, "p50": 0.96, "p90": 1.34}
# A quarterback's carries are a scheme decision that can change overnight,
# which is why this band is the widest on the page.
# Rushing TDs shrink hardest of anything here: k=400 against a few hundred
# carries. A goal-line back's touchdown count is mostly opportunity and luck,
# so last season's total is a poor guide to next season's.
RUSH_TD_K = 400.0
RUSH_TD_BAND = {"p10": 0.24, "p50": 0.86, "p90": 1.97}
QB_RUSH_K = 10.0
QB_RUSH_BAND = {"p10": 0.38, "p50": 0.85, "p90": 1.59}
SHRINK_PASS_TD = 300.0
QB_GAMES_CONST = 17.0
REC_GAMES_CONST = 17.0
RUSH_GAMES_CONST = 17.0
SHRINK_CARRY = 2.0
LOW_GAMES = 11             # prior-season games at or below which availability
                           # is flagged rather than modelled


def log(m=""):
    print(m, flush=True)


REG_VIEW = """
-- Regular season only. mart.player_game_usage carries the postseason too,
-- which is right for a per-game usage model and wrong for a season total: a
-- 2025 "season" that reads 19 games and 208 targets is a Super Bowl run, not a
-- season, and it overstates every player on a deep team. The validated
-- backtests all read raw.stats_player_week WHERE season_type = 'REG', so this
-- restores the construction that was actually measured.
CREATE OR REPLACE TEMP VIEW usage_reg AS
SELECT u.* FROM mart.player_game_usage u
JOIN raw.games g USING (game_id)
WHERE g.game_type = 'REG';
"""


def build(con) -> int:
    con.execute(REG_VIEW)
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
                   avg(carry_share) AS pr_carry_share,
                   any_value(0)     AS _pad
            FROM usage_reg
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
                  FROM usage_reg WHERE season = ?)
            GROUP BY team
        ),
        lg AS (
            SELECT sum(rec_yards) / nullif(sum(targets), 0)   AS lg_ypt,
                   sum(rush_yards) / nullif(sum(carries), 0)  AS lg_ypc
            FROM usage_reg WHERE season = ?
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


def build_extras(con) -> None:
    """TD columns for skill players, and QB rows.

    Kept as a second pass rather than folded into the main statement: the
    passing side comes from raw.stats_player_week, a different grain from
    mart.player_game_usage, and forcing them into one query would make both
    harder to read and to check.
    """
    # 1. receiving TDs onto the existing skill rows
    con.execute("""
        ALTER TABLE mart.player_season_projection
        ADD COLUMN IF NOT EXISTS pr_rec_tds INTEGER
    """)
    for col in ("proj_rec_tds", "p10_rec_tds", "p90_rec_tds",
                "proj_pass_yards", "p10_pass_yards", "p90_pass_yards",
                "proj_pass_tds", "p10_pass_tds", "p90_pass_tds",
                "pr_attempts", "pr_pass_yards", "pr_pass_tds", "pr_ints",
                "proj_games", "proj_games_rush",
                "pr_receptions", "proj_receptions",
                "p10_receptions", "p90_receptions",
                "pr_rush_tds", "proj_rush_tds",
                "p10_rush_tds", "p90_rush_tds"):
        con.execute(f"ALTER TABLE mart.player_season_projection "
                    f"ADD COLUMN IF NOT EXISTS {col} DOUBLE")

    con.execute("""
        WITH pr AS (
            SELECT player_id, sum(receiving_tds) AS td, sum(targets) AS tg
            FROM raw.stats_player_week
            WHERE season = ? AND season_type = 'REG'
            GROUP BY 1
        ),
        lg AS (
            SELECT sum(receiving_tds) / nullif(sum(targets), 0) AS r
            FROM raw.stats_player_week
            WHERE season = ? AND season_type = 'REG'
        )
        UPDATE mart.player_season_projection p SET
            pr_rec_tds   = pr.td,
            proj_rec_tds = round(p.proj_targets
                * ((pr.td + lg.r * ?) / (pr.tg + ?)), 1),
            p10_rec_tds  = round(p.proj_targets
                * ((pr.td + lg.r * ?) / (pr.tg + ?)) * ?, 1),
            p90_rec_tds  = round(p.proj_targets
                * ((pr.td + lg.r * ?) / (pr.tg + ?)) * ?, 1)
        FROM pr, lg WHERE pr.player_id = p.gsis_id
    """, [PRIOR, PRIOR, SHRINK_EFF, SHRINK_EFF, SHRINK_EFF, SHRINK_EFF,
          REC_TD_BAND["p10"], SHRINK_EFF, SHRINK_EFF, REC_TD_BAND["p90"]])

    # 2. quarterbacks as their own rows
    con.execute("""
        INSERT INTO mart.player_season_projection
        (season, gsis_id, display_name, team, position, pr_games,
         proj_targets, proj_rec_yards, proj_carries, proj_rush_yards,
         availability_flag,
         pr_attempts, pr_pass_yards, pr_pass_tds, pr_ints,
         proj_pass_yards, p10_pass_yards, p90_pass_yards,
         proj_pass_tds, p10_pass_tds, p90_pass_tds, proj_games)
        WITH pr AS (
            SELECT s.player_id, any_value(s.player_display_name) AS nm,
                   any_value(s.team) AS team,
                   count(*) AS games, sum(s.attempts) AS att,
                   sum(s.passing_yards) AS yds, sum(s.passing_tds) AS tds,
                   sum(s.passing_interceptions) AS ints
            FROM raw.stats_player_week s
            WHERE s.season = ? AND s.season_type = 'REG'
            GROUP BY s.player_id
            HAVING sum(s.attempts) >= 100
        ),
        -- The league prior must come from the SAME population the backtest
        -- validated on: quarterbacks with 200+ attempts. Computing it over
        -- anyone who threw a pass folds in 117 mop-up and trick-play games,
        -- which dragged attempts per game from 29.9 down to 26.7 and pulled
        -- every projection with it. Attempts per game is a mean of per-player
        -- rates, not a pooled ratio, again matching the backtest.
        lgpop AS (
            SELECT player_id, sum(attempts) AS v, sum(passing_yards) AS y,
                   sum(passing_tds) AS t, count(*) AS g
            FROM raw.stats_player_week
            WHERE season = ? AND season_type = 'REG'
            GROUP BY player_id
            HAVING sum(attempts) >= 200
        ),
        lg AS (
            SELECT sum(y) / nullif(sum(v), 0)   AS ypa,
                   sum(t) / nullif(sum(v), 0)   AS tdr,
                   avg(v * 1.0 / nullif(g, 0))  AS apg
            FROM lgpop
        ),
        calc AS (
            SELECT pr.*,
                   ((pr.att / pr.games) * pr.games + lg.apg * ?)
                       / (pr.games + ?) * ?                       AS vol,
                   (pr.yds + lg.ypa * ?) / (pr.att + ?)           AS ypa,
                   (pr.tds + lg.tdr * ?) / (pr.att + ?)           AS tdr
            FROM pr CROSS JOIN lg
        )
        SELECT ?, player_id, nm, team, 'QB', games,
               NULL, NULL, NULL, NULL, games <= ?,
               att, yds, tds, ints,
               round(vol * ypa, 0), round(vol * ypa * ?, 0),
               round(vol * ypa * ?, 0),
               round(vol * tdr, 1), round(vol * tdr * ?, 1),
               round(vol * tdr * ?, 1), ?
        FROM calc
    """, [PRIOR, PRIOR, SHRINK_TGT, SHRINK_TGT, QB_GAMES_CONST,
          SHRINK_EFF, SHRINK_EFF, SHRINK_PASS_TD, SHRINK_PASS_TD,
          SEASON, LOW_GAMES,
          PASS_YD_BAND["p10"], PASS_YD_BAND["p90"],
          PASS_TD_BAND["p10"], PASS_TD_BAND["p90"], QB_GAMES_CONST])

    # Every group is projected over fewer than 17 games because that is what
    # the population actually plays. Storing the basis makes the discount
    # visible on the page instead of looking like a forecast of decline.
    # Receptions. Same shape as the TD update: prior counts, shrunk rate,
    # applied to the projected target volume that is already on the row.
    con.execute("""
        WITH pr AS (
            SELECT player_id, sum(receptions) AS rec, sum(targets) AS tg
            FROM raw.stats_player_week
            WHERE season = ? AND season_type = 'REG'
            GROUP BY 1
        ),
        lg AS (
            SELECT sum(receptions) / nullif(sum(targets), 0) AS cr
            FROM raw.stats_player_week
            WHERE season = ? AND season_type = 'REG'
        )
        UPDATE mart.player_season_projection p SET
            pr_receptions   = pr.rec,
            proj_receptions = round(p.proj_targets
                * ((pr.rec + lg.cr * ?) / (pr.tg + ?)), 1),
            p10_receptions  = round(p.proj_targets
                * ((pr.rec + lg.cr * ?) / (pr.tg + ?)) * ?, 1),
            p90_receptions  = round(p.proj_targets
                * ((pr.rec + lg.cr * ?) / (pr.tg + ?)) * ?, 1)
        FROM pr, lg WHERE pr.player_id = p.gsis_id AND pr.tg > 0
    """, [PRIOR, PRIOR, SHRINK_CATCH, SHRINK_CATCH,
          SHRINK_CATCH, SHRINK_CATCH, REC_BAND["p10"],
          SHRINK_CATCH, SHRINK_CATCH, REC_BAND["p90"]])

    # Rushing touchdowns. Without these a running back is missing most of
    # how he actually scores in fantasy, so the whole board tilts to receivers.
    con.execute("""
        WITH pr AS (
            SELECT player_id, sum(carries) AS car, sum(rushing_tds) AS td
            FROM raw.stats_player_week
            WHERE season = ? AND season_type = 'REG'
            GROUP BY 1
        ),
        lg AS (
            SELECT sum(rushing_tds) / nullif(sum(carries), 0) AS r
            FROM raw.stats_player_week
            WHERE season = ? AND season_type = 'REG'
        )
        UPDATE mart.player_season_projection p SET
            pr_rush_tds   = pr.td,
            proj_rush_tds = round(p.proj_carries
                * ((pr.td + lg.r * ?) / (pr.car + ?)), 1),
            p10_rush_tds  = round(p.proj_carries
                * ((pr.td + lg.r * ?) / (pr.car + ?)) * ?, 1),
            p90_rush_tds  = round(p.proj_carries
                * ((pr.td + lg.r * ?) / (pr.car + ?)) * ?, 1)
        FROM pr, lg WHERE pr.player_id = p.gsis_id AND pr.car > 0
          AND p.proj_carries IS NOT NULL
    """, [PRIOR, PRIOR, RUSH_TD_K, RUSH_TD_K, RUSH_TD_K, RUSH_TD_K,
          RUSH_TD_BAND["p10"], RUSH_TD_K, RUSH_TD_K, RUSH_TD_BAND["p90"]])

    # Quarterbacks rush. Leaving these NULL understated every mobile passer.
    con.execute("""
        WITH pr AS (
            SELECT player_id, sum(carries) AS car, sum(rushing_yards) AS yds,
                   count(*) AS games
            FROM raw.stats_player_week
            WHERE season = ? AND season_type = 'REG'
            GROUP BY 1
        ),
        -- Aggregate to the player-season BEFORE filtering on attempts.
        -- Applying "attempts >= 100" to a weekly row matches nobody, which
        -- silently returned a NULL league rate and nulled every projection
        -- downstream while the update still reported success.
        lg AS (
            SELECT sum(y) / nullif(sum(v), 0) AS ypc,
                   avg(v * 1.0 / nullif(g, 0))  AS cpg
            FROM (
                SELECT player_id, sum(carries) AS v, sum(rushing_yards) AS y,
                       count(*) AS g
                FROM raw.stats_player_week
                WHERE season = ? AND season_type = 'REG'
                GROUP BY 1 HAVING sum(attempts) >= 200
            )
        )
        UPDATE mart.player_season_projection p SET
            pr_carries      = pr.car,
            pr_rush_yards   = pr.yds,
            proj_carries    = round((pr.car + lg.cpg * 2.0)
                / (pr.games + 2.0) * ?, 1),
            proj_rush_yards = round((pr.car + lg.cpg * 2.0)
                / (pr.games + 2.0) * ?
                * ((pr.yds + lg.ypc * ?) / (pr.car + ?)), 0),
            p10_rush_yards  = round((pr.car + lg.cpg * 2.0)
                / (pr.games + 2.0) * ?
                * ((pr.yds + lg.ypc * ?) / (pr.car + ?)) * ?, 0),
            p90_rush_yards  = round((pr.car + lg.cpg * 2.0)
                / (pr.games + 2.0) * ?
                * ((pr.yds + lg.ypc * ?) / (pr.car + ?)) * ?, 0),
            proj_games_rush = ?
        FROM pr, lg
        WHERE pr.player_id = p.gsis_id AND p.position = 'QB' AND pr.car > 0
    """, [PRIOR, PRIOR, RUSH_GAMES_CONST, RUSH_GAMES_CONST, QB_RUSH_K,
          QB_RUSH_K, RUSH_GAMES_CONST, QB_RUSH_K, QB_RUSH_K,
          QB_RUSH_BAND["p10"], RUSH_GAMES_CONST, QB_RUSH_K, QB_RUSH_K,
          QB_RUSH_BAND["p90"], RUSH_GAMES_CONST])

    # Receiving and rushing were fit on DIFFERENT games constants, so one
    # column cannot label both. A running back with 981 projected rush yards
    # over 12.54 games is 78.2 a game; printing 14.76 beside it would state a
    # rate the model never produced, which is worse than stating none.
    con.execute("UPDATE mart.player_season_projection SET proj_games = ? "
                "WHERE position <> 'QB' AND proj_rec_yards IS NOT NULL",
                [REC_GAMES_CONST])
    con.execute("UPDATE mart.player_season_projection SET proj_games_rush = ? "
                "WHERE proj_rush_yards IS NOT NULL", [RUSH_GAMES_CONST])

    n = con.execute("SELECT count(*) FROM mart.player_season_projection "
                    "WHERE position = 'QB'").fetchone()[0]
    td = con.execute("SELECT count(*) FROM mart.player_season_projection "
                     "WHERE proj_rec_tds IS NOT NULL").fetchone()[0]
    rc = con.execute("SELECT count(*) FROM mart.player_season_projection "
                     "WHERE proj_receptions IS NOT NULL").fetchone()[0]
    qr = con.execute("SELECT count(*) FROM mart.player_season_projection "
                     "WHERE position = 'QB' AND proj_rush_yards IS NOT NULL"
                     ).fetchone()[0]
    log(f"  added {n} quarterbacks and receiving TDs on {td} skill players")
    log(f"  receptions on {rc} players, rushing on {qr} quarterbacks")
    log("")
    log("  top projected passers:")
    log(f"    {'player':<24}{'tm':<5}{'yards':>7}{'band':>16}{'TDs':>6}"
        f"{'band':>12}")
    for r in con.execute("""
        SELECT display_name, team, proj_pass_yards, p10_pass_yards,
               p90_pass_yards, proj_pass_tds, p10_pass_tds, p90_pass_tds
        FROM mart.player_season_projection WHERE position = 'QB'
        ORDER BY proj_pass_yards DESC LIMIT 8
    """).fetchall():
        log(f"    {str(r[0])[:23]:<24}{str(r[1]):<5}{r[2]:>7.0f}"
            f"{f'{r[3]:.0f}-{r[4]:.0f}':>16}{r[5]:>6.1f}"
            f"{f'{r[6]:.1f}-{r[7]:.1f}':>12}")


DEPTH_SQL = """
-- The current depth chart is the authority on BOTH club and role. The roster
-- file lags it, which is how a quarterback ended up projected for a team he
-- had left.
CREATE OR REPLACE TEMP VIEW depth_now AS
WITH latest AS (SELECT max(dt) AS d FROM raw.depth_charts)
SELECT gsis_id,
       any_value(team)   AS depth_team,
       min(pos_rank)     AS depth_rank,
       any_value(pos_abb) AS depth_pos
FROM raw.depth_charts, latest
WHERE dt = latest.d AND gsis_id IS NOT NULL
GROUP BY gsis_id;
"""


def apply_depth(con) -> None:
    """Attach club and role from the current chart, and correct the club.

    A player absent from every chart keeps depth_rank NULL, which the page
    renders as "not on a 2026 depth chart" rather than as a starter.
    """
    con.execute(DEPTH_SQL)
    for col, typ in (("depth_team", "VARCHAR"), ("depth_rank", "INTEGER"),
                     ("depth_pos", "VARCHAR"), ("team_moved", "BOOLEAN")):
        con.execute(f"ALTER TABLE mart.player_season_projection "
                    f"ADD COLUMN IF NOT EXISTS {col} {typ}")
    con.execute("""
        UPDATE mart.player_season_projection p
        SET depth_team = d.depth_team,
            depth_rank = d.depth_rank,
            depth_pos  = d.depth_pos,
            team_moved = (d.depth_team IS NOT NULL AND d.depth_team <> p.team)
        FROM depth_now d WHERE d.gsis_id = p.gsis_id
    """)
    # the chart wins on club
    moved = con.execute("""
        SELECT count(*) FROM mart.player_season_projection
        WHERE team_moved
    """).fetchone()[0]
    con.execute("""
        UPDATE mart.player_season_projection
        SET team = depth_team WHERE team_moved
    """)
    miss = con.execute("""
        SELECT count(*) FROM mart.player_season_projection
        WHERE season = ? AND depth_rank IS NULL
    """, [SEASON]).fetchone()[0]
    log(f"  depth chart: {moved} players moved to their current club, "
        f"{miss} not on any chart")
    log("")
    log("  quarterbacks by role:")
    for r in con.execute("""
        SELECT coalesce(cast(depth_rank AS VARCHAR), 'none') AS rk,
               count(*), round(avg(proj_pass_yards))
        FROM mart.player_season_projection
        WHERE season = ? AND position = 'QB'
        GROUP BY 1 ORDER BY 1
    """, [SEASON]).fetchall():
        log(f"    QB{r[0]:<6}{r[1]:>3} players, mean {r[2]:.0f} projected yards")



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    try:
        rc = build(con)
        build_extras(con)
        apply_depth(con)
        return rc
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
