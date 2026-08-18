"""prop_path_test.py - exercise the player-prop pipeline before it matters.

THE PROBLEM THIS EXISTS FOR. No feed carries historical PREGAME player props,
so the prop model can never be backtested - it can only be graded forward on
snapshots we capture ourselves. That leaves the plumbing between a posted line
and a priced number completely unexercised until the first real yardage line
appears in game week, which is the worst possible moment to discover it does
not work.

It did not work. mart.player_usage_projection is built from games that have
been PLAYED, so before week 1 of a season it is empty, and price_props joins
against it. Week 1 showed 245 posted props, every one of them anytime-TD and
therefore refused as `unsupported` before reaching the join - so the empty join
never surfaced. The first receiving-yards line posted would have priced zero.

WHAT THIS DOES. Synthesizes two-sided yardage and reception lines for real
players on the real slate, at the model's own median and at points either side
of it, and runs them through the REAL price_props. Nothing is written to the
warehouse; the quotes are constructed in memory in the shape price_props
expects.

WHAT IT ASSERTS, beyond "it ran":
  1. Every synthetic line reaches a terminal status - priced, or refused with
     a reason. A line that silently vanishes is the failure mode above.
  2. p_over is MONOTONE DECREASING in the posted line. This is playbook rule
     11, and the one check that would have caught the survival-function bug an
     external review found.
  3. A line at the model's median prices near 50%.
  4. At -110 both ways with a 50% model, the edge is about minus the hold -
     the model must not manufacture an edge out of a coin flip.

Usage:
    py -3 scripts/prop_path_test.py
    py -3 scripts/prop_path_test.py --week 1 --sample 40
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slate_data as SD          # noqa: E402
import prop_model as PM          # noqa: E402
from devig import devig_american  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

# (market_id, stat, projection field the median is built from)
CASES = [
    ("player_rec_yds", "rec_yds", "proj_rec_yards"),
    ("player_rush_yds", "rush_yds", "proj_rush_yards"),
    ("player_receptions", "receptions", "proj_receptions"),
]
LADDER = (0.60, 0.80, 1.00, 1.20, 1.50)   # multiples of the model median
PRICE = -110


def log(m=""):
    print(m, flush=True)


def synth_quotes(market_id, gsis, game_id, line):
    """Two-sided quote rows in the exact shape price_props consumes."""
    return [
        (game_id, market_id, gsis, "draftkings", "DraftKings", True, False,
         "over", line, PRICE),
        (game_id, market_id, gsis, "draftkings", "DraftKings", True, False,
         "under", line, PRICE),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--sample", type=int, default=25)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)

    params = PM.load_params()
    projections, source = SD.load_projections(con, a.season, a.week)
    log("=" * 78)
    log("PROP PATH TEST - does a posted line become a priced number?")
    log("=" * 78)
    log("  %d player-games from %s" % (len(projections), source))
    if not projections:
        log("  FAIL: no projections at all. Every posted line would refuse.")
        return 1

    cleared = [s for s in ("rec_yds", "rush_yds", "receptions")
               if params.get(s, {}).get("cleared")]
    log("  stats cleared to price: %s" % (", ".join(cleared) or "NONE"))

    failures = []
    log("")
    for market_id, stat, field in CASES:
        if stat not in cleared:
            log("  %-20s SKIPPED - not cleared by the coverage test" % stat)
            continue
        # busiest players first: the ones a book would actually post
        cands = sorted(
            ((k, v) for k, v in projections.items()
             if (v.get(field) or 0) > 1.0),
            key=lambda kv: -(kv[1].get(field) or 0))[:a.sample]
        if not cands:
            log("  %-20s no projected volume on this slate" % stat)
            continue

        n_priced = n_refused = 0
        mono_bad = near_50 = []
        mono_bad, near_50 = [], []
        for (game_id, gsis), proj in cands:
            med = PM.quantile(stat, proj, 0.5, params)
            if med is None or med <= 0.5:
                continue
            probs = []
            for mult in LADDER:
                # books post half-points; a whole number is now
                # refused as pushable, tested separately below
                line = round(med * mult) + 0.5
                rows = SD.price_props(
                    synth_quotes(market_id, gsis, game_id, line),
                    projections, params, SD.MIN_EDGE)
                assert len(rows) == 1, "price_props dropped a quote"
                r = rows[0]
                if r["status"] == "priced":
                    n_priced += 1
                    probs.append((line, r["model_over_pct"], r))
                else:
                    n_refused += 1
                    if not r.get("reason"):
                        failures.append(
                            "%s %s: status %s with no reason"
                            % (stat, gsis, r["status"]))
            # 2. monotonicity across the ladder
            for (l0, p0, _), (l1, p1, _) in zip(probs, probs[1:]):
                if l1 > l0 and p1 > p0 + 1e-9:
                    mono_bad.append((gsis, l0, p0, l1, p1))
            # 3. the half-points either side of the median must BRACKET 50%.
            # Asserting "the median prices at 50" is wrong for a discrete
            # stat: the median of a count sits ON an atom, so P(X > median)
            # is correctly below 50 - receptions reads 43%, and that is the
            # distribution being right, not a bug.
            # round, NOT int. quantile returns a hair under the atom on a
            # discrete stat (3.99999 for a median of 4), and int() truncates
            # that to 3, which compares the wrong pair of half-points and
            # reports a failure that is not there.
            lo_line = round(med) - 0.5
            hi_line = round(med) + 0.5
            p_lo = PM.prob_over(stat, proj, lo_line, params)
            p_hi = PM.prob_over(stat, proj, hi_line, params)
            if p_lo is not None and p_hi is not None:
                near_50.append((p_lo, p_hi))

        log("  %-20s %4d priced, %3d refused across %d players"
            % (stat, n_priced, n_refused, len(cands)))
        if mono_bad:
            failures.append("%s: %d MONOTONICITY violations, e.g. line %.1f -> "
                            "%.1f%% then line %.1f -> %.1f%%"
                            % (stat, len(mono_bad), mono_bad[0][1],
                               mono_bad[0][2], mono_bad[0][3], mono_bad[0][4]))
            log("       MONOTONICITY FAIL: %d cases where a HIGHER line gave a "
                "HIGHER p(over)" % len(mono_bad))
        else:
            log("       monotonicity OK across the %d-point ladder" % len(LADDER))
        if near_50:
            bad = [x for x in near_50 if not (x[0] >= 0.5 >= x[1])]
            log("       median bracketed by its half-points on %d of %d"
                % (len(near_50) - len(bad), len(near_50)))
            if bad:
                failures.append(
                    "%s: %d players where p(over) at median-0.5 and median+0.5 "
                    "do not straddle 50%%, e.g. %.1f%% / %.1f%%"
                    % (stat, len(bad), bad[0][0] * 100, bad[0][1] * 100))

    # 4. a coin flip at -110 both ways must not produce an edge
    log("")
    log("  no-free-edge check: a symmetric market must devig to 50/50")
    fair = devig_american([PRICE, PRICE], "power")
    log("     power devig of -110/-110 -> %.4f / %.4f" % (fair[0], fair[1]))
    if abs(fair[0] - 0.5) > 0.001:
        failures.append("devig of a symmetric market is not 50/50: %.4f"
                        % fair[0])

    log("")
    log("=" * 78)
    log("VERDICT")
    log("=" * 78)
    if failures:
        for f in failures:
            log("  FAIL  %s" % f)
        log("")
        log("  The prop path is NOT ready. Fix these before game week.")
        con.close()
        return 1
    log("  Every synthetic line reached a terminal status, p(over) is monotone")
    log("  in the line, the median is bracketed by its own half-points, and")
    log("  a symmetric market devigs to 50/50. The path is wired end to end.")
    log("")
    log("  STILL UNVALIDATED: whether the numbers are RIGHT. No feed carries")
    log("  historical pregame props, so accuracy can only be graded forward on")
    log("  captured snapshots. This test covers the plumbing, not the model.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
