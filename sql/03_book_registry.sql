-- ============================================================================
-- Book registry, reconciled against a LIVE Odds API response (2026-08-16).
-- Idempotent - safe to re-run.
--
-- HARD FINDING: of the six Louisiana-placeable books, The Odds API carries
-- only DraftKings, FanDuel and BetMGM. bet365, Caesars and Fanatics are absent
-- from every region probed (us, us2, us_ex, uk, eu, au). Those three stay in
-- ref.book as placeable so the access rule remains true, but they will never
-- receive prices from this feed - line shopping is effectively halved and that
-- gap must be closed manually or by a second feed.
--
-- GOOD FINDING: Pinnacle IS available via the `eu` region. The sharp CLV
-- anchor therefore costs nothing extra and SportsGameOdds ($99/mo) is not
-- needed for grading.
-- ============================================================================

-- Books discovered live that were not previously registered.
-- is_placeable is set from Louisiana access, NOT from feed availability.
INSERT INTO ref.book (book_id, book_name, is_placeable, is_sharp, clv_priority, note) VALUES
    -- US, not placeable in LA (confirmed dead or never available)
    ('betrivers',      'BetRivers',        FALSE, FALSE, NULL, 'NOT available in LA'),
    ('espnbet',        'theScore Bet',     FALSE, FALSE, NULL, 'ex-ESPN Bet; NOT available in LA'),
    ('ballybet',       'Bally Bet',        FALSE, FALSE, NULL, 'NOT available in LA'),
    ('betparx',        'betPARX',          FALSE, FALSE, NULL, 'not in LA book set'),
    ('hardrockbet',    'Hard Rock Bet',    FALSE, FALSE, NULL, 'not in LA book set'),
    ('hardrockbet_fl', 'Hard Rock Bet FL', FALSE, FALSE, NULL, 'state variant'),
    ('hardrockbet_oh', 'Hard Rock Bet OH', FALSE, FALSE, NULL, 'state variant'),
    -- offshore: never placeable, never a price we can take
    ('betus',          'BetUS',            FALSE, FALSE, NULL, 'offshore'),
    ('bovada',         'Bovada',           FALSE, FALSE, NULL, 'offshore'),
    ('betonlineag',    'BetOnline.ag',     FALSE, FALSE, NULL, 'offshore'),
    ('lowvig',         'LowVig.ag',        FALSE, FALSE, NULL, 'offshore'),
    ('mybookieag',     'MyBookie.ag',      FALSE, FALSE, NULL, 'offshore'),
    -- exchanges / prediction markets (us_ex): reference only
    ('novig',          'Novig',            FALSE, FALSE, NULL, 'exchange, us_ex'),
    ('kalshi',         'Kalshi',           FALSE, FALSE, NULL, 'prediction market, us_ex'),
    ('polymarket',     'Polymarket',       FALSE, FALSE, NULL, 'prediction market, us_ex'),
    -- eu region: pulled only to reach Pinnacle, but they arrive in the payload
    ('betsson',        'Betsson',          FALSE, FALSE, NULL, 'eu payload'),
    ('coolbet',        'Coolbet',          FALSE, FALSE, NULL, 'eu payload'),
    ('gtbets',         'GTbets',           FALSE, FALSE, NULL, 'eu payload'),
    ('leovegas_se',    'LeoVegas SE',      FALSE, FALSE, NULL, 'eu payload'),
    ('nordicbet',      'NordicBet',        FALSE, FALSE, NULL, 'eu payload'),
    ('onexbet',        '1xBet',            FALSE, FALSE, NULL, 'eu payload'),
    ('tipico_de',      'Tipico DE',        FALSE, FALSE, NULL, 'eu payload'),
    ('unibet_nl',      'Unibet NL',        FALSE, FALSE, NULL, 'eu payload'),
    ('unibet_se',      'Unibet SE',        FALSE, FALSE, NULL, 'eu payload'),
    -- surfaced by audit.unmapped_books on the first live snapshot
    ('betclic_fr',     'Betclic FR',       FALSE, FALSE, NULL, 'eu payload'),
    ('betfair_ex_eu',  'Betfair Exchange EU', FALSE, FALSE, NULL, 'eu exchange; h2h_lay ignored'),
    ('marathonbet',    'Marathon Bet',     FALSE, FALSE, NULL, 'eu payload')
ON CONFLICT DO NOTHING;

-- Flag the placeable-but-unreachable books so this is discoverable in SQL and
-- not only in a runbook paragraph.
UPDATE ref.book
   SET note = 'placeable in LA but ABSENT from The Odds API (all regions, 2026-08-16)'
 WHERE book_id IN ('bet365', 'caesars', 'fanatics');

UPDATE ref.book
   SET note = 'sharp CLV anchor - available via eu region'
 WHERE book_id = 'pinnacle';

UPDATE ref.book
   SET note = 'sharp openers - NOT in The Odds API; manual entry only'
 WHERE book_id = 'circa';
