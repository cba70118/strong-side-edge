"""build_charting.py - the FTN and participation charting layer.

WHAT THIS UNLOCKS. Both files were already in the warehouse and neither had ever
been read. FTN carries what was CALLED on a play (play action, screen, RPO,
motion, no huddle, blitzers) and what HAPPENED to the ball (catchable,
contested, dropped). Participation carries what the defense showed (man or
zone). Neither is derivable from play-by-play, so this is the first genuinely
new lens since the drive simulator.

TWO KINDS OF NUMBER, AND THEY DO NOT BEHAVE ALIKE. A call rate describes a
coach and should stabilize like PROE. An outcome rate describes a player and,
on this project's record, efficiency never stabilizes. The two are built into
separate tables so nothing on a page can quietly average them together, and
metric_stability.py measures both rather than assuming.

COVERAGE, which is narrower than either file's row count suggests:
    FTN            2022-2025, 140,610 plays joined to pbp
    man/zone       about 22,000 charted plays a season, roughly half of all
                   plays, so a man-rate is a rate over CHARTED dropbacks only.
                   The league man rate reads 29.0 / 41.9 / 48.9 / 31.0 percent
                   across 2022-2025 on near-identical charted volumes. A real
                   league tendency does not swing twenty points and back, so
                   treat man_rate as comparable WITHIN a season and never
                   across them.

NOT AVAILABLE. Slot versus outside alignment is in neither file. Do not proxy
it from formation columns; that needs charted data or nothing.

Usage:
    py -3 scripts/build_charting.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

SQL = """
-- One row per team-game: what this offense called, and what it faced.
CREATE OR REPLACE TABLE mart.team_charting AS
WITH f AS (
    SELECT p.game_id, p.season, p.week, p.posteam, p.defteam,
           p.qb_dropback, p.pass, p.rush,
           c.is_play_action, c.is_screen_pass, c.is_rpo, c.is_no_huddle,
           c.is_motion, c.is_qb_out_of_pocket, c.is_throw_away,
           c.n_blitzers, c.n_pass_rushers, c.n_defense_box,
           c.qb_location
    FROM mart.pbp_clean p
    JOIN raw.ftn_charting c
      ON c.nflverse_game_id = p.game_id AND c.nflverse_play_id = p.play_id
    JOIN raw.games g ON g.game_id = p.game_id
    WHERE p.posteam IS NOT NULL AND g.game_type = 'REG'
),
off AS (
    SELECT game_id, season, week, posteam AS team,
           count(*)                                          AS plays,
           avg(CASE WHEN is_no_huddle THEN 1.0 ELSE 0 END)   AS no_huddle_rate,
           avg(CASE WHEN is_motion THEN 1.0 ELSE 0 END)      AS motion_rate,
           avg(CASE WHEN is_rpo THEN 1.0 ELSE 0 END)         AS rpo_rate,
           -- play action and screens are PASS calls, so the denominator is
           -- dropbacks. Rating them per play would make a run-heavy team look
           -- like it never uses play action.
           sum(CASE WHEN is_play_action AND qb_dropback = 1 THEN 1 ELSE 0 END)
             / nullif(sum(qb_dropback), 0)                   AS play_action_rate,
           sum(CASE WHEN is_screen_pass AND qb_dropback = 1 THEN 1 ELSE 0 END)
             / nullif(sum(qb_dropback), 0)                   AS screen_rate,
           sum(CASE WHEN qb_location = 'S' AND qb_dropback = 1 THEN 1 ELSE 0 END)
             / nullif(sum(qb_dropback), 0)                   AS shotgun_rate,
           sum(CASE WHEN is_qb_out_of_pocket AND qb_dropback = 1 THEN 1 ELSE 0 END)
             / nullif(sum(qb_dropback), 0)                   AS out_of_pocket_rate,
           avg(CASE WHEN qb_dropback = 1 THEN n_blitzers END) AS blitz_faced,
           avg(CASE WHEN qb_dropback = 1 THEN n_pass_rushers END)
                                                             AS rushers_faced,
           avg(n_defense_box)                                AS box_faced
    FROM f GROUP BY 1, 2, 3, 4
),
dfn AS (
    SELECT game_id, defteam AS team,
           avg(CASE WHEN qb_dropback = 1 THEN n_blitzers END) AS blitz_sent,
           avg(CASE WHEN qb_dropback = 1 THEN n_pass_rushers END)
                                                              AS rushers_sent,
           sum(CASE WHEN is_play_action AND qb_dropback = 1 THEN 1 ELSE 0 END)
             / nullif(sum(qb_dropback), 0)                    AS pa_faced_rate
    FROM f GROUP BY 1, 2
),
-- Man versus zone comes from participation, not FTN, and only about half of
-- plays are charted, so this is a rate over charted dropbacks.
cov AS (
    SELECT p.game_id, p.defteam AS team,
           count(*)                                              AS charted,
           avg(CASE WHEN pt.defense_man_zone_type = 'MAN_COVERAGE'
                    THEN 1.0 ELSE 0 END)                         AS man_rate
    FROM mart.pbp_clean p
    JOIN raw.pbp_participation pt
      ON pt.nflverse_game_id = p.game_id AND pt.play_id = p.play_id
    WHERE p.qb_dropback = 1 AND p.defteam IS NOT NULL
      AND pt.defense_man_zone_type IN ('MAN_COVERAGE', 'ZONE_COVERAGE')
    GROUP BY 1, 2
)
SELECT o.*, d.blitz_sent, d.rushers_sent, d.pa_faced_rate,
       c.charted AS coverage_charted, c.man_rate
