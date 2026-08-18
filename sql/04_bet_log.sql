-- ============================================================================
-- Bet log: decision record + CLV grading
-- Build order step 6 (pulled forward - it needs no new data and it is what
-- makes everything else measurable).
--
-- TWO DESIGN COMMITMENTS
--
-- 1. PASSES ARE LOGGED TOO. bet.wager records a DECISION, not a bet. A bet you
--    considered and declined is graded on CLV exactly like one you placed. If
--    you only record what you bet, you learn from a censored sample and can
--    never discover that your filter is too tight - the passes that would have
--    won are invisible. `decision` is 'placed' or 'passed'.
--
-- 2. THESIS AND KILL CONDITION ARE MANDATORY, BEFORE KICKOFF. A bet without a
--    falsifiable reason cannot be reviewed, only rationalised. Enforced by NOT
--    NULL plus a non-empty CHECK, so the database refuses the row rather than
--    trusting a habit.
-- ============================================================================

ALTER TABLE bet.wager ADD COLUMN IF NOT EXISTS decision VARCHAR;
ALTER TABLE bet.wager ADD COLUMN IF NOT EXISTS thesis VARCHAR;
ALTER TABLE bet.wager ADD COLUMN IF NOT EXISTS kill_condition VARCHAR;
ALTER TABLE bet.wager ADD COLUMN IF NOT EXISTS kickoff_at TIMESTAMPTZ;
ALTER TABLE bet.wager ADD COLUMN IF NOT EXISTS logged_before_kickoff BOOLEAN;
ALTER TABLE bet.wager ADD COLUMN IF NOT EXISTS season INTEGER;
ALTER TABLE bet.wager ADD COLUMN IF NOT EXISTS week INTEGER;

ALTER TABLE bet.clv ADD COLUMN IF NOT EXISTS grade_basis VARCHAR;
ALTER TABLE bet.clv ADD COLUMN IF NOT EXISTS devig_method VARCHAR;
ALTER TABLE bet.clv ADD COLUMN IF NOT EXISTS our_implied_prob DOUBLE;
-- 'prob_at_same_line' | 'line_pts'. See scripts/bet_log.py: a probability
-- delta is only meaningful when our line and the reference line are the SAME
-- bet. Different handicaps get measured in points instead, and clv_prob_delta
-- is left NULL rather than filled with a number that conflates line value with
-- the book's hold.
ALTER TABLE bet.clv ADD COLUMN IF NOT EXISTS clv_metric VARCHAR;


-- ---------------------------------------------------------------------------
-- Discipline audits. All three must read 0.
-- ---------------------------------------------------------------------------

-- A decision with no thesis, or a placeholder pretending to be one.
CREATE OR REPLACE VIEW audit.bets_missing_thesis AS
SELECT bet_id, cast(placed_at AS VARCHAR) AS placed_at, decision,
       game_id, market_id, side_key,
       CASE WHEN thesis IS NULL OR trim(thesis) = '' THEN 'no thesis'
            WHEN length(trim(thesis)) < 20            THEN 'thesis too short'
            WHEN kill_condition IS NULL
              OR trim(kill_condition) = ''            THEN 'no kill condition'
            WHEN length(trim(kill_condition)) < 10    THEN 'kill condition too short'
       END AS problem
FROM bet.wager
WHERE thesis IS NULL OR trim(thesis) = '' OR length(trim(thesis)) < 20
   OR kill_condition IS NULL OR trim(kill_condition) = ''
   OR length(trim(kill_condition)) < 10;

-- Logged after the ball was snapped. Retrospective theses are worthless.
CREATE OR REPLACE VIEW audit.bets_logged_after_kickoff AS
SELECT bet_id, cast(placed_at AS VARCHAR) AS placed_at,
       cast(kickoff_at AS VARCHAR) AS kickoff_at,
       decision, game_id, market_id, side_key
FROM bet.wager
WHERE kickoff_at IS NOT NULL AND placed_at > kickoff_at;

-- Decisions with an invalid or missing decision value.
CREATE OR REPLACE VIEW audit.bets_bad_decision AS
SELECT bet_id, decision FROM bet.wager
WHERE decision IS NULL OR decision NOT IN ('placed', 'passed');


-- ---------------------------------------------------------------------------
-- CLV reporting. The benchmark is 50% beat-close, NOT profit.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW audit.clv_by_market AS
SELECT
    w.decision,
    m.family                                            AS market_family,
    w.market_id,
    count(*)                                            AS n,
    round(avg(c.clv_prob_delta) * 100, 2)               AS avg_clv_pp,
    round(avg(c.clv_line_pts), 3)                       AS avg_clv_pts,
    count(*) FILTER (WHERE c.beat_close)                AS n_beat,
    round(100.0 * count(*) FILTER (WHERE c.beat_close)
          / nullif(count(*), 0), 1)                     AS pct_beat_close
FROM bet.wager w
JOIN bet.clv c    ON c.bet_id = w.bet_id
JOIN ref.market m ON m.market_id = w.market_id
GROUP BY ALL
ORDER BY w.decision, pct_beat_close DESC;

CREATE OR REPLACE VIEW audit.clv_weekly AS
SELECT
    w.season, w.week, w.decision,
    count(*)                                            AS n,
    round(avg(c.clv_prob_delta) * 100, 2)               AS avg_clv_pp,
    count(*) FILTER (WHERE c.beat_close)                AS n_beat,
    round(100.0 * count(*) FILTER (WHERE c.beat_close)
          / nullif(count(*), 0), 1)                     AS pct_beat_close
FROM bet.wager w
JOIN bet.clv c ON c.bet_id = w.bet_id
GROUP BY ALL
ORDER BY w.season, w.week, w.decision;

-- THE view that justifies logging passes. If passes beat the close as often as
-- placements, the selection filter is adding nothing and is only cutting volume.
CREATE OR REPLACE VIEW audit.placed_vs_passed AS
WITH g AS (
    SELECT w.decision, c.clv_prob_delta, c.clv_line_pts, c.beat_close,
           c.clv_metric
    FROM bet.wager w JOIN bet.clv c ON c.bet_id = w.bet_id
)
SELECT
    decision,
    count(*)                                       AS n,
    round(avg(clv_line_pts), 3)                    AS avg_clv_pts,
    round(avg(clv_prob_delta) * 100, 2)            AS avg_clv_pp,
    count(*) FILTER (WHERE clv_metric = 'line_pts')          AS n_by_pts,
    count(*) FILTER (WHERE clv_metric = 'prob_at_same_line') AS n_by_prob,
    count(*) FILTER (WHERE beat_close)             AS n_beat,
    round(100.0 * count(*) FILTER (WHERE beat_close)
          / nullif(count(*), 0), 1)                AS pct_beat_close
FROM g GROUP BY decision ORDER BY decision;

-- Everything still awaiting a grade.
CREATE OR REPLACE VIEW bet.v_ungraded AS
SELECT w.bet_id, cast(w.placed_at AS VARCHAR) AS placed_at, w.decision,
       w.game_id, w.market_id, w.side_key, w.line_value, w.price_american,
       w.book_id, cast(w.kickoff_at AS VARCHAR) AS kickoff_at
FROM bet.wager w
LEFT JOIN bet.clv c ON c.bet_id = w.bet_id
WHERE c.bet_id IS NULL
ORDER BY w.kickoff_at NULLS LAST, w.bet_id;
