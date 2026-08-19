-- ============================================================================
-- Freshness. A quote is only a live line for as long as the book is still
-- being captured; after that it is history.
--
-- The window is measured against the newest capture in the table, NOT against
-- wall clock, so a rebuild on a laptop that slept overnight still works and
-- does not empty the board.
-- ============================================================================

CREATE OR REPLACE VIEW odds.line_fresh AS
SELECT l.*
FROM odds.line_pregame l
WHERE l.captured_at >= (
        SELECT max(captured_at) - INTERVAL 24 HOUR FROM odds.line_pregame);

-- What the freshness rule is excluding, so it can never thin the board quietly.
CREATE OR REPLACE VIEW audit.stale_books AS
WITH mx AS (SELECT max(captured_at) AS m FROM odds.line_pregame)
SELECT l.book_id,
       cast(max(l.captured_at) AS VARCHAR)                        AS last_quote,
       round(date_diff('minute', max(l.captured_at),
                       (SELECT m FROM mx)) / 60.0, 1)             AS hours_old,
       count(*)                                                   AS lines,
       any_value(b.is_placeable)                                  AS placeable
FROM odds.line_pregame l
JOIN ref.book b USING (book_id)
GROUP BY l.book_id
HAVING max(l.captured_at) < (SELECT m FROM mx) - INTERVAL 24 HOUR
ORDER BY hours_old DESC;
