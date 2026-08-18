-- ============================================================================
-- mart.drive - one row per possession, the substrate for game simulation.
--
-- WHY DRIVES AND NOT PLAYS. EPA per play tells you how good a team is; it does
-- not tell you how many points they score. Points arrive in units of 3 and 7,
-- through a possession that starts somewhere and ends in one of a handful of
-- outcomes. A simulator built on drives produces key numbers NATURALLY -
-- margins of 3 and 7 fall out of the arithmetic instead of being pasted on as
-- multipliers, which is exactly what went wrong with the margin model.
--
-- Field position is `yardline_100` on the drive's first play: yards to the
-- opponent's goal line, so 75 is your own 25 and 20 is the red zone. This is
-- the natural scale for a drive model because scoring probability is a
-- function of distance remaining.
-- ============================================================================

CREATE OR REPLACE TABLE mart.drive AS
WITH plays AS (
    SELECT
        p.game_id, p.season, p.week, p.posteam, p.defteam, p.fixed_drive,
        p.fixed_drive_result, p.yardline_100, p.play_id, p.qtr,
        p.game_seconds_remaining, p.half_seconds_remaining,
        p.drive_play_count, p.drive_time_of_possession,
        p.score_differential, p.posteam_score, p.defteam_score,
        row_number() OVER (PARTITION BY p.game_id, p.fixed_drive, p.posteam
                           ORDER BY p.play_id) AS rn_first,
        row_number() OVER (PARTITION BY p.game_id, p.fixed_drive, p.posteam
                           ORDER BY p.play_id DESC) AS rn_last
    FROM raw.pbp p
    WHERE p.fixed_drive IS NOT NULL
      AND p.posteam IS NOT NULL
      -- SCRIMMAGE plays only when locating the start of a drive. Kickoffs are
      -- assigned to the receiving team's fixed_drive and carry yardline_100=35
      -- (the kicking spot), so including them put 2,917 drives per season at a
      -- phantom "own 35" and pushed half of all drives into a field-position
      -- bucket that is actually opponent territory. Extra points do the same at
      -- yardline_100=15. The drive result is a drive-level column, so it is
      -- unaffected by which play we read it from.
      AND p.play_type IN ('pass', 'run', 'qb_kneel', 'qb_spike')
),
firsts AS (
    SELECT game_id, season, week, posteam, defteam, fixed_drive,
           fixed_drive_result, yardline_100 AS start_yl, qtr AS start_qtr,
           game_seconds_remaining AS start_secs,
           half_seconds_remaining AS start_half_secs,
           drive_play_count, drive_time_of_possession,
           score_differential AS start_score_diff
    FROM plays WHERE rn_first = 1
)
SELECT
    f.*,
    -- points the POSSESSING team gets from this drive
    CASE f.fixed_drive_result
        WHEN 'Touchdown'     THEN 7
        WHEN 'Field goal'    THEN 3
        WHEN 'Opp touchdown' THEN -7
        WHEN 'Safety'        THEN -2
        ELSE 0
    END                                              AS drive_points,
    CASE WHEN f.fixed_drive_result IN ('Touchdown','Field goal')
         THEN 1 ELSE 0 END                           AS scored,
    -- collapse the long tail into the states a simulator transitions between
    CASE
        WHEN f.fixed_drive_result = 'Touchdown'          THEN 'TD'
        WHEN f.fixed_drive_result = 'Field goal'         THEN 'FG'
        WHEN f.fixed_drive_result = 'Missed field goal'  THEN 'MISSED_FG'
        WHEN f.fixed_drive_result = 'Punt'               THEN 'PUNT'
        WHEN f.fixed_drive_result IN ('Turnover','Opp touchdown') THEN 'TURNOVER'
        WHEN f.fixed_drive_result = 'Turnover on downs'  THEN 'DOWNS'
        WHEN f.fixed_drive_result = 'Safety'             THEN 'SAFETY'
        WHEN f.fixed_drive_result IN ('End of half','End of game') THEN 'END_HALF'
        ELSE 'OTHER'
    END                                              AS outcome
FROM firsts f
WHERE f.start_yl IS NOT NULL;
