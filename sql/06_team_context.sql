-- ============================================================================
-- ref.team_context - the 2026 season preview, as structured data.
--
-- WHY THIS MATTERS MORE THAN IT LOOKS. Our team ratings are end-of-2025 and
-- cannot see a single thing that happened since: ten head-coaching changes, the
-- largest quarterback carousel in memory, Myles Garrett to the Rams. The
-- preview's win totals CAN see all of it, because the market priced them in
-- August 2026. That makes them the best available preseason strength prior,
-- and the gap between a win total and our 2025 rating is a measurement of
-- exactly what changed.
--
-- The qualitative fields are here so the report can say WHY a team moved, and
-- so a coaching or quarterback change is visible to anything reading team
-- strength rather than silently absent from it.
--
-- Provenance: 2026 season preview, published 2026-08-16, built on nflverse
-- play-by-play with an adversarial fact-check pass. Prices are DraftKings
-- unless a book split is noted, captured 2026-07-23 to 2026-08-16 - they have
-- moved and must be re-verified before use.
-- ============================================================================

CREATE TABLE IF NOT EXISTS ref.team_context (
    team            VARCHAR PRIMARY KEY,
    season          INTEGER NOT NULL,
    power_rank      INTEGER,
    tier            INTEGER,
    record_2025     VARCHAR,
    their_net_epa   DOUBLE,      -- independently computed; corr 0.947 with ours
    win_total       DOUBLE,
    win_total_alt   DOUBLE,      -- where books split by a full win
    head_coach      VARCHAR,
    coach_is_new    BOOLEAN,
    starting_qb     VARCHAR,
    qb_is_new       BOOLEAN,
    qb_health_flag  VARCHAR,     -- non-null = a known medical question
    context_note    VARCHAR
);

-- qb_is_new MEANS "did not start for THIS team last season". It was wrong on
-- four rows - Stafford, Rodgers, Brissett and Sanders were each their team's
-- QB1 by pass attempts in 2025 and were still flagged as new starters, a 44%
-- error rate on the nine rows that carried the flag. audit.qb_is_new_mismatch
-- now checks every row against who actually threw the passes, so a narrative
-- field cannot quietly contradict the play-by-play again.

DELETE FROM ref.team_context WHERE season = 2026;

