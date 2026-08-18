-- ============================================================================
-- ref.analyst_take - Move The Line's 2026 positions, extracted from transcript.
--
-- WHAT THIS IS. Every explicit position taken across the ten August 2026 shows:
-- what they bet, what they LEANED but did not bet, and - most valuable - what
-- they deliberately PASSED on and why. The passes matter as much as the plays.
-- A sharp saying "I like this team but I won't bet the number until week five"
-- is a market-timing claim, and market-timing claims are testable.
--
-- WHAT THIS IS NOT. Not the user's opinion, not ground truth, and not a signal
-- to follow. It is a second opinion from a source with a different method than
-- ours: they read camp reports, coordinator trees and depth charts; we read
-- play-by-play. Where we agree, confidence goes up. Where we disagree, one of
-- us is looking at something the other cannot see, and that gap is the work.
--
-- HOW TO USE IT HONESTLY.
--   * Prices are as quoted on air, 2026-07-29 to 2026-08-12. They have moved.
--     Never place off a price in this table; re-pull the board first.
--   * `stance` separates conviction levels that a summary would flatten:
--     bet > lean > watch > pass > fade. Only 'bet' means they claim to have
--     money down.
--   * `status` tracks revision. The 2026-08-12 injury episode explicitly
--     killed some earlier theses; those rows are 'revised', not deleted,
--     because a reversed opinion is evidence about the source's process.
--   * Nothing here has been validated against results, because none of it
--     can be until Week 1. Grade it forward through bet_log, same as ours.
--
-- Provenance: ref.transcript, Move The Line (4for4), Connor Allen & Ryan Noonan.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS ref;

CREATE TABLE IF NOT EXISTS ref.analyst_take (
    take_id      INTEGER PRIMARY KEY,
    source       VARCHAR  NOT NULL,     -- 'move_the_line'
    published    DATE     NOT NULL,
    video_id     VARCHAR,               -- joins ref.transcript
    team         VARCHAR,               -- null for league-wide structural takes
    market       VARCHAR  NOT NULL,     -- win_total|division|playoffs|superbowl|
                                        -- spread|prop|award|structural
    selection    VARCHAR,               -- over|under|yes|team|player + side
    line         DOUBLE,
    price        VARCHAR,               -- as quoted on air; American odds
    stance       VARCHAR  NOT NULL,     -- bet|lean|watch|pass|fade
    conviction   INTEGER,               -- 1 low .. 3 flag-plant
    thesis       VARCHAR  NOT NULL,
    timing_note  VARCHAR,               -- their own stated entry plan, if any
    status       VARCHAR DEFAULT 'live' -- live|revised|dead
);

DELETE FROM ref.analyst_take WHERE source = 'move_the_line';

INSERT INTO ref.analyst_take VALUES
-- ---------------------------------------------------------------- AFC East --
( 1,'move_the_line','2026-07-30','mzm7k38Qb20','MIA','win_total','under',4.5,NULL,'bet',3,
  'Worst roster in football; WR room is four WR4s; Malik Willis has 209 career dropbacks behind a bad line. To go over they must sweep the Jets.',
  'Already played and logged before the show aired; number had moved down.','live'),
( 2,'move_the_line','2026-07-30','mzm7k38Qb20','MIA','win_total','under',2.5,'+200s','lean',1,
  'Alt under as a fun swing; floor is unbelievably low.',NULL,'live'),
( 3,'move_the_line','2026-07-30','mzm7k38Qb20','BUF','win_total',NULL,10.5,NULL,'pass',2,
  'New HC, new OC, 4-3 to 3-4 conversion, and the hardest opening five (at HOU, DET, LAC, NE, at LA) plus the hardest unique-three in the league. Defense lost talent off a unit that was 31st in EPA against the run on an easy schedule.',
  'Explicit: reassess after the Rams game in week five. A 2-3 or 3-2 start buys a better number than 10.5.','live'),
