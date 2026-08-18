"""slate_data.py - assemble everything known about one week, as JSON.

Every number here comes from the warehouse or from a validated model. Nothing
is estimated to fill a gap, and where a market cannot be priced the reason is
carried through to the output rather than dropped.

THE CENTRAL DISCIPLINE. The drive simulator supplies the SHAPE of the margin
and total distributions; the MARKET supplies their center. That split is not
stylistic - it was measured. Backtested on 816 games the simulator's own spread
has a partial correlation of -0.030 with the realised margin once the closing
line is held fixed, and betting its disagreement went 46.8% against the spread.
So the sim is recentered on the market line before any probability is read off
it. What survives that recentering is real: push probabilities, key-number
mass, alt-spread and teaser pricing - things the market prices lazily and the
sim reproduces to within 1.95pp on the null test.

WHAT IS ABSENT AND WHY
  * Player props - the books have posted anytime-touchdown on three games and
    nothing else this far out. Our usage projections exist and are included as
    context, but a point estimate with no distribution cannot price a prop, so
    none is priced.
  * Injuries and weather - the season has not started and kickoff is three
    weeks out. There is no source, so there is no claim.
  * A side opinion - see above. Sides are limited to price shopping at a
    matched line.

Usage:
    py -3 scripts/slate_data.py --week 1 -o week01_slate.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import devig_american, prob_to_american            # noqa: E402
from drive_sim import DriveModel, MODEL_PATH, HFA_POINTS      # noqa: E402
import prop_model as PM                                       # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
OPP = {"home": "away", "away": "home", "over": "under", "under": "over"}
MARKETS = ("spread_game", "total_game", "ml_game")

# Posted player markets we know how to price, mapped to the prop model's stats.
# Anything not listed is captured and shown but never quoted.
PROP_MARKETS = {
    "player_rec_yds": "rec_yds",
    "player_rush_yds": "rush_yds",
    "player_receptions": "receptions",
}

# Percent of stake. Below this the number is inside the error of the devig
# itself, so it is reported as thin and never counted as a play.
MIN_EDGE = 1.0


def log(m: str = "") -> None:
    print(m, flush=True)


# ------------------------------------------------------------------ pricing

def quotes_for(con, season: int, week: int):
    """Latest pregame quote per (game, market, book, side)."""
    return con.execute("""
        WITH r AS (
            SELECT l.game_id, l.market_id, l.book_id, b.book_name, l.side_key,
                   l.line_value, l.price_american, b.is_placeable, b.is_sharp,
                   row_number() OVER (
                       PARTITION BY l.game_id, l.market_id, l.book_id, l.side_key
                       ORDER BY l.captured_at DESC) rn
            FROM odds.line_pregame l
            JOIN ref.book b USING (book_id)
            JOIN raw.games g ON g.game_id = l.game_id
            WHERE g.season = ? AND g.week = ?
              AND l.market_id IN ('spread_game','total_game','ml_game'))
        SELECT game_id, market_id, book_id, book_name, side_key, line_value,
               price_american, is_placeable, is_sharp
        FROM r WHERE rn = 1
    """, [season, week]).fetchall()


def prop_quotes(con, season, week):
    """Latest pregame two-sided player prop quote per (game, market, book, player)."""
    return con.execute("""
        WITH r AS (
            SELECT l.game_id, l.market_id, l.gsis_id, l.book_id, b.book_name,
                   b.is_placeable, b.is_sharp, l.side_key, l.line_value,
                   l.price_american,
                   row_number() OVER (
                       PARTITION BY l.game_id, l.market_id, l.book_id,
                                    l.gsis_id, l.side_key
                       ORDER BY l.captured_at DESC) rn
            FROM odds.line_pregame l
            JOIN ref.book b USING (book_id)
            JOIN ref.market m USING (market_id)
            JOIN raw.games g ON g.game_id = l.game_id
            WHERE g.season = ? AND g.week = ? AND m.is_player
              AND l.gsis_id IS NOT NULL)
        SELECT game_id, market_id, gsis_id, book_id, book_name, is_placeable,
               is_sharp, side_key, line_value, price_american
        FROM r WHERE rn = 1
    """, [season, week]).fetchall()


def load_projections(con, season, week):
    """Projection rows keyed by (game_id, gsis_id) - the join target for any
    posted player line.

    THE PRE-SEASON FALLBACK IS THE POINT. mart.player_usage_projection is built
    from games that have been PLAYED, so before week 1 of a new season it is
    empty and every posted yardage line resolved to `no_projection`. That
    failure was silent: Week 1 showed 245 posted props, all of them anytime-TD
    and therefore `unsupported`, so nothing ever reached the join and nothing
    ever complained. The day a book posted a receiving-yards line the pipeline
    would have priced exactly zero of them.

    mart.player_projection_pre holds the pre-season projection but is keyed by
    player alone - it has no game_id, because it is built before a schedule is
    relevant. Crossing it with the week's schedule on team supplies the missing
    key. Returns the source so the caller can say which one it used.
    """
    pcols = ["gsis_id", "display_name", "team", "position", "proj_targets",
             "proj_receptions", "proj_rec_yards", "proj_carries",
             "proj_rush_yards", "proj_target_share", "n_prior"]
    projections = {}
    for r in con.execute(f"""
        SELECT game_id, {', '.join(pcols)}
        FROM mart.player_usage_projection
        WHERE season = ? AND week = ?
    """, [season, week]).fetchall():
        projections[(r[0], r[1])] = dict(zip(pcols, r[1:]))
    if projections:
        return projections, "player_usage_projection"

    for r in con.execute("""
        SELECT g.game_id, p.gsis_id, p.display_name, p.team, p.position,
               p.proj_targets, p.proj_receptions, p.proj_rec_yards,
               p.proj_carries, p.proj_rush_yards, p.proj_target_share,
               p.n_games
        FROM mart.player_projection_pre p
        JOIN raw.games g
          ON g.season = ? AND g.week = ?
         AND (g.home_team = p.team OR g.away_team = p.team)
        WHERE p.gsis_id IS NOT NULL
    """, [season, week]).fetchall():
        projections[(r[0], r[1])] = dict(zip(pcols, r[1:]))
    return projections, "player_projection_pre x schedule"


def price_props(quotes, projections, params, min_edge):
    """Join posted player lines to our projections and price the ones we can.

    THIS IS THE WHOLE POINT OF THE PROJECTION LAYER. A usage projection that
    never meets a market price is a report, not a model. Every posted line is
    carried through with an explicit outcome - priced, or refused with the
    reason - so that the day the books post yardage props this pipeline
    produces numbers without anyone rewiring it.
    """
    per = {}
    for (g, mid, gsis, bid, bn, place, sharp, side, line, price) in quotes:
        per.setdefault((g, mid, gsis, bid, bn, place, sharp), {})[side] = (line, price)

    out = []
    for (g, mid, gsis, bid, bn, place, sharp), sides in per.items():
        stat = PROP_MARKETS.get(mid)
        proj = projections.get((g, gsis))
        row = {"game_id": g, "market_id": mid, "gsis_id": gsis, "book": bn,
               "placeable": bool(place), "sharp": bool(sharp),
               "name": (proj or {}).get("display_name"),
               "team": (proj or {}).get("team"),
               "line": next((v[0] for v in sides.values()), None),
               "sides": {k: {"line": v[0], "price": v[1]}
                         for k, v in sides.items()}}
        if stat is None:
            row["status"] = "unsupported"
            row["reason"] = ("No distribution for this market. Only receiving "
                             "and rushing yards are cleared to price.")
            out.append(row)
            continue
        if proj is None:
            row["status"] = "no_projection"
            row["reason"] = ("Player resolved to a gsis_id but has no 2026 "
                             "usage projection - no prior games to project from.")
            out.append(row)
            continue
        if len(sides) != 2:
            row["status"] = "one_sided"
            row["reason"] = "Only one side posted, so the price cannot be de-vigged."
            out.append(row)
            continue

        k0 = next(iter(sides))
        k1 = OPP.get(k0)
        if k1 is None or k1 not in sides:
            row["status"] = "unpaired"
            row["reason"] = (f"Side keys {sorted(sides)} are not a two-way "
                             f"over/under pair, so there is nothing to de-vig.")
            out.append(row)
            continue
        if sides[k0][0] != sides[k1][0]:
            row["status"] = "split_line"
            row["reason"] = (f"The two sides are posted at different numbers "
                             f"({sides[k0][0]} vs {sides[k1][0]}); pricing both "
                             f"off one line would invent a middle.")
            out.append(row)
            continue
        line = sides[k0][0]
        # A WHOLE-NUMBER LINE CAN PUSH, AND WE DO NOT PRICE THE PUSH.
        # prob_over returns P(X > line); the two-way pricing then treats the
        # under as 1 - P(over), which silently pays the entire tie mass to the
        # under. Measured on the Week 1 slate that mass is 17.9% on receptions
        # and 1.3-1.4% on yardage - both larger than MIN_EDGE, so both would
        # manufacture an edge that is really a pricing error. Refuse rather
        # than approximate, the same way a split line is refused.
        if float(line) == int(float(line)):
            push = None
            try:
                a = PM.prob_over(stat, proj, float(line) - 1.0, params)
                b = PM.prob_over(stat, proj, float(line), params)
                push = (a - b) if (a is not None and b is not None) else None
            except Exception:
                push = None
            row["status"] = "pushable_line"
            row["push_pct"] = round(push * 100, 2) if push is not None else None
            row["reason"] = (
                "Whole-number line can push and the two-way pricing has no "
                "push term"
                + (f"; tie mass {push * 100:.1f}%." if push is not None else "."))
            out.append(row)
            continue
        try:
            fair_mkt = devig_american([sides[k0][1], sides[k1][1]], "power")
        except Exception:
            fair_mkt = None
        p_over = PM.prob_over(stat, proj, float(line), params)
        if p_over is None:
            row["status"] = "refused"
            row["reason"] = PM.refusal_reason(stat, proj, float(line), params)
            out.append(row)
            continue
        row["status"] = "priced"
        row["model_over_pct"] = round(p_over * 100, 2)
        row["proj_value"] = round(
            proj.get({"rec_yds": "proj_rec_yards", "rush_yds": "proj_rush_yards",
                      "receptions": "proj_receptions"}[stat]) or 0, 1)
        best = None
        for k in (k0, k1):
            pr = sides[k][1]
            # prob_over is a STRICT P(X > line); on an integer line the push
            # belongs to neither side, so the under is P(X < line), not 1-P.
            if k == "over":
                fair = p_over
            elif float(line).is_integer():
                fair = PM.prob_over(stat, proj, float(line) - 1.0, params,
                                    gate=False)
                fair = (1.0 - fair) if fair is not None else (1 - p_over)
            else:
                fair = 1 - p_over
            edge = _edge_pct(fair, pr)
            if best is None or edge > best["edge_pct"]:
                best = {"side": k, "price": pr, "edge_pct": round(edge, 2),
                        "model_pct": round(fair * 100, 2)}
        if fair_mkt:
            row["market_fair_pct"] = round(
                (fair_mkt[0] if k0 == "over" else fair_mkt[1]) * 100, 2)
        row["best"] = best
        row["verdict"] = ("play" if best["edge_pct"] >= min_edge and place
                          else "thin" if best["edge_pct"] > 0 else "pass")
        out.append(row)
    return out


def price_book(quotes):
    """-> sharp reference (line + no-vig fair per side), and the best placeable
    price per (game, market, side, line). Two-way markets devig with `power`,
    which is the routing rule for two outcomes."""
    per = {}
    for g, m, b, bn, side, line, price, place, sharp in quotes:
        per.setdefault((g, m, b, bn, place, sharp), {})[side] = (line, price)

    # Every book's number, kept for the dispersion view. Where 29 books sit on
    # one game is the most direct picture of market disagreement we have, and
    # summarising it to a single "best" throws that away.
    spread_all = {}
    sharp_ref, best, consensus = {}, {}, {}
    for (g, m, b, bn, place, sharp), sides in per.items():
        if len(sides) != 2:
            continue
        k0 = next(iter(sides))
        k1 = OPP.get(k0)
        if k1 not in sides:
            continue
        try:
            fair = devig_american([sides[k0][1], sides[k1][1]], "power")
        except (ValueError, ZeroDivisionError, TypeError) as exc:
            log(f"  WARN devig failed for {bn} {m} on {g}: {exc}")
            continue
        if sharp:
            sharp_ref[(g, m)] = {
                k0: {"line": sides[k0][0], "fair": fair[0], "price": sides[k0][1]},
                k1: {"line": sides[k1][0], "fair": fair[1], "price": sides[k1][1]},
            }
        home_side = "home" if "home" in sides else "over"
        if home_side in sides and sides[home_side][0] is not None:
            spread_all.setdefault((g, m), []).append({
                "book": bn, "line": sides[home_side][0],
                "price": sides[home_side][1],
                "placeable": bool(place), "sharp": bool(sharp)})
        if place:
            for k in (k0, k1):
                line, price = sides[k]
                consensus.setdefault((g, m, k), []).append(line)
                cur = best.get((g, m, k))
                if cur is None or _rank(m, k, line, price) > \
                        _rank(m, k, cur["line"], cur["price"]):
                    best[(g, m, k)] = {"price": price, "line": line, "book": bn}
    return sharp_ref, best, consensus, spread_all


def _rank(market_id, side, line, price):
    """Sort key for "best available", number first and price as tiebreak.

    The number dominates because half a point is worth far more than a couple
    of cents of juice on these markets. Direction differs by market: a spread
    stores each side's OWN signed handicap so higher is always better, while a
    total stores one shared line, where the over wants it low and the under
    wants it high. Moneylines carry no line at all.
    """
    if market_id == "ml_game" or line is None:
        return (0.0, price)
    if market_id == "total_game":
        return (-line, price) if side == "over" else (line, price)
    return (line, price)


# ---------------------------------------------------------------- simulation

def sim_game(model, hs, as_, neutral, n_sims):
    sims = model.simulate(hs, as_, n_sims=n_sims,
                          hfa=0.0 if neutral else HFA_POINTS)
    margins = [h - a for h, a in sims]
    totals = [h + a for h, a in sims]
    return margins, totals


def tune_to_market(model, hs, as_, target_margin, target_total, neutral,
                   n_sims, iters=7):
    """Re-simulate until the sim's own mean spread and total ARE the market's.

    The obvious approach - simulate once, then slide the output distribution
    onto the market number - is wrong, and wrong in a way that is easy to
    ship. Margins live on an integer lattice with real lumps at 3 and 7,
    because that is how football scores. Sliding by a fractional amount has to
    smear each lump across two integers, and how badly depends on the fraction:
    P(margin = 3) came out anywhere from 3.8% to 13.0% across games whose
    spreads differed by half a point. The lumps belong to the SCORE lattice,
    not to the mean, so they must not move relative to zero.

    Tuning the inputs instead keeps them where they belong. Two controls:

      d  - a symmetric strength split. Moves the margin, leaves the total flat.
      ds - a multiplier on the drive count. Moves the total, leaves the margin
           flat, and drops margin variance as it drops - which is right, since
           a 39-point game really is shorter and less variable than a 52-point
           one. Possessions are used rather than a common strength offset
           because strength clamps at the edge of the fitted range and floors
           the total near 42; the market prices Week 1 games as low as 39.

    Returns the achieved numbers alongside the targets. A double-digit
    favorite can still exhaust the strength range, and a caller that assumed
    the target was hit would be quoting a number the simulator never produced.
    """
    d_slope = model.meta.get("points_per_strength") or 35.8
    ds_slope = model.meta.get("total_per_drive_scale") or 43.7
    hfa = 0.0 if neutral else HFA_POINTS
    d, ds = 0.0, 1.0
    margins, totals, mm, tt = [], [], 0.0, 0.0
    for k in range(iters):
        # ONE SEED AND ONE SAMPLE SIZE FOR EVERY ITERATION. Re-drawing the
        # noise each step makes the loop chase sampling error rather than the
        # target; converging on a cheap sample and then re-rolling a bigger one
        # for the output silently undoes the convergence, because the same seed
        # at a different n is a different sample. Holding both fixed makes the
        # map from (d, drive_scale) to (margin, total) deterministic, so the
        # correction settles and the settled parameters are the ones that
        # produced the distribution actually reported.
        sims = model.simulate(hs + d / 2, as_ - d / 2, n_sims=n_sims, hfa=hfa,
                              seed=7, drive_scale=ds)
        margins = [h - a for h, a in sims]
        totals = [h + a for h, a in sims]
        mm = sum(margins) / len(margins)
        tt = sum(totals) / len(totals)
        if k == iters - 1:
            break
        # Damped so the two controls, which are only near-orthogonal, do not
        # chase each other.
        d += 0.9 * (target_margin - mm) / d_slope
        ds += 0.9 * (target_total - tt) / ds_slope
        ds = min(max(ds, 0.60), 1.45)
    ok = abs(mm - target_margin) <= 0.5 and abs(tt - target_total) <= 0.75
    return margins, totals, {
        "achieved_margin": round(mm, 2), "achieved_total": round(tt, 2),
        "target_margin": round(target_margin, 2),
        "target_total": round(target_total, 2),
        "margin_residual": round(mm - target_margin, 2),
        "total_residual": round(tt - target_total, 2),
        "drive_scale": round(ds, 3),
        "converged": ok,
    }


def pmf_of(values):
    n = len(values)
    return {k: c / n for k, c in sorted(Counter(values).items())}


def cover_probs(pmf, handicap):
    """P(home covers), P(push), P(away covers) for a home handicap.

    `handicap` is the home team's spread in market convention: negative when
    the home side is laying points.
    """
    need = -handicap                       # home margin must exceed this
    win = sum(p for m, p in pmf.items() if m > need)
    push = sum(p for m, p in pmf.items() if abs(m - need) < 1e-9)
    return win, push, 1.0 - win - push


def over_probs(pmf, line):
    over = sum(p for t, p in pmf.items() if t > line)
    push = sum(p for t, p in pmf.items() if abs(t - line) < 1e-9)
    return over, push, 1.0 - over - push


def find_middle(best, gid, market_id, pmf, n_sims):
    """A window where both sides of the same game can win, priced off the sim.

    This is the one place the simulator earns its keep outright. A middle is a
    question purely about the SHAPE of the distribution - how much probability
    mass sits between two numbers - and shape is the thing the sim reproduces
    to within 1.95pp on key numbers while the market prices it lazily. No view
    on who wins is required, which is fortunate, because we do not have one.

    Spread: home handicap h and away handicap a both win when -h < margin < a,
    so a window exists whenever h + a > 0. Total: over at lo and under at hi
    both win when lo < total < hi.
    """
    if market_id == "spread_game":
        h, a = best.get((gid, market_id, "home")), best.get((gid, market_id, "away"))
        if not h or not a or h["line"] + a["line"] <= 0:
            return None
        lo, hi = -h["line"], a["line"]
        legs = [("home", h), ("away", a)]
    elif market_id == "total_game":
        o, u = best.get((gid, market_id, "over")), best.get((gid, market_id, "under"))
        if not o or not u or u["line"] <= o["line"]:
            return None
        lo, hi = o["line"], u["line"]
        legs = [("over", o), ("under", u)]
    else:
        return None
    tot = sum(pmf.values()) or 1.0
    win = sum(p for k, p in pmf.items() if lo < k < hi) / tot
    # WHICH leg survives outside the window changes the payout, so the two
    # tails have to be priced separately. Taking max() of the two decimal
    # prices assumed the better-priced leg always won, which understated the
    # breakeven on every unequal pair - Buffalo at Houston came out +0.11pp
    # against a true -1.33pp, and it was the worked example in the report.
    p_a = sum(p for k, p in pmf.items() if k >= hi) / tot   # first leg alone
    p_b = sum(p for k, p in pmf.items() if k <= lo) / tot   # second leg alone

    # Two legs at American prices. Outside the window exactly one leg wins, so
    # the loss is the net of one payout against one stake.
    a_dec = [(x["price"] / 100.0 + 1) if x["price"] > 0
             else (100.0 / -x["price"] + 1) for _, x in legs]
    both = sum(a_dec) - 2.0                      # both legs win
    tails = p_a + p_b
    if tails <= 0:
        return None
    # Mass-weighted result when exactly one leg wins.
    split = (p_a * (a_dec[0] - 2.0) + p_b * (a_dec[1] - 2.0)) / tails
    ev = win * both + p_a * (a_dec[0] - 2.0) + p_b * (a_dec[1] - 2.0)
    breakeven = (-split / (both - split)) if both != split else 0.0
    # A Monte Carlo probability has a standard error, and a middle that clears
    # breakeven by less than two of them has not cleared it. Buffalo at Houston
    # came out 5.96% against a 5.84% breakeven - a margin of 0.12pp on an SE of
    # 0.17pp, which is a coin flip wearing a decimal point.
    se = (win * (1 - win) / n_sims) ** 0.5 if n_sims else 0.0
    if win - breakeven > 2 * se:
        verdict = "positive"
    elif breakeven - win > 2 * se:
        verdict = "negative"
    else:
        verdict = "line ball"
    return {
        "window": [lo, hi],
        "verdict": verdict,
        "edge_pp": round((win - breakeven) * 100, 2),
        "se_pp": round(se * 100, 2),
        "hit_prob_pct": round(win * 100, 2),
        "ev_units_per_1_each": round(ev, 4),
        "breakeven_prob_pct": round(breakeven * 100, 2),
        "legs": [{"side": s, "line": x["line"], "price": x["price"],
                  "book": x["book"]} for s, x in legs],
    }


# --------------------------------------------------------------------- build

def build(con, season, week, n_sims, min_edge=MIN_EDGE):
    model = DriveModel.load(MODEL_PATH)
    try:
        pm_params = PM.load_params()
    except (OSError, ValueError) as exc:
        log(f"  WARN prop model unavailable ({exc}); props and projection "
            f"lines will be omitted. Run: py -3 scripts/prop_model.py fit")
        pm_params = None

    teams = dict(con.execute(
        "SELECT team_id, full_name FROM ref.team WHERE is_current").fetchall())
    nick = dict(con.execute(
        "SELECT team_id, coalesce(nickname, full_name) FROM ref.team "
        "WHERE is_current").fetchall())

    games = con.execute("""
        SELECT game_id, away_team, home_team, gameday, gametime, location,
               stadium, roof, surface, spread_line, total_line,
               away_rest, home_rest, div_game
        FROM raw.games WHERE season=? AND week=? ORDER BY gameday, gametime
    """, [season, week]).fetchall()

    prior = {r[0]: dict(zip(
        ["wt", "mkt_rating", "r25", "blend", "off", "def"], r[1:]))
        for r in con.execute("""
            SELECT team, win_total, market_rating, rating_2025, blended_rating,
                   blended_off, blended_def FROM mart.preseason_prior
        """).fetchall()}

    ctx = {r[0]: dict(zip(
        ["rank", "tier", "rec", "epa", "wt", "coach", "coach_new", "qb",
         "qb_new", "qb_flag", "note"], r[1:]))
        for r in con.execute("""
            SELECT team, power_rank, tier, record_2025, their_net_epa,
                   win_total, head_coach, coach_is_new, starting_qb,
                   qb_is_new, qb_health_flag, context_note
            FROM ref.team_context WHERE season=?
        """, [season]).fetchall()}

    takes = {}
    for r in con.execute("""
        SELECT team, market, selection, line, price, stance, conviction,
               thesis, timing_note, status, published
        FROM ref.analyst_take WHERE team IS NOT NULL
        ORDER BY conviction DESC, take_id
    """).fetchall():
        takes.setdefault(r[0], []).append(dict(zip(
            ["market", "selection", "line", "price", "stance", "conviction",
             "thesis", "timing", "status", "published"], r[1:])))

    proj = {}
    for r in con.execute("""
        SELECT team, display_name, position, proj_targets, proj_rec_yards,
               proj_carries, proj_rush_yards, proj_target_share
        FROM mart.player_projection_pre
        WHERE display_name IS NOT NULL AND position IN ('WR','TE','RB')
        ORDER BY proj_targets DESC NULLS LAST
    """).fetchall():
        d = dict(zip(["name", "pos", "tgt", "rec_yds", "car", "rush_yds",
                      "tgt_share"], r[1:]))
        # OUR NUMBER, IN THE UNITS A BOOK POSTS. The median of the fitted
        # distribution is the line at which we would be indifferent, and the
        # 25th/75th points bound where the model is calibrated enough to
        # quote. Publishing them means the moment a book posts a line, the
        # comparison is immediate rather than a modeling exercise.
        if pm_params:
            pr = {"proj_targets": d["tgt"], "proj_rec_yards": d["rec_yds"],
                  "proj_carries": d["car"], "proj_rush_yards": d["rush_yds"],
                  "proj_receptions": None}
            for stat, key in (("rec_yds", "rec"), ("rush_yds", "rush")):
                if not pm_params.get(stat, {}).get("cleared"):
                    continue
                band = pm_params[stat].get("band") or list(PM.QUOTE_BAND)
                med = PM.quantile(stat, pr, 0.5, pm_params)
                lo = PM.quantile(stat, pr, band[0], pm_params)
                hi = PM.quantile(stat, pr, band[1], pm_params)
                # A None low edge means that quantile sits inside the zero
                # atom - about 16% of appearances return nothing - so the
                # honest lower bound is zero, not a missing value.
                if lo is None:
                    lo = 0.0
                if med is not None and hi is not None and med > 0.5:
                    d[key + "_line"] = round(med, 1)
                    d[key + "_band"] = [round(lo, 1), round(hi, 1)]
                    d[key + "_band_q"] = band
        proj.setdefault(r[0], []).append(d)

    projections, proj_source = load_projections(con, season, week)

    props = (price_props(prop_quotes(con, season, week), projections,
                         pm_params, min_edge) if pm_params else [])
    log(f"  projections: {len(projections):,} player-games "
        f"from {proj_source}")
    if not projections:
        log("  WARNING no projections for this week - every posted player "
            "line will refuse with no_projection")

    teams_panel = team_season(con, season)

    q = quotes_for(con, season, week)
    sharp_ref, best, _consensus, spread_all = price_book(q)
    book_count = len({(b, bn) for _, _, b, bn, _, _, _, _, _ in q})
    captured = con.execute(
        "SELECT cast(max(captured_at) AS VARCHAR) FROM odds.line").fetchone()[0]

    out_games = []
    for (gid, away, home, day, tod, loc, stadium, roof, surface, spread,
         total, arest, hrest, div) in games:
        neutral = bool(loc) and str(loc).lower().startswith("neutral")
        miss = [x for x in (home, away)
                if x not in prior or prior[x]["off"] is None
                or prior[x]["def"] is None]
        if miss:
            raise KeyError(f"no preseason prior for {miss} - cannot simulate "
                           f"{away} at {home}; rebuild mart.preseason_prior")
        hs = prior[home]["off"] + prior[away]["def"]
        as_ = prior[away]["off"] + prior[home]["def"]
        # SIGN CONVENTION, and it is a trap. raw.games.spread_line is
        # home-PERSPECTIVE (+3.5 = home favored by 3.5, so expected home
        # margin is +3.5). odds.line.line_value is the HANDICAP (-4.5 on the
        # home side means home lays 4.5). They are opposite signs. Centering
        # the margin distribution on the raw handicap put a home favorite at
        # a 31% win probability before this was caught.
        sref = sharp_ref.get((gid, "spread_game"), {}).get("home", {})
        tref = sharp_ref.get((gid, "total_game"), {}).get("over", {})
        home_handicap = float(sref["line"]) if "line" in sref else -float(spread)
        spread_center = -home_handicap                 # expected home margin
        total_center = float(tref.get("line", total))

        raw = model.simulate(hs, as_, n_sims=6000,
                             hfa=0.0 if neutral else HFA_POINTS)
        untuned_spread = sum(h - a for h, a in raw) / len(raw)
        untuned_total = sum(h + a for h, a in raw) / len(raw)
        margins, totals, fitinfo = tune_to_market(
            model, hs, as_, spread_center, total_center, neutral, n_sims)
        mpmf, tpmf = pmf_of(margins), pmf_of(totals)

        g = {
            "game_id": gid, "away": away, "home": home,
            "away_name": teams.get(away, away), "home_name": teams.get(home, home),
            "away_nick": nick.get(away, away), "home_nick": nick.get(home, home),
            "kickoff": f"{day} {tod}", "stadium": stadium,
            "roof": roof or "unknown", "surface": surface,
            "neutral": neutral, "div_game": bool(div),
            "rest": {"away": arest, "home": hrest},
            "market": {"spread": float(spread), "total": float(total)},
            "sim": {
                "n_sims": n_sims,
                "hfa_applied": 0.0 if neutral else HFA_POINTS,
                "untuned_spread": round(untuned_spread, 2),
                "untuned_total": round(untuned_total, 2),
                "tuning": fitinfo,
                
                "margin_sd": round(
                    (sum((m - fitinfo["achieved_margin"]) ** 2 for m in margins)
                     / len(margins)) ** 0.5, 2),
                "key_numbers": {str(k): round(mpmf.get(k, 0) + mpmf.get(-k, 0), 4)
                                for k in (3, 7, 6, 10, 4, 14)},
                "home_win": round(sum(p for m, p in mpmf.items() if m > 0), 4),
                # A genuine tie after overtime. `margins` is the tuned sim's
                # own output - nothing is shifted after the fact - so margin
                # zero means the game really ended level.
                "tie": round(sum(1 for x in margins if x == 0) / len(margins), 4),
                "margin_pmf": {str(k): round(v, 5)
                               for k, v in mpmf.items() if abs(v) > 0
                               and -28 <= k <= 28},
            },
            "context": {"away": ctx.get(away), "home": ctx.get(home)},
            "prior": {"away": prior.get(away), "home": prior.get(home)},
            "takes": {"away": takes.get(away, []), "home": takes.get(home, [])},
            "projections": {"away": proj.get(away, [])[:5],
                            "home": proj.get(home, [])[:5]},
            "markets": [],
        }

        # ---- side ------------------------------------------------------
        g["markets"].append(build_side(gid, home_handicap, sharp_ref, best, mpmf, min_edge))
        # ---- total -----------------------------------------------------
        g["markets"].append(build_total(gid, total_center, sharp_ref, best, tpmf, min_edge))
        # ---- moneyline -------------------------------------------------
        g["markets"].append(build_ml(gid, sharp_ref, best, mpmf, min_edge, fitinfo["converged"]))
        # A gap of half a point on a market whose outcomes are integers is
        # not a middle - no result can land strictly inside it. Those windows
        # price at exactly zero and are dropped rather than shown as findings.
        # SHAPE PRODUCTS ARE GATED ON CONVERGENCE. A middle and a push
        # probability are read straight off the distribution, so they are only
        # as good as the tuning. Where strength clamped at the edge of the
        # fitted range - double-digit favorites - the simulator never reached
        # the market's number and its shape sits around the wrong center.
        # Quoting a middle off that would be quoting a game nobody is pricing.
        # Book-by-book numbers are NOT gated on convergence - they are observed
        # prices, not model output, and stay valid however the simulator fared.
        g["book_lines"] = {
            "spread": sorted(spread_all.get((gid, "spread_game"), []),
                             key=lambda x: x["line"]),
            "total": sorted(spread_all.get((gid, "total_game"), []),
                            key=lambda x: x["line"]),
        }
        if fitinfo["converged"]:
            g["middles"] = [x for x in (
                find_middle(best, gid, "spread_game", mpmf, n_sims),
                find_middle(best, gid, "total_game", tpmf, n_sims))
                if x and x["hit_prob_pct"] > 0]
        else:
            g["middles"] = []
            # Actually suppress what the note says is suppressed. Previously
            # only `middles` was dropped while key_numbers, home_win, the full
            # margin_pmf and both push_probs stayed in the JSON - the renderer
            # hid them, so every other consumer got ungated numbers.
            for _k in ("key_numbers", "home_win", "margin_pmf", "tie"):
                g["sim"].pop(_k, None)
            for _m in g["markets"]:
                _m.pop("push_prob", None)
            g["shape_gated"] = (
                f"Simulator could not reach this market's number: it tops out "
                f"at {fitinfo['achieved_margin']:+.1f} against a line of "
                f"{fitinfo['target_margin']:+.1f}, because team strength clamps "
                f"at the edge of the fitted range on a favorite this big. "
                f"Push, key-number and middle figures are suppressed here.")
        # ---- props -----------------------------------------------------
        mine = [x for x in props if x["game_id"] == gid]
        g["props"] = mine
        priced = [x for x in mine if x["status"] == "priced"]
        pm = {"type": "Props", "market_id": "player_props",
              "posted": len(mine), "priced": len(priced), "offers": []}
        if not mine:
            pm["status"] = "unavailable"
            pm["reason"] = ("No player market posted for this game yet. The "
                            "pricer is live and will quote receiving and "
                            "rushing yards the moment lines appear.")
        elif not priced:
            pm["status"] = "unpriceable"
            pm["reason"] = (f"{len(mine)} player lines posted, none priceable: "
                            + (mine[0].get("reason") or ""))
        else:
            for x in priced:
                b = x["best"]
                pm["offers"].append({
                    "side": f'{x["name"]} {b["side"]} {x["line"]:g}',
                    "line": x["line"], "price": b["price"], "book": x["book"],
                    "edge_pct": b["edge_pct"], "verdict": x["verdict"]})
            plays = [o for o in pm["offers"] if o["verdict"] == "play"]
            pm["status"] = "play" if plays else "pass"
            if not plays:
                pm["reason"] = ("Player lines priced, none clear the edge "
                                "threshold.")
        g["markets"].append(pm)
        out_games.append(g)

    return {
        "teams": teams_panel,
        "meta": {
            "season": season, "week": week, "captured": captured,
            "books": book_count, "n_sims": n_sims, "min_edge_pct": min_edge,
            "hfa_points": HFA_POINTS,
            "sim_note": "Margin and total distributions are the simulator's "
                        "shape recentered on the market's number. The "
                        "simulator's own spread is reported but never priced "
                        "against: partial correlation -0.030 over 816 games.",
        },
        "games": out_games,
    }


def classify(offers, min_edge):
    """Split priced offers into plays, thin edges and passes.

    A THIN edge is not a small bet, it is a coin flip dressed up. Half a cent
    of hold on a two-way market is inside the error of the devig itself, so
    anything under `min_edge` is reported and not played. Where BOTH sides of
    one market clear the sharp number the situation is a middle, not two
    independent bets, and it is labeled that way so it cannot be double
    counted in a play total.
    """
    live = [o for o in offers if (o.get("edge_pct") or 0) > 0]
    for o in offers:
        e = o.get("edge_pct")
        if e is None:
            o["verdict"] = "unpriceable"
        elif e >= min_edge:
            o["verdict"] = "play"
        elif e > 0:
            o["verdict"] = "thin"
        else:
            o["verdict"] = "pass"
    middle = len(live) == 2
    if middle:
        for o in offers:
            if (o.get("edge_pct") or 0) > 0:
                o["middle"] = True
    return middle


def team_season(con, season: int, prior_season: int | None = None):
    """Season-level profile per team, for the Teams panel.

    Deliberately reads the LAST COMPLETED season for measured play, and the
    current preseason prior for what the market now thinks. Those two together
    are the interesting object: our measurement of what a team was, beside the
    market's view of what it will be, with the gap between them visible.
    """
    prior_season = prior_season or (season - 1)
    rows = con.execute("""
        SELECT team,
               avg(off_epa_play_neutral)      AS oepa,
               avg(def_epa_play_neutral)      AS depa,
               avg(off_success)               AS succ,
               avg(off_proe_neutral)          AS proe,
               avg(off_sec_per_play_neutral)  AS pace,
               avg(off_explosive_rate)        AS expl,
               avg(def_explosive_rate)        AS d_expl,
               avg(points_for)                AS pf,
               avg(points_against)            AS pa,
               sum(CASE WHEN margin > 0 THEN 1 ELSE 0 END) AS w,
               sum(CASE WHEN margin < 0 THEN 1 ELSE 0 END) AS l,
               sum(CASE WHEN margin = 0 THEN 1 ELSE 0 END) AS d,
               count(*)                       AS g
        FROM mart.team_game
        WHERE season = ? AND game_type = 'REG'
        GROUP BY 1
    """, [prior_season]).fetchall()
    cols = ["oepa", "depa", "succ", "proe", "pace", "expl", "d_expl",
            "pf", "pa", "w", "l", "d", "g"]
    out = {r[0]: dict(zip(cols, r[1:])) for r in rows}

    # Weekly net rating through the prior season - the trend line. These are
    # already point-in-time, so no leakage guard is needed to plot them.
    for team, wk, net in con.execute("""
        SELECT team, week, net_rating FROM mart.team_rating
        WHERE season = ? ORDER BY team, week
    """, [prior_season]).fetchall():
        out.setdefault(team, {}).setdefault("trend", []).append(
            [wk, round(net or 0.0, 5)])

    for r in con.execute("""
        SELECT team, win_total, market_rating, rating_2025, blended_rating
        FROM mart.preseason_prior
    """).fetchall():
        if r[0] in out:
            out[r[0]].update(zip(
                ["win_total", "market_rating", "rating_prior", "blended"], r[1:]))

    try:
        for r in con.execute("""
            SELECT team, power_rank, tier, record_2025, head_coach,
                   coach_is_new, starting_qb, qb_is_new, context_note
            FROM ref.team_context WHERE season = ?
        """, [season]).fetchall():
            if r[0] in out:
                out[r[0]].update(zip(
                    ["rank", "tier", "record", "coach", "coach_new", "qb",
                     "qb_new", "note"], r[1:]))
    except Exception as exc:
        log(f"  WARN team_context unavailable: {exc}")

    # The running narrative log. Absent until team_profile.py has been seeded,
    # which is fine - the panel degrades to the tables and figures.
    try:
        for team, wk, kind, as_of, head, body, src in con.execute("""
            SELECT team, week, kind, as_of, headline, body, source
            FROM ref.team_log WHERE season = ? ORDER BY week DESC, kind
        """, [season]).fetchall():
            if team in out:
                out[team].setdefault("log", []).append({
                    "week": wk, "kind": kind, "as_of": str(as_of),
                    "headline": head, "body": body, "source": src})
    except Exception as exc:
        log(f"  WARN team_log unavailable ({exc}); run team_profile.py seed")

    for k, v in out.items():
        v["team"] = k
        v["season_measured"] = prior_season
    return out


def _edge_pct(fair_prob, price):
    """Expected value in percent of stake at a quoted American price."""
    dec = (price / 100.0 + 1) if price > 0 else (100.0 / -price + 1)
    return (fair_prob * dec - 1.0) * 100.0


def build_side(gid, home_handicap, sharp_ref, best, mpmf, min_edge):
    m = {"type": "Side", "market_id": "spread_game",
         "market_line": home_handicap, "offers": []}
    ref = sharp_ref.get((gid, "spread_game"))
    if not ref:
        m["status"] = "no_reference"
        m["reason"] = "Pinnacle does not price this game in the capture."
        return m
    m["sharp"] = {k: {"line": v["line"], "fair_pct": round(v["fair"] * 100, 1),
                      "price": v["price"]} for k, v in ref.items()}
    for side in ("home", "away"):
        b = best.get((gid, "spread_game", side))
        if not b or side not in ref:
            continue
        matched = abs(b["line"] - ref[side]["line"]) < 1e-9
        offer = {"side": side, "line": b["line"], "price": b["price"],
                 "book": b["book"], "matched_line": matched,
                 "sharp_line": ref[side]["line"],
                 "fair_price": prob_to_american(ref[side]["fair"])}
        if matched:
            offer["edge_pct"] = round(_edge_pct(ref[side]["fair"], b["price"]), 2)
        else:
            # A different handicap is a different bet. The margin model is
            # gated for half-point pricing (4.10pp conditional error), so we
            # cannot convert the line difference into probability.
            offer["edge_pct"] = None
            offer["note"] = ("line differs from the sharp handicap; margin "
                             "model is gated for half-point conversion")
        m["offers"].append(offer)
    m["middle"] = classify(m["offers"], min_edge)
    plays = [o for o in m["offers"] if o["verdict"] == "play"]
    m["status"] = "play" if plays else "pass"
    if not plays:
        m["reason"] = ("No matched-line price advantage over the sharp number. "
                       "We do not form a side opinion: the sim carries no "
                       "incremental signal over the close (partial corr "
                       "-0.030), so any disagreement would be manufactured.")
    m["push_prob"] = round(cover_probs(mpmf, home_handicap)[1] * 100, 2)
    return m


def build_total(gid, total_center, sharp_ref, best, tpmf, min_edge):
    m = {"type": "Total", "market_id": "total_game",
         "market_line": float(total_center), "offers": []}
    ref = sharp_ref.get((gid, "total_game"))
    if not ref:
        m["status"] = "no_reference"
        m["reason"] = "Pinnacle does not price this game in the capture."
        return m
    m["sharp"] = {k: {"line": v["line"], "fair_pct": round(v["fair"] * 100, 1),
                      "price": v["price"]} for k, v in ref.items()}
    for side in ("over", "under"):
        b = best.get((gid, "total_game", side))
        if not b or side not in ref:
            continue
        gap = b["line"] - ref[side]["line"]
        offer = {"side": side, "line": b["line"], "price": b["price"],
                 "book": b["book"], "sharp_line": ref[side]["line"],
                 "line_gap": round(gap, 1),
                 "fair_price": prob_to_american(ref[side]["fair"])}
        if abs(gap) < 1e-9:
            offer["edge_pct"] = round(_edge_pct(ref[side]["fair"], b["price"]), 2)
        else:
            # DIFFERENCES ONLY. The model is used for what a line move is
            # worth, never for the level. Anchoring the level on the model
            # instead would price in every gap between our distribution and
            # the market's - here that was worth a fictional 8pp on ATL at PIT,
            # because recentering forces our probability at the line to ~50%
            # while the sharp price implied 47.1%. The market sets the level;
            # the simulator, validated on shape to 1.95pp, sets the increment.
            def _p(line):
                o, p, u = over_probs(tpmf, line)
                v = o if side == "over" else u
                return v / (1.0 - p) if p < 1.0 else v
            delta = _p(b["line"]) - _p(ref[side]["line"])
            fair = min(max(ref[side]["fair"] + delta, 1e-4), 1 - 1e-4)
            offer["edge_pct"] = round(_edge_pct(fair, b["price"]), 2)
            offer["model_fair_pct"] = round(fair * 100, 1)
            offer["pts_of_line_value_pp"] = round(delta * 100, 1)
        better = (gap < 0) if side == "over" else (gap > 0)
        offer["better_number"] = better
        m["offers"].append(offer)
    m["middle"] = classify(m["offers"], min_edge)
    plays = [o for o in m["offers"] if o["verdict"] == "play"]
    m["status"] = "play" if plays else "pass"
    if not plays:
        m["reason"] = "No price or number advantage over the sharp reference."
    m["push_prob"] = round(over_probs(tpmf, float(total_center))[1] * 100, 2)
    return m


def build_ml(gid, sharp_ref, best, mpmf, min_edge, converged=True):
    m = {"type": "Moneyline", "market_id": "ml_game", "offers": []}
    ref = sharp_ref.get((gid, "ml_game"))
    if not ref:
        m["status"] = "no_reference"
        m["reason"] = "Pinnacle does not price this game in the capture."
        return m
    m["sharp"] = {k: {"fair_pct": round(v["fair"] * 100, 1), "price": v["price"]}
                  for k, v in ref.items()}
    # Consistency check the market rarely gets wrong but sometimes does: the
    # win probability implied by the spread's shape versus the moneyline's own
    # no-vig price. A gap means the two markets disagree with each other.
    # Only meaningful when the simulator actually reached this game's line.
    # On a clamped game the entire "disagreement" is the tuning residual -
    # ARI at LAC showed -8.5pp that was really 2.8 points of unreached spread.
    if converged:
        hw = sum(p for mg, p in mpmf.items() if mg > 0)
        tie = mpmf.get(0, 0.0)
        hw_adj = hw / (1.0 - tie) if tie < 1 else hw
        m["spread_implied_home_win_pct"] = round(hw_adj * 100, 1)
        if "home" in ref:
            m["ml_vs_spread_gap_pp"] = round(
                (hw_adj - ref["home"]["fair"]) * 100, 1)
    else:
        m["shape_note"] = ("Spread-implied win probability suppressed: the "
                           "simulator did not reach this game's line, so any "
                           "gap would be tuning residual, not disagreement.")
    for side in ("home", "away"):
        b = best.get((gid, "ml_game", side))
        if not b or side not in ref:
            continue
        offer = {"side": side, "price": b["price"], "book": b["book"],
                 "fair_price": prob_to_american(ref[side]["fair"]),
                 "edge_pct": round(_edge_pct(ref[side]["fair"], b["price"]), 2)}
        m["offers"].append(offer)
    m["middle"] = classify(m["offers"], min_edge)
    plays = [o for o in m["offers"] if o["verdict"] == "play"]
    m["status"] = "play" if plays else "pass"
    if not plays:
        m["reason"] = "No placeable price beats the sharp no-vig number."
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--sims", type=int, default=20000)
    ap.add_argument("--min-edge", type=float, default=MIN_EDGE)
    ap.add_argument("-o", "--out", default="week01_slate.json")
    a = ap.parse_args()

    con = duckdb.connect(str(a.db), read_only=True)
    try:
        data = build(con, a.season, a.week, a.sims, a.min_edge)
    finally:
        con.close()
    Path(a.out).write_text(
        json.dumps(data, indent=1, default=str), encoding="utf-8")

    ng = len(data["games"])
    plays = sum(1 for g in data["games"] for m in g["markets"]
                if m.get("status") == "play")
    log(f"  {ng} games, {data['meta']['books']} books, "
        f"{a.sims:,} sims per game")
    log(f"  {plays} priced plays -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
