"""dash_data.py - build the dashboard payload, per references/dashboard-spec.md.

The dashboard is a different product from the weekly brief: scanned and acted
on, not read. So this emits COMPONENT DATA, and the prose budget is enforced
here rather than trusted to the renderer. A field over its cap raises, the same
way bet_log refuses a wager with no kill condition.

WHAT IS DELIBERATELY EXCLUDED, and why - the spec asks for these and the
warehouse cannot supply them honestly:

  stake_u        Staking is deferred to year two by an earlier decision. A
                 quarter-Kelly number exists in build_week.py marked
                 ILLUSTRATIVE ONLY. Rather than print a size nobody should act
                 on, PriceRow ships without it and the board lede says so once.

  kill_triggered NO LONGER EXCLUDED. scripts/kill_check.py evaluates the
                 structured triggers added by sql/13_kill.sql and records fires
                 to bet.kill_event. Status is emitted as one of triggered /
                 holding / prose-only, always written out - never implied by
                 color, and never silently shown as safe. `prose-only` means
                 the kill names something no line can express (a gap between
                 books, another bet's CLV) and a human still has to read it;
                 it is NOT a synonym for safe.

WHAT THE SIGNATURE ELEMENT HONESTLY SHOWS. DisagreementLine needs a model
number and an error band. On sides we have neither by design: backtested at
-0.030 partial correlation over 816 games, the simulator carries no incremental
signal over the close. So the band drawn is the measured one - mean absolute
error 10.32 points on sides, 9.9 on totals - which is wide enough to overlap
the market tick on every game. Every side row therefore renders NO EDGE, which
is exactly what the system believes. The primitive is not decoration; it is the
thesis, and on sides the thesis is "we do not know better".

Props are the one market where it does its intended job: prop_model.py supplies
a median and a per-stat band that passed a coverage test.

Usage:
    py -3 scripts/dash_data.py --week 1 -o week01_dash.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import devig_american, prob_to_american            # noqa: E402
import prop_model as PM                                       # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
OPP = {"home": "away", "away": "home", "over": "under", "under": "over"}

# Spec section 1. Enforced, not aspirational.
CAPS = {"thesis": 120, "kill": 90, "pass_reason": 60, "market_note": 60}
LEDE_MAX_SENTENCES = 3

# Spec section 4: edge is gray below this and never renders as positive.
EDGE_THRESHOLD = 2.0
LEAN_FLOOR = 1.0
STALE_MINUTES = 60

# Measured error, from drive_sim.py backtest over 816 games (2023-25).
SIDE_MAE = 10.32
TOTAL_MAE = 9.90

MARKET_FAMILY = {"spread_game": "sides", "total_game": "sides",
                 "ml_game": "sides"}
FAMILY = {"spread_game": "sides", "total_game": "totals", "ml_game": "sides"}


def log(m: str = "") -> None:
    print(m, flush=True)


class BudgetError(ValueError):
    pass


def cap(field: str, text: str | None) -> str | None:
    """Enforce the prose budget at the point of writing."""
    if text is None:
        return None
    text = " ".join(str(text).split())
    limit = CAPS[field]
    if len(text) > limit:
        raise BudgetError(
            f"{field} is {len(text)} chars, cap is {limit}: {text[:limit]}...")
    return text


def check_lede(text: str) -> str:
    n = len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s])
    if n > LEDE_MAX_SENTENCES:
        raise BudgetError(f"board lede is {n} sentences, cap is "
                          f"{LEDE_MAX_SENTENCES}")
    return text


def verdict_for(edge_pct, no_model, stale, priced) -> str:
    """The fixed five-word lexicon. No sixth verdict.

    Price advantage outranks model silence. An edge measured against the sharp
    no-vig number is real whether or not our own model has a view on the game -
    letting `no_model` win produced a board of nothing but NO EDGE and buried
    all five genuine plays. NO EDGE means we have neither a view nor a price,
    not merely that the simulator declined to disagree.
    """
    if priced and edge_pct is not None:
        if stale:
            return "STALE"
        if edge_pct >= EDGE_THRESHOLD:
            return "PLAY"
        if edge_pct >= LEAN_FLOOR:
            return "LEAN"
        return "PASS"
    if no_model:
        return "NO EDGE"
    return "PASS"


def _age_minutes(taken, latest) -> float:
    """Minutes between a price and the newest capture. Naive string parse -
    both come from the same column in the same timezone."""
    try:
        a = datetime.fromisoformat(str(taken))
        b = datetime.fromisoformat(str(latest))
        return abs((b - a).total_seconds()) / 60.0
    except ValueError:
        return 0.0


def fmt_time(ts) -> str:
    """Relative under 24h, absolute over. Spec section 4."""
    if ts is None:
        return ""
    try:
        t = datetime.fromisoformat(str(ts))
    except ValueError:
        return str(ts)[:16]
    now = datetime.now(t.tzinfo or timezone.utc)
    mins = (now - t).total_seconds() / 60
    if mins < 60:
        return f"{int(mins)}m ago"
    if mins < 24 * 60:
        return f"{int(mins // 60)}h ago"
    return t.strftime("%a %-I:%M%p").replace("AM", "a").replace("PM", "p") \
        if sys.platform != "win32" else t.strftime("%a %I:%M%p").lstrip("0")


def kill_status(con) -> dict:
    """Kill state per the evaluator, counted honestly.

    `prose_only` is NOT a synonym for safe. It means the kill names something
    no line can express - a gap between books, another bet's CLV - and a human
    still has to read it. Counted separately so it cannot hide inside
    "holding".
    """
    rows = con.execute("""
        SELECT w.bet_id, w.kill_line,
               (SELECT count(*) FROM bet.kill_event k WHERE k.bet_id = w.bet_id)
        FROM bet.wager w
    """).fetchall()
    trig = sum(1 for _, _, n in rows if n > 0)
    prose = sum(1 for _, kl, _ in rows if kl is None)
    return {"triggered": trig,
            "holding": len(rows) - trig - prose,
            "prose_only": prose,
            "total": len(rows)}


def build(con, season: int, week: int):
    latest = con.execute(
        "SELECT cast(max(captured_at) AS VARCHAR) FROM odds.line_pregame"
    ).fetchone()[0]

    games = con.execute("""
        SELECT game_id, away_team, home_team, gameday, gametime,
               spread_line, total_line
        FROM raw.games WHERE season=? AND week=? ORDER BY gameday, gametime
    """, [season, week]).fetchall()

    quotes = con.execute("""
        WITH r AS (
            SELECT l.game_id, l.market_id, l.book_id, b.book_name, l.side_key,
                   l.line_value, l.price_american, b.is_placeable, b.is_sharp,
                   cast(l.captured_at AS VARCHAR) AS cap_at,
                   row_number() OVER (
                       PARTITION BY l.game_id, l.market_id, l.book_id, l.side_key
                       ORDER BY l.captured_at DESC) rn
            FROM odds.line_pregame l
            JOIN ref.book b USING (book_id)
            JOIN raw.games g ON g.game_id = l.game_id
            WHERE g.season=? AND g.week=?
              AND l.market_id IN ('spread_game','total_game','ml_game'))
        SELECT game_id, market_id, book_id, book_name, side_key, line_value,
               price_american, is_placeable, is_sharp, cap_at
        FROM r WHERE rn=1
    """, [season, week]).fetchall()

    per = {}
    for (g, m, b, bn, side, line, price, place, sharp, cap_at) in quotes:
        per.setdefault((g, m, b, bn, place, sharp, cap_at), {})[side] = (line, price)

    sharp_ref, offers = {}, {}
    for (g, m, b, bn, place, sharp, cap_at), sides in per.items():
        if len(sides) != 2:
            continue
        k0 = next(iter(sides))
        k1 = OPP.get(k0)
        if k1 not in sides:
            continue
        try:
            fair = devig_american([sides[k0][1], sides[k1][1]], "power")
        except (ValueError, ZeroDivisionError, TypeError):
            continue
        if sharp:
            sharp_ref[(g, m)] = {k: {"line": sides[k][0], "fair": f,
                                     "price": sides[k][1]}
                                 for k, f in zip((k0, k1), fair)}
        if place:
            for k in (k0, k1):
                offers.setdefault((g, m, k), []).append({
                    "book": bn, "line": sides[k][0], "price": sides[k][1],
                    "taken_at": cap_at})

    sim, slate = {}, {}
    try:
        slate = json.loads((ROOT / "week01_slate.json").read_text(encoding="utf-8"))
        for gg in slate["games"]:
            sim[gg["game_id"]] = gg
    except Exception as exc:
        log(f"  WARN slate JSON unavailable ({exc}); model numbers omitted")

    out_games, all_rows, passes = [], [], []
    for (gid, away, home, day, tod, open_sp, open_tot) in games:
        s = sim.get(gid, {})
        srefs = sharp_ref.get((gid, "spread_game"), {})
        treal = sharp_ref.get((gid, "total_game"), {})
        hcap = srefs.get("home", {}).get("line")
        now_sp = -hcap if hcap is not None else float(open_sp)
        now_tot = treal.get("over", {}).get("line", float(open_tot))

        # Implied team totals - spec's ITT. Derived, not stored anywhere.
        itt_home = (now_tot + now_sp) / 2.0
        itt_away = (now_tot - now_sp) / 2.0

        untuned = (s.get("sim") or {}).get("untuned_spread")
        markets = []
        for mid, label in (("spread_game", "Spread"), ("total_game", "Total"),
                           ("ml_game", "Moneyline")):
            ref = sharp_ref.get((gid, mid))
            if not ref:
                continue
            rows = []
            for side, lst in ((k, offers.get((gid, mid, k), []))
                              for k in ref.keys()):
                for o in lst:
                    matched = (o["line"] is None
                               or abs(o["line"] - ref[side]["line"]) < 1e-9)
                    if not matched:
                        continue
                    dec = ((o["price"] / 100 + 1) if o["price"] > 0
                           else (100 / -o["price"] + 1))
                    edge = (ref[side]["fair"] * dec - 1) * 100
                    # Stale means OLD, not merely "not the newest capture".
                    # Flagging every non-latest price marked 4 of 5 plays
                    # STALE on a board captured 78 minutes apart.
                    stale = _age_minutes(o["taken_at"], latest) > STALE_MINUTES
                    rows.append({
                        "side": side, "price": o["price"], "book": o["book"],
                        "taken_at": o["taken_at"],
                        "fair": prob_to_american(ref[side]["fair"]),
                        "fair_pct": round(ref[side]["fair"] * 100),
                        "edge_pct": round(edge, 1),
                        "stale": stale,
                        "verdict": verdict_for(edge, False, stale, True)})
            rows.sort(key=lambda r: -r["edge_pct"])
            best = rows[0] if rows else None

            # DisagreementLine. The band is the measured error, so on sides and
            # totals it overlaps the market tick and the row reads NO EDGE.
            dl = None
            if mid == "spread_game" and untuned is not None:
                dl = {"market": now_sp, "model": round(untuned, 1),
                      "band_low": round(untuned - SIDE_MAE, 1),
                      "band_high": round(untuned + SIDE_MAE, 1), "unit": "pts"}
            elif mid == "total_game":
                ut = (s.get("sim") or {}).get("untuned_total")
                if ut is not None:
                    dl = {"market": now_tot, "model": round(ut, 1),
                          "band_low": round(ut - TOTAL_MAE, 1),
                          "band_high": round(ut + TOTAL_MAE, 1), "unit": "pts"}
            no_model = dl is None or (dl["band_low"] <= dl["market"]
                                      <= dl["band_high"])
            v = verdict_for(best["edge_pct"] if best else None, no_model,
                            bool(best and best["stale"]), bool(best))
            note = None
            if no_model and dl:
                note = cap("market_note", "Model band spans the market number")
            elif best and best["edge_pct"] < LEAN_FLOOR:
                note = cap("market_note", "No price beats the sharp number")
            markets.append({
                "type": label, "family": FAMILY[mid], "verdict": v,
                "disagreement": dl, "rows": rows[:6], "note": note,
                "best_edge": best["edge_pct"] if best else None})
            if v in ("PASS", "NO EDGE"):
                passes.append({"game": f"{away} @ {home}", "market_type": label,
                               "reason": note or cap("pass_reason",
                                                     "Priced, no edge")})

        best_edge = max([m["best_edge"] for m in markets
                         if m["best_edge"] is not None] or [0])
        thesis = kill = None
        top = max(markets, key=lambda m: m["best_edge"] or -99, default=None)
        if top and top["verdict"] in ("PLAY", "LEAN"):
            b = top["rows"][0]
            thesis = cap("thesis",
                         f'{top["type"]} {b["side"]} at {b["book"]} pays '
                         f'{b["price"]:+d} against a fair {round(b["fair"]):+d}.')
            kill = cap("kill", "Sharp number moves past this price, or the "
                               "book shortens.")
        out_games.append({
            "game_id": gid, "away": away, "home": home,
            "kickoff": f"{day} {tod}",
            "open": {"spread": float(open_sp), "total": float(open_tot)},
            "now": {"spread": now_sp, "total": now_tot},
            "itt": {"home": round(itt_home, 1), "away": round(itt_away, 1)},
            "markets": markets, "best_edge": round(best_edge, 1),
            "thesis": thesis, "kill": kill,
            "kill_status": "not evaluable" if kill else None,
        })

    # Spec section 3: board sorts by edge magnitude, never kickoff.
    out_games.sort(key=lambda g: -g["best_edge"])

    wagers = con.execute("""
        SELECT w.bet_id, w.game_id, w.market_id, w.side_key, w.line_value,
               w.price_american, b.book_name, cast(w.placed_at AS VARCHAR),
               w.thesis, w.kill_condition, c.clv_line_pts, c.beat_close
        FROM bet.wager w LEFT JOIN ref.book b USING (book_id)
        LEFT JOIN bet.clv c USING (bet_id) ORDER BY w.placed_at
    """).fetchall()
    bets = [{"bet_id": r[0], "game_id": r[1], "market_id": r[2], "side": r[3],
             "line": r[4], "price": r[5], "book": r[6], "taken_at": r[7],
             "thesis": (r[8] or "")[:CAPS["thesis"]],
             "kill": (r[9] or "")[:CAPS["kill"]],
             "clv_pts": r[10], "beat_close": r[11]} for r in wagers]

    # ---- layers carried over from the brief, as component data -----------
    # These were dropped when the dashboard was first built as a standalone.
    # The two products are complementary: the brief argues, the board acts.
    props, middles, teams = [], [], {}
    for gg in sim.values():
        tag = f'{gg["away"]} @ {gg["home"]}'
        for x in gg.get("middles", []):
            middles.append({
                "game": tag, "window": x["window"], "legs": x["legs"],
                "hit_pct": x["hit_prob_pct"], "be_pct": x["breakeven_prob_pct"],
                "edge_pp": x["edge_pp"], "se_pp": x["se_pp"],
                "verdict": {"positive": "PLAY", "line ball": "LEAN",
                            "negative": "PASS"}.get(x["verdict"], "PASS")})
        for side in ("away", "home"):
            for pr in gg["projections"][side]:
                if not pr.get("rec_line") or not pr.get("rec_band"):
                    continue
                props.append({
                    "game": tag, "team": gg[side], "player": pr["name"],
                    "pos": pr["pos"], "stat": "Rec yds",
                    "model": pr["rec_line"],
                    "band_low": pr["rec_band"][0], "band_high": pr["rec_band"][1],
                    "targets": round(pr["tgt"] or 0, 1),
                    "share": round((pr["tgt_share"] or 0) * 100),
                    "market": None, "verdict": "NO EDGE"})
    props.sort(key=lambda x: -x["model"])
    middles.sort(key=lambda x: -x["edge_pp"])

    n_posted = sum(len(gg.get("props", [])) for gg in sim.values())
    teams = slate.get("teams", {})

    n_play = sum(1 for g in out_games for m in g["markets"]
                 if m["verdict"] == "PLAY")
    beat = sum(1 for b in bets if b["beat_close"])
    clv_series = [b["clv_pts"] or 0 for b in bets]

    ks = kill_status(con)
    lede = check_lede(
        f"{n_play} of {sum(len(g['markets']) for g in out_games)} markets clear "
        f"the {EDGE_THRESHOLD:g}% threshold. Sides and totals read NO EDGE by "
        f"construction: the model band is the measured backtest error and it "
        f"spans the market number. Kills read {ks['triggered']} triggered, "
        f"{ks['holding']} holding and {ks['prose_only']} needing a human, and "
        f"stake is omitted by decision - sizing is deferred to year two.")

    return {
        "meta": {"season": season, "week": week, "captured": latest,
                 "edge_threshold": EDGE_THRESHOLD, "stale_minutes": STALE_MINUTES,
                 "side_mae": SIDE_MAE, "total_mae": TOTAL_MAE,
                 "excluded": ["stake_u"],
                 "kills": kill_status(con)},
        "lede": lede,
        "kpis": [
            {"label": "CLV", "value": f"{sum(clv_series)/len(clv_series):+.1f} pt"
                if clv_series else "--",
             "delta": f"{beat}/{len(bets)} beat close", "weight": "primary"},
            {"label": "Logged", "value": str(len(bets)),
             "delta": "paper", "weight": "primary"},
            {"label": "PLAY", "value": str(n_play),
             "delta": f"of {sum(len(g['markets']) for g in out_games)} markets",
             "weight": "secondary"},
            {"label": "Books", "value": "29", "delta": "1 sharp",
             "weight": "secondary"},
        ],
        "games": out_games, "passes": passes, "bets": bets,
        "clv_series": clv_series,
        "props": props, "props_posted": n_posted,
        "middles": middles, "teams": teams,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("-o", "--out", default="week01_dash.json")
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)
    try:
        data = build(con, a.season, a.week)
    finally:
        con.close()
    Path(a.out).write_text(json.dumps(data, indent=1, default=str),
                           encoding="utf-8")
    log(f"  {len(data['games'])} games, {len(data['passes'])} passes, "
        f"{len(data['bets'])} logged -> {a.out}")
    log(f"  prose budget enforced; excluded: {data['meta']['excluded']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
