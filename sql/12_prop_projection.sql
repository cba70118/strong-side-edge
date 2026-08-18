-- ============================================================================
-- Prop projection layer: point-in-time usage, then production.
--
-- DESIGN IS DICTATED BY scripts/usage_stability.py, measured on 20,303
-- player-games (2020-2025, WR/TE/RB, snap% >= 25%, nflverse definitions):
--
--   carry share      corr 0.919      <- project this
--   route share      corr 0.724      <- project this
--   air-yard share   corr 0.691      <- project this
--   snap %           corr 0.691      <- project this
--   WOPR             corr 0.685      <- composite, beats neither component
--   target share     corr 0.633      <- project this
--   ---------------------------------------------------------------
--   receptions       corr 0.523      <- DERIVE, do not project
--   receiving yards  corr 0.500      <- DERIVE, do not project
--   yards per target corr 0.136      <- DO NOT TRUST PLAYER HISTORY
--   RACR             corr 0.070      <- second, independent confirmation
--
-- The last line is the important one. A player's own past yards-per-target
-- carries almost no week-to-week signal, so multiplying projected targets by
-- his personal YPT injects noise. Efficiency is therefore shrunk HARD toward a
-- positional baseline (k = 40 targets), while usage - which is genuinely
-- predictable - is shrunk only lightly (k = 2 games).
--
-- Target-share correlation saturates quickly: 0.567 at a 1-game window, 0.664
-- at 4, 0.681 at 8. A 6-game window captures nearly all of it, so WINDOW = 6.
--
-- EVERYTHING HERE IS POINT-IN-TIME. Every window is `ROWS BETWEEN 6 PRECEDING
-- AND 1 PRECEDING`, which excludes the current row by construction.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS mart;

-- ---------------------------------------------------------------------------
-- Positional baselines. Shrinkage targets, computed on established roles only
-- so that deep bench players do not drag the prior down.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart.usage_baseline AS
SELECT
    position,
    count(*)                              AS n_games,
    avg(offense_pct)                      AS base_snap_pct,
    avg(route_proxy_pct)                  AS base_route_pct,
    avg(target_share)                     AS base_target_share,
    avg(air_yards_share)                  AS base_ay_share,
    avg(carry_share)                      AS base_carry_share,
    sum(rec_yards) / nullif(sum(targets), 0)     AS base_ypt,
    sum(receptions) / nullif(sum(targets), 0)    AS base_catch_rate,
    sum(rush_yards) / nullif(sum(carries), 0)    AS base_ypc
FROM mart.player_game_usage
WHERE position IN ('WR', 'TE', 'RB') AND offense_pct >= 0.25
GROUP BY position;


-- ---------------------------------------------------------------------------
-- Team pass/rush volume, point-in-time. Converts a projected SHARE into a
-- projected COUNT.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart.team_volume_projection AS
WITH t AS (
    SELECT game_id, season, week, team,
           team_pass_plays, team_rush_plays, team_targets
    FROM (
        SELECT DISTINCT game_id, season, week, team,
               team_pass_plays, team_rush_plays, team_targets
        FROM mart.player_game_usage
    )
)
SELECT
    game_id, season, week, team,
    team_pass_plays  AS actual_pass_plays,
    team_rush_plays  AS actual_rush_plays,
    avg(team_pass_plays) OVER w  AS proj_pass_plays,
    avg(team_rush_plays) OVER w  AS proj_rush_plays,
    -- UNITS MUST MATCH THE SHARE. target_share is a share of team TARGETS,
    -- so it has to be multiplied by projected team TARGETS. Multiplying it by
    -- pass PLAYS silently inflates every projection by the sack/throwaway
    -- rate, which showed up as +0.51 bias on targets and +4.2 yards on
    -- receiving before this column existed.
    avg(team_targets)    OVER w  AS proj_team_targets,
    count(*)             OVER w  AS n_prior
FROM t
WINDOW w AS (PARTITION BY team ORDER BY season, week
             ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING);


