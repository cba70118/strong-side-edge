"""metric_stability.py - how many games before a metric describes the team?

THE QUESTION THIS ANSWERS. Every in-season read is a bet that what you have
seen predicts what is coming. A metric that has not stabilized is describing
the schedule. So for each metric: correlate the first N games of a team-season
against the REST of that same season. If the correlation is near zero at N=4,
an argument built on four games of it is an argument about opponents faced.

WHY FIRST-N-VS-REST AND NOT SPLIT-HALF. Split-half reliability answers "is this
metric measuring something repeatable", which is a different and easier
question. First-N-vs-rest answers the one a bettor actually has in week 5,
including the part where early opponents differ from late ones. It is the
harsher and more honest test, and it is the one the claimed windows are about.

READ THE COLUMNS, NOT A SINGLE NUMBER. A metric is usable when its correlation
is both high and FLAT across N: still climbing at N=10 means it needs a season.

Noise controls are included deliberately. Red zone TD rate, third down rate and
turnover margin are where most public arguments are built, so they are measured
beside the rest rather than dismissed by assertion.

Usage:
    py -3 scripts/metric_stability.py
    py -3 scripts/metric_stability.py --from-season 2023
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
SPLITS = (4, 6, 8, 10)
MIN_REST = 4          # games remaining for the second half to mean anything

# label, expression, claimed window. The claim is recorded so the output can
# agree or disagree with it in the same row.
METRICS = [
    ("OFFENSE", None, None),
    ("off EPA/play",        "off_epa_play",             "6-10 gm"),
    ("off pass EPA",        "off_pass_epa",             "6-10 gm"),
    ("off rush EPA",        "off_rush_epa",             "season+"),
    ("off success rate",    "off_success",              "6-10 gm"),
    ("PROE",                "off_proe",                 "3-5 gm"),
    ("PROE (neutral)",      "off_proe_neutral",         "3-5 gm"),
    ("pace sec/play",       "off_sec_per_play_neutral", "3-5 gm"),
    ("off explosive rate",  "off_explosive_rate",       "variance"),
    ("pressure allowed",    "pressure_rate_allowed",    "6-10 gm"),
    ("sack rate allowed",   "sack_rate_allowed",        "lagging"),
    ("pressure-to-sack",    "pressure_to_sack",         "QB trait"),
    ("time to throw",       "time_to_throw",            "QB trait"),
    ("DEFENSE", None, None),
    ("def EPA/play",        "def_epa_play",             "season+"),
    ("def pass EPA",        "def_pass_epa",             "season+"),
    ("def rush EPA",        "def_rush_epa",             "season+"),
    ("def success rate",    "def_success",              "season+"),
    ("def explosive rate",  "def_explosive_rate",       "season+"),
    ("pressure generated",  "pressure_rate_made",       "faster"),
    ("sack rate generated", "sack_rate_made",           "lagging"),
    ("NOISE CONTROLS", None, None),
    ("red zone TD rate",    "rz_td_rate",               "noise"),
    ("third down rate",     "third_down_rate",          "noise"),
    ("turnover margin/gm",  "to_margin",                "noise"),
    ("points for/gm",       "points_for",               "noise"),
    ("points against/gm",   "points_against",           "noise"),
    ("yards per carry",     "ypc",                      "noise"),
]

PRESSURE_COLS = {"pressure_rate_allowed", "sack_rate_allowed",
                 "pressure_to_sack", "time_to_throw",
                 "pressure_rate_made", "sack_rate_made"}

BASE = """
CREATE OR REPLACE TEMP VIEW tg AS
WITH plays AS (
    SELECT *, CASE WHEN rush = 1 THEN yards_gained END AS rush_yards_gained
    FROM mart.pbp_clean
),
situ AS (
    -- Situational rates the mart does not carry. Computed here rather than
    -- added to team_game because their only purpose is to be measured against
    -- the metrics that are supposed to replace them.
    SELECT game_id, posteam AS team,
           sum(CASE WHEN yardline_100 <= 20 AND touchdown = 1
                    THEN 1 ELSE 0 END)
             / nullif(count(DISTINCT CASE WHEN yardline_100 <= 20
                                          THEN drive END), 0)  AS rz_td_rate,
           avg(CASE WHEN down = 3 THEN first_down END)        AS third_down_rate,
           sum(rush_yards_gained) / nullif(sum(rush), 0)       AS ypc
    FROM plays
    WHERE posteam IS NOT NULL
    GROUP BY 1, 2
)
SELECT t.*, p.pressure_rate_allowed, p.sack_rate_allowed, p.pressure_to_sack,
       p.time_to_throw, p.pressure_rate_made, p.sack_rate_made, p.native,
       s.rz_td_rate, s.third_down_rate, s.ypc,
       t.def_turnovers_forced - t.off_turnovers AS to_margin
