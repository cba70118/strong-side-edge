-- ============================================================================
-- NFL Betting Analytics — Core Schema
-- DuckDB 1.5.x  |  build order step 1  |  schema first, loaders after
--
-- Design notes:
--  * Wide nflverse facts (pbp = 372 cols) are schema-on-read: loaders
--    materialize them with SELECT *, and raw._column_contract asserts the
--    ~40 columns we actually depend on. We do not hand-maintain 372 columns.
--  * Spine/contract tables (games, teams, players, odds, model, bet) ARE
--    defined explicitly, because everything downstream joins on them.
--  * odds.line is APPEND-ONLY. Never UPDATE, never DELETE. The path is data.
--  * ref.book.is_placeable encodes Louisiana book access structurally, so an
--    EV scan physically cannot surface a price we can't take.
--  * DuckDB does NOT support cross-schema foreign keys, so cross-schema
--    relationships are documented in comments and enforced by the referential
--    integrity views in 02_audit_views.sql. This is preferable regardless: a
--    rejected INSERT loses the row, while an audit view keeps the bad row
--    visible and diagnosable. Within-schema FKs are still declared.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS raw;    -- landing zone, 1:1 with nflverse assets
CREATE SCHEMA IF NOT EXISTS ref;    -- dimensions + entity resolution
CREATE SCHEMA IF NOT EXISTS mart;   -- modeling-ready derived tables
CREATE SCHEMA IF NOT EXISTS odds;   -- full odds path, append-only
CREATE SCHEMA IF NOT EXISTS model;  -- projections, sims, fair prices, registry
CREATE SCHEMA IF NOT EXISTS bet;    -- bet log + CLV grading
CREATE SCHEMA IF NOT EXISTS audit;  -- validation views (02_audit_views.sql)


-- ============================================================================
-- RAW : ingest bookkeeping
-- ============================================================================

CREATE SEQUENCE IF NOT EXISTS raw.seq_ingest START 1;

-- Every load attempt is recorded, success or failure. This is the audit trail
-- that answers "is this table stale, and where did it come from".
CREATE TABLE IF NOT EXISTS raw._ingest_log (
    ingest_id       BIGINT PRIMARY KEY DEFAULT nextval('raw.seq_ingest'),
    asset_tag       VARCHAR NOT NULL,   -- nflverse release tag, e.g. 'pbp'
    asset_file      VARCHAR NOT NULL,   -- e.g. 'play_by_play_2025.parquet'
    target_table    VARCHAR NOT NULL,   -- e.g. 'raw.pbp'
    source_url      VARCHAR NOT NULL,
    season          INTEGER,            -- NULL for all-season assets
    row_count       BIGINT,
    byte_size       BIGINT,
    load_started_at TIMESTAMPTZ NOT NULL,
    load_ms         INTEGER,
    status          VARCHAR NOT NULL,   -- ok | failed | skipped_unchanged
    error_text      VARCHAR
);

-- The column contract. Loaders assert these exist with the expected type;
-- audit.column_contract_violations surfaces drift when nflverse changes shape.
CREATE TABLE IF NOT EXISTS raw._column_contract (
    target_table    VARCHAR NOT NULL,
    column_name     VARCHAR NOT NULL,
    expected_type   VARCHAR,            -- NULL = any type, presence only
    is_required     BOOLEAN NOT NULL DEFAULT TRUE,
    note            VARCHAR,
    PRIMARY KEY (target_table, column_name)
);


-- ============================================================================
-- REF : dimensions
-- ============================================================================

-- Franchises. active_to handles relocations (OAK->LV, SD->LAC, STL->LAR)
-- so historical joins don't silently drop or double-count.
CREATE TABLE IF NOT EXISTS ref.team (
    team_id         VARCHAR PRIMARY KEY,   -- nflverse abbr, canonical: 'KC'
    full_name       VARCHAR NOT NULL,
    nickname        VARCHAR,
    conference      VARCHAR,               -- AFC | NFC
    division        VARCHAR,               -- North | South | East | West
    active_from     INTEGER,
    active_to       INTEGER,               -- NULL = currently active
    is_current      BOOLEAN NOT NULL DEFAULT TRUE
);

