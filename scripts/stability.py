"""stability.py - which football measurements are actually predictive?

NOT A BETTING SCRIPT. It never reads a line, a price or a book. It asks one
football question: of everything we measure about a team, what carries from one
season to the next, and what is noise wearing a decimal point?

This matters because every model in this repo had drifted into anchoring on the
market. That is a defensible betting posture and it is not analysis - it cannot
tell you anything the market has not already told you. An independent view has
to be built from measurements that survive their own stability test, and until
now nothing here had run that test at team level.

Two tests per metric, because they answer different questions:

  YEAR OVER YEAR  correlation between a team's value this season and the same
                  team's value next season. This is PREDICTIVENESS - does
                  knowing it help you about the future.
  SPLIT HALF      correlation between a team's first-half and second-half value
                  within one season. This is RELIABILITY - is the metric even
                  measuring a stable property, or is the sample too thin.

The gap between them is the interesting part. A metric can be reliable within a
season and still not carry across one (form that genuinely changes), and a
metric with low reliability cannot be predictive no matter what - the ceiling
on year-over-year correlation is set by how well the thing measures itself.

Usage:
    py -3 scripts/stability.py
    py -3 scripts/stability.py --min-season 2016
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

# label -> (sql expression, weight column or None, kind)
# `kind` is the claim being tested: does this measure PROCESS (how a team plays)
# or OUTCOME (what happened to them)?
METRICS = [
    ("off EPA/play",        "off_epa_play_neutral", "off_plays_neutral", "process"),
    ("def EPA/play",        "def_epa_play_neutral", "def_plays_neutral", "process"),
    ("off success rate",    "off_success",          "off_plays",         "process"),
    ("def success rate",    "def_success",          "def_plays",         "process"),
    ("pass rate over exp",  "off_proe_neutral",     "off_plays_neutral", "process"),
    ("pace (sec/play)",     "off_sec_per_play_neutral", "off_plays_neutral", "process"),
    ("off pass EPA",        "off_pass_epa",         "off_plays",         "process"),
    ("off rush EPA",        "off_rush_epa",         "off_plays",         "process"),
    ("off explosive rate",  "off_explosive_rate",   "off_plays",         "outcome"),
    ("def explosive rate",  "def_explosive_rate",   "def_plays",         "outcome"),
    ("sacks taken",         "off_sacks_taken",      None,                "outcome"),
    ("sacks made",          "def_sacks_made",       None,                "outcome"),
    ("turnovers lost",      "off_turnovers",        None,                "outcome"),
    ("turnovers forced",    "def_turnovers_forced", None,                "outcome"),
    ("points for",          "points_for",           None,                "outcome"),
    ("points against",      "points_against",       None,                "outcome"),
    ("point margin",        "margin",               None,                "outcome"),
]


def log(m: str = "") -> None:
    print(m, flush=True)


def corr(pairs):
    n = len(pairs)
    if n < 8:
        return None, n
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    syy = sum((b - my) ** 2 for _, b in pairs)
    if sxx <= 0 or syy <= 0:
        return None, n
    return sxy / math.sqrt(sxx * syy), n


def season_values(con, expr, wcol, min_season, half=None):
    """Team-season value, play-weighted where a weight column exists.

    Weighting matters: a simple mean of per-game rates lets a game with two
    neutral-down plays count as much as one with fifty, which is exactly the
    error that put a 3-14 team at +0.170 net EPA through week nine.
    """
    wk = ""
    if half == 1:
        wk = "AND week <= 9"
    elif half == 2:
        wk = "AND week > 9"
    if wcol:
        agg = (f"sum({expr} * {wcol}) / nullif(sum({wcol}), 0)")
    else:
        agg = f"avg({expr})"
    rows = con.execute(f"""
        SELECT season, team, {agg} AS v
        FROM mart.team_game
        WHERE game_type = 'REG' AND season >= ? AND {expr} IS NOT NULL {wk}
        GROUP BY 1, 2
    """, [min_season]).fetchall()
    return {(s, t): v for s, t, v in rows if v is not None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--min-season", type=int, default=2020)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)

    log("=" * 78)
    log("METRIC STABILITY - what carries, and what is noise")
    log("=" * 78)
    log(f"seasons {a.min_season}+, regular season only, play-weighted where")
    log("a play count exists. No market data is read anywhere in this test.")
    log("")
    log(f"  {'metric':<20}{'kind':<9}{'yr/yr':>8}{'n':>5}"
        f"{'split-half':>12}{'n':>5}   verdict")
    log("  " + "-" * 74)

    results = []
    for label, expr, wcol, kind in METRICS:
        cur = season_values(con, expr, wcol, a.min_season)
        yoy_pairs = [(v, cur[(s + 1, t)]) for (s, t), v in cur.items()
                     if (s + 1, t) in cur]
        r_yoy, n_yoy = corr(yoy_pairs)

        h1 = season_values(con, expr, wcol, a.min_season, half=1)
        h2 = season_values(con, expr, wcol, a.min_season, half=2)
        sh_pairs = [(v, h2[k]) for k, v in h1.items() if k in h2]
        r_sh, n_sh = corr(sh_pairs)

        if r_yoy is None:
            verdict = "insufficient"
        elif r_yoy >= 0.40:
            verdict = "CARRIES"
        elif r_yoy >= 0.25:
            verdict = "weak"
        else:
            verdict = "NOISE year to year"
        results.append((label, kind, r_yoy, r_sh, verdict))
        log(f"  {label:<20}{kind:<9}{(f'{r_yoy:+.3f}' if r_yoy is not None else '  --'):>8}"
            f"{n_yoy:>5}{(f'{r_sh:+.3f}' if r_sh is not None else '  --'):>12}"
            f"{n_sh:>5}   {verdict}")

    proc = [r for r in results if r[1] == "process" and r[2] is not None]
    outc = [r for r in results if r[1] == "outcome" and r[2] is not None]
    mp = sum(r[2] for r in proc) / len(proc)
    mo = sum(r[2] for r in outc) / len(outc)

    log("")
    log("=" * 78)
    log("VERDICT")
    log("=" * 78)
    log(f"  mean year-over-year correlation")
    log(f"    process metrics  {mp:+.3f}   ({len(proc)} metrics)")
    log(f"    outcome metrics  {mo:+.3f}   ({len(outc)} metrics)")
    log(f"    gap              {mp - mo:+.3f}")
    log("")
    carries = [r[0] for r in results if r[4] == "CARRIES"]
    noise = [r[0] for r in results if r[4] == "NOISE year to year"]
    log(f"  CARRIES (>=0.40): {', '.join(carries) or 'none'}")
    log(f"  NOISE   (<0.25) : {', '.join(noise) or 'none'}")
    log("")
    if mp - mo > 0.10:
        log("  The process/outcome split is real. An independent rating should")
        log("  be built from what carries and should discount what does not -")
        log("  which is the opposite of ranking teams by points or by margin.")
    else:
        log("  The process/outcome split is NOT supported here. Do not claim it.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
