"""metric_edge_test.py - does any of this beat the closing spread?

STABILITY IS NOT EDGE. A metric can be perfectly stable and worth nothing to a
bettor, because a stable metric is one the market has had every opportunity to
price. Points scored per game is highly stable and completely priced. So the
stability table answers "is this measuring the team", and this script answers
the only question that pays: "does knowing it beat the number".

THE TEST. For each game, build every metric point-in-time from that team's
PRIOR games in the same season, take the home-minus-away differential, and
compute its PARTIAL correlation with the home margin while holding the closing
spread fixed. Raw correlation is meaningless here: every one of these metrics
correlates with margin, and so does the spread. The only question is whether
the metric knows something the spread does not.

THE BAR IS 0.05, and it is set by precedent, not convenience: opponent-adjusted
EPA ratings measured -0.04 on this same test and were retired from pricing.
Anything that cannot beat what has already been rejected is not a candidate.

Usage:
    py -3 scripts/metric_edge_test.py
    py -3 scripts/metric_edge_test.py --min-prior 6
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
BAR = 0.05

METRICS = [
    ("off EPA/play",        "off_epa_play",             True),
    ("off pass EPA",        "off_pass_epa",             True),
    ("off success rate",    "off_success",              True),
    ("off explosive rate",  "off_explosive_rate",       True),
    ("PROE",                "off_proe",                 True),
    ("pace sec/play",       "off_sec_per_play_neutral", True),
    ("def EPA/play",        "def_epa_play",             True),
    ("def success rate",    "def_success",              True),
    ("def explosive rate",  "def_explosive_rate",       True),
    ("pressure allowed",    "pressure_rate_allowed",    False),
    ("pressure generated",  "pressure_rate_made",       False),
    ("sack rate allowed",   "sack_rate_allowed",        False),
    ("sack rate generated", "sack_rate_made",           False),
    ("time to throw",       "time_to_throw",            False),
    ("points for/gm",       "points_for",               True),
    ("points against/gm",   "points_against",           True),
    ("turnover margin/gm",  "to_margin",                True),
]

VIEW = """
CREATE OR REPLACE TEMP VIEW tg AS
SELECT t.game_id, t.season, t.week, t.team, t.is_home,
       t.home_margin, t.spread_line_home,
       t.off_epa_play, t.off_pass_epa, t.off_success, t.off_explosive_rate,
       t.off_proe, t.off_sec_per_play_neutral,
       t.def_epa_play, t.def_success, t.def_explosive_rate,
       t.points_for, t.points_against,
       t.def_turnovers_forced - t.off_turnovers AS to_margin,
       p.pressure_rate_allowed, p.pressure_rate_made,
       p.sack_rate_allowed, p.sack_rate_made, p.time_to_throw, p.native
FROM mart.team_game t
LEFT JOIN mart.team_pressure p USING (game_id, team)
WHERE t.game_type = 'REG' AND t.season >= {season}
  AND t.spread_line_home IS NOT NULL;