( 4,'move_the_line','2026-07-30','mzm7k38Qb20','NE','division','NE',NULL,'+125','lean',2,
  'Better than Buffalo on both sides; continuity everywhere, AJ Brown in, OL leveled up with Vera-Tucker. Prefer +125 on the Pats to laying -130 on the Bills for what is closer to a coin flip.',
  NULL,'live'),
( 5,'move_the_line','2026-07-30','mzm7k38Qb20','NE','spread','NE',3.5,NULL,'bet',2,
  'Week 1 at Seattle. Seahawks minus the number will be the squarest side of the year; no Kenneth Walker, Seattle secondary assimilating new pieces, Pats match up better than the Super Bowl scoreline suggests.',
  'Bet early expecting the line to run to 4/4.5 on public money.','live'),
( 6,'move_the_line','2026-07-30','mzm7k38Qb20','NYJ','win_total','over',5.5,NULL,'lean',1,
  'Roster is better than the record suggests - Gino behind a real OL, Wilson/Hall/Taylor, improved DL. But the coaching staff may be the worst in the league.',
  'Not dying about it; no money down.','live'),
( 7,'move_the_line','2026-07-30','mzm7k38Qb20','LV','spread','LV',3.5,NULL,'lean',2,
  'Week 1 vs Miami. Raiders should pound Jeanty behind a better line against a team that cannot move the ball.',NULL,'live'),
( 8,'move_the_line','2026-07-30','mzm7k38Qb20','BUF','award','Caleb Elams DROY',NULL,'+20000','lean',1,
  'TCU LB, 4th round, efficient tackler with a path to snaps. Longshot; also want his tackle props once books post them.',
  'Books will not post his tackle props early - may need an exchange.','live'),
( 9,'move_the_line','2026-07-30','mzm7k38Qb20','MIA','prop','Malik Willis rushing over',NULL,NULL,'lean',2,
  'Repeatable pattern from 2025: a scoreless first drive drops his rushing line to ~24, then one scramble puts him at 60-70. Buy the deflated in-game number.',
  'In-season live angle, not a preseason bet.','live'),
(10,'move_the_line','2026-07-30','mzm7k38Qb20','MIA','prop','Tutu Atwell under',1.5,NULL,'lean',1,
  'WR4 body in a WR1 role; receptions under and low-yardage unders on the whole Miami pass-catching group.',NULL,'live'),

-- --------------------------------------------------------------- AFC North --
(11,'move_the_line','2026-07-31','MUIROsxWpnU','PIT','division','PIT',NULL,'+550','bet',3,
  'Best team-level price on the board. Roster grades above an 8.5 win total WHILE being graded at league-worst QB play alongside NYJ/MIA/CLE/LV - Rodgers will beat that bar. Defense is deep and healthy-graded: Watt/Highsmith/Herbig/Sawyer, Ramsey back to nickel, Brisker and Elliott at safety.',
  'Favored in five of eight before the bye; post-bye slate is brutal. The price is a bet on the first half of the season.','live'),
(12,'move_the_line','2026-07-31','MUIROsxWpnU','PIT','spread','PIT',-3.0,NULL,'lean',2,
  'Week 1 at home vs Atlanta, laying three. Falcons have Penix rehabbing and Tua not practicing, both in a brand-new system.',NULL,'live'),
(13,'move_the_line','2026-07-31','MUIROsxWpnU','CLE','win_total','under',5.5,NULL,'bet',3,
  'Under or nothing. Wholesale OL turnover in front of two QBs who take sacks at a high rate; Garrett gone; second-worst roster in the model, only 1.5 points better than Miami.',
  'Schedule is fourth easiest, which is the only thing holding the number up.','live'),
(14,'move_the_line','2026-07-31','MUIROsxWpnU','CLE','prop','game unders / team total unders',NULL,NULL,'lean',2,
  'New play caller plus an inexperienced OL plus a run-first intent plus a defense that holds up - unders on every axis, especially the first month.',
  'Pairs with the league-wide OC-turnover under trend (take 60).','live'),
