-- ============================================================================
-- SportsGameOdds integration.
--
-- ROLE SPLIT. SGO is subscribed for other leagues, so its NFL marginal cost is
-- zero. It therefore becomes the PLACEABLE BOARD - DraftKings, FanDuel,
-- BetMGM and Caesars, the last of which The Odds API cannot see at any tier.
-- The Odds API drops to PINNACLE ONLY (regions=eu, 3 markets = 3 credits per
-- snapshot, ~52/month), which fits inside its FREE tier. Net new spend: none.
--
-- MEASURED TRAPS THIS SCHEMA DEFENDS AGAINST
--   * SGO silently ignores `season` and `week`. Only startsAfter/startsBefore
--     are honoured, so every capture is date-filtered and startsAt is asserted.
--   * SGO stores the LAST-SEEN odds state, which for a played game is an
--     IN-PLAY line (98% of sampled 2025 history was updated after kickoff).
--     book_last_updated is therefore captured on every row so a quote can be
--     proven pregame rather than assumed.
-- ============================================================================

-- statEntityID: 'all' (game), 'home'/'away' (team), or an SGO playerID.
-- Without this, a game total and a team total collapse to the same key.
ALTER TABLE odds.line ADD COLUMN IF NOT EXISTS entity_key VARCHAR;

-- The book's own last-update timestamp, distinct from our captured_at.
-- captured_at proves when WE looked; this proves how fresh the quote was.
ALTER TABLE odds.line ADD COLUMN IF NOT EXISTS book_last_updated VARCHAR;

-- Kickoff, denormalized so pregame/in-play can be asserted without a join.
ALTER TABLE odds.line ADD COLUMN IF NOT EXISTS event_starts_at VARCHAR;

-- Markets auto-registered from the {statID, periodID, betTypeID, entity}
-- quadruple rather than hand-enumerated. SGO exposes far more markets than we
-- could sensibly list, and dropping unknown ones would silently lose data.
ALTER TABLE ref.market ADD COLUMN IF NOT EXISTS is_auto BOOLEAN DEFAULT FALSE;
ALTER TABLE ref.market ADD COLUMN IF NOT EXISTS sgo_stat_id VARCHAR;
ALTER TABLE ref.market ADD COLUMN IF NOT EXISTS sgo_bet_type VARCHAR;


-- ---------------------------------------------------------------------------
-- Any quote whose book last updated it at or after kickoff is IN-PLAY and must
-- never reach a pregame model, an EV scan or a CLV grade. This is the guard
-- that the 2025 history evaluation showed is essential: a naive backtest
-- against in-play lines reports correlation 0.905 and "proves" the market
-- omniscient.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW audit.inplay_contamination AS
SELECT
    l.line_id, l.snapshot_id, l.game_id, l.book_id, l.market_id,
    l.event_starts_at, l.book_last_updated,
    date_diff('minute', cast(l.event_starts_at AS TIMESTAMP),
              cast(l.book_last_updated AS TIMESTAMP)) AS mins_after_kickoff
FROM odds.line l
WHERE l.book_last_updated IS NOT NULL
  AND l.event_starts_at IS NOT NULL
  AND cast(l.book_last_updated AS TIMESTAMP)
      >= cast(l.event_starts_at AS TIMESTAMP);

-- Pregame-only view. EVERY downstream consumer should read this, not odds.line.
CREATE OR REPLACE VIEW odds.line_pregame AS
SELECT l.*
FROM odds.line l
WHERE l.book_last_updated IS NULL          -- feed does not report freshness
   OR l.event_starts_at IS NULL
   OR cast(l.book_last_updated AS TIMESTAMP)
      < cast(l.event_starts_at AS TIMESTAMP);