"""


def log(m=""):
    print(m, flush=True)


def pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def partial(x, y, z):
    """corr(x, y) with z held fixed. z is the closing spread."""
    rxy, rxz, ryz = pearson(x, y), pearson(x, z), pearson(y, z)
    den = math.sqrt((1 - rxz ** 2) * (1 - ryz ** 2))
    return (rxy - rxz * ryz) / den if den > 1e-12 else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--min-prior", type=int, default=4,
                    help="games a team must already have played this season")
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))

    log("=" * 82)
    log("INCREMENTAL SIGNAL OVER THE CLOSING SPREAD")
    log("=" * 82)
    log(f"point-in-time, at least {a.min_prior} prior games in the same season")
    log(f"the bar is {BAR:.2f}: opponent-adjusted EPA measured -0.04 here and "
        f"was retired")
    log("")
    # 17 metrics tested at once. Bonferroni on 17 two-sided tests at 0.05
    # puts the critical t near 3.0; even an uncorrected 95% test needs 1.96.
    results = []
    keep = []
    tcrit = 3.0
    log(f"  {'metric':<22}{'games':>7}{'raw':>9}{'partial':>10}{'SE':>7}"
        f"{'t':>7}{'verdict':>12}")
    log("  " + "-" * 76)

    for label, col, full_era in METRICS:
        season0 = 2020 if full_era else 2023
        con.execute(VIEW.format(season=season0))
        gate = "" if full_era else "AND native = 1"
        rows = con.execute(f"""
            WITH pit AS (
                -- strictly prior games, same season: no leakage
                SELECT game_id, season, week, team, is_home,
                       home_margin, spread_line_home,
                       avg({col}) OVER (
                           PARTITION BY season, team ORDER BY week
                           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                       ) AS prior,
                       count(*) OVER (
                           PARTITION BY season, team ORDER BY week
                           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                       ) AS n_prior
                FROM tg WHERE {col} IS NOT NULL {gate}
            )
            SELECT h.home_margin, h.spread_line_home, h.prior - v.prior
            FROM pit h
            JOIN pit v ON v.game_id = h.game_id AND v.is_home = false
            WHERE h.is_home = true
              AND h.n_prior >= {a.min_prior} AND v.n_prior >= {a.min_prior}
              AND h.prior IS NOT NULL AND v.prior IS NOT NULL
        """).fetchall()
        if len(rows) < 100:
            log(f"  {label:<22}{len(rows):>7}{'--':>9}{'--':>10}"
                f"{'too few games':>14}")
            continue
        marg = [r[0] for r in rows]
        # the spread is home-perspective, positive = home favored; negate so it
        # points the same way as margin
        spread = [-r[1] for r in rows]
        diff = [r[2] for r in rows]
        raw = pearson(diff, marg)
        pc = partial(diff, marg, spread)
        # A correlation at n=624 has a standard error near 0.040 all by itself.
        # Reading a bare 0.06 as signal, across 17 metrics, is reading noise:
        # under the null some of them WILL clear an absolute threshold. So the
        # verdict needs the t-statistic and the multiplicity, not just the bar.
        se = 1.0 / math.sqrt(len(rows) - 4)
        tstat = pc / se
        results.append((label, pc, tstat))
        if abs(pc) < BAR:
            verdict = "no edge"
        elif abs(tstat) < tcrit:
            verdict = "within noise"
        elif pc < 0:
            verdict = "wrong sign"
        else:
            verdict = "CANDIDATE"
        keep.append((label, len(rows), raw, pc, se, tstat, verdict))
        log(f"  {label:<22}{len(rows):>7}{raw:>+9.3f}{pc:>+10.3f}{se:>7.3f}"
            f"{tstat:>+7.2f}{verdict:>12}")

    log("")
    log("  raw     = correlates with margin, as everything does")
    log("  partial = what it knows that the closing spread does not")
    log(f"  a metric needs |t| > {tcrit:.1f} to survive testing {len(results)} "
        f"of them at once")
    log("")
    surv = [r for r in results if abs(r[1]) >= BAR and abs(r[2]) >= tcrit
            and r[1] > 0]
    if surv:
        log("  SURVIVES: " + ", ".join(f"{n} ({v:+.3f})" for n, v, _ in surv))
    else:
        big = max(results, key=lambda r: abs(r[2]))
        log(f"  Nothing survives. The largest |t| is {big[0]} at "
            f"{big[2]:+.2f}, which is what {len(results)} draws from noise")
        log("  look like. The market prices all of it.")

    con.execute("""
        CREATE OR REPLACE TABLE mart.metric_edge (
            metric VARCHAR, games INTEGER, raw DOUBLE, partial DOUBLE,
            se DOUBLE, tstat DOUBLE, verdict VARCHAR)
    """)
    con.executemany("INSERT INTO mart.metric_edge VALUES (?,?,?,?,?,?,?)", keep)
    log(f"  mart.metric_edge  {len(keep)} metrics")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
