-- ============================================================================
-- NFL Betting Analytics — Audit Views
-- Every view here answers a question of the form "what is silently wrong?"
-- Run audit.run_all (see RUNBOOK) after every load. A clean audit is the
-- precondition for trusting anything downstream.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Ingest health
-- ---------------------------------------------------------------------------

-- Latest successful load per target table, with age. Stale substrate is the
-- most common cause of a confidently wrong number.
CREATE OR REPLACE VIEW audit.ingest_freshness AS
SELECT
    target_table,
    max(load_started_at)                                   AS last_ok_load,
    date_diff('hour', max(load_started_at), now())         AS hours_stale,
    arg_max(row_count, load_started_at)                    AS last_row_count,
    arg_max(season,    load_started_at)                    AS last_season
FROM raw._ingest_log
WHERE status = 'ok'
GROUP BY target_table
ORDER BY hours_stale DESC;

-- Loads that failed and were never subsequently retried successfully.
CREATE OR REPLACE VIEW audit.ingest_failures AS
WITH last_status AS (
    SELECT target_table, asset_file,
           arg_max(status,     load_started_at) AS latest_status,
           arg_max(error_text, load_started_at) AS latest_error,
           max(load_started_at)                 AS latest_at
    FROM raw._ingest_log
    GROUP BY target_table, asset_file
)
SELECT * FROM last_status WHERE latest_status = 'failed';

-- Declared-required columns missing from a table that HAS been materialized.
-- Catches nflverse upstream schema drift before it becomes a silent NULL.
-- Deliberately scoped to existing tables only: a contracted table that has not
-- been loaded yet is "not loaded", not "violating its contract". The distinct
-- question of which contracted tables are absent is audit.contract_tables_missing.
CREATE OR REPLACE VIEW audit.column_contract_violations AS
WITH materialized AS (
    SELECT DISTINCT schema_name || '.' || table_name AS target_table
    FROM duckdb_columns()
)
SELECT
    c.target_table,
    c.column_name        AS missing_column,
    c.expected_type,
    c.note
FROM raw._column_contract c
JOIN materialized m ON m.target_table = c.target_table
LEFT JOIN duckdb_columns() d
       ON  d.schema_name || '.' || d.table_name = c.target_table
       AND d.column_name = c.column_name
WHERE c.is_required
  AND d.column_name IS NULL;

-- Contracted tables that do not exist yet. Expected to be non-zero until the
-- loaders are written; informational, and excluded from the run_all roll-up.
CREATE OR REPLACE VIEW audit.contract_tables_missing AS
WITH materialized AS (
    SELECT DISTINCT schema_name || '.' || table_name AS target_table
    FROM duckdb_columns()
)
SELECT DISTINCT c.target_table, count(*) OVER (PARTITION BY c.target_table) AS contracted_cols
FROM raw._column_contract c
LEFT JOIN materialized m ON m.target_table = c.target_table
WHERE m.target_table IS NULL;


-- ---------------------------------------------------------------------------
-- Entity resolution health
-- ---------------------------------------------------------------------------

-- Odds rows whose game could not be tied to the nflverse spine. Anything here
-- is invisible to every downstream join, so this must be zero before a scan.
CREATE OR REPLACE VIEW audit.unresolved_games AS
SELECT
    source, source_game_id, source_home_raw, source_away_raw,
    commence_time, match_method
FROM odds.game_map
WHERE game_id IS NULL
ORDER BY commence_time;

-- Prop lines whose player could not be resolved to a gsis_id. These are the
-- rows a manual override in ref/overrides/player_overrides.json must fix.
CREATE OR REPLACE VIEW audit.unresolved_players AS
SELECT
    source, source_player_key, source_name_raw, source_team,
    source_position, match_method, match_confidence
FROM ref.player_xref
WHERE gsis_id IS NULL
ORDER BY source, source_name_raw;