FROM off o
LEFT JOIN dfn d ON d.game_id = o.game_id AND d.team = o.team
LEFT JOIN cov c ON c.game_id = o.game_id AND c.team = o.team;

-- One row per receiver-game. FTN has no player column, so the ball-outcome
-- flags are attributed through pbp's receiver on the same play.
CREATE OR REPLACE TABLE mart.player_charting AS
WITH t AS (
    SELECT p.game_id, p.season, p.week, p.posteam AS team,
           p.receiver_player_id AS gsis_id,
           c.is_catchable_ball, c.is_contested_ball, c.is_drop,
           c.is_created_reception, c.is_play_action, c.is_screen_pass,
           pt.defense_man_zone_type AS cov,
           p.complete_pass, p.yards_gained
    FROM mart.pbp_clean p
    JOIN raw.ftn_charting c
      ON c.nflverse_game_id = p.game_id AND c.nflverse_play_id = p.play_id
    LEFT JOIN raw.pbp_participation pt
      ON pt.nflverse_game_id = p.game_id AND pt.play_id = p.play_id
    JOIN raw.games g ON g.game_id = p.game_id
    WHERE p.receiver_player_id IS NOT NULL AND p.pass = 1
      -- Regular season only, the same rule the season projections use. A
      -- 208-target 2025 for Nacua is a playoff run, not a season.
      AND g.game_type = 'REG'
)
SELECT game_id, season, week, team, gsis_id,
       count(*)                                           AS targets,
       avg(CASE WHEN is_catchable_ball THEN 1.0 ELSE 0 END) AS catchable_rate,
       avg(CASE WHEN is_contested_ball THEN 1.0 ELSE 0 END) AS contested_rate,
       -- A drop is only meaningful against a ball he could have caught, but
       -- FTN flags 63 plays league-wide as a drop while marking the same ball
       -- uncatchable, which is a contradiction in the source. Counting a drop
       -- into its own denominator keeps the rate inside [0,1] without
       -- discarding the charting call.
       sum(CASE WHEN is_drop THEN 1 ELSE 0 END)
         / nullif(sum(CASE WHEN is_catchable_ball OR is_drop
                           THEN 1 ELSE 0 END), 0)            AS drop_rate,
       avg(CASE WHEN is_created_reception THEN 1.0 ELSE 0 END)
                                                            AS created_rate,
       sum(CASE WHEN cov = 'MAN_COVERAGE' THEN 1 ELSE 0 END) AS tgt_vs_man,
       sum(CASE WHEN cov = 'ZONE_COVERAGE' THEN 1 ELSE 0 END) AS tgt_vs_zone,
       sum(CASE WHEN cov = 'MAN_COVERAGE' THEN yards_gained ELSE 0 END)
                                                            AS yds_vs_man,
       sum(CASE WHEN cov = 'ZONE_COVERAGE' THEN yards_gained ELSE 0 END)
                                                            AS yds_vs_zone,
       avg(CASE WHEN is_play_action THEN 1.0 ELSE 0 END)     AS pa_tgt_rate,
       avg(CASE WHEN is_screen_pass THEN 1.0 ELSE 0 END)     AS screen_tgt_rate
FROM t GROUP BY 1, 2, 3, 4, 5;
"""


def log(m=""):
    print(m, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    try:
        con.execute(SQL)
        n1 = con.execute("SELECT count(*) FROM mart.team_charting").fetchone()[0]
        n2 = con.execute("SELECT count(*) FROM mart.player_charting").fetchone()[0]
        log(f"mart.team_charting    {n1:,} team-games")
        log(f"mart.player_charting  {n2:,} receiver-games")

        log("")
        log("  league call rates by season (offense)")
        log(f"    {'szn':<6}{'PA':>7}{'screen':>8}{'RPO':>7}{'motion':>8}"
            f"{'shotgun':>9}{'nohud':>7}{'blitz':>7}{'man%':>7}")
        for r in con.execute("""
            SELECT season, avg(play_action_rate), avg(screen_rate),
                   avg(rpo_rate), avg(motion_rate), avg(shotgun_rate),
                   avg(no_huddle_rate), avg(blitz_sent), avg(man_rate)
            FROM mart.team_charting GROUP BY 1 ORDER BY 1
        """).fetchall():
            log(f"    {r[0]:<6}{r[1] * 100:>6.1f}%{r[2] * 100:>7.1f}%"
                f"{r[3] * 100:>6.1f}%{r[4] * 100:>7.1f}%{r[5] * 100:>8.1f}%"
                f"{r[6] * 100:>6.1f}%{r[7]:>7.2f}{r[8] * 100:>6.1f}%")

        # Every rate must sit inside [0, 1]. A rate above 1 means the
        # denominator is wrong, which is the failure mode these joins invite.
        bad = con.execute("""
            SELECT count(*) FROM mart.team_charting
            WHERE play_action_rate > 1 OR screen_rate > 1 OR shotgun_rate > 1
               OR motion_rate > 1 OR man_rate > 1
        """).fetchone()[0]
        bad2 = con.execute("""
            SELECT count(*) FROM mart.player_charting
            WHERE catchable_rate > 1 OR contested_rate > 1 OR drop_rate > 1
        """).fetchone()[0]
        log("")
        log(f"  rates outside [0,1]: team {bad}, player {bad2}")
        assert bad == 0 and bad2 == 0, "a charting rate exceeds 1; check joins"
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