(15,'move_the_line','2026-07-31','MUIROsxWpnU','BAL','playoffs',NULL,NULL,'-390','pass',2,
  'Deserved favorites but will not lay the juice. Harbaugh gone after 18 years, a 29-year-old first-time play caller in Doyle, and last year proved the downside is real.',
  'Prefer to attack week to week rather than pay the freight in futures.','live'),
(16,'move_the_line','2026-07-31','MUIROsxWpnU','CIN','superbowl','CIN',NULL,'+2200','lean',1,
  'Ceiling-outcome pricing only. Soft schedule (eight of the bottom ten teams), Burrow health is the entire handicap - 2025 point differential was +40 with him and -117 without.',
  'Bet the ceiling via alts and futures, not the win total.','live'),

-- --------------------------------------------------------------- AFC South --
(17,'move_the_line','2026-08-03','HMaGaPsYU8E','HOU','win_total','over',9.5,NULL,'bet',2,
  'Best corner trio and arguably best edge duo in the league - year-over-year stats are not sticky but talent is. Healthy Nico/Dell/Metchie plus Montgomery solving short yardage. This is an 11-win team.',
  'Also likes over 10.5 at plus money as the better-priced version.','live'),
(18,'move_the_line','2026-08-03','HMaGaPsYU8E','HOU','spread','HOU',-2.5,NULL,'lean',2,
  'Week 2 vs Cincinnati. Texans defense is a bad matchup for the Bengals and their offense can expose that Cincy middle-of-field coverage. No play on the week 1 Buffalo game.',NULL,'live'),
(19,'move_the_line','2026-08-03','HMaGaPsYU8E','IND','division','IND',NULL,'+380','bet',3,
  'The value bet of the division. Colts belong just below Houston, not next to Tennessee where the market has them. Offense was humming pre-Jones-injury; defense materially better with Sauce, Walley back at nickel, Halsey at safety, healthy Buckner.',
  'Opening at BAL, at KC, vs HOU could easily be 0-3. That is the buying period, not a reason to fade.','live'),
(20,'move_the_line','2026-08-03','HMaGaPsYU8E','IND','win_total','over',7.5,NULL,'lean',2,
  'Should win eight-plus comfortably if Jones is right; number may already be moving to 8.5.',NULL,'live'),
(21,'move_the_line','2026-08-03','HMaGaPsYU8E','TEN','win_total','under',7.5,NULL,'watch',2,
  'Profile screams year-two leap - Saleh, Daboll, Ward, Tate, Wan Dale - but the offensive line grades worst or third-worst in the league and camp reports are awful. Ceiling is about seven wins against a 6.5 number.',
  'The stated structure: let Tennessee beat the Jets in week 1, let the number inflate on it, then take under 7.5 in-season.','live'),
(22,'move_the_line','2026-08-03','HMaGaPsYU8E','JAX','win_total',NULL,9.5,NULL,'pass',1,
  'Bullish on the offense - Parker Washington ascending, Etienne healthy, Hunter usage - but 2025 turnover variance ran hard in their favor and the London back-to-back is a tough draw. An over-team without a win-total bet.',NULL,'live'),

-- ---------------------------------------------------------------- AFC West --
(23,'move_the_line','2026-08-04','G98I593Hg8o','DEN','division','DEN',NULL,'+225','bet',3,
  'Flag plant. Best football team in the division. Running it back on the first and second level of both sides plus Waddle, who pushes Sutton to WR2 where he belongs. The models ding them for exceeding their win total every year - the alternative reading is that the number keeps being wrong.',
  'Rams/SF/LAC/SEA stretch could leave them 3-3 or 3-4 through week seven, then the schedule opens into ARI/CAR/LV x2/MIA/NYJ. Buy the dip.','live'),
(24,'move_the_line','2026-08-04','G98I593Hg8o','DEN','win_total','over',9.5,NULL,'lean',2,
  'Same thesis as the division price; both are fine, division is the better price.',NULL,'live'),