-- Fuzzy matches that were accepted but are weak enough to deserve eyeballs.
CREATE OR REPLACE VIEW audit.low_confidence_players AS
SELECT source, source_name_raw, gsis_id, match_method, match_confidence
FROM ref.player_xref
WHERE gsis_id IS NOT NULL
  AND match_method = 'fuzzy'
  AND coalesce(match_confidence, 0) < 0.92
ORDER BY match_confidence;

-- Book keys seen in the feed that we never mapped. An unmapped book is a book
-- whose prices are being silently discarded.
CREATE OR REPLACE VIEW audit.unmapped_books AS
SELECT DISTINCT l.book_id AS unmapped_book_key, count(*) AS line_rows
FROM odds.line l
LEFT JOIN ref.book b ON b.book_id = l.book_id
WHERE b.book_id IS NULL
GROUP BY l.book_id
ORDER BY line_rows DESC;


-- ---------------------------------------------------------------------------
-- Odds path integrity
-- ---------------------------------------------------------------------------

-- Snapshot cadence coverage. We expect opener / post_wed / post_fri / close
-- for every season-week. Gaps here are permanently unrecoverable data.
CREATE OR REPLACE VIEW audit.snapshot_coverage AS
SELECT
    season, week,
    count(*) FILTER (WHERE phase = 'opener')    AS n_opener,
    count(*) FILTER (WHERE phase = 'post_wed')  AS n_post_wed,
    count(*) FILTER (WHERE phase = 'post_fri')  AS n_post_fri,
    count(*) FILTER (WHERE phase = 'close')     AS n_close,
    count(*)                                    AS n_total,
    min(captured_at)                            AS first_capture,
    max(captured_at)                            AS last_capture
FROM odds.snapshot
GROUP BY season, week
ORDER BY season, week;

-- Season-weeks missing any of the four required phases.
CREATE OR REPLACE VIEW audit.snapshot_gaps AS
SELECT season, week, n_opener, n_post_wed, n_post_fri, n_close
FROM audit.snapshot_coverage
WHERE n_opener = 0 OR n_post_wed = 0 OR n_post_fri = 0 OR n_close = 0;

-- Exact duplicate lines within one snapshot. Indicates a loader replay bug;
-- silently doubles the weight of a price in any averaging.
--
-- Keys on source_game_id, NOT game_id. game_id is NULL until resolution
-- succeeds, and grouping on a nullable column collapses every unresolved game
-- into a single bucket - which reported 24 phantom duplicates that were
-- actually distinct games sharing a common total. Always group odds rows on
-- the identifier that is guaranteed present.
-- entity_key is REQUIRED in this grouping. It is what distinguishes one player
-- from another on the same prop market, and a game total from a team total.
-- Without it, 23 different players' anytime-TD lines at one book collapse into
-- a single "duplicate" because gsis_id is NULL until resolution runs. Twice now
-- this view has reported phantom duplicates by grouping on a nullable column.
CREATE OR REPLACE VIEW audit.duplicate_lines AS
SELECT snapshot_id, source_game_id, book_id, market_id, side_key, entity_key,
       gsis_id, line_value, count(*) AS n
FROM odds.line
GROUP BY ALL
HAVING count(*) > 1;

-- Sanity band on prices. Anything outside is almost certainly a parse error.
CREATE OR REPLACE VIEW audit.implausible_prices AS
SELECT line_id, snapshot_id, game_id, book_id, market_id, side_key,
       line_value, price_american
FROM odds.line
WHERE price_american BETWEEN -99 AND 99
   OR abs(price_american) > 100000
   OR price_american = 0;


-- ---------------------------------------------------------------------------
-- Access control: the phantom-edge guard
-- ---------------------------------------------------------------------------

-- Any bet logged at a book we cannot actually use. Must always be empty.
CREATE OR REPLACE VIEW audit.unplaceable_bets AS
SELECT w.bet_id, w.placed_at, w.game_id, w.market_id, w.book_id, b.book_name
FROM bet.wager w
JOIN ref.book b ON b.book_id = w.book_id
WHERE b.is_placeable = FALSE;