-- ---------------------------------------------------------------------------
-- Player usage projection, point-in-time and shrunk.
--   usage shrink  k = 2 games   (usage is predictable, shrink lightly)
--   efficiency    k = 40 targets / 40 carries (barely predictable, shrink hard)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE mart.player_usage_projection AS
WITH lagged AS (
    SELECT
        u.game_id, u.season, u.week, u.gsis_id, u.team, u.opponent,
        u.display_name, u.position, u.is_home,
        -- actuals, retained for validation only
        u.offense_pct, u.route_proxy_pct, u.target_share, u.air_yards_share,
        u.carry_share, u.targets, u.receptions, u.rec_yards, u.carries,
        u.rush_yards,
        -- point-in-time priors
        avg(u.offense_pct)     OVER w AS p_snap,
        avg(u.route_proxy_pct) OVER w AS p_route,
        avg(u.target_share)    OVER w AS p_tgt_share,
        avg(u.air_yards_share) OVER w AS p_ay_share,
        avg(u.carry_share)     OVER w AS p_carry_share,
        count(*)               OVER w AS n_prior,
        -- efficiency priors accumulate over a longer window
        sum(u.rec_yards)   OVER we AS p_rec_yds_sum,
        sum(u.targets)     OVER we AS p_tgt_sum,
        sum(u.receptions)  OVER we AS p_rec_sum,
        sum(u.rush_yards)  OVER we AS p_rush_yds_sum,
        sum(u.carries)     OVER we AS p_carry_sum
    FROM mart.player_game_usage u
    WHERE u.position IN ('WR', 'TE', 'RB')
    WINDOW
        w  AS (PARTITION BY u.gsis_id ORDER BY u.season, u.week
               ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING),
        we AS (PARTITION BY u.gsis_id ORDER BY u.season, u.week
               ROWS BETWEEN 16 PRECEDING AND 1 PRECEDING)
),
shrunk AS (
    SELECT
        l.*,
        b.base_snap_pct, b.base_route_pct, b.base_target_share,
        b.base_ay_share, b.base_carry_share, b.base_ypt, b.base_catch_rate,
        b.base_ypc,
        -- usage: light shrinkage (k = 2 games)
        (coalesce(l.p_snap, b.base_snap_pct) * l.n_prior
            + b.base_snap_pct * 2.0) / (l.n_prior + 2.0)         AS proj_snap_pct,
        (coalesce(l.p_route, b.base_route_pct) * l.n_prior
            + b.base_route_pct * 2.0) / (l.n_prior + 2.0)        AS proj_route_pct,
        (coalesce(l.p_tgt_share, b.base_target_share) * l.n_prior
            + b.base_target_share * 2.0) / (l.n_prior + 2.0)     AS proj_target_share,
        (coalesce(l.p_ay_share, b.base_ay_share) * l.n_prior
            + b.base_ay_share * 2.0) / (l.n_prior + 2.0)         AS proj_ay_share,
        (coalesce(l.p_carry_share, b.base_carry_share) * l.n_prior
            + b.base_carry_share * 2.0) / (l.n_prior + 2.0)      AS proj_carry_share,
        -- efficiency: HARD shrinkage (k = 40 attempts), because measured
        -- week-to-week correlation of yards per target is only 0.136
        (coalesce(l.p_rec_yds_sum, 0) + b.base_ypt * 40.0)
            / (coalesce(l.p_tgt_sum, 0) + 40.0)                  AS proj_ypt,
        (coalesce(l.p_rec_sum, 0) + b.base_catch_rate * 40.0)
            / (coalesce(l.p_tgt_sum, 0) + 40.0)                  AS proj_catch_rate,
        (coalesce(l.p_rush_yds_sum, 0) + b.base_ypc * 40.0)
            / (coalesce(l.p_carry_sum, 0) + 40.0)                AS proj_ypc
    FROM lagged l
    JOIN mart.usage_baseline b ON b.position = l.position
)
SELECT
    s.game_id, s.season, s.week, s.gsis_id, s.display_name, s.position,
    s.team, s.opponent, s.is_home, s.n_prior,
    s.proj_snap_pct, s.proj_route_pct, s.proj_target_share,
    s.proj_ay_share, s.proj_carry_share,
    s.proj_ypt, s.proj_catch_rate, s.proj_ypc,
    v.proj_pass_plays, v.proj_rush_plays, v.proj_team_targets,
    -- production is DERIVED from usage x volume x baseline efficiency
    s.proj_target_share * v.proj_team_targets                 AS proj_targets,
    s.proj_target_share * v.proj_team_targets * s.proj_catch_rate AS proj_receptions,
    s.proj_target_share * v.proj_team_targets * s.proj_ypt    AS proj_rec_yards,
    s.proj_carry_share  * v.proj_rush_plays                   AS proj_carries,
    s.proj_carry_share  * v.proj_rush_plays * s.proj_ypc      AS proj_rush_yards,
    -- actuals for validation
    s.offense_pct, s.route_proxy_pct, s.target_share, s.air_yards_share,
    s.carry_share, s.targets, s.receptions, s.rec_yards, s.carries,
    s.rush_yards
FROM shrunk s
LEFT JOIN mart.team_volume_projection v
       ON v.game_id = s.game_id AND v.team = s.team;
