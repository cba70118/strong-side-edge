-- ============================================================================
-- NFL Betting Analytics — Mart Layer (deterministic tables)
-- Build order step 4a. Point-in-time ratings are built separately by
-- scripts/build_mart.py because they require iteration.
--
-- DELIBERATE CHOICE: neutral-script filtering uses `wp`, NOT `vegas_wp`.
-- vegas_wp is derived from the closing spread, so using it would leak market
-- information into the projection lens that is supposed to be an INDEPENDENT
-- challenger to the market. Keeping them separate is the entire point.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS mart;

-- ---------------------------------------------------------------------------
-- mart.pbp_clean : the modeling substrate
-- Real scrimmage plays only. Kneels/spikes/ST/penalty-nullified are excluded.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart.pbp_clean AS
WITH base AS (
    SELECT
        game_id, play_id, season, week, season_type,
        posteam, defteam, posteam_type,
        play_type, down, ydstogo, yardline_100, goal_to_go,
        yards_gained, epa, success, wp, pass, rush, qb_dropback,
        pass_oe, xpass, air_yards, complete_pass, sack, interception,
        fumble_lost, touchdown, first_down, drive,
        score_differential, game_seconds_remaining, half_seconds_remaining,
        qtr, passer_player_id, rusher_player_id, receiver_player_id,
        -- seconds burned since the previous snap on the same drive; the
        -- 1..60 band drops clock stoppages, reviews and quarter breaks
        lag(game_seconds_remaining) OVER (
            PARTITION BY game_id, drive ORDER BY play_id
        ) - game_seconds_remaining AS sec_elapsed
    FROM raw.pbp
    WHERE play_type IN ('pass', 'run')
      AND epa IS NOT NULL
      AND posteam IS NOT NULL
      AND defteam IS NOT NULL
)
SELECT
    *,
    -- situation-neutral: game still competitive, pre-garbage-time
    (wp BETWEEN 0.20 AND 0.80)                    AS is_neutral,
    (down IN (1, 2))                              AS is_early_down,
    (yardline_100 <= 20)                          AS is_redzone,
    CASE WHEN pass = 1 AND yards_gained >= 15 THEN TRUE
         WHEN rush = 1 AND yards_gained >= 10 THEN TRUE
         ELSE FALSE END                           AS is_explosive,
    CASE WHEN sec_elapsed BETWEEN 1 AND 60 THEN sec_elapsed END AS pace_sec
FROM base;