-- ---------------------------------------------------------------------------
-- Model health
-- ---------------------------------------------------------------------------

-- Null-test scoreboard. A sim fed the market's own inputs must reproduce the
-- market's own derived prices. Failures here invalidate every edge that sim
-- reports — the sim is wrong, not the market.
CREATE OR REPLACE VIEW audit.calibration_summary AS
SELECT
    r.model_id,
    t.market_id,
    count(*)                                        AS n_tests,
    round(avg(t.abs_error),  5)                     AS mean_abs_err,
    round(quantile_cont(t.abs_error, 0.95), 5)      AS p95_abs_err,
    count(*) FILTER (WHERE t.passed)                AS n_passed,
    round(100.0 * count(*) FILTER (WHERE t.passed) / nullif(count(*), 0), 1) AS pct_passed
FROM model.calibration_test t
JOIN model.sim_run  s ON s.sim_id   = t.sim_id
JOIN model.registry r ON r.model_id = s.model_id
GROUP BY r.model_id, t.market_id
ORDER BY pct_passed;

-- Projection decomposition must reconcile to the projected spread. A drift
-- here means a contribution term is unaccounted for.
CREATE OR REPLACE VIEW audit.projection_decomp_drift AS
SELECT
    projection_id, model_id, game_id, proj_spread,
    coalesce(c_ratings,0) + coalesce(c_hfa,0) + coalesce(c_rest,0)
      + coalesce(c_travel,0) + coalesce(c_weather,0) + coalesce(c_injury,0)
      + coalesce(c_qb,0) + coalesce(c_other,0)                    AS decomp_sum,
    proj_spread - (coalesce(c_ratings,0) + coalesce(c_hfa,0) + coalesce(c_rest,0)
      + coalesce(c_travel,0) + coalesce(c_weather,0) + coalesce(c_injury,0)
      + coalesce(c_qb,0) + coalesce(c_other,0))                   AS drift
FROM model.projection
WHERE abs(proj_spread - (coalesce(c_ratings,0) + coalesce(c_hfa,0) + coalesce(c_rest,0)
      + coalesce(c_travel,0) + coalesce(c_weather,0) + coalesce(c_injury,0)
      + coalesce(c_qb,0) + coalesce(c_other,0))) > 0.01;

-- CLV by model. This is the scoreboard that decides whether a signal is real.
-- Benchmark is 50% beat_close; anything at or below that is noise.
CREATE OR REPLACE VIEW audit.clv_by_model AS
SELECT
    w.model_id,
    m.market_id,
    count(*)                                          AS n_bets,
    round(avg(c.clv_line_pts),  3)                    AS avg_clv_pts,
    round(avg(c.clv_prob_delta), 4)                   AS avg_clv_prob,
    count(*) FILTER (WHERE c.beat_close)              AS n_beat_close,
    round(100.0 * count(*) FILTER (WHERE c.beat_close) / nullif(count(*),0), 1) AS pct_beat_close
FROM bet.wager w
JOIN bet.clv   c ON c.bet_id = w.bet_id
JOIN ref.market m ON m.market_id = w.market_id
GROUP BY w.model_id, m.market_id
ORDER BY pct_beat_close DESC;

-- Bets still awaiting a CLV grade. Ungraded bets are unmeasured bets.
CREATE OR REPLACE VIEW audit.ungraded_bets AS
SELECT w.bet_id, w.placed_at, w.game_id, w.market_id, w.side_key, w.book_id
FROM bet.wager w
LEFT JOIN bet.clv c ON c.bet_id = w.bet_id
WHERE c.bet_id IS NULL
ORDER BY w.placed_at;


