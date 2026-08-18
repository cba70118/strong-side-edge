"""
build_player_proj.py - per-game player projections for an unplayed season.

THE PROBLEM THIS SOLVES. mart.player_usage_projection is point-in-time: it
projects a game from that player's PRIOR games. For Week 1 of a season nobody
has played, there are no prior games, so it produces nothing. Naively reaching
back to last season is wrong in a specific, measurable way: of the 304 skill
players with 20+ touches in 2025, only 71% are on the same team in 2026. 21%
moved and 8% are not on a roster at all. Attaching last year's usage to last
year's team would project players onto clubs they have left.

THE METHOD.
  1. Team assignment comes from the REAL 2026 Week 1 roster, not from 2025.
  2. Each player's rate comes from their last 6 games of 2025, shrunk toward a
     positional baseline exactly as the in-season model does.
  3. Shares are RENORMALIZED within the 2026 roster. This is the step that
     handles churn: every target goes to someone, so a team that lost its WR1
     must redistribute that share among whoever remains. Without it a team that
     gained a target hog would have shares summing past 100%.
  4. Volume is the 2026 team's 2025 team-target and rush-attempt rate.
  5. Efficiency is the shrunk positional baseline, because player efficiency is
     not predictable (yards per target 0.136, RACR 0.070).

WHAT IT CANNOT DO, stated plainly rather than papered over:
  * Rookies and anyone without 2025 NFL usage get NO projection. They are
    listed separately as unprojected. Inventing a rookie's target share from a
    depth chart is exactly the kind of fabrication this project avoids.
  * Coaching, scheme and personnel changes are not modeled.
  * No injury information exists yet.
  * This output is 100% prior. It carries no 2026 information whatsoever and
    should be read as "what last year implies", not "what will happen".

Usage:
    py -3 scripts/build_player_proj.py --season 2026 --week 1
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

PRIOR_GAMES = 6
SHRINK_GAMES = 2.0
EFF_SHRINK = 40.0


def log(m: str = "") -> None:
    print(m, flush=True)


SQL = """
CREATE OR REPLACE TABLE mart.player_projection_pre AS
WITH roster AS (
    -- REAL 2026 team assignment. Active players only.
    --
    -- TEAM CODE MUST BE NORMALIZED HERE. The nflverse ROSTER files abbreviate
    -- Arizona as 'AZ' while every other asset in the release uses 'ARI', so an
    -- unnormalized roster silently dropped all 24 Arizona skill players out of
    -- every downstream join - the team pages rendered an empty roster and the
    -- prop path had no projection for any Cardinal. It is the only unmapped
    -- code in the warehouse, and it was invisible until something asked a
    -- question per team.
    SELECT DISTINCT r.gsis_id,
           coalesce(a.team_id, r.team) AS team,
           r.position
    FROM raw.roster_weekly r
    LEFT JOIN ref.team_alias a ON a.alias = r.team AND a.source = 'legacy'
    WHERE r.season = {season} AND r.week = 1
      AND r.gsis_id IS NOT NULL
      AND r.position IN ('WR','TE','RB')
      AND (r.status IS NULL OR r.status IN ('ACT','A01','RES'))
),
ranked AS (
    -- each player's most recent games, whenever they were
    SELECT u.gsis_id, u.target_share, u.carry_share, u.offense_pct,
           u.route_proxy_pct, u.targets, u.receptions, u.rec_yards,
           u.carries, u.rush_yards,
           row_number() OVER (PARTITION BY u.gsis_id
                              ORDER BY u.season DESC, u.week DESC) AS rn
    FROM mart.player_game_usage u
    WHERE u.season <= {season} - 1
),
prior AS (
    SELECT gsis_id,
           count(*)                       AS n_games,
           avg(target_share)              AS p_tgt,
           avg(carry_share)               AS p_car,
           avg(offense_pct)               AS p_snap,
           sum(rec_yards)                 AS s_recyd,
           sum(targets)                   AS s_tgt,
           sum(receptions)                AS s_rec,
           sum(rush_yards)                AS s_rushyd,
           sum(carries)                   AS s_car
    FROM ranked WHERE rn <= {prior_games} GROUP BY gsis_id
),
vol AS (
    -- the 2026 team's own 2025 volume
    SELECT team,
           avg(team_targets)     AS proj_team_targets,
           avg(team_rush_plays)  AS proj_team_rush
    FROM (SELECT DISTINCT game_id, team, team_targets, team_rush_plays
          FROM mart.player_game_usage WHERE season = {season} - 1)
    GROUP BY team
),
shrunk AS (
    SELECT
        r.gsis_id, r.team, r.position, pl.display_name,
        coalesce(p.n_games, 0) AS n_games,
        CASE WHEN p.n_games IS NULL THEN NULL ELSE
          (p.p_tgt * p.n_games + b.base_target_share * {sg})
          / (p.n_games + {sg}) END                       AS raw_tgt_share,
        CASE WHEN p.n_games IS NULL THEN NULL ELSE
          (p.p_car * p.n_games + b.base_carry_share * {sg})
          / (p.n_games + {sg}) END                       AS raw_car_share,
        CASE WHEN p.n_games IS NULL THEN NULL ELSE
          (p.p_snap * p.n_games + b.base_snap_pct * {sg})
          / (p.n_games + {sg}) END                       AS proj_snap_pct,
        (coalesce(p.s_recyd,0) + b.base_ypt * {es})
          / (coalesce(p.s_tgt,0) + {es})                 AS proj_ypt,
        (coalesce(p.s_rec,0) + b.base_catch_rate * {es})
          / (coalesce(p.s_tgt,0) + {es})                 AS proj_catch_rate,
        (coalesce(p.s_rushyd,0) + b.base_ypc * {es})
          / (coalesce(p.s_car,0) + {es})                 AS proj_ypc
    FROM roster r
    LEFT JOIN prior p ON p.gsis_id = r.gsis_id
    JOIN mart.usage_baseline b ON b.position = r.position
    LEFT JOIN raw.players pl ON pl.gsis_id = r.gsis_id
),
norm AS (
    -- RENORMALIZE within the 2026 roster: every target goes to someone.
    SELECT s.*,
           sum(raw_tgt_share) OVER (PARTITION BY team) AS tm_tgt_sum,
           sum(raw_car_share) OVER (PARTITION BY team) AS tm_car_sum
    FROM shrunk s
)
SELECT
    n.gsis_id, n.team, n.position, n.display_name, n.n_games,
    n.proj_snap_pct,
    CASE WHEN n.tm_tgt_sum > 0 THEN n.raw_tgt_share / n.tm_tgt_sum END
                                                        AS proj_target_share,
    CASE WHEN n.tm_car_sum > 0 THEN n.raw_car_share / n.tm_car_sum END
                                                        AS proj_carry_share,
    v.proj_team_targets, v.proj_team_rush,
    CASE WHEN n.tm_tgt_sum > 0 THEN
      n.raw_tgt_share / n.tm_tgt_sum * v.proj_team_targets END  AS proj_targets,
    CASE WHEN n.tm_tgt_sum > 0 THEN
      n.raw_tgt_share / n.tm_tgt_sum * v.proj_team_targets
      * n.proj_catch_rate END                                   AS proj_receptions,
    CASE WHEN n.tm_tgt_sum > 0 THEN
      n.raw_tgt_share / n.tm_tgt_sum * v.proj_team_targets
      * n.proj_ypt END                                          AS proj_rec_yards,
    CASE WHEN n.tm_car_sum > 0 THEN
      n.raw_car_share / n.tm_car_sum * v.proj_team_rush END     AS proj_carries,
    CASE WHEN n.tm_car_sum > 0 THEN
      n.raw_car_share / n.tm_car_sum * v.proj_team_rush
      * n.proj_ypc END                                          AS proj_rush_yards,
    n.proj_ypt, n.proj_catch_rate, n.proj_ypc