-- Entity resolution for teams. Odds feeds say "Kansas City Chiefs";
-- nflverse says "KC"; PFR says "KAN". One row per (source, alias).
CREATE TABLE IF NOT EXISTS ref.team_alias (
    source          VARCHAR NOT NULL,      -- 'the_odds_api' | 'pfr' | 'espn' | ...
    alias           VARCHAR NOT NULL,
    team_id         VARCHAR NOT NULL REFERENCES ref.team(team_id),
    is_manual       BOOLEAN NOT NULL DEFAULT FALSE,  -- from overrides JSON
    added_at        TIMESTAMPTZ,
    PRIMARY KEY (source, alias)
);

-- Venue + environment. lat/lon feed the NWS weather pull; roof gates it
-- (no point fetching wind for a dome).
CREATE TABLE IF NOT EXISTS ref.stadium (
    stadium_id      VARCHAR PRIMARY KEY,
    stadium_name    VARCHAR NOT NULL,
    team_id         VARCHAR REFERENCES ref.team(team_id),
    city            VARCHAR,
    state           VARCHAR,
    lat             DOUBLE,
    lon             DOUBLE,
    elevation_ft    INTEGER,
    roof_type       VARCHAR,               -- outdoors | dome | closed | open
    surface         VARCHAR,
    tz              VARCHAR,               -- IANA, e.g. 'America/Chicago'
    nws_grid_id     VARCHAR,               -- cached NWS office/grid
    nws_grid_x      INTEGER,
    nws_grid_y      INTEGER
);

-- Sportsbooks. THIS TABLE ENCODES BOOK ACCESS.
--   is_placeable = we can actually take this price in Louisiana
--   is_sharp     = usable as anchoring / CLV grading truth, never a bet target
-- EV scans MUST filter is_placeable = TRUE. Phantom edge is a solved problem
-- if the join does the work instead of the analyst remembering.
CREATE TABLE IF NOT EXISTS ref.book (
    book_id         VARCHAR PRIMARY KEY,   -- canonical internal key
    book_name       VARCHAR NOT NULL,
    is_placeable    BOOLEAN NOT NULL DEFAULT FALSE,
    is_sharp        BOOLEAN NOT NULL DEFAULT FALSE,
    clv_priority    INTEGER,               -- 1 = preferred CLV grading source
    note            VARCHAR
);