(25,'move_the_line','2026-08-04','G98I593Hg8o','DEN','superbowl','DEN AFC rep',NULL,'+1000','lean',2,
  'Should have been there last year with a healthy Bo Nix.',NULL,'live'),
(26,'move_the_line','2026-08-04','G98I593Hg8o','LV','win_total','under',6.5,NULL,'bet',2,
  'Not a seven-win team. Cousins has had two good games in four years; WR room is dusty behind Bowers; six games against the division behemoths.',
  'Prefers plus money on under 5.5 to laying juice on 6.5. Week 16-18 (TEN, ARI, resting KC) is the sweat.','live'),
(27,'move_the_line','2026-08-04','G98I593Hg8o','KC','win_total',NULL,10.5,NULL,'pass',2,
  '10.5 feels optimistic and the WR room is thin behind Rice and Worthy, but this is never a train to run in front of. Softer schedule from finishing third.',NULL,'live'),
(28,'move_the_line','2026-08-04','G98I593Hg8o','KC','prop','Mahomes attempts under / Walker attempts over',NULL,NULL,'lean',2,
  'Weeks 1-4 into a week five bye is the perfect setup to ease Mahomes back post-ACL and jam Kenneth Walker. Also reception unders for the receivers outside Rice.',NULL,'live'),
(29,'move_the_line','2026-08-04','G98I593Hg8o','LAC','playoffs',NULL,NULL,'-170','pass',2,
  'Lot to like - McDaniel over Roman is a real upgrade, Alt and Slater back - but will not lay that price, and the travel and rest schedule is brutal.',NULL,'live'),
(30,'move_the_line','2026-08-04','G98I593Hg8o','LV','prop','Brock Bowers alts / OPOY',NULL,NULL,'lean',2,
  'Only real target on the roster; 170-target paths exist even on a bad team.',NULL,'live'),

-- ---------------------------------------------------------------- NFC East --
(31,'move_the_line','2026-08-05','LtvaMVax6Xc','WAS','win_total','over',7.5,NULL,'watch',2,
  'Jayden Daniels can carry a team, Diggs signed, defense materially rebuilt and better than market. But the opening nine (at PHI, at DAL, SEA, IND, at SF, bye, PHI, LA) is one of the toughest in the league - underdogs in all but two.',
  'Explicit plan: do not bet preseason. Revisit week 3-4 and try to buy 6.5 in-season.','revised'),
(32,'move_the_line','2026-08-05','LtvaMVax6Xc','PHI','award','Jonathan Greenard sack leader',NULL,'+9000','watch',2,
  'Projections flag it as one of the better values on the board at 90-1. But he is dealing with a pec injury in a new system.',
  'Explicitly NOT a bet yet - watch the market, it will not take money, and the price should hold until he is cleared.','revised'),
(33,'move_the_line','2026-08-05','LtvaMVax6Xc','PHI','division',NULL,NULL,'+115','pass',2,
  'Cleanest roster in the division, top-three OL, elite Fangio defense, deserved favorite. Just not chasing a barely-plus-money divisional favorite.',NULL,'live'),
(34,'move_the_line','2026-08-05','LtvaMVax6Xc','DAL','award','Schottenheimer COY',NULL,'+1500','lean',2,
  'Better route to the Cowboys ceiling than Dak MVP at 13-1, which needs about 14 wins. COY gets there on 12.',NULL,'live'),
(35,'move_the_line','2026-08-05','LtvaMVax6Xc','DAL','award','most regular season wins',NULL,'+3000','lean',1,
  'Ceiling exploration. Seven new defensive starters; if the defense is merely 20th they win 10-plus with that offense.',
  'Warns Dallas markets carry a public-sentiment tax - 25-1 to win the Super Bowl is already juiced by habitual Dallas money.','live'),
(36,'move_the_line','2026-08-05','LtvaMVax6Xc','DAL','award','Jay Bahrum DROY',NULL,'+20000','lean',1,
  'Taking first-team reps next to Overshown two days running on a national-TV team. Same shape as the Caleb Elams longshot.',NULL,'live'),