FROM norm n
LEFT JOIN vol v ON v.team = n.team;
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    a = ap.parse_args()

    con = duckdb.connect(str(a.db))
    con.execute(SQL.format(season=a.season, prior_games=PRIOR_GAMES,
                           sg=SHRINK_GAMES, es=EFF_SHRINK))

    n, proj, unproj, tms = con.execute("""
        SELECT count(*), count(proj_targets),
               count(*) FILTER (WHERE n_games = 0), count(DISTINCT team)
        FROM mart.player_projection_pre
    """).fetchone()
    log(f"mart.player_projection_pre: {n:,} rostered skill players across {tms} teams")
    log(f"  projected            : {proj:,}")
    log(f"  no 2025 usage history: {unproj:,}  (listed, never invented)")

    log("\n  share normalization check (should sum to ~1.00 per team):")
    for t, ts, cs in con.execute("""
        SELECT team, round(sum(proj_target_share),3), round(sum(proj_carry_share),3)
        FROM mart.player_projection_pre GROUP BY team ORDER BY team LIMIT 5
    """).fetchall():
        log(f"    {t:<4} targets {ts}   carries {cs}")

    log("\n  top projected receivers:")
    log(f"    {'player':<24}{'tm':<4}{'pos':<4}{'tgt':>6}{'rec':>6}"
        f"{'yds':>7}{'share':>8}")
    for r in con.execute("""
        SELECT display_name, team, position, proj_targets, proj_receptions,
               proj_rec_yards, proj_target_share
        FROM mart.player_projection_pre
        WHERE proj_rec_yards IS NOT NULL
        ORDER BY proj_rec_yards DESC LIMIT 12
    """).fetchall():
        log(f"    {str(r[0])[:23]:<24}{r[1]:<4}{r[2]:<4}{r[3]:>6.1f}{r[4]:>6.1f}"
            f"{r[5]:>7.1f}{r[6]*100:>7.1f}%")

    log("\n  top projected rushers:")
    for r in con.execute("""
        SELECT display_name, team, position, proj_carries, proj_rush_yards
        FROM mart.player_projection_pre
        WHERE proj_rush_yards IS NOT NULL
        ORDER BY proj_rush_yards DESC LIMIT 8
    """).fetchall():
        log(f"    {str(r[0])[:23]:<24}{r[1]:<4}{r[2]:<4}{r[3]:>6.1f}"
            f"{r[4]:>7.1f} yds")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