INSERT INTO ref.team_context VALUES
('LA' ,2026, 1,1,'12-5', 0.236,11.5,NULL,'Sean McVay',       FALSE,'Matthew Stafford',FALSE,NULL,'Garrett + McDuffie acquired; new OC Scheelhaase; LT and Nacua await league discipline'),
('SEA',2026, 2,1,'14-3', 0.206,10.5,NULL,'Mike Macdonald',   FALSE,'Sam Darnold',    FALSE,NULL,'Champions. Lost OC Kubiak and SB MVP Walker; defense returns whole; first-time OC Fleury'),
('BUF',2026, 3,1,'12-5', 0.116,10.5,NULL,'Joe Brady',        TRUE ,'Josh Allen',     FALSE,NULL,'McDermott fired off 12 wins; 4-3 to 3-4 conversion under Leonhard; DJ Moore in'),
('NE' ,2026, 4,1,'14-3', 0.118,10.5,NULL,'Mike Vrabel',      FALSE,'Drake Maye',     FALSE,NULL,'A.J. Brown acquired from PHI; lost SB LX to Seattle; Gonzalez contract standoff'),
('BAL',2026, 5,1,'8-9',  0.015,11.5,NULL,'Jesse Minter',     TRUE ,'Lamar Jackson',  FALSE,NULL,'18-year Harbaugh era over; two new coordinators; Hendrickson in; AFC-high win total off a losing season'),
('PHI',2026, 6,2,'11-6', 0.101,10.5,NULL,'Nick Sirianni',    FALSE,'Jalen Hurts',    FALSE,NULL,'Traded A.J. Brown, OC out, OL coach retired; elite defense carrying an offense in transition'),
('DEN',2026, 7,2,'14-3', 0.041, 9.5,NULL,'Sean Payton',      FALSE,'Bo Nix',         FALSE,'ankle rehab','14-3 but only 14th by EPA - largest record-vs-process gap in football; Waddle acquired'),
('LAC',2026, 8,2,'11-6', 0.046, 9.5,NULL,'Jim Harbaugh',     FALSE,'Justin Herbert', FALSE,NULL,'Worst pass protection in football rebuilt; Herbert graded 91.0 when kept clean; lost DC Minter'),
('DET',2026, 9,2,'9-8',  0.060,10.5,NULL,'Dan Campbell',     FALSE,'Jared Goff',     FALSE,NULL,'OL dissolved and rebuilt; BOTH starting safeties on PUP into December'),
('CHI',2026,10,2,'12-5', 0.056, 9.5,NULL,'Ben Johnson',      FALSE,'Caleb Williams', FALSE,NULL,'Won division; quietly subtractive offseason; league-toughest schedule (.550)'),
('KC' ,2026,11,2,'6-11', 0.009,10.5,NULL,'Andy Reid',        FALSE,'Patrick Mahomes',FALSE,'ACL/LCL return','First losing season of the Mahomes era; Walker signed; cleared ~7.5 months post-op'),
('JAX',2026,12,2,'13-4', 0.085, 8.5,NULL,'Liam Coen',        FALSE,'Trevor Lawrence',FALSE,NULL,'13-win division champ faded 4.5 wins by the market; staff intact; Hunter healthy'),
('HOU',2026,13,3,'12-5', 0.087, 9.5,NULL,'DeMeco Ryans',     FALSE,'C.J. Stroud',    FALSE,NULL,'Third-best defense; OL rebuilt; org declined to extend Stroud after two years of decline'),
('GB' ,2026,14,3,'9-7-1',0.079, 9.5,NULL,'Matt LaFleur',     FALSE,'Jordan Love',    FALSE,NULL,'Parsons ACL, Gary and Clark gone - September pass rush is a genuine hole'),
('CIN',2026,15,3,'6-11',-0.114,10.5, 9.5,'Zac Taylor',       FALSE,'Joe Burrow',     FALSE,NULL,'2025 broken by Burrow turf toe; Lawrence acquired for a top-10 pick; books split 10.5/9.5'),
('SF' ,2026,16,3,'12-5', 0.061, 9.5,10.5,'Kyle Shanahan',    FALSE,'Brock Purdy',    FALSE,NULL,'Top-5 offense, 25th defense; Pearsall out for year, Kittle PUP; books split 9.5/10.5'),
('DAL',2026,17,3,'7-9-1',-0.066,9.5,NULL,'Brian Schottenheimer',FALSE,'Dak Prescott',FALSE,NULL,'4th offense strapped to the 32nd defense; all-defense offseason; first-time DC'),
('IND',2026,18,3,'8-9',  0.068, 7.5,NULL,'Shane Steichen',   FALSE,'Daniel Jones',   FALSE,'Achilles rehab','8-2 then seven straight losses; Gardner acquired; Steichen on the hot seat'),
('TB' ,2026,19,4,'8-9', -0.001, 8.5,NULL,'Todd Bowles',      FALSE,'Baker Mayfield', FALSE,NULL,'Evans gone, David retired; Vea trade request; contract-year Mayfield in a McVay-tree offense'),
('PIT',2026,20,4,'10-7', 0.023, 8.5, 7.5,'Mike McCarthy',    TRUE ,'Aaron Rodgers',  FALSE,'age 42','First post-Tomlin coach since 2007; Rodgers off his worst-graded season; books split 8.5/7.5'),
('MIN',2026,21,4,'9-8', -0.017, 8.5,NULL,'Kevin OConnell',   FALSE,'Kyler Murray',   TRUE ,NULL,'30th offense, 6th defense; Murray signed at the minimum and named starter over McCarthy'),
('WAS',2026,22,4,'5-12',-0.096, 7.5,NULL,'Dan Quinn',        FALSE,'Jayden Daniels', FALSE,NULL,'2025 destroyed by a twice-dislocated elbow; Diggs added; unconfirmed Tunsil triceps report'),
('NYG',2026,23,4,'4-13',-0.054, 7.5,NULL,'John Harbaugh',    TRUE ,'Jaxson Dart',    FALSE,NULL,'Biggest coaching splash of the offseason; roster remade in Baltimores image; Nabers ACL return'),
('ATL',2026,24,4,'8-9',  0.030, 6.5, 7.5,'Kevin Stefanski',  TRUE ,'Tua Tagovailoa', TRUE ,NULL,'Two-time COY hired, Matt Ryan as President; Tua duels a rehabbing Penix; books split 6.5/7.5'),
('NO' ,2026,25,5,'6-11',-0.079, 7.5, 8.5,'Kellen Moore',     FALSE,'Tyler Shough',   FALSE,NULL,'Shough graded 2025s best rookie QB over a 5-4 finish; Tyson drafted 8th; books split 7.5/8.5'),
('CAR',2026,26,5,'8-9', -0.099, 7.5,NULL,'Dave Canales',     FALSE,'Bryce Young',    FALSE,NULL,'Division winner at 24th by EPA; BOTH starting tackles likely out to open the season'),
('TEN',2026,27,5,'3-14',-0.276, 6.5,NULL,'Robert Saleh',     TRUE ,'Cam Ward',       FALSE,NULL,'Saleh defense plus Daboll offense around a year-2 QB; Tate drafted 4th overall'),
('LV' ,2026,28,5,'3-14',-0.184, 5.5,NULL,'Klint Kubiak',     TRUE ,'Kirk Cousins',   TRUE ,NULL,'Kubiak from the champion Seahawks offense; Mendoza No.1 overall waiting behind Cousins'),
('NYJ',2026,29,5,'3-14',-0.319, 5.5,NULL,'Aaron Glenn',      FALSE,'Geno Smith',     TRUE ,NULL,'Worst process profile in football; a defense that did not intercept a pass all season'),
('CLE',2026,30,5,'5-12',-0.127, 5.5,NULL,'Todd Monken',      TRUE ,'Watson/Sanders', FALSE,'open competition','Traded Myles Garrett; 31st offense and got younger; genuine 50/50 QB derby'),
('MIA',2026,31,5,'7-10',-0.091, 4.5,NULL,'Jeff Hafley',      TRUE ,'Malik Willis',   TRUE ,NULL,'Full teardown - Tua released, Waddle traded, Hill released; Willis has 209 career dropbacks'),
('ARI',2026,32,5,'3-14',-0.062, 3.5,NULL,'Mike LaFleur',     TRUE ,'Jacoby Brissett',FALSE,NULL,'22nd by EPA, not 31st - Harrison/McBride core is real; Love drafted 3rd overall');