(37,'move_the_line','2026-08-05','LtvaMVax6Xc','NYG','win_total','under',7.5,NULL,'lean',1,
  'Eight wins is a bridge too far. Offensive line does not project well, WR room is Nabers plus nothing, Dart is fun but unproven.',
  'Sweaty - they play TEN, ARI, CLE, so six or seven wins is very live.','live'),
(38,'move_the_line','2026-08-05','LtvaMVax6Xc','NYG','prop','Abdul Carter sacks over',NULL,NULL,'watch',2,
  'Four sacks last year but an expected-sack number near ten; pressure rate and win rate were elite. Sacks are the noisy stat, pressure is the sticky one.',
  'Waiting for the market to post.','live'),

-- --------------------------------------------------------------- NFC North --
(39,'move_the_line','2026-08-06','jXqrDUbQ22I','DET','award','most regular season wins',NULL,NULL,'bet',3,
  'By far the easiest schedule in the league - JAX/NYJ/ARI/MIA/ATL/TEN/NYG plus a division where they are favored or coin-flip in every game. Top-five roster even with a genuinely bad secondary.',
  'Bet the ceiling markets (most wins, NFC #1 seed), NOT the 10.5 win total. Also fine as a -220 make-playoffs parlay leg.','live'),
(40,'move_the_line','2026-08-06','jXqrDUbQ22I','DET','prop','game overs / passing overs',NULL,NULL,'lean',2,
  'Secondary is bad enough that this is a volume-and-points team on both sides early. Bears-Lions specifically flagged as a shootout.',NULL,'live'),
(41,'move_the_line','2026-08-06','jXqrDUbQ22I','GB','win_total','under',9.5,NULL,'lean',2,
  'Thin everywhere. Jordan Morgan at LT with 51 career snaps, worst-graded pass-blocking center in the league, Parsons out to about week 5-6, no edge depth, corner depth issues, Jacobs unsettled.',
  'Explicit: this is a LATE SEPTEMBER add. They open MIN/NYJ/ATL and could be 3-0; the schedule turns brutal after the bye.','live'),
(42,'move_the_line','2026-08-06','jXqrDUbQ22I','MIN','win_total','over',8.5,NULL,'bet',3,
  'Flag-plant team. Won nine games last year starting Brosmer and McCarthy; Kyler at the minimum is a clear upgrade. Flores defense was one of the best in the league schedule-adjusted, blitzing 51%. Every QB in this system except Brosmer has been serviceable to good.',
  NULL,'live'),
(43,'move_the_line','2026-08-06','jXqrDUbQ22I','MIN','division','MIN',NULL,'+500','bet',2,
  'Should not be 5-1 in a division where they are competitive with anyone. The plus-money make-playoffs price (+150/+160) is the safer expression of the same view.',NULL,'live'),
(44,'move_the_line','2026-08-06','jXqrDUbQ22I','CHI','award','Luther Burden receiving yards leader',NULL,'+6000','bet',2,
  'Ben Johnson does not blow smoke and has compared him to Amon-Ra and Landry. Not a great price outright but the usage upside is real.',
  'Widely available at 50-1.','revised'),
(45,'move_the_line','2026-08-06','jXqrDUbQ22I','CHI','win_total',NULL,9.5,NULL,'pass',2,
  'Massive regression candidate - most takeaways in the league but 29th in yards per play allowed, highest explosive pass rate allowed, 27th in pressure rate, against an easy schedule. Offense should improve; defense got worse and the schedule got harder.',
  'Prefer passing-stat overs to the win total.','live'),

-- --------------------------------------------------------------- NFC South --
(46,'move_the_line','2026-08-10','fit9yD2zhFM','NO','division','NO',NULL,'+240','bet',3,
  'Preferred NFC South bet. Shough was among the better QBs down the stretch on a 4-1 finish, good offensive line, Kellen Moore scheming short-to-intermediate, Etienne and Kamara behind him. Anything over 2-1 is a play.',
  'Opening at DET and at BAL - a 0-2 start is very live and would improve the division price into mid-late September.','live'),