-- ---------------------------------------------------------------------------
-- Books SGO returns that were not already registered. All unplaceable in
-- Louisiana - registered so audit.unmapped_books and audit.orphan_refs stay
-- meaningful rather than drowning in known-irrelevant keys.
-- ---------------------------------------------------------------------------
INSERT INTO ref.book (book_id, book_name, is_placeable, is_sharp, clv_priority, note) VALUES
    ('underdog',          'Underdog Fantasy',   FALSE, FALSE, NULL, 'DFS pick-em, not a sportsbook'),
    ('sugarhouse',        'SugarHouse',         FALSE, FALSE, NULL, 'sgo payload'),
    ('pointsbet',         'PointsBet',          FALSE, FALSE, NULL, 'sgo payload'),
    ('fliff',             'Fliff',              FALSE, FALSE, NULL, 'sweepstakes'),
    ('matchbook',         'Matchbook',          FALSE, FALSE, NULL, 'exchange'),
    ('betway',            'Betway',             FALSE, FALSE, NULL, 'sgo payload'),
    ('sportsbet',         'Sportsbet AU',       FALSE, FALSE, NULL, 'au payload'),
    ('playup',            'PlayUp',             FALSE, FALSE, NULL, 'au payload'),
    ('tab',               'TAB',                FALSE, FALSE, NULL, 'au payload'),
    ('tabtouch',          'TABtouch',           FALSE, FALSE, NULL, 'au payload'),
    ('neds',              'Neds',               FALSE, FALSE, NULL, 'au payload'),
    ('paddypower',        'Paddy Power',        FALSE, FALSE, NULL, 'uk payload'),
    ('betvictor',         'BetVictor',          FALSE, FALSE, NULL, 'uk payload'),
    ('casumo',            'Casumo',             FALSE, FALSE, NULL, 'uk payload'),
    ('grosvenor',         'Grosvenor',          FALSE, FALSE, NULL, 'uk payload'),
    ('virginbet',         'Virgin Bet',         FALSE, FALSE, NULL, 'uk payload'),
    ('livescorebet',      'LiveScore Bet',      FALSE, FALSE, NULL, 'uk payload'),
    ('coral',             'Coral',              FALSE, FALSE, NULL, 'uk payload'),
    ('ladbrokes',         'Ladbrokes',          FALSE, FALSE, NULL, 'uk payload'),
    ('boylesports',       'BoyleSports',        FALSE, FALSE, NULL, 'uk payload'),
    ('williamhill',       'William Hill (UK)',  FALSE, FALSE, NULL, 'UK entity, not Caesars US'),
    ('betfairsportsbook', 'Betfair Sportsbook', FALSE, FALSE, NULL, 'uk payload'),
    ('betrsportsbook',    'betr Sportsbook',    FALSE, FALSE, NULL, 'sgo payload'),
    ('bookmakereu',       'Bookmaker.eu',       FALSE, FALSE, NULL, 'offshore'),
    ('fourwinds',         'Four Winds',         FALSE, FALSE, NULL, 'tribal, sgo payload')
ON CONFLICT DO NOTHING;


-- ---------------------------------------------------------------------------
-- Canonical markets for the SGO quadruples we expect. Anything not listed is
-- auto-registered at load time with is_auto = TRUE.
-- ---------------------------------------------------------------------------
INSERT INTO ref.market
    (market_id, market_name, family, period, is_player, stat_key, is_alt,
     is_auto, sgo_stat_id, sgo_bet_type)