-- ---------------------------------------------------------------------------
-- mart.team_game : one row per TEAM per game (2 rows per game)
-- Offensive metrics are that team's own; defensive metrics are what the
-- opponent's offense produced against them.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart.team_game AS
WITH off AS (
    SELECT
        game_id, season, week, posteam AS team, defteam AS opponent,
        count(*)                                        AS off_plays,
        avg(epa)                                        AS off_epa_play,
        avg(CASE WHEN pass = 1 THEN epa END)            AS off_pass_epa,
        avg(CASE WHEN rush = 1 THEN epa END)            AS off_rush_epa,
        avg(success::DOUBLE)                            AS off_success,
        avg(pass::DOUBLE)                               AS off_pass_rate,
        avg(pass_oe)                                    AS off_proe,
        avg(is_explosive::DOUBLE)                       AS off_explosive_rate,
        -- neutral-script variants: the versions that actually predict
        count(*) FILTER (WHERE is_neutral)              AS off_plays_neutral,
        avg(CASE WHEN is_neutral THEN epa END)          AS off_epa_play_neutral,
        avg(CASE WHEN is_neutral THEN pass::DOUBLE END) AS off_pass_rate_neutral,
        avg(CASE WHEN is_neutral THEN pass_oe END)      AS off_proe_neutral,
        avg(CASE WHEN is_neutral AND is_early_down
                 THEN pass::DOUBLE END)                 AS off_ed_pass_rate_neutral,
        avg(CASE WHEN is_neutral THEN pace_sec END)     AS off_sec_per_play_neutral,
        sum(CASE WHEN sack = 1 THEN 1 ELSE 0 END)       AS off_sacks_taken,
        sum(CASE WHEN interception = 1 THEN 1 ELSE 0 END)
          + sum(CASE WHEN fumble_lost = 1 THEN 1 ELSE 0 END) AS off_turnovers
    FROM mart.pbp_clean
    GROUP BY game_id, season, week, posteam, defteam
)
SELECT
    o.game_id, o.season, o.week, o.team, o.opponent,
    (g.home_team = o.team)                              AS is_home,
    g.game_type,
    -- offense
    o.off_plays, o.off_epa_play, o.off_pass_epa, o.off_rush_epa,
    o.off_success, o.off_pass_rate, o.off_proe, o.off_explosive_rate,
    o.off_plays_neutral, o.off_epa_play_neutral, o.off_pass_rate_neutral,
    o.off_proe_neutral, o.off_ed_pass_rate_neutral,
    o.off_sec_per_play_neutral, o.off_sacks_taken, o.off_turnovers,
    -- defense = what the opponent's offense did
    d.off_plays            AS def_plays,
    d.off_epa_play         AS def_epa_play,
    d.off_pass_epa         AS def_pass_epa,
    d.off_rush_epa         AS def_rush_epa,
    d.off_success          AS def_success,
    d.off_explosive_rate   AS def_explosive_rate,
    d.off_plays_neutral    AS def_plays_neutral,
    d.off_epa_play_neutral AS def_epa_play_neutral,
    d.off_sacks_taken      AS def_sacks_made,
    d.off_turnovers        AS def_turnovers_forced,
    -- outcome + context (spread_line is HOME perspective, positive = home fav)
    CASE WHEN g.home_team = o.team THEN g.home_score ELSE g.away_score END AS points_for,
    CASE WHEN g.home_team = o.team THEN g.away_score ELSE g.home_score END AS points_against,
    CASE WHEN g.home_team = o.team THEN g.result ELSE -g.result END        AS margin,
    CASE WHEN g.home_team = o.team THEN g.spread_line ELSE -g.spread_line END AS spread_line_team,
    g.total_line, g.result AS home_margin, g.spread_line AS spread_line_home,
    CASE WHEN g.home_team = o.team THEN g.home_rest ELSE g.away_rest END   AS rest,
    g.div_game, g.roof, g.surface, g.temp, g.wind, g.gameday
FROM off o
JOIN off d
  ON d.game_id = o.game_id AND d.team = o.opponent
JOIN raw.games g
  ON g.game_id = o.game_id;