(47,'move_the_line','2026-08-10','fit9yD2zhFM','NO','win_total','over',7.5,NULL,'bet',2,
  'Same thesis. Winnable games stacked after the opening two: LV, ATL, MIN, NYG, PIT, CLE, CAR x2, ARI.',
  'Can bet now and double down later, or wait for the 0-2 start.','live'),
(48,'move_the_line','2026-08-10','fit9yD2zhFM','NO','spread','NO',7.0,NULL,'bet',2,
  'Week 1 at Detroit getting seven, and week 2 getting 7.5. Detroit secondary is a mess right now and this Saints offense can keep it inside a touchdown.',
  'Wants to catch Detroit early before the secondary heals.','live'),
(49,'move_the_line','2026-08-10','fit9yD2zhFM','CAR','win_total','under',7.5,'-130','bet',3,
  'Cluster offensive-line injuries: Ekwonu ruptured patellar tendon, Moton blood clot in his lung out indefinitely, Scourton ACL day one of camp, Horn opened on NFI. They won by running the ball and holding on; that path is now closed. Model has them 6.4 against a 7.5 number and a first-place schedule.',
  'Best price -130, DK/MGM -136. Alt under 5.5 and last-in-division also flagged.','live'),
(50,'move_the_line','2026-08-10','fit9yD2zhFM','TB','win_total','under',8.5,NULL,'lean',2,
  'Model at 7.6 against 8.5. Evans gone and Egbuka did not get open outside; David retired; corner room is thin and bad; coverage linebackers remain a weakness.',
  'Will not lay -130 for it.','live'),
(51,'move_the_line','2026-08-10','fit9yD2zhFM','NO','award','Kellen Moore COY',NULL,'+1200','lean',2,
  'Checks every box the award rewards - ascend over expectation, win a division nobody expects.',NULL,'live'),
(52,'move_the_line','2026-08-10','fit9yD2zhFM','NO','award','Jordan Tyson OROY',NULL,'+750','lean',2,
  'Fifth on the board. Was excellent in college whenever healthy; the health question is the whole bet.',NULL,'live'),
(53,'move_the_line','2026-08-10','fit9yD2zhFM','TB','award','Reuben Bain DROY',NULL,'+550','pass',1,
  'Believes he deserves to be the favorite - high snap volume, impacts run and pass. Just not a price worth chasing at the top of the market.',NULL,'live'),
(54,'move_the_line','2026-08-10','fit9yD2zhFM','ATL','win_total',NULL,6.5,NULL,'pass',1,
  '6.5 is about right; a five-to-eight win team. Defense may simply stink with Walker gone and Pierce facing possible suspension, off a wildly unsustainable pressure-to-sack rate.',
  'Prop angle instead: an extremely condensed target tree of London, Bijan and Pitts.','live'),

-- ---------------------------------------------------------------- NFC West --
(55,'move_the_line','2026-08-11','vzmcghw-96g','LA','win_total',NULL,11.5,NULL,'pass',2,
  'Best team in the model and there is no way to bet it - the price is chalk in every derivative market. Downside is roster fragility: Stafford aging, Puka is hurt on seemingly every possession, thin at receiver and along the OL.',
  'No discount available anywhere. Explicit non-bet.','live'),
(56,'move_the_line','2026-08-11','vzmcghw-96g','SEA','win_total','under',10.5,NULL,'lean',1,
  'One of the biggest model-vs-market gaps in the league, over a full win under. Receiving group thin behind JSN, Walker gone, backfield unsettled, Kubiak to Vegas means another new coordinator for Darnold.',
  'Explicitly NOT running to bet it - coaching is a talent maximizer and Macdonald has earned that benefit.','live'),
(57,'move_the_line','2026-08-11','vzmcghw-96g','SF','win_total',NULL,10.5,NULL,'pass',2,
  'Terrifying to take either side. Model loves the roster; the floor is wildly low given the injury pattern, McCaffrey coming off 400 touches, Bosa/Warner fragility. +300 division is the least-bad expression.',
  'Handicap it week to week once the actual inactives are known.','live'),