-- Book name resolution across odds feeds (API keys drift; verify, don't assume).
CREATE TABLE IF NOT EXISTS ref.book_alias (
    source          VARCHAR NOT NULL,
    alias           VARCHAR NOT NULL,      -- e.g. 'williamhill_us'
    book_id         VARCHAR NOT NULL REFERENCES ref.book(book_id),
    is_manual       BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (source, alias)
);

-- Market taxonomy. family/period let us slice "derivatives only" or
-- "player props only" without string-matching market names everywhere.
CREATE TABLE IF NOT EXISTS ref.market (
    market_id       VARCHAR PRIMARY KEY,   -- 'spread_game' | 'player_rec_yds' | ...
    market_name     VARCHAR NOT NULL,
    family          VARCHAR NOT NULL,      -- side | total | moneyline | team_total | player_prop | derivative
    period          VARCHAR NOT NULL,      -- game | 1H | 2H | 1Q | drive
    is_player       BOOLEAN NOT NULL DEFAULT FALSE,
    stat_key        VARCHAR,               -- for props: 'rec_yds', 'pass_tds'
    is_alt          BOOLEAN NOT NULL DEFAULT FALSE,
    note            VARCHAR
);

-- Player entity resolution. gsis_id is canonical (nflverse). Prop feeds give
-- us names only, so this is the module that decides whether "M.Harrison" is
-- Marvin Harrison Jr. Built early and deliberately — never inferred at scan time.
CREATE TABLE IF NOT EXISTS ref.player_xref (
    source            VARCHAR NOT NULL,
    source_player_key VARCHAR NOT NULL,    -- source id if any, else normalized name
    gsis_id           VARCHAR,             -- canonical; NULL = UNRESOLVED
    source_name_raw   VARCHAR NOT NULL,
    source_team       VARCHAR,
    source_position   VARCHAR,
    match_method      VARCHAR,             -- exact | normalized | fuzzy | manual | unresolved
    match_confidence  DOUBLE,              -- 0..1
    is_manual         BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at       TIMESTAMPTZ,
    PRIMARY KEY (source, source_player_key)
);


-- ============================================================================
-- ODDS : full path, append-only
-- ============================================================================

CREATE SEQUENCE IF NOT EXISTS odds.seq_snapshot START 1;
CREATE SEQUENCE IF NOT EXISTS odds.seq_line     START 1;

-- One row per API pull. phase captures the NFL line-movement structure:
-- opener (Sun night/Mon) -> post_wed (first injury report) -> post_fri
-- (final report) -> close. Credits tracked so budget is observable.
CREATE TABLE IF NOT EXISTS odds.snapshot (
    snapshot_id     BIGINT PRIMARY KEY DEFAULT nextval('odds.seq_snapshot'),
    captured_at     TIMESTAMPTZ NOT NULL,
    source          VARCHAR NOT NULL,      -- 'the_odds_api' | 'manual_unabated' | ...
    season          INTEGER NOT NULL,
    week            INTEGER,
    phase           VARCHAR NOT NULL,      -- opener | post_wed | post_fri | close | adhoc
    markets_pulled  VARCHAR,               -- comma list, as requested
    credits_used    INTEGER,
    credits_left    INTEGER,
    http_status     INTEGER,
    note            VARCHAR
);

-- Resolution of external game identifiers to the nflverse game_id spine.
-- Odds feeds have their own ids; this is where they get mapped, with the
-- fallback match keys retained so a bad match is diagnosable.
CREATE TABLE IF NOT EXISTS odds.game_map (
    source            VARCHAR NOT NULL,
    source_game_id    VARCHAR NOT NULL,
    game_id           VARCHAR,             -- nflverse '2026_01_KC_BAL'; NULL = UNRESOLVED
    commence_time     TIMESTAMPTZ,
    source_home_raw   VARCHAR,
    source_away_raw   VARCHAR,
    match_method      VARCHAR,             -- id | teams_date | fuzzy | manual | unresolved
    resolved_at       TIMESTAMPTZ,
    PRIMARY KEY (source, source_game_id)
);

-- The odds path itself. Tall format so sides, totals, team totals and player
-- props all live in one table and one scanner reads all of them.
-- APPEND ONLY.
CREATE TABLE IF NOT EXISTS odds.line (
    line_id         BIGINT PRIMARY KEY DEFAULT nextval('odds.seq_line'),
    snapshot_id     BIGINT NOT NULL REFERENCES odds.snapshot(snapshot_id),
    game_id         VARCHAR,               -- resolved; NULL until game_map resolves
    source_game_id  VARCHAR,
    book_id         VARCHAR,               -- -> ref.book(book_id)   [audit-enforced]
    market_id       VARCHAR,               -- -> ref.market(market_id) [audit-enforced]
    side_key        VARCHAR NOT NULL,      -- 'home' | 'away' | 'over' | 'under' | team_id
    gsis_id         VARCHAR,               -- player props only
    line_value      DOUBLE,                -- spread/total/prop threshold; NULL for ML
    price_american  INTEGER NOT NULL,
    price_decimal   DOUBLE,
    implied_prob    DOUBLE,                -- raw, vig included
    captured_at     TIMESTAMPTZ NOT NULL,  -- denormalized for fast time queries
    is_alt          BOOLEAN NOT NULL DEFAULT FALSE
);


-- ============================================================================
-- MODEL : registry, projections, sims
-- ============================================================================

-- Every number we produce is attributable to a versioned model. CLV is tracked
-- per model_id, so a signal that stops working is visible as a registry row
-- going cold rather than as a vague feeling.
CREATE TABLE IF NOT EXISTS model.registry (
    model_id        VARCHAR PRIMARY KEY,   -- 'proj_epa_v1' | 'sim_empirical_v1'
    model_name      VARCHAR NOT NULL,
    layer           VARCHAR NOT NULL,      -- ratings | projection | sim | market | prop
    version         VARCHAR NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    config_json     VARCHAR,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    is_paper_only   BOOLEAN NOT NULL DEFAULT TRUE,  -- year one: everything paper
    note            VARCHAR
);

-- Game-result projection. The decomposition columns are non-negotiable:
-- when a number is wrong we need to know WHICH input was wrong, not just that
-- the total was off. Contributions are in points and should sum to the spread.
CREATE TABLE IF NOT EXISTS model.projection (
    projection_id   BIGINT PRIMARY KEY,
    model_id        VARCHAR NOT NULL REFERENCES model.registry(model_id),
    game_id         VARCHAR NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL,
    proj_spread     DOUBLE,                -- negative = home favored
    proj_total      DOUBLE,
    proj_home_pts   DOUBLE,
    proj_away_pts   DOUBLE,
    -- decomposition (points attributable to each input)
    c_ratings       DOUBLE,
    c_hfa           DOUBLE,
    c_rest          DOUBLE,
    c_travel        DOUBLE,
    c_weather       DOUBLE,
    c_injury        DOUBLE,
    c_qb            DOUBLE,
    c_other         DOUBLE,
    inputs_json     VARCHAR
);

-- Simulation runs. Anchored sims record which market line they were fed,
-- which is what makes the null test reproducible.
CREATE TABLE IF NOT EXISTS model.sim_run (
    sim_id          BIGINT PRIMARY KEY,
    model_id        VARCHAR NOT NULL REFERENCES model.registry(model_id),
    game_id         VARCHAR NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL,
    n_sims          INTEGER NOT NULL,
    anchor_spread   DOUBLE,                -- market no-vig spread fed in
    anchor_total    DOUBLE,
    anchor_source   VARCHAR,               -- which book/consensus
    inputs_json     VARCHAR
);

-- Sim output as fair prices, one row per priceable outcome. This is what the
-- EV scanner joins against odds.line.
CREATE TABLE IF NOT EXISTS model.sim_fair_price (
    sim_id          BIGINT NOT NULL REFERENCES model.sim_run(sim_id),
    market_id       VARCHAR NOT NULL,      -- -> ref.market(market_id) [audit-enforced]
    side_key        VARCHAR NOT NULL,
    gsis_id         VARCHAR,
    line_value      DOUBLE,
    fair_prob       DOUBLE NOT NULL,
    fair_price_am   INTEGER,
    mc_stderr       DOUBLE,                -- sim noise, so we don't chase it
    PRIMARY KEY (sim_id, market_id, side_key, line_value, gsis_id)
);

-- Usage-based player projections (prop engine). Distributional, not point —
-- a prop fair price needs a distribution, not a mean.
CREATE TABLE IF NOT EXISTS model.player_projection (
    projection_id   BIGINT PRIMARY KEY,
    model_id        VARCHAR NOT NULL REFERENCES model.registry(model_id),
    game_id         VARCHAR NOT NULL,
    gsis_id         VARCHAR NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL,
    stat_key        VARCHAR NOT NULL,      -- 'rec_yds' | 'rush_att' | ...
    proj_mean       DOUBLE,
    proj_sd         DOUBLE,
    dist_family     VARCHAR,               -- normal | negbin | gamma | empirical
    dist_params_json VARCHAR,
    -- usage inputs kept alongside output so a miss is diagnosable
    exp_snap_pct    DOUBLE,
    exp_route_pct   DOUBLE,
    exp_target_share DOUBLE,
    exp_ay_share    DOUBLE,
    exp_rz_share    DOUBLE,
    inputs_json     VARCHAR
);

-- Null-test results. A sim that cannot reproduce the market's own derived
-- prices when fed the market's own inputs has no business flagging edges.
CREATE TABLE IF NOT EXISTS model.calibration_test (
    test_id         BIGINT PRIMARY KEY,
    sim_id          BIGINT NOT NULL REFERENCES model.sim_run(sim_id),
    market_id       VARCHAR NOT NULL,
    side_key        VARCHAR,
    line_value      DOUBLE,
    market_novig_prob DOUBLE NOT NULL,
    sim_prob        DOUBLE NOT NULL,
    abs_error       DOUBLE,
    passed          BOOLEAN,
    tolerance       DOUBLE,
    tested_at       TIMESTAMPTZ NOT NULL
);


-- ============================================================================
-- BET : log + CLV grading (staking deferred — stake is nullable)
-- ============================================================================

CREATE SEQUENCE IF NOT EXISTS bet.seq_wager START 1;

CREATE TABLE IF NOT EXISTS bet.wager (
    bet_id          BIGINT PRIMARY KEY DEFAULT nextval('bet.seq_wager'),
    placed_at       TIMESTAMPTZ NOT NULL,
    is_paper        BOOLEAN NOT NULL DEFAULT TRUE,   -- year one default
    game_id         VARCHAR NOT NULL,
    market_id       VARCHAR NOT NULL,      -- -> ref.market(market_id)     [audit-enforced]
    side_key        VARCHAR NOT NULL,
    gsis_id         VARCHAR,
    book_id         VARCHAR NOT NULL,      -- -> ref.book(book_id)         [audit-enforced]
    line_value      DOUBLE,
    price_american  INTEGER NOT NULL,
    stake           DOUBLE,                -- NULL while staking is deferred
    model_id        VARCHAR,               -- -> model.registry(model_id)  [audit-enforced]
    fair_prob       DOUBLE,                -- our number at placement time
    ev_pct          DOUBLE,
    snapshot_id     BIGINT,                -- -> odds.snapshot(snapshot_id)[audit-enforced]
    note            VARCHAR
);

-- CLV is ground truth. Graded against the sharp close, not the book we used.
CREATE TABLE IF NOT EXISTS bet.clv (
    bet_id          BIGINT PRIMARY KEY REFERENCES bet.wager(bet_id),
    close_line      DOUBLE,
    close_price_am  INTEGER,
    close_book_id   VARCHAR,               -- -> ref.book(book_id) [audit-enforced]
    close_novig_prob DOUBLE,
    clv_line_pts    DOUBLE,                -- line points gained (+ = good)
    clv_prob_delta  DOUBLE,                -- fair_prob_at_close - implied_at_bet
    clv_cents       DOUBLE,
    beat_close      BOOLEAN,
    graded_at       TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS bet.result (
    bet_id          BIGINT PRIMARY KEY REFERENCES bet.wager(bet_id),
    outcome         VARCHAR NOT NULL,      -- win | loss | push | void
    settled_at      TIMESTAMPTZ NOT NULL,
    actual_value    DOUBLE,                -- realized stat/margin, for diagnosis
    pnl             DOUBLE                 -- NULL while stake is NULL
);


-- ============================================================================
-- SEED : books (encodes Louisiana access) + core markets
-- ============================================================================

INSERT INTO ref.book (book_id, book_name, is_placeable, is_sharp, clv_priority, note) VALUES
    ('bet365',    'bet365',      TRUE,  FALSE, NULL, 'placeable LA'),
    ('draftkings','DraftKings',  TRUE,  FALSE, NULL, 'placeable LA'),
    ('fanduel',   'FanDuel',     TRUE,  FALSE, NULL, 'placeable LA'),
    ('betmgm',    'BetMGM',      TRUE,  FALSE, NULL, 'placeable LA'),
    ('caesars',   'Caesars',     TRUE,  FALSE, NULL, 'placeable LA'),
    ('fanatics',  'Fanatics',    TRUE,  FALSE, NULL, 'placeable LA'),
    ('pinnacle',  'Pinnacle',    FALSE, TRUE,  1,    'CLV truth, NEVER placeable'),
    ('circa',     'Circa',       FALSE, TRUE,  2,    'openers/lookaheads, not placeable in LA'),
    ('consensus', 'No-vig consensus', FALSE, TRUE, 3, 'derived anchor, not a book')
ON CONFLICT DO NOTHING;

INSERT INTO ref.market (market_id, market_name, family, period, is_player, stat_key, is_alt) VALUES
    ('spread_game',     'Point Spread',         'side',       'game', FALSE, NULL, FALSE),
    ('total_game',      'Game Total',           'total',      'game', FALSE, NULL, FALSE),
    ('ml_game',         'Moneyline',            'moneyline',  'game', FALSE, NULL, FALSE),
    ('team_total',      'Team Total',           'team_total', 'game', FALSE, NULL, FALSE),
    ('spread_1h',       '1H Point Spread',      'derivative', '1H',   FALSE, NULL, FALSE),
    ('total_1h',        '1H Total',             'derivative', '1H',   FALSE, NULL, FALSE),
    ('spread_alt',      'Alternate Spread',     'side',       'game', FALSE, NULL, TRUE),
    ('total_alt',       'Alternate Total',      'total',      'game', FALSE, NULL, TRUE),
    ('player_pass_yds', 'Player Passing Yards', 'player_prop','game', TRUE,  'pass_yds', FALSE),
    ('player_rush_yds', 'Player Rushing Yards', 'player_prop','game', TRUE,  'rush_yds', FALSE),
    ('player_rec_yds',  'Player Receiving Yards','player_prop','game',TRUE,  'rec_yds',  FALSE),
    ('player_receptions','Player Receptions',   'player_prop','game', TRUE,  'receptions', FALSE),
    ('player_pass_tds', 'Player Passing TDs',   'player_prop','game', TRUE,  'pass_tds', FALSE),
    ('player_anytime_td','Anytime Touchdown',   'player_prop','game', TRUE,  'anytime_td', FALSE)
ON CONFLICT DO NOTHING;