-- ---------------------------------------------------------------------------
-- mart.player_game_usage : the prop engine's input layer
--
-- Snap counts key on pfr_player_id and must be bridged through
-- raw.players.pfr_id to reach the canonical gsis_id (99.5% resolution).
-- Route participation is derived from participation.offense_players, a
-- semicolon-delimited gsis_id list. NOTE: pass_snaps is a ROUTE PROXY, not
-- charted routes run - a player on the field for a dropback is presumed to
-- have run a route. Blocking backs/TEs inflate it. Labeled accordingly.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart.player_game_usage AS
WITH team_tot AS (
    SELECT game_id, posteam AS team,
           count(*) FILTER (WHERE pass = 1)                 AS team_pass_plays,
           count(*) FILTER (WHERE rush = 1)                 AS team_rush_plays,
           -- team TARGETS, not pass plays: a target requires an intended
           -- receiver, so sacks and throwaways are excluded. This is the
           -- denominator the standard target-share definition uses.
           count(*) FILTER (WHERE pass = 1
                            AND receiver_player_id IS NOT NULL) AS team_targets,
           sum(CASE WHEN pass = 1 THEN coalesce(air_yards, 0) ELSE 0 END) AS team_air_yards,
           count(*) FILTER (WHERE pass = 1 AND is_redzone)  AS team_rz_pass,
           count(*) FILTER (WHERE rush = 1 AND is_redzone)  AS team_rz_rush
    FROM mart.pbp_clean GROUP BY game_id, posteam
),
tgt AS (
    SELECT game_id, posteam AS team, receiver_player_id AS gsis_id,
           count(*)                                   AS targets,
           sum(coalesce(air_yards, 0))                AS air_yards,
           count(*) FILTER (WHERE is_redzone)         AS rz_targets,
           sum(CASE WHEN complete_pass = 1 THEN 1 ELSE 0 END) AS receptions,
           sum(CASE WHEN complete_pass = 1 THEN yards_gained ELSE 0 END) AS rec_yards
    FROM mart.pbp_clean WHERE pass = 1 AND receiver_player_id IS NOT NULL
    GROUP BY game_id, posteam, receiver_player_id
),
car AS (
    SELECT game_id, posteam AS team, rusher_player_id AS gsis_id,
           count(*)                           AS carries,
           sum(yards_gained)                  AS rush_yards,
           count(*) FILTER (WHERE is_redzone) AS rz_carries
    FROM mart.pbp_clean WHERE rush = 1 AND rusher_player_id IS NOT NULL
    GROUP BY game_id, posteam, rusher_player_id
),
-- route proxy: on-field for a dropback
routes AS (
    SELECT p.nflverse_game_id AS game_id,
           unnest(string_split(p.offense_players, ';')) AS gsis_id
    FROM raw.pbp_participation p
    JOIN mart.pbp_clean c
      ON c.game_id = p.nflverse_game_id AND c.play_id = p.play_id
    WHERE c.pass = 1 AND p.offense_players IS NOT NULL AND p.offense_players <> ''
),
route_ct AS (
    SELECT game_id, gsis_id, count(*) AS pass_snaps
    FROM routes WHERE gsis_id <> '' GROUP BY game_id, gsis_id
),
snaps AS (
    SELECT s.game_id, pl.gsis_id, s.team,
           s.offense_snaps, s.offense_pct, s.position
    FROM raw.snap_counts s
    JOIN raw.players pl ON pl.pfr_id = s.pfr_player_id
    WHERE pl.gsis_id IS NOT NULL
),
keys AS (
    SELECT game_id, team, gsis_id FROM tgt
    UNION SELECT game_id, team, gsis_id FROM car
    UNION SELECT game_id, team, gsis_id FROM snaps
)
-- DEFINITIONS COME FROM NFLVERSE WHERE NFLVERSE PUBLISHES THEM.
--
-- Our first pass derived target_share from play-by-play as
--   player targets / team PASS PLAYS
-- which is not the standard metric. nflverse (and everyone else) uses
--   player targets / team TARGETS
-- The difference is sacks and throwaways sitting in our denominator, and it is
-- not cosmetic: cross-checked over 23,608 player-games our version correlated
-- 0.9866 with theirs at MAE 0.020, which against a mean share of ~0.11 is
-- roughly 18% relative error - enough to move a prop line. air_yards_share
-- agreed almost exactly (0.9996), so that one was already right.
--
-- So the canonical columns now carry nflverse's values, and the pbp-derived
-- versions are retained as pbp_* for cross-checking rather than deleted.
-- racr and wopr come along free; wopr = 1.5*target_share + 0.7*air_yards_share
-- is the standard opportunity composite and we were computing both halves
-- without ever combining them.
SELECT
    k.game_id, tg.season, tg.week, k.team, k.gsis_id,
    pl.display_name, coalesce(sn.position, pl.position) AS position,
    tg.opponent, tg.is_home,
    sn.offense_snaps, sn.offense_pct,
    rc.pass_snaps                                          AS route_proxy_snaps,
    CASE WHEN tt.team_pass_plays > 0
         THEN rc.pass_snaps::DOUBLE / tt.team_pass_plays END AS route_proxy_pct,
    coalesce(t.targets, 0)                                 AS targets,
    -- canonical: nflverse definition, falling back to ours if absent
    coalesce(sw.target_share,
             CASE WHEN tt.team_targets > 0
                  THEN coalesce(t.targets, 0)::DOUBLE / tt.team_targets END)
                                                           AS target_share,
    coalesce(sw.air_yards_share,
             CASE WHEN tt.team_air_yards > 0
                  THEN coalesce(t.air_yards,0)::DOUBLE / tt.team_air_yards END)
                                                           AS air_yards_share,
    sw.racr,
    sw.wopr,
    -- our pbp-derived versions, kept for cross-check only
    CASE WHEN tt.team_pass_plays > 0
         THEN coalesce(t.targets, 0)::DOUBLE / tt.team_pass_plays END
                                                           AS pbp_target_share,
    CASE WHEN tt.team_air_yards > 0
         THEN coalesce(t.air_yards, 0)::DOUBLE / tt.team_air_yards END
                                                           AS pbp_air_yards_share,
    coalesce(t.receptions, 0)                              AS receptions,
    coalesce(t.rec_yards, 0)                               AS rec_yards,
    coalesce(t.air_yards, 0)                               AS air_yards,
    coalesce(c.carries, 0)                                 AS carries,
    CASE WHEN tt.team_rush_plays > 0
         THEN coalesce(c.carries, 0)::DOUBLE / tt.team_rush_plays END AS carry_share,
    coalesce(c.rush_yards, 0)                              AS rush_yards,
    coalesce(t.rz_targets, 0) + coalesce(c.rz_carries, 0)  AS rz_touches,
    tt.team_pass_plays, tt.team_rush_plays, tt.team_targets