-- ---------------------------------------------------------------------------
-- Referential integrity across schemas
-- DuckDB cannot declare cross-schema foreign keys, so these views ARE the
-- constraint. A non-zero count is an orphaned row: still present and
-- inspectable, but broken. Never let these sit non-zero.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW audit.orphan_refs AS
SELECT 'odds.line.book_id'  AS relation, cast(l.book_id AS VARCHAR) AS bad_value, count(*) AS n
FROM odds.line l LEFT JOIN ref.book b ON b.book_id = l.book_id
WHERE l.book_id IS NOT NULL AND b.book_id IS NULL GROUP BY 1, 2

UNION ALL
SELECT 'odds.line.market_id', cast(l.market_id AS VARCHAR), count(*)
FROM odds.line l LEFT JOIN ref.market m ON m.market_id = l.market_id
WHERE l.market_id IS NOT NULL AND m.market_id IS NULL GROUP BY 1, 2

UNION ALL
SELECT 'model.sim_fair_price.market_id', cast(f.market_id AS VARCHAR), count(*)
FROM model.sim_fair_price f LEFT JOIN ref.market m ON m.market_id = f.market_id
WHERE m.market_id IS NULL GROUP BY 1, 2

UNION ALL
SELECT 'bet.wager.market_id', cast(w.market_id AS VARCHAR), count(*)
FROM bet.wager w LEFT JOIN ref.market m ON m.market_id = w.market_id
WHERE m.market_id IS NULL GROUP BY 1, 2

UNION ALL
SELECT 'bet.wager.book_id', cast(w.book_id AS VARCHAR), count(*)
FROM bet.wager w LEFT JOIN ref.book b ON b.book_id = w.book_id
WHERE b.book_id IS NULL GROUP BY 1, 2

UNION ALL
SELECT 'bet.wager.model_id', cast(w.model_id AS VARCHAR), count(*)
FROM bet.wager w LEFT JOIN model.registry r ON r.model_id = w.model_id
WHERE w.model_id IS NOT NULL AND r.model_id IS NULL GROUP BY 1, 2

UNION ALL
SELECT 'bet.wager.snapshot_id', cast(w.snapshot_id AS VARCHAR), count(*)
FROM bet.wager w LEFT JOIN odds.snapshot s ON s.snapshot_id = w.snapshot_id
WHERE w.snapshot_id IS NOT NULL AND s.snapshot_id IS NULL GROUP BY 1, 2

UNION ALL
SELECT 'bet.clv.close_book_id', cast(c.close_book_id AS VARCHAR), count(*)
FROM bet.clv c LEFT JOIN ref.book b ON b.book_id = c.close_book_id
WHERE c.close_book_id IS NOT NULL AND b.book_id IS NULL GROUP BY 1, 2;


-- ---------------------------------------------------------------------------
-- Roll-up: one row per check, for a single pass/fail read
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW audit.run_all AS
SELECT 'ingest_failures'            AS check_name, count(*) AS n_issues FROM audit.ingest_failures
UNION ALL SELECT 'orphan_refs',               count(*) FROM audit.orphan_refs
UNION ALL SELECT 'column_contract_violations', count(*) FROM audit.column_contract_violations
UNION ALL SELECT 'unresolved_games',           count(*) FROM audit.unresolved_games
UNION ALL SELECT 'unresolved_players',         count(*) FROM audit.unresolved_players
UNION ALL SELECT 'low_confidence_players',     count(*) FROM audit.low_confidence_players
UNION ALL SELECT 'unmapped_books',             count(*) FROM audit.unmapped_books
UNION ALL SELECT 'snapshot_gaps',              count(*) FROM audit.snapshot_gaps
UNION ALL SELECT 'duplicate_lines',            count(*) FROM audit.duplicate_lines
UNION ALL SELECT 'implausible_prices',         count(*) FROM audit.implausible_prices
UNION ALL SELECT 'unplaceable_bets',           count(*) FROM audit.unplaceable_bets
UNION ALL SELECT 'projection_decomp_drift',    count(*) FROM audit.projection_decomp_drift
UNION ALL SELECT 'ungraded_bets',              count(*) FROM audit.ungraded_bets
ORDER BY n_issues DESC, check_name;
