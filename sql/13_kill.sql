-- ============================================================================
-- Kill conditions: from prose to something that can actually fire.
--
-- THE PROBLEM. bet.wager.kill_condition is mandatory and non-empty, which was
-- the right call - a bet without a falsifiable exit can only be rationalised.
-- But it is FREE TEXT, and free text never fires. Five decisions were logged
-- with kills and not one had ever been evaluated, because evaluating them
-- meant a human re-reading five sentences and comparing them to a board that
-- moves hourly. A kill that is never checked is a comment, not a control.
--
-- THE DESIGN. Keep the prose - it carries the reasoning and a structured
-- trigger cannot. Add a STRUCTURED trigger beside it for the mechanical part,
-- which in practice is nearly all of them: "line moves to X or worse at BOOK".
-- The checker fires on the structured trigger and, for any kill that has none,
-- prints the prose next to the actual line movement so the judgement is made
-- against numbers rather than memory.
--
-- Deliberately NOT attempting to parse the English. A regex that is right most
-- of the time on a control surface is worse than no control at all, because it
-- reads as coverage.
-- ============================================================================

ALTER TABLE bet.wager ADD COLUMN IF NOT EXISTS kill_line DOUBLE;
ALTER TABLE bet.wager ADD COLUMN IF NOT EXISTS kill_dir VARCHAR;
-- 'at_or_worse' : fires when the available line reaches kill_line or beyond in
--                 the direction that hurts the position (the usual case).
-- 'at_or_better': fires when the line improves to kill_line - used for a pass
--                 that becomes interesting again, which is a re-entry trigger
--                 rather than an exit.
ALTER TABLE bet.wager ADD COLUMN IF NOT EXISTS kill_book VARCHAR;
-- NULL means "any placeable book". A kill written against a specific book is
-- checked only at that book.

CREATE TABLE IF NOT EXISTS bet.kill_event (
    kill_event_id   BIGINT,
    bet_id          BIGINT NOT NULL,
    fired_at        TIMESTAMPTZ NOT NULL,
    trigger_book    VARCHAR,
    trigger_line    DOUBLE,
    logged_line     DOUBLE,
    move_pts        DOUBLE,
    snapshot_id     VARCHAR,
    note            VARCHAR
);

-- Every open decision with its worst-case current line, so a kill that has no
-- structured trigger still gets adjudicated against a number.
CREATE OR REPLACE VIEW bet.v_kill_watch AS
WITH latest AS (
    SELECT l.game_id, l.market_id, l.side_key, l.book_id, l.line_value,
           l.price_american, l.captured_at,
           row_number() OVER (PARTITION BY l.game_id, l.market_id, l.side_key,
                                           l.book_id
                              ORDER BY l.captured_at DESC) AS rn
    FROM odds.line_pregame l
    JOIN ref.book b USING (book_id)
    WHERE b.is_placeable
)
SELECT w.bet_id, w.decision, w.game_id, w.market_id, w.side_key,
       w.line_value      AS logged_line,
       w.price_american  AS logged_price,
       w.kill_line, w.kill_dir, w.kill_book, w.kill_condition,
       min(la.line_value) AS min_line_now,
       max(la.line_value) AS max_line_now,
       count(*)           AS books_now
FROM bet.wager w
LEFT JOIN latest la
       ON la.game_id = w.game_id AND la.market_id = w.market_id
      AND la.side_key = w.side_key AND la.rn = 1
GROUP BY ALL;
