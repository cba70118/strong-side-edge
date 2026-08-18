"""
ev_scan.py - price the placeable board against the sharp reference.

For every quote at a book we can actually bet, compute the fair probability of
THAT EXACT BET under the distribution implied by the SHARP line, then compare it
to the price being offered.

    edge = P(our exact bet | sharp line) - implied(price we are offered)

This captures both sources of value in one number:
  * LINE difference  - the book hangs -3.0 where the sharp number is -3.5
  * PRICE difference - the book pays -105 where the sharp fair price is -110

Pricing the book's line under the sharp distribution is the only way to compare
them when the lines differ. Differencing raw probabilities across different
handicaps measures the hold, not the edge - the mistake bet_log.py documents.

Sharp reference is Pinnacle (via The Odds API, `eu` region), de-vigged. Where
Pinnacle is absent for a market, the no-vig consensus of every book carrying it
is used instead and the row is labeled accordingly.

ONLY placeable books are scanned. A price we cannot take is not an edge.
Only PREGAME quotes are read (odds.line_pregame).

Usage:
    py -3 scripts/ev_scan.py
    py -3 scripts/ev_scan.py --min-edge 1.0 --market spread_game
    py -3 scripts/ev_scan.py --show-all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import american_to_prob, devig_american, prob_to_american  # noqa: E402
import margin_model as MM  # noqa: E402
import totals_model as TM  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

SCANNED = ("spread_game", "total_game", "ml_game")
OPP = {"home": "away", "away": "home", "over": "under", "under": "over"}


def log(m: str = "") -> None:
    print(m, flush=True)


def latest_quotes(con):
    """Most recent pregame quote per (game, market, book, side)."""
    return con.execute(f"""
        WITH ranked AS (
            SELECT l.game_id, l.market_id, l.book_id, l.side_key,
                   l.line_value, l.price_american, l.captured_at,
                   s.source, b.is_placeable, b.is_sharp,
                   row_number() OVER (
                     PARTITION BY l.game_id, l.market_id, l.book_id, l.side_key
                     ORDER BY l.captured_at DESC) AS rn
            FROM odds.line_pregame l
            JOIN odds.snapshot s ON s.snapshot_id = l.snapshot_id
            JOIN ref.book b      ON b.book_id = l.book_id
            WHERE l.game_id IS NOT NULL
              AND l.market_id IN {SCANNED}
        )
        SELECT game_id, market_id, book_id, side_key, line_value,
               price_american, source, is_placeable, is_sharp
        FROM ranked WHERE rn = 1
    """).fetchall()


def build_reference(quotes):
    """Sharp (or consensus) no-vig probability and line, per (game, market, side)."""
    by_bms = {}
    for g, m, b, side, line, price, src, place, sharp in quotes:
        by_bms.setdefault((g, m, b), {})[side] = (line, price, sharp)

    sharp_ref, cons = {}, {}
    for (g, m, b), sides in by_bms.items():
        keys = list(sides)
        if len(keys) != 2:
            continue
        k0, k1 = keys[0], OPP.get(keys[0])
        if k1 not in sides:
            continue
        p0, p1 = sides[k0][1], sides[k1][1]
        try:
            fair = devig_american([p0, p1], "power")
        except Exception:
            continue
        rec = {k0: (sides[k0][0], fair[0]), k1: (sides[k1][0], fair[1])}
        if sides[k0][2]:                       # is_sharp
            sharp_ref[(g, m)] = rec
        cons.setdefault((g, m), []).append(rec)

    reference = {}
    for key, rec in sharp_ref.items():
        reference[key] = (rec, "pinnacle")
    for key, recs in cons.items():
        if key in reference:
            continue
        merged = {}
        for side in set().union(*[set(r) for r in recs]):
            vals = [r[side] for r in recs if side in r]
            merged[side] = (sum(v[0] for v in vals if v[0] is not None)
                            / max(sum(1 for v in vals if v[0] is not None), 1)
                            if any(v[0] is not None for v in vals) else None,
                            sum(v[1] for v in vals) / len(vals))
        reference[key] = (merged, f"consensus({len(recs)})")
    return reference


def fair_of_bet(market, side, our_line, ref_line, mm, tm):
    """P(our exact bet) under the distribution implied by the reference line.

    SPREADS ARE GATED. margin_model reproduces the UNCONDITIONAL key-number
    distribution well (max 0.40pp error) and the moneyline (2.70pp MAE), but it
    is NOT validated for absolute cover probability at a specific half-point.
    Measured against 2006+ history it swings +5.3pp across the 3 (line 2.5 ->
    3.5) where reality swings -2.0pp, and +2.4pp across the 7 where reality
    swings -1.9pp. Sample-weighted error on half-point lines is 4.10pp - larger
    than any edge worth betting.

    The cause is that the multiplier profile is attached to ABSOLUTE margin and
    then translated to the line, so it breaks the symmetry a market-anchored
    model must have: at the market's own number, cover probability should be
    ~50%. Line placement is itself informative, and the market has already
    absorbed the key-number interaction.

    So when our line equals the reference we do not need the model at all - the
    sharp no-vig price IS the answer, and price shopping is the whole edge.
    When the lines differ, the model is the only bridge, and the row is marked
    low-confidence so it is never mistaken for a validated number.
    """
    a, b, sig, mult = mm
    tsig, tmult = tm
    if market == "spread_game":
        if ref_line is None or our_line is None:
            return None
        if abs(float(our_line) - float(ref_line)) < 1e-9:
            return None                # same line -> use the no-vig price
        spread_line = -float(ref_line) if side == "home" else float(ref_line)
        pmf = MM.pmf_for_spread(round(spread_line * 2) / 2, a, b, sig, mult)
        w, push = MM.cover_prob(pmf, float(our_line), side)
        return w / (1.0 - push) if push < 1.0 else None
    if market == "ml_game":
        if ref_line is None:
            return None
        # ml has no line of its own; condition on the reference SPREAD elsewhere
        return None
    if market == "total_game":
        if ref_line is None or our_line is None:
            return None
        pmf = TM.pmf_for_total(round(float(ref_line) * 2) / 2, tsig, tmult)
        o, push = TM.over_prob(pmf, float(our_line))
        p_over = o / (1.0 - push) if push < 1.0 else None
        if p_over is None:
            return None
        return p_over if side == "over" else 1.0 - p_over
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--min-edge", type=float, default=0.5,
                    help="report edges above this many percentage points")
    ap.add_argument("--market", default=None)
    ap.add_argument("--show-all", action="store_true")
    a = ap.parse_args()

    con = duckdb.connect(str(a.db), read_only=True)
    mm = MM.load_params(con)
    tm = TM.load_params(con)
    quotes = latest_quotes(con)
    reference = build_reference(quotes)
    games = dict(con.execute("""
        SELECT game_id, away_team || ' @ ' || home_team FROM raw.games
        WHERE season >= 2026
    """).fetchall())

    log("=" * 96)
    log("EV SCAN - placeable board vs sharp reference")
    log("=" * 96)
    log(f"pregame quotes: {len(quotes):,} | markets with a reference: "
        f"{len(reference):,}")
    nsharp = sum(1 for v in reference.values() if v[1] == "pinnacle")
    log(f"reference source: {nsharp} pinnacle, "
        f"{len(reference)-nsharp} consensus\n")

    rows = []
    for g, m, b, side, line, price, src, place, sharp in quotes:
        if not place:
            continue
        if a.market and m != a.market:
            continue
        ref = reference.get((g, m))
        if not ref:
            continue
        rec, refsrc = ref
        if side not in rec:
            continue
        ref_line = rec[side][0]
        fair = fair_of_bet(m, side, line, ref_line, mm, tm)
        if fair is None:
            fair = rec[side][1]           # the sharp no-vig price IS the answer
            basis = "novig"
        else:
            basis = "MODEL*"              # * = model-bridged, see gating below
        ours = american_to_prob(price)
        gap = (abs(float(line) - float(ref_line))
               if line is not None and ref_line is not None else 0.0)
        rows.append({
            "game": games.get(g, g), "market": m, "book": b, "side": side,
            "line": line, "ref_line": ref_line, "price": price,
            "fair": fair, "edge": (fair - ours) * 100,
            "refsrc": refsrc, "basis": basis, "gap": gap,
        })

    # --- gating -----------------------------------------------------------
    # 1. Spread rows bridged by margin_model are SUPPRESSED. That path has
    #    4.10pp error against 2006+ history at half-point lines - larger than
    #    any edge worth betting, and it manufactures exactly the kind of
    #    plausible +2pp dog edges seen before this gate existed.
    # 2. Any row whose line sits more than a point off the reference is far
    #    more likely a stale quote than a real edge. Flagged, not trusted.
    for r in rows:
        bad_spread = r["basis"] == "MODEL*" and r["market"] == "spread_game"
        r["stale"] = r["gap"] > 1.0
        r["confident"] = not bad_spread and not r["stale"]

    rows.sort(key=lambda r: -r["edge"])
    if a.show_all:
        shown, gated, stale = rows, [], []
    else:
        cand = [r for r in rows if r["edge"] >= a.min_edge]
        shown = [r for r in cand if r["confident"]]
        gated = [r for r in cand if not r["confident"] and not r["stale"]]
        stale = [r for r in cand if r["stale"]]

    log(f"{'game':<14}{'market':<13}{'book':<12}{'side':<6}{'line':>7}"
        f"{'ref':>7}{'price':>7}{'fair':>9}{'edge pp':>9}  basis")
    log("-" * 96)
    for r in shown[:40]:
        log(f"{r['game']:<14}{r['market']:<13}{r['book']:<12}{r['side']:<6}"
            f"{str(r['line']) if r['line'] is not None else '-':>7}"
            f"{str(r['ref_line']) if r['ref_line'] is not None else '-':>7}"
            f"{r['price']:>+7d}{r['fair']*100:>8.2f}%{r['edge']:>+9.2f}"
            f"  {r['basis']}/{r['refsrc']}")
    if not shown:
        log("  (nothing above the threshold)")
    if gated:
        log(f"\n  SUPPRESSED - {len(gated)} spread row(s) priced by margin_model at")
        log("  a line differing from the reference. Measured 4.10pp error vs")
        log("  history at half-points; the model swings +5.3pp across the 3")
        log("  where reality swings -2.0pp. These are artifacts, not edges.")
    if stale:
        log(f"\n  SUPPRESSED - {len(stale)} row(s) more than a point off the")
        log("  reference line. A gap that size is a stale quote far more often")
        log("  than a real edge; check capture time before acting.")

    log("\n" + "=" * 96)
    log("SUMMARY")
    log("=" * 96)
    for m in SCANNED:
        sub = [r for r in rows if r["market"] == m]
        if not sub:
            continue
        pos = [r for r in sub if r["edge"] > 0]
        log(f"  {m:<14} quotes {len(sub):>4}  "
            f"mean edge {sum(r['edge'] for r in sub)/len(sub):>+6.2f}pp  "
            f"positive {len(pos):>3}  best {max(r['edge'] for r in sub):>+6.2f}pp")

    log("\n  What survives the gate: rows comparing our price to the SHARP")
    log("  NO-VIG PRICE AT THE SAME LINE - pure price shopping, no model risk.")
    log("  That is currently the only spread edge this system can honestly")
    log("  claim. Totals may also use the totals model, which validates at")
    log("  0.97pp against the market's own over/under prices.")
    log("\n  Interpretation. A -110 price implies 52.38%, so a fairly-priced")
    log("  coin flip scores -2.38pp. Mean edge near that is a healthy board")
    log("  with no mispricing. Anything above ~+1pp on a main market is more")
    log("  likely a stale line or a bad reference than a real edge - check the")
    log("  capture timestamp before believing it.")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
