"""
preseason_prior.py - a Week 1 team strength that can see 2026.

THE PROBLEM. mart.team_rating is end-of-2025 and structurally blind to
everything since: ten head-coaching changes, nine new starting quarterbacks,
five quarterbacks with a medical flag, Myles Garrett traded. Feeding it to the
drive simulator for Week 1 projects last season's teams.

THE FIX. A win total is the market's summary of a team's 2026 strength, priced
in August with all of that information. Converting it to the same scale as our
rating gives a prior we can blend, and the gap between the two IS a measurement
of what changed: Cincinnati moves 18 rank places (a 2025 sample broken by
Burrow's turf toe), Baltimore 10 (new staff, full Lamar priced), Arizona minus
10 the other way.

CONVERSION. Expected wins W over 17 games -> per-game win probability W/17 ->
the spread that implies, inverted through the margin model -> a rating using
the 39.18 points-per-rating-unit we measured. Schedule strength is ignored,
which is the main approximation here and is why the prior is blended rather
than trusted outright.

BLENDING. Week 1 is prior-dominated and says so; the 2025 rating takes over as
in-season data arrives. Target-share stability work showed a single game already
carries real signal, so the prior decays fast.

Usage:
    py -3 scripts/preseason_prior.py build
    py -3 scripts/preseason_prior.py compare
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import margin_model as MM  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

PTS_PER_RATING = 39.18      # measured in build_mart.py --evaluate
GAMES = 17
PRIOR_WEIGHT_WK1 = 0.70     # how much of Week 1 strength is the market prior
PRIOR_HALFLIFE_WEEKS = 4.0

# Share of a re-rating that lands on offense. MEASURED two independent ways
# and they agree: regressing the year-over-year change in off_rating on the
# change in net_rating over 160 consecutive team-season pairs gives 0.560
# (defense 0.440, summing to 1 as it must); and offense's share of rating
# variance is 0.072 / (0.072 + 0.0573) = 0.557. Not a chosen constant.
DELTA_OFF_SHARE = 0.560


def log(m: str = "") -> None:
    print(m, flush=True)


def wins_to_margin(w: float, mm) -> float:
    """Expected wins -> average margin, by inverting the margin model."""
    a, b, sig, mult = mm
    target = max(0.02, min(0.98, w / GAMES))
    lo, hi = -20.0, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        p = MM.win_prob(MM.pmf_for_spread(round(mid * 2) / 2, a, b, sig, mult),
                        "home")
        if p < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def build(con) -> int:
    mm = MM.load_params(con)
    mx = con.execute(
        "SELECT max(week) FROM mart.team_rating WHERE season = 2025").fetchone()[0]
    rows = con.execute(f"""
        SELECT c.team, c.win_total, c.power_rank, c.coach_is_new, c.qb_is_new,
               c.qb_health_flag, r.net_rating, r.off_rating, r.def_rating
        FROM ref.team_context c
        LEFT JOIN mart.team_rating r
               ON r.team = c.team AND r.season = 2025 AND r.week = {mx}
        WHERE c.season = 2026
    """).fetchall()

    # league-center the implied margins: win totals do not sum to a zero-sum
    # league because books shade every total toward the over
    implied = {t: wins_to_margin(wt, mm) for t, wt, *_ in rows}
    mu = sum(implied.values()) / len(implied)
    out = []
    for t, wt, rk, cn, qn, fl, net, off, dfn in rows:
        mkt_rating = (implied[t] - mu) / PTS_PER_RATING
        net25, off25, def25 = (net or 0.0), (off or 0.0), (dfn or 0.0)
        blended = PRIOR_WEIGHT_WK1 * mkt_rating + (1 - PRIOR_WEIGHT_WK1) * net25

        # Split the blended net back into offense and defense.
        #
        # THE OLD VERSION WAS BROKEN. It set blended_off = blended * share and
        # blended_def = -blended * (1 - share), with share from the ratio of
        # 2025 magnitudes. Both sides were therefore proportional to the same
        # signed net, so a team's offense and defense were FORCED to opposite
        # signs: any team with a positive net came out with a good offense and
        # a good defense no matter what it actually did. Seven of the nineteen
        # teams with a below-average 2025 defense were rated above average
        # defensively, Cincinnati among them - a genuinely bad 2025 defense
        # (+0.089) turned into a good one (-0.040).
        #
        # The fix keeps the 2025 levels and allocates only the CHANGE. The
        # market's win total re-rates a team's total strength; it does not
        # re-describe which side of the ball that team is good at.
        delta = blended - net25
        b_off = off25 + delta * DELTA_OFF_SHARE
        b_def = def25 - delta * (1 - DELTA_OFF_SHARE)
        # identity: offense minus defense must reproduce the blended net
        assert abs((b_off - b_def) - blended) < 1e-9, f"split broke net for {t}"

        out.append((t, 2026, wt, round(implied[t], 3), round(mkt_rating, 5),
                    round(net25, 5), round(blended, 5),
                    round(b_off, 5), round(b_def, 5), PRIOR_WEIGHT_WK1))

    con.execute("""
        CREATE OR REPLACE TABLE mart.preseason_prior (
            team VARCHAR PRIMARY KEY, season INTEGER, win_total DOUBLE,
            implied_margin DOUBLE, market_rating DOUBLE, rating_2025 DOUBLE,
            blended_rating DOUBLE, blended_off DOUBLE, blended_def DOUBLE,
            prior_weight DOUBLE)
    """)
    con.executemany(
        "INSERT INTO mart.preseason_prior VALUES (?,?,?,?,?,?,?,?,?,?)", out)
    log(f"mart.preseason_prior: {len(out)} teams")
    log(f"  prior weight at Week 1: {PRIOR_WEIGHT_WK1:.0%} market, "
        f"{1-PRIOR_WEIGHT_WK1:.0%} last season")
    log(f"  league-centered; mean implied margin removed ({mu:+.2f} pts)")
    return 0


def compare(con) -> int:
    log("=" * 78)
    log("WHERE THE MARKET AND LAST SEASON DISAGREE MOST")
    log("=" * 78)
    log("A large gap is a team whose 2025 record or process does not describe")
    log("the 2026 roster. That is information, not error.\n")
    log(f"  {'tm':<5}{'WT':>5}{'mkt rtg':>9}{'2025 rtg':>10}{'gap':>8}  what changed")
    for r in con.execute("""
        SELECT p.team, p.win_total, p.market_rating, p.rating_2025,
               p.market_rating - p.rating_2025 AS gap,
               c.coach_is_new, c.qb_is_new, c.starting_qb, c.qb_health_flag,
               c.context_note
        FROM mart.preseason_prior p
        JOIN ref.team_context c ON c.team = p.team AND c.season = 2026
        ORDER BY abs(p.market_rating - p.rating_2025) DESC LIMIT 10
    """).fetchall():
        why = []
        if r[5]:
            why.append("new HC")
        if r[6]:
            why.append(f"new QB {r[7]}")
        if r[8]:
            why.append(r[8])
        log(f"  {r[0]:<5}{r[1]:>5}{r[2]:>+9.3f}{r[3]:>+10.3f}{r[4]:>+8.3f}  "
            f"{', '.join(why) if why else r[9][:44]}")

    log("\n" + "=" * 78)
    log("BLENDED WEEK 1 STRENGTH, TOP AND BOTTOM")
    log("=" * 78)
    rows = con.execute("""
        SELECT team, win_total, blended_rating, market_rating, rating_2025
        FROM mart.preseason_prior ORDER BY blended_rating DESC
    """).fetchall()
    log(f"  {'tm':<5}{'WT':>6}{'blended':>10}{'market':>9}{'2025':>9}")
    for r in rows[:6] + [("...", 0, 0, 0, 0)] + rows[-6:]:
        if r[0] == "...":
            log("  ...")
            continue
        log(f"  {r[0]:<5}{r[1]:>6}{r[2]:>+10.4f}{r[3]:>+9.4f}{r[4]:>+9.4f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "compare"])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=(a.cmd == "compare"))
    try:
        return build(con) if a.cmd == "build" else compare(con)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
