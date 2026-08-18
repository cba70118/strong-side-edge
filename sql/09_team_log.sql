-- ============================================================================
-- ref.team_log - a running, append-only narrative record per club.
--
-- WHY A TABLE AND NOT A GENERATED PARAGRAPH. The point of this is that it
-- ACCUMULATES. A profile regenerated from scratch each week is just a view of
-- current state; what we want is the record of what was true in week 3 and
-- still saying so in week 12, so that a read can be checked against what
-- actually happened. That only works if entries are written once and kept.
--
-- WHAT MAY GO IN HERE. Entries are composed from the warehouse - measured EPA,
-- ratings, market prices, results - or quoted from an attributed source. The
-- generator states nothing it cannot point at. That restraint is the whole
-- value: a season-long log of confident prose that turns out to be invented is
-- worse than no log, because it reads exactly like the real thing.
--
-- Each entry is idempotent on (team, season, week, kind) so a re-run rewrites
-- its own row rather than stacking duplicates, but never touches an earlier
-- week. `metrics` carries the numbers the prose was built from, so a later
-- reader can audit a sentence without re-deriving the season.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS ref;

CREATE TABLE IF NOT EXISTS ref.team_log (
    team        VARCHAR NOT NULL,
    season      INTEGER NOT NULL,
    week        INTEGER NOT NULL,   -- 0 = preseason entry
    kind        VARCHAR NOT NULL,   -- preseason | weekly | market | manual
    as_of       DATE    NOT NULL,
    headline    VARCHAR NOT NULL,
    body        VARCHAR NOT NULL,
    metrics     VARCHAR,            -- JSON of the figures behind the prose
    source      VARCHAR,            -- 'generated' | 'user' | attributed name
    PRIMARY KEY (team, season, week, kind)
);

-- Most recent entry per club, for a panel that shows the current read.
CREATE OR REPLACE VIEW ref.team_log_latest AS
SELECT * FROM (
    SELECT *, row_number() OVER (PARTITION BY team, season
                                 ORDER BY week DESC, as_of DESC) AS rn
    FROM ref.team_log
) WHERE rn = 1;
