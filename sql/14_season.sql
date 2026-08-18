-- ============================================================================
-- Season state: this is a rolling resource, not a Week 1 report.
--
-- THE PROBLEM. Everything built so far described one slate. The slate JSON is
-- overwritten on every run, the artifact was titled "Week 1", and nothing
-- recorded what we thought at the time - so by week 3 there would be no way to
-- answer "what did we say in week 1 and were we right", which is the only
-- question that compounds.
--
-- WHAT ALREADY ACCUMULATES, and is therefore not duplicated here:
--   bet.wager / bet.clv     every decision and its CLV grade
--   ref.team_log            append-only narrative per team
--   mart.team_rating        point-in-time, one row per team per week
--   odds.snapshot           the capture history
--
-- WHAT WAS MISSING is a per-week record of what the SYSTEM produced: how many
-- markets it priced, how many cleared, what it passed on. That cannot be
-- reconstructed from the warehouse afterwards because the slate payload is
-- transient. log.week_build is append-only for exactly that reason - a build
-- never edits an earlier week's row.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS log;

CREATE TABLE IF NOT EXISTS log.week_build (
    build_id      BIGINT,
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    built_at      TIMESTAMP NOT NULL,
    n_games       INTEGER,
    n_markets     INTEGER,
    n_plays       INTEGER,
    n_thin        INTEGER,
    n_middles     INTEGER,
    n_props_posted   INTEGER,
    n_props_priced   INTEGER,
    captured_at   VARCHAR,      -- the odds snapshot the build read
    note          VARCHAR
);

-- One row per scheduled week, with results filled in as they are played. This
-- is the spine the season view walks.
CREATE OR REPLACE VIEW mart.season_week AS
WITH g AS (
    SELECT season, week,
           count(*)                         AS games,
           count(result)                    AS played,
           min(gameday)                     AS first_game,
           max(gameday)                     AS last_game,
           avg(CASE WHEN result IS NOT NULL
                    THEN abs(result) END)    AS mean_margin,
           avg(CASE WHEN result IS NOT NULL
                    THEN home_score + away_score END) AS mean_total
    FROM raw.games
    WHERE game_type = 'REG'
    GROUP BY 1, 2
),
b AS (
    SELECT season, week, max(built_at) AS last_built,
           count(*) AS builds,
           max(n_plays) AS n_plays, max(n_middles) AS n_middles,
           max(n_props_priced) AS n_props_priced
    FROM log.week_build GROUP BY 1, 2
),
w AS (
    SELECT season, week,
           count(*) AS decisions,
           count(*) FILTER (WHERE decision = 'placed') AS placed,
           count(*) FILTER (WHERE decision = 'passed') AS passed
    FROM bet.wager GROUP BY 1, 2
)
SELECT g.season, g.week, g.games, g.played,
       g.played = g.games AND g.games > 0 AS complete,
       g.first_game, g.last_game,
       round(g.mean_margin, 2) AS mean_margin,
       round(g.mean_total, 2)  AS mean_total,
       b.last_built, coalesce(b.builds, 0) AS builds,
       b.n_plays, b.n_middles, b.n_props_priced,
       coalesce(w.decisions, 0) AS decisions,
       coalesce(w.placed, 0)    AS placed,
       coalesce(w.passed, 0)    AS passed
FROM g
LEFT JOIN b ON b.season = g.season AND b.week = g.week
LEFT JOIN w ON w.season = g.season AND w.week = g.week
ORDER BY g.season, g.week;

-- The current week is the earliest with an unplayed game. Before the opener
-- that is week 1; it advances on its own as results land, so nothing has to be
-- edited each week.
CREATE OR REPLACE VIEW mart.current_week AS
SELECT season, min(week) AS week
FROM mart.season_week
WHERE played < games
GROUP BY season;