FROM keys k
JOIN mart.team_game tg  ON tg.game_id = k.game_id AND tg.team = k.team
JOIN team_tot tt        ON tt.game_id = k.game_id AND tt.team = k.team
LEFT JOIN tgt t         ON t.game_id = k.game_id AND t.gsis_id = k.gsis_id AND t.team = k.team
LEFT JOIN car c         ON c.game_id = k.game_id AND c.gsis_id = k.gsis_id AND c.team = k.team
LEFT JOIN route_ct rc   ON rc.game_id = k.game_id AND rc.gsis_id = k.gsis_id
LEFT JOIN snaps sn      ON sn.game_id = k.game_id AND sn.gsis_id = k.gsis_id
LEFT JOIN raw.players pl ON pl.gsis_id = k.gsis_id
LEFT JOIN raw.stats_player_week sw
       ON sw.player_id = k.gsis_id AND sw.season = tg.season AND sw.week = tg.week
WHERE k.gsis_id IS NOT NULL;


-- ---------------------------------------------------------------------------
-- mart.team_rating : written by scripts/build_mart.py (iterative).
-- Declared here so the shape is contract, not incidental.
-- POINT-IN-TIME: a row for (season, week, team) uses ONLY games played
-- strictly BEFORE that week. Never join a rating to a game with week <= its
-- own week from a later snapshot - that is lookahead leakage.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mart.team_rating (
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,   -- rating AS OF the start of this week
    team            VARCHAR NOT NULL,
    games_used      INTEGER,
    plays_used      INTEGER,
    off_rating      DOUBLE,             -- adj EPA/play, league-centered
    def_rating      DOUBLE,             -- adj EPA/play allowed, NEGATIVE = good
    off_pass_rating DOUBLE,
    off_rush_rating DOUBLE,
    def_pass_rating DOUBLE,
    def_rush_rating DOUBLE,
    net_rating      DOUBLE,             -- off_rating - def_rating
    prior_weight    DOUBLE,             -- share of rating from prior-season carryover
    built_at        TIMESTAMPTZ,
    PRIMARY KEY (season, week, team)
);