-- ---------------------------------------------------------------------------
-- Does the narrative field agree with who actually took the snaps?
--
-- Compare against EVERY passer with real volume, not just QB1. An injured
-- incumbent is not a new starter: Purdy threw 298 for SF behind Mac Jones's
-- 304, and Daniels 205 for WAS behind Mariota's 244 - a QB1-only test called
-- both of them new and would have been ignored as noise within a week.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW audit.qb_is_new_mismatch AS
WITH att AS (
    SELECT posteam AS team, passer_player_name AS qb, count(*) AS att
    FROM raw.pbp
    WHERE season = 2025 AND season_type = 'REG' AND pass = 1
      AND passer_player_name IS NOT NULL
    GROUP BY 1, 2
    HAVING count(*) >= 50          -- a real workload, not a trick play
),
matched AS (
    SELECT c.team, c.starting_qb, c.qb_is_new,
           max(CASE WHEN lower(split_part(a.qb, '.', -1))
                       = lower(regexp_extract(c.starting_qb, '([A-Za-z]+)$', 1))
                    THEN a.att END) AS att_2025
    FROM ref.team_context c
    LEFT JOIN att a ON a.team = c.team
    WHERE c.season = 2026
    GROUP BY 1, 2, 3
)
SELECT team, starting_qb, qb_is_new, att_2025,
       CASE WHEN qb_is_new THEN 'flagged new but threw ' || att_2025
                                || ' passes here in 2025'
            ELSE 'not flagged new but has no 2025 workload here' END AS problem
FROM matched
WHERE (qb_is_new AND att_2025 IS NOT NULL)
   OR (NOT qb_is_new AND att_2025 IS NULL);