VALUES
    ('team_total_1h',   '1H Team Total',      'team_total', '1H', FALSE, NULL, FALSE, FALSE, 'points', 'ou'),
    ('ml_1h',           '1H Moneyline',       'derivative', '1H', FALSE, NULL, FALSE, FALSE, 'points', 'ml'),
    ('spread_1q',       '1Q Point Spread',    'derivative', '1Q', FALSE, NULL, FALSE, FALSE, 'points', 'sp'),
    ('total_1q',        '1Q Total',           'derivative', '1Q', FALSE, NULL, FALSE, FALSE, 'points', 'ou'),
    ('ml_1q',           '1Q Moneyline',       'derivative', '1Q', FALSE, NULL, FALSE, FALSE, 'points', 'ml'),
    ('spread_2h',       '2H Point Spread',    'derivative', '2H', FALSE, NULL, FALSE, FALSE, 'points', 'sp'),
    ('total_2h',        '2H Total',           'derivative', '2H', FALSE, NULL, FALSE, FALSE, 'points', 'ou'),
    ('player_rush_rec_yds', 'Rush + Rec Yards','player_prop','game', TRUE, 'rush_rec_yds', FALSE, FALSE, 'rushing+receiving_yards', 'ou'),
    ('player_pass_rush_yds','Pass + Rush Yards','player_prop','game', TRUE, 'pass_rush_yds', FALSE, FALSE, 'passing+rushing_yards', 'ou'),
    ('player_rush_att', 'Rushing Attempts',   'player_prop', 'game', TRUE, 'rush_att', FALSE, FALSE, 'rushing_attempts', 'ou'),
    ('player_pass_att', 'Passing Attempts',   'player_prop', 'game', TRUE, 'pass_att', FALSE, FALSE, 'passing_attempts', 'ou'),
    ('player_pass_comp','Passing Completions','player_prop', 'game', TRUE, 'pass_comp', FALSE, FALSE, 'passing_completions', 'ou'),
    ('player_pass_int', 'Passing Interceptions','player_prop','game', TRUE, 'pass_int', FALSE, FALSE, 'passing_interceptions', 'ou'),
    ('player_longest_rec','Longest Reception','player_prop', 'game', TRUE, 'longest_rec', FALSE, FALSE, 'receiving_longestReception', 'ou'),
    ('player_longest_rush','Longest Rush',    'player_prop', 'game', TRUE, 'longest_rush', FALSE, FALSE, 'rushing_longestRush', 'ou'),
    ('player_first_td', 'First Touchdown',    'player_prop', 'game', TRUE, 'first_td', FALSE, FALSE, 'firstTouchdown', 'yn'),
    ('player_tackles',  'Combined Tackles',   'player_prop', 'game', TRUE, 'tackles', FALSE, FALSE, 'defense_combinedTackles', 'ou'),
    ('player_sacks',    'Sacks',              'player_prop', 'game', TRUE, 'sacks', FALSE, FALSE, 'defense_sacks', 'ou')
ON CONFLICT DO NOTHING;

-- Backfill the SGO identifiers onto the markets seeded in 01_schema.sql.
UPDATE ref.market SET sgo_stat_id='points', sgo_bet_type='sp' WHERE market_id='spread_game';
UPDATE ref.market SET sgo_stat_id='points', sgo_bet_type='ou' WHERE market_id='total_game';
UPDATE ref.market SET sgo_stat_id='points', sgo_bet_type='ml' WHERE market_id='ml_game';
UPDATE ref.market SET sgo_stat_id='points', sgo_bet_type='ou' WHERE market_id='team_total';
UPDATE ref.market SET sgo_stat_id='points', sgo_bet_type='sp' WHERE market_id='spread_1h';
UPDATE ref.market SET sgo_stat_id='points', sgo_bet_type='ou' WHERE market_id='total_1h';
UPDATE ref.market SET sgo_stat_id='passing_yards',        sgo_bet_type='ou' WHERE market_id='player_pass_yds';
UPDATE ref.market SET sgo_stat_id='rushing_yards',        sgo_bet_type='ou' WHERE market_id='player_rush_yds';
UPDATE ref.market SET sgo_stat_id='receiving_yards',      sgo_bet_type='ou' WHERE market_id='player_rec_yds';
UPDATE ref.market SET sgo_stat_id='receiving_receptions', sgo_bet_type='ou' WHERE market_id='player_receptions';
UPDATE ref.market SET sgo_stat_id='passing_touchdowns',   sgo_bet_type='ou' WHERE market_id='player_pass_tds';
UPDATE ref.market SET sgo_stat_id='touchdowns',           sgo_bet_type='yn' WHERE market_id='player_anytime_td';