FROM mart.team_game t
LEFT JOIN mart.team_pressure p USING (game_id, team)
LEFT JOIN situ s USING (game_id, team)
WHERE t.game_type = 'REG' AND t.season >= {season};
"""


def log(m=""):
    print(m, flush=True)


def corr(pairs):
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(pairs)
    if n < 20:
        return None, n
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    syy = sum((b - my) ** 2 for _, b in pairs)
    if sxx <= 0 or syy <= 0:
        return None, n
    return sxy / math.sqrt(sxx * syy), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--from-season", type=int, default=2020)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    # a CREATE VIEW cannot be prepared in duckdb, so the season
    # bound is formatted in; it is an int from argparse
    con.execute(BASE.format(season=int(a.from_season)))

    log("=" * 84)
    log("METRIC STABILITY - first N games vs the REST of the same season")
    log("=" * 84)
    log(f"seasons {a.from_season}+, regular season only, at least {MIN_REST} "
        f"games remaining")
    log("A metric is usable when the number is high AND flat across N.")
    log("Still climbing at N=10 means it needs a full season.")
    log("")
    header = "".join(f"{'N=' + str(n):>8}" for n in SPLITS)
    log(f"  {'metric':<22}{'claimed':>10}{header}{'verdict':>12}{'n':>7}")
    log("  " + "-" * 78)

    results = {}
    keep = []
    group = ""
    for label, col, claim in METRICS:
        if col is None:
            group = label
            log("")
            log(f"  {label}")
            continue
        gate = "AND native = 1" if col in PRESSURE_COLS else ""
        rows = con.execute(f"""
            SELECT season, team, week, {col} AS v
            FROM tg WHERE {col} IS NOT NULL {gate}
            ORDER BY season, team, week
        """).fetchall()
        seasons = {}
        for s, t, w, v in rows:
            seasons.setdefault((s, t), []).append(v)

        out, counts = [], []
        for n in SPLITS:
            pairs = []
            for vs in seasons.values():
                if len(vs) < n + MIN_REST:
                    continue
                pairs.append((sum(vs[:n]) / n, sum(vs[n:]) / len(vs[n:])))
            r, cnt = corr(pairs)
            out.append(r)
            counts.append(cnt)

        got = [x for x in out if x is not None]
        best = max(got) if got else None
        if best is None:
            verdict = "no data"
        elif best < 0.20:
            verdict = "NOISE"
        elif out[0] is not None and out[0] >= 0.45 and best - out[0] < 0.10:
            verdict = "fast"
        elif best >= 0.45:
            verdict = "slow"
        else:
            verdict = "weak"
        results[label] = (out, verdict)
        # grp is the SECTION this row sits under. Tagging pressure metrics
        # here instead spawned stray section headers mid-table; the native-era
        # caveat belongs in the prose, not in the grouping key.
        keep.append((label, claim, out[0], out[1], out[2], out[3], verdict,
                     group))
        cells = "".join(f"{x:>8.2f}" if x is not None else f"{'--':>8}"
                        for x in out)
        log(f"  {label:<22}{claim:>10}{cells}{verdict:>12}{counts[0]:>7}")

    log("")
    log("  fast  = already predictive by game 4 and does not improve much")
    log("  slow  = predictive eventually, still climbing at N=10")
    log("  NOISE = never exceeds 0.20; an argument built on it is about the past")

    con.execute("""
        CREATE OR REPLACE TABLE mart.metric_stability (
            metric VARCHAR, claimed VARCHAR, n4 DOUBLE, n6 DOUBLE,
            n8 DOUBLE, n10 DOUBLE, verdict VARCHAR, grp VARCHAR)
    """)
    con.executemany("INSERT INTO mart.metric_stability VALUES (?,?,?,?,?,?,?,?)",
                    keep)
    log("")
    log(f"  mart.metric_stability  {len(keep)} metrics")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
