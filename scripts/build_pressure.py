"""build_pressure.py - pressure rate allowed and generated, per team-game.

WHY THIS ONE AND NOT SACKS. A sack is a pressure that finished, and whether it
finishes depends heavily on the quarterback: pocket movement, time to throw,
willingness to take the checkdown. Pressure rate is closer to what the line and
the front actually did. Which of the two is the more reliable signal is not
taken on faith here; scripts/metric_stability.py measures both.

SUBSTRATE, AND ITS TRAP. raw.pbp_participation.was_pressure runs 2020-2025,
but before 2023 it is populated on non-sack dropbacks ONLY: every sack carries
NULL. Read naively that drops the most-pressured plays in football from both
numerator and denominator, and the script's first run duly reported a 0.0% sack
rate for 2020-2022 while looking otherwise plausible.

It is recoverable. In 2023-2025, where the field is native on every dropback,
4,201 of 4,203 sacks are flagged pressured: 99.95%. So a sack is treated as a
pressure, which restores the earlier seasons at a known error of 0.05%. The
1,005-odd non-sack dropbacks a year that NGS simply did not chart stay OUT of
the denominator rather than being assumed clean, since an uncharted play is
unknown, not unpressured. Rows carry `native` so any model can restrict to the
era that needs no imputation.

NOT AVAILABLE, and worth stating so nobody looks for it again: true yards per
route run. participation.route is a single column describing the play, not a
per-player route list, so routes run per receiver cannot be recovered. The
existing route_proxy_snaps in mart.player_game_usage remains the best available
opportunity denominator.

Usage:
    py -3 scripts/build_pressure.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

SQL = """
CREATE OR REPLACE TABLE mart.team_pressure AS
WITH db AS (
    -- One row per dropback, carrying who had the ball and who was rushing.
    SELECT p.game_id, p.season, p.week, p.posteam, p.defteam,
           CASE WHEN pt.was_pressure OR p.sack = 1 THEN 1 ELSE 0 END
                                                      AS pressured,
           p.sack,
           CASE WHEN p.season >= 2023 THEN 1 ELSE 0 END AS native,
           pt.number_of_pass_rushers AS rushers,
           pt.time_to_throw
    FROM mart.pbp_clean p
    JOIN raw.pbp_participation pt
      ON pt.nflverse_game_id = p.game_id AND pt.play_id = p.play_id
    WHERE p.qb_dropback = 1
      AND p.posteam IS NOT NULL
      -- a charted play, or a sack, which is a pressure by definition
      AND (pt.was_pressure IS NOT NULL OR p.sack = 1)
),
off AS (
    SELECT game_id, season, week, posteam AS team,
           count(*)                              AS dropbacks,
           avg(pressured)                        AS pressure_rate_allowed,
           avg(sack)                             AS sack_rate_allowed,
           -- Conditional on being pressured: a quarterback trait far more than
           -- a line trait, which is why it is kept separate from both rates.
           sum(sack) / nullif(sum(pressured), 0) AS pressure_to_sack,
           avg(time_to_throw)                    AS time_to_throw,
           max(native)                           AS native
    FROM db GROUP BY 1, 2, 3, 4
),
def AS (
    SELECT game_id, defteam AS team,
           count(*)      AS pass_snaps_faced,
           avg(pressured) AS pressure_rate_made,
           avg(sack)      AS sack_rate_made,
           avg(rushers)   AS rushers_sent
    FROM db GROUP BY 1, 2
)
SELECT o.game_id, o.season, o.week, o.team,
       o.dropbacks, o.pressure_rate_allowed, o.sack_rate_allowed,
       o.pressure_to_sack, o.time_to_throw,
       d.pass_snaps_faced, d.pressure_rate_made, d.sack_rate_made,
       d.rushers_sent, o.native
FROM off o
JOIN def d ON d.game_id = o.game_id AND d.team = o.team;
"""


def log(m=""):
    print(m, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    try:
        con.execute(SQL)
        n, t = con.execute("SELECT count(*), count(DISTINCT team) "
                           "FROM mart.team_pressure").fetchone()
        log(f"mart.team_pressure  {n:,} team-games, {t} teams")

        log("")
        log("  coverage and league rates by season")
        log(f"    {'szn':<6}{'team-gm':>9}{'db/gm':>8}{'press%':>9}"
            f"{'sack%':>8}{'p2s%':>8}{'ttt':>7}")
        for r in con.execute("""
            SELECT season, count(*), avg(dropbacks),
                   avg(pressure_rate_allowed), avg(sack_rate_allowed),
                   avg(pressure_to_sack), avg(time_to_throw)
            FROM mart.team_pressure GROUP BY 1 ORDER BY 1
        """).fetchall():
            log(f"    {r[0]:<6}{r[1]:>9,}{r[2]:>8.1f}{r[3] * 100:>9.1f}"
                f"{r[4] * 100:>8.1f}{r[5] * 100:>8.1f}{r[6] or 0:>7.2f}")

        # A sack must also be a pressure. If it is not, the join is wrong.
        bad = con.execute("""
            SELECT count(*) FROM mart.team_pressure
            WHERE sack_rate_allowed > pressure_rate_allowed + 1e-9
        """).fetchone()[0]
        log("")
        log(f"  team-games where sacks exceed pressures: {bad}"
            f"  {'(a sack is a pressure, so this must be 0)' if bad else 'OK'}")
        assert bad == 0, "sack rate exceeds pressure rate; the join is wrong"
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