(58,'move_the_line','2026-08-11','vzmcghw-96g','ARI','win_total',NULL,3.5,NULL,'pass',2,
  'Was a good under at 4.5, is now fairly priced at 3.5. Model has them well above the number - real skill players in McBride, Love, Wilson, Harrison - but the schedule is outrageous and the QB is Brissett.',
  'Only reprieve before week 12 is at the Giants. All the late-season home games are the bull case.','live'),
(59,'move_the_line','2026-08-11','vzmcghw-96g','ARI','prop','Carson Beck games started over',3.5,NULL,'lean',2,
  'Underdog posted 3.5 and that looks low - expects Beck by about week four or five, and he looked good in the first preseason game.',NULL,'live'),

-- ------------------------------------------------------- league-wide takes --
(60,'move_the_line','2026-07-29','D8c1z_tQjlE',NULL,'structural','early-season unders',NULL,NULL,'lean',2,
  '21 new offensive coordinators and 14-15 new defensive coordinators this cycle. The 2022 comp - 15 OC/play-caller changes - saw unders go 56-37 in the first five to six weeks. The refined filter is an OC change stacked with heavy personnel turnover on the SAME unit, not a coaching change alone.',
  'Cleveland named as the archetype: new system, new OL, QB uncertainty. Applies to the first month only.','live'),
(61,'move_the_line','2026-07-29','D8c1z_tQjlE','LAC','structural','Mike McDaniel OC',NULL,NULL,'lean',2,
  'Named the single most impactful coaching change in the league. Motion rate 73% in his Miami offenses vs under 50% for the Chargers under Roman.',NULL,'live'),
(62,'move_the_line','2026-07-29','D8c1z_tQjlE','ARI','structural','offensive downside',NULL,NULL,'lean',2,
  'More downside in this offense than is being discussed. The 2025 garbage-time volume - down two or three scores, then 55 attempts - was unsustainable and cannot be projected forward under new play callers.',NULL,'live'),

-- ------------------------------------- 2026-08-12 injury show: revisions ----
(63,'move_the_line','2026-08-12','YMA8LD35XWE','WAS','win_total',NULL,7.5,NULL,'fade',3,
  'REVERSAL of take 31. Laremie Tunsil torn tricep, surgery 8 Aug, likely out most or all of the season. Coleman kicks back out to tackle; the line drops toward the bottom five to seven. The entire Washington bull case rested on an offensive resurgence that needed Tunsil.',
  'Ceiling outcome and playoff upside explicitly off the table. Do not run the week 3-4 buy-in plan from take 31.','live'),
(64,'move_the_line','2026-08-12','YMA8LD35XWE','CHI','award','Colston Loveland 1000+ receiving yards',NULL,'+300','bet',2,
  'The Luther Burden pivot. Burden missed effectively a month of preseason and betting against that profile has been profitable historically. From week 7 on Loveland ran 6.9 targets/4.6 rec/60.9 yards a game; over the five games he ran a route on 75%+ of dropbacks it was 9.8/6.6/94.',
  'Explicitly a pivot off take 44, which is not withdrawn but is downgraded.','live'),
(65,'move_the_line','2026-08-12','YMA8LD35XWE','MIN','win_total','over',8.5,NULL,'bet',3,
  'Reinforcement of take 42. Kyler Murray officially named starter; the win total did not move and MVP only went 55 to 50, so roughly 95% was already priced. Feels better about the position, not differently.',NULL,'live'),
(66,'move_the_line','2026-08-12','YMA8LD35XWE','GB','prop','Josh Jacobs rushing yards under',NULL,NULL,'lean',2,
  'Off the board everywhere because of the legal situation, plus a groin injury and a worse offensive line. If a number is postable around 950, that is a good look on the under.',
  'May need a prediction market to get down - suggests posting the number yourself.','live'),
