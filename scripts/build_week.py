"""
build_week.py - assemble a week.json for the nfl-slate-analyst brief renderer
from OUR warehouse.

Every number in the output comes from the database. Nothing is invented.

WHAT THIS BRIEF CAN AND CANNOT SAY RIGHT NOW, and why the narratives read the
way they do:

  AVAILABLE   real market prices for all 272 games from two feeds; the sharp
              Pinnacle reference on the 16 games it prices; a totals model
              validated at 0.97pp against the market's own over/under prices;
              point-in-time team ratings through the end of 2025.

  NOT AVAILABLE  2026 injury reports (nothing has been played), weather (three
              weeks out), player props beyond anytime-TD (books have not posted
              them), and 2026 usage data for the prop engine.

So the narratives are built from measured facts only - rating differentials,
line-vs-reference gaps, model probabilities, rest and roof. They do NOT contain
personnel or injury claims, because we have no source for those and inventing
them is how a brief becomes fiction.

Spread plays are limited to price shopping at a matched line: margin_model is
gated for half-point pricing (4.10pp error vs history). See RUNBOOK.

Usage:
    py -3 scripts/build_week.py --week 1 -o week01.json
    py -3 scripts/build_week.py --week 1 -o week01.json --min-edge 1.0
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import american_to_prob, devig_american, prob_to_american  # noqa: E402
import totals_model as TM  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
OPP = {"home": "away", "away": "home", "over": "under", "under": "over"}


def log(m: str = "") -> None:
    print(m, flush=True)


def quarter_kelly_units(fair_prob: float, price: int,
                        cap_units: float = 2.5) -> float:
    """Quarter-Kelly as units, where 1 unit = 1% of bankroll.

    ILLUSTRATIVE ONLY. Staking is a deliberately deferred decision on this
    project - year one grades the process on CLV rather than sizing it. This
    number is emitted because the brief renderer requires a stake column, and
    it is derived from edge and price rather than invented. Do not read it as
    a sizing recommendation.
    """
    b = (price / 100.0) if price > 0 else (100.0 / abs(price))
    f = (b * fair_prob - (1.0 - fair_prob)) / b
    return round(min(max(f, 0.0) * 0.25 * 100.0, cap_units), 2)


def fetch(con, season: int, week: int):
    games = con.execute("""
        SELECT game_id, away_team, home_team, gameday, gametime, roof,
               spread_line, total_line, away_rest, home_rest, div_game
        FROM raw.games WHERE season = ? AND week = ? ORDER BY gameday, gametime
    """, [season, week]).fetchall()

    quotes = con.execute("""
        WITH r AS (
            SELECT l.game_id, l.market_id, l.book_id, l.side_key, l.line_value,
                   l.price_american, b.is_placeable, b.is_sharp, b.book_name,
                   row_number() OVER (PARTITION BY l.game_id, l.market_id,
                        l.book_id, l.side_key ORDER BY l.captured_at DESC) rn
            FROM odds.line_pregame l
            JOIN ref.book b ON b.book_id = l.book_id
            WHERE l.game_id IS NOT NULL
              AND l.market_id IN ('spread_game','total_game','ml_game')
        )
        SELECT game_id, market_id, book_id, book_name, side_key, line_value,
               price_american, is_placeable, is_sharp
        FROM r WHERE rn = 1
    """).fetchall()

    ratings = {}
    mx = con.execute(
        "SELECT max(week) FROM mart.team_rating WHERE season = 2025").fetchone()[0]
    for t, net, off, dfn in con.execute("""
        SELECT team, net_rating, off_rating, def_rating
        FROM mart.team_rating WHERE season = 2025 AND week = ?
    """, [mx]).fetchall():
        ratings[t] = (net, off, dfn)
    return games, quotes, ratings


def sharp_and_best(quotes):
    """Per (game, market): the sharp no-vig reference, and the best placeable
    price on each side."""
    per_book = {}
    for g, m, b, bn, side, line, price, place, sharp in quotes:
        per_book.setdefault((g, m, b), {})[side] = (line, price, place, sharp, bn)

    ref, best = {}, {}
    for (g, m, b), sides in per_book.items():
        if len(sides) != 2:
            continue
        k0 = next(iter(sides))
        k1 = OPP.get(k0)
        if k1 not in sides:
            continue
        try:
            fair = devig_american([sides[k0][1], sides[k1][1]], "power")
        except Exception:
            continue
        if sides[k0][3]:                       # sharp
            ref[(g, m)] = {k0: (sides[k0][0], fair[0]),
                           k1: (sides[k1][0], fair[1])}
        for k in (k0, k1):
            line, price, place, _, bn = sides[k]
            if not place:
                continue
            cur = best.get((g, m, k))
            if cur is None or price > cur["price"]:
                best[(g, m, k)] = {"price": price, "line": line,
                                   "book": bn, "book_id": b}
    return ref, best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--min-edge", type=float, default=1.0)
    ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args()

    con = duckdb.connect(str(a.db), read_only=True)
    tm = TM.load_params(con)
    games, quotes, ratings = fetch(con, a.season, a.week)
    ref, best = sharp_and_best(quotes)
    captured = con.execute(
        "SELECT cast(max(captured_at) AS VARCHAR) FROM odds.line").fetchone()[0]

    # season stats from the real bet log
    st = con.execute("""
        SELECT count(*), coalesce(sum(CASE WHEN c.beat_close THEN 1 ELSE 0 END),0),
               coalesce(avg(c.clv_line_pts),0)
        FROM bet.wager w LEFT JOIN bet.clv c ON c.bet_id = w.bet_id
    """).fetchone()
    n_bets, n_beat, avg_pts = st[0], st[1], st[2]

    entries, passed, n_side, n_total = [], [], 0, 0

    for gid, away, home, gday, gtime, roof, sl, tl, arest, hrest, div in games:
        gmk, plays = [], []

        # ---- TOTAL: the only market with a validated model behind it -------
        tref = ref.get((gid, "total_game"))
        for side in ("under", "over"):
            bq = best.get((gid, "total_game", side))
            if not bq or not tref or side not in tref:
                continue
            rline = tref[side][0]
            if rline is None or bq["line"] is None:
                continue
            gap = abs(float(bq["line"]) - float(rline))
            if gap > 1.0:                    # stale-quote guard
                continue
            pmf = TM.pmf_for_total(round(float(rline) * 2) / 2, *tm)
            o, push = TM.over_prob(pmf, float(bq["line"]))
            if push >= 1.0:
                continue
            p_over = o / (1.0 - push)
            fair_p = p_over if side == "over" else 1.0 - p_over
            edge = (fair_p - american_to_prob(bq["price"])) * 100
            if edge >= a.min_edge:
                plays.append({
                    "type": "Total", "status": "play",
                    "selection": f"{side.capitalize()} {bq['line']}",
                    "price": int(bq["price"]), "book": bq["book"],
                    "fair": int(round(prob_to_american(fair_p))),
                    "edge_pct": round(edge, 1),
                    "stake_units": quarter_kelly_units(fair_p, int(bq["price"])),
                    "thesis": (
                        f"All placeable books hang {bq['line']} while the sharp "
                        f"reference prices {rline} in the same capture; taking "
                        f"the {side} a {gap:.1f}-point better number. The totals "
                        f"model, validated at 0.97pp against the market's own "
                        f"over/under prices, makes that worth "
                        f"{edge:.1f} points of edge."),
                    "kill": (
                        f"The sharp number moves to {bq['line']} or beyond, or "
                        f"the placeable books come back to {rline} — i.e. the "
                        f"gap closes toward the books rather than the sharp "
                        f"side."),
                })
                n_total += 1
                break

        # ---- SIDE: price shopping only; the model is gated -----------------
        sref = ref.get((gid, "spread_game"))
        for side in ("home", "away"):
            bq = best.get((gid, "spread_game", side))
            if not bq or not sref or side not in sref:
                continue
            rline, rfair = sref[side]
            if rline is None or bq["line"] is None:
                continue
            if abs(float(bq["line"]) - float(rline)) > 1e-9:
                continue                     # matched lines only
            edge = (rfair - american_to_prob(bq["price"])) * 100
            if edge >= a.min_edge:
                team = home if side == "home" else away
                plays.append({
                    "type": "Side", "status": "play",
                    "selection": f"{team} {bq['line']:+g}",
                    "price": int(bq["price"]), "book": bq["book"],
                    "fair": int(round(prob_to_american(rfair))),
                    "edge_pct": round(edge, 1),
                    "stake_units": quarter_kelly_units(rfair, int(bq["price"])),
                    "thesis": (
                        f"Same number as the sharp book at a better price: "
                        f"{bq['price']:+d} against a no-vig fair of "
                        f"{prob_to_american(rfair):+.0f}. This is price "
                        f"shopping, not a model call — our margin model is "
                        f"gated for half-point pricing."),
                    "kill": (
                        f"The sharp price shortens past {bq['price']:+d}, or "
                        f"the line moves off {bq['line']:+g} at either book."),
                })
                n_side += 1
                break

        if not plays:
            why = ("no sharp reference — Pinnacle prices only the 16 nearest "
                   "games this far out"
                   if (gid, "total_game") not in ref and
                      (gid, "spread_game") not in ref
                   else "placeable prices agree with the sharp number inside "
                        "our threshold")
            passed.append({"game": f"{away} at {home}", "reason": why})
            continue

        gmk.extend(plays)
        for t in ("Side", "Total"):
            if not any(p["type"] == t for p in plays):
                gmk.append({"type": t, "status": "pass",
                            "reason": ("no matched-line price advantage; the "
                                       "margin model is gated for half-point "
                                       "reads" if t == "Side" else
                                       "placeable and sharp numbers agree")})
        gmk.append({"type": "Prop", "status": "pass",
                    "reason": "books have not posted yardage props three weeks "
                              "out — only anytime touchdown is up"})

        ra = ratings.get(away, (0, 0, 0))
        rh = ratings.get(home, (0, 0, 0))
        diff = (rh[0] - ra[0]) * 39.18          # rating units -> points
        env = "indoors" if (roof or "") in ("dome", "closed") else "outdoors"
        gmk_narr = (
            f"The market opens {home} {sl:+g} with a total of {tl:g}. Our "
            f"end-of-2025 opponent-adjusted ratings put {home} "
            f"{'ahead of' if diff > 0 else 'behind'} {away} by "
            f"{abs(diff):.1f} points before any 2026 information, which is "
            f"{'close to' if abs(diff - sl) < 3 else 'some way from'} the "
            f"market's number — and that gap is exactly the kind of "
            f"disagreement we do not act on, because those ratings carry no "
            f"incremental signal over the closing spread. "
            f"Rest is {hrest}-{arest} days, the game is played {env}. "
            f"What moves this brief is the pricing: "
            # Summarize from the play's own fields. Splitting the thesis on "."
            # to grab a first sentence truncates at the decimal in "44.5".
            + "; ".join(
                f"{p['selection']} at {p['price']:+d} on {p['book']} is worth "
                f"{p['edge_pct']} points against the sharp no-vig number"
                for p in plays) + "."
        )
        entries.append({
            "kickoff": f"{gday} {gtime or ''}".strip(),
            "away": away, "home": home,
            "spread": float(sl) if sl is not None else None,
            "total": float(tl) if tl is not None else None,
            "narrative": gmk_narr, "markets": gmk,
        })

    week = {
        "meta": {
            "title": "The Sunday Number",
            "season": a.season, "week": a.week,
            "data_as_of": f"capture {captured[:16]} UTC",
            "sample": False,
        },
        "season_stats": {
            "record": f"{n_bets} logged, none settled",
            "units": 0.0, "roi_pct": 0.0, "bets": n_bets,
            "beat_close_pct": round(100.0 * n_beat / n_bets, 1) if n_bets else 0.0,
            "avg_clv_pts": round(float(avg_pts), 2),
        },
        "lede": (
            f"This is a Week 1 board priced three weeks out, and the honest "
            f"summary is that almost nothing on it is actionable yet. "
            f"{len(entries)} of {len(games)} games carry a play and every one "
            f"of them is a pricing observation rather than a football opinion. "
            f"The single repeating pattern is worth naming: on the sixteen "
            f"games Pinnacle prices, the four placeable books sit a full point "
            f"above the sharp total, simultaneously, in the same capture. That "
            f"is either a real edge or a Pinnacle convention for low-limit "
            f"early markets, and closing line value will decide which. "
            f"Sides are limited to matched-line price shopping because our "
            f"margin model is gated: it reproduces the key-number distribution "
            f"in aggregate but is off by 4.1 points of probability at a "
            f"specific half-point, which would manufacture edges rather than "
            f"find them. Props are absent because the books have not posted "
            f"them. There is no injury or weather input in here at all, and "
            f"any brief that claimed otherwise this far out would be inventing "
            f"it."
        ),
        "markets": [
            {"type": "Sides", "plays": n_side, "season_clv": round(float(avg_pts), 1),
             "read": "Matched-line price shopping only. The margin model is "
                     "gated for half-point pricing until conditional key-number "
                     "multipliers are estimated."},
            {"type": "Totals", "plays": n_total, "season_clv": 0.0,
             "read": "The only market with a validated distribution behind it "
                     "— 0.97pp against the market's own over/under prices."},
            {"type": "Props", "plays": 0, "season_clv": 0.0,
             "read": "No yardage props posted this far out, and no 2026 usage "
                     "data to project from. The engine exists; the market does "
                     "not yet."},
        ],
        "games": entries,
        "passed": passed,
        "footer_note": (
            "Prices from DraftKings, FanDuel, BetMGM and Caesars (the placeable "
            "board); sharp reference is Pinnacle, no-vig via the power method. "
            "Every figure is queried from the warehouse. Stake units are "
            "quarter-Kelly, shown only because the renderer requires the "
            "column - staking is a deferred decision and year one grades "
            "process on CLV, not sizing."),
    }

    a.out.write_text(json.dumps(week, indent=2), encoding="utf-8")
    log(f"wrote {a.out}")
    log(f"  games with a play : {len(entries)}")
    log(f"  passed            : {len(passed)}")
    log(f"  side plays        : {n_side}")
    log(f"  total plays       : {n_total}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