(67,'move_the_line','2026-08-12','YMA8LD35XWE','CHI','prop','Bears game overs',NULL,NULL,'lean',2,
  'Kyler/Kobe Bryant at safety now out most of the season on top of a secondary that was already the weak point. More passing volume, more Caleb, Bears-Lions flagged specifically.',NULL,'live'),
(68,'move_the_line','2026-08-12','YMA8LD35XWE','KC','prop','Isiah Pacheco unders / Gibbs overs',NULL,NULL,'lean',2,
  'Pacheco MCL sprain, expected back for week 1. Views him as washed regardless - will bet any under. The corollary is turning Jahmyr Gibbs UP in Detroit.',NULL,'live'),
(69,'move_the_line','2026-08-12','YMA8LD35XWE','IND','prop','Tyler Warren over',NULL,NULL,'lean',2,
  'Alec Pierce still limping with conflicting injection reports and Pittman gone. If Pierce is not right the target concentration moves to Warren, and the Colts may have to trade for a receiver.',
  'A downgrade to the take 19/20 Colts thesis, not a reversal of it.','live'),
(70,'move_the_line','2026-08-12','YMA8LD35XWE','LA','structural','roster fragility',NULL,NULL,'lean',2,
  'Puka soreness plus reported possible discipline for both Nacua and Josh Jacobs, plus a Garrett knee issue. Nothing individually serious - the point is that the top-heavy roster thesis is already being tested in August.',NULL,'live'),
(71,'move_the_line','2026-08-12','YMA8LD35XWE','TB','win_total','under',8.5,NULL,'lean',2,
  'Reinforcement of take 50. Egbuka rolled up in practice with unknown severity; behind him it is McMillan, an old Chris Godwin and Tez Johnson banged up, on a team already skeptical after losing Evans.',NULL,'live'),
(72,'move_the_line','2026-08-12','YMA8LD35XWE','BAL','structural','Nate Wiggins knee',NULL,NULL,'watch',1,
  'Carted off with a knee injury, sounds like nothing structural. Big deal for their ability to play man on the perimeter; unresolved as of the recording.',NULL,'live'),
(73,'move_the_line','2026-08-12','YMA8LD35XWE','PHI','award','Jonathan Greenard sack leader',NULL,'+9000','watch',1,
  'Update to take 32. Coordinator said at a press conference he does not know when Greenard is back; week 1 now in question and he is behind on assimilating the system. Ceiling for the Philly defense is meaningfully different with and without him.',
  'Still not a bet. Team-level impact judged small; the sack-leader ticket is what dies.','live');

-- ---------------------------------------------------------------------------
-- ref.take_vs_our_rating - where the second opinion agrees, and where it does
-- not. `agreement` compares the DIRECTION of their win-total side against the
-- sign of our own 2025 rating minus the market-implied rating.
--
-- READ THE DISAGREEMENTS WITH A CAVEAT. Our rating is end-of-2025 and cannot
-- see ten new head coaches, nine new quarterbacks, or a full teardown. On a
-- roster that turned over hard (Miami, Green Bay), THEY are looking at the
-- 2026 team and we are looking at the 2025 one - the conflict is evidence
-- about our blind spot, not about their read.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW ref.take_vs_our_rating AS
SELECT a.take_id, a.team, a.selection AS their_side, a.line, a.stance, a.conviction,
       p.win_total                        AS market_win_total,
       p.rating_2025 - p.market_rating    AS our_edge_vs_market,
       CASE
         WHEN (a.selection = 'over'  AND p.rating_2025 > p.market_rating)
           OR (a.selection = 'under' AND p.rating_2025 < p.market_rating)
              THEN 'agree'
         WHEN abs(p.rating_2025 - p.market_rating) < 0.02 THEN 'neutral'
         ELSE 'conflict'
       END                                AS agreement,
       a.thesis
FROM ref.analyst_take a
JOIN mart.preseason_prior p USING (team)
WHERE a.market = 'win_total'
  AND a.selection IN ('over','under')
  AND a.stance IN ('bet','lean','fade');
