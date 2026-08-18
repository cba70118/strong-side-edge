"""
bet_log.py - decision log and CLV grading.

Records what you bet AND what you passed, refuses anything without a
falsifiable thesis, and grades every decision against the sharp close.

Subcommands:
    log      record a decision (placed or passed)
    grade    attach CLV to every ungraded decision
    report   CLV by market, by week, placed vs passed
    show     list decisions

Examples:
    py -3 scripts/bet_log.py log --game 2026_01_NE_SEA --market spread_game \\
        --side home --book draftkings --decision placed \\
        --thesis "SEA line 1pt short of Pinnacle with no injury news to explain it" \\
        --kill "Darnold downgraded or line moves through -4.5 before Sunday"

    py -3 scripts/bet_log.py grade
    py -3 scripts/bet_log.py report

Price is looked up from the latest odds.line snapshot unless --price is given,
so a logged decision reflects a price we actually observed rather than one
remembered.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import (american_to_prob, default_method,  # noqa: E402
                   devig_american, hold_pct)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

MIN_THESIS = 20
MIN_KILL = 10

OPPOSITE = {"home": "away", "away": "home",
            "over": "under", "under": "over",
            "yes": "no", "no": "yes"}


def log(m: str = "") -> None:
    print(m, flush=True)


def connect(db: Path, read_only: bool = False):
    con = duckdb.connect(str(db), read_only=read_only)
    return con


# --------------------------------------------------------------------------
# log
# --------------------------------------------------------------------------

def cmd_log(con, a) -> int:
    if a.thesis is None or len(a.thesis.strip()) < MIN_THESIS:
        log(f"REFUSED: --thesis must be at least {MIN_THESIS} characters.")
        log("  A bet without a falsifiable reason cannot be reviewed, only")
        log("  rationalised after the fact. Write why: which number, which")
        log("  book, what the market is missing.")
        return 1
    if a.kill is None or len(a.kill.strip()) < MIN_KILL:
        log(f"REFUSED: --kill (kill condition) must be at least {MIN_KILL} chars.")
        log("  State what would make this bet wrong BEFORE you find out.")
        return 1

    g = con.execute("""
        SELECT game_id, season, week, gameday, home_team, away_team
        FROM raw.games WHERE game_id = ?
    """, [a.game]).fetchone()
    if not g:
        log(f"REFUSED: unknown game_id {a.game!r}. "
            f"Use the nflverse form, e.g. 2026_01_NE_SEA.")
        return 1

    if not con.execute("SELECT 1 FROM ref.market WHERE market_id = ?",
                       [a.market]).fetchone():
        mk = [r[0] for r in con.execute(
            "SELECT market_id FROM ref.market ORDER BY 1").fetchall()]
        log(f"REFUSED: unknown market {a.market!r}. Known: {', '.join(mk)}")
        return 1

    bk = con.execute(
        "SELECT book_id, book_name, is_placeable FROM ref.book WHERE book_id = ?",
        [a.book]).fetchone()
    if not bk:
        log(f"REFUSED: unknown book {a.book!r}.")
        return 1
    if a.decision == "placed" and not bk[2]:
        log(f"REFUSED: {bk[1]} is not placeable in Louisiana. "
            f"A price you cannot take is not a bet.")
        return 1

    # price: prefer an observed one
    price, line_val, snap_id = a.price, a.line, None
    if price is None:
        row = con.execute("""
            SELECT l.price_american, l.line_value, l.snapshot_id
            FROM odds.line l
            WHERE l.game_id = ? AND l.market_id = ? AND l.side_key = ?
              AND l.book_id = ?
            ORDER BY l.captured_at DESC LIMIT 1
        """, [a.game, a.market, a.side, a.book]).fetchone()
        if not row:
            log(f"REFUSED: no observed price for {a.side} {a.market} at "
                f"{a.book} on {a.game}, and no --price given.")
            log("  Take a snapshot first, or pass --price explicitly.")
            return 1
        price, line_val, snap_id = row[0], (a.line if a.line is not None else row[1]), row[2]

    now = datetime.now(timezone.utc)

    # kickoff_at stays inside SQL. Reading a TIMESTAMPTZ back into Python makes
    # duckdb demand pytz, and adding a dependency to format one timestamp is a
    # bad trade against the stdlib-lean rule.
    bet_id = con.execute("""
        INSERT INTO bet.wager
            (placed_at, is_paper, decision, game_id, season, week, market_id,
             side_key, gsis_id, book_id, line_value, price_american, stake,
             model_id, fair_prob, ev_pct, snapshot_id, thesis, kill_condition,
             kickoff_at, logged_before_kickoff, note)
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
               k.kick,
               (k.kick IS NULL OR ? <= k.kick),
               ?
        FROM (SELECT min(commence_time) AS kick
              FROM odds.game_map WHERE game_id = ?) k
        RETURNING bet_id
    """, [now, not a.real, a.decision, a.game, g[1], g[2], a.market, a.side,
          a.player, a.book, line_val, int(price), a.stake, a.model,
          a.fair_prob, a.ev, snap_id, a.thesis.strip(), a.kill.strip(),
          now, a.note, a.game]).fetchone()[0]

    late = con.execute("""
        SELECT logged_before_kickoff, cast(kickoff_at AS VARCHAR)
        FROM bet.wager WHERE bet_id = ?
    """, [bet_id]).fetchone()

    log(f"logged bet_id={bet_id}  [{a.decision.upper()}]"
        f"{'  (PAPER)' if not a.real else ''}")
    log(f"  {g[5]} @ {g[4]}  {a.market} {a.side} "
        f"{line_val if line_val is not None else ''} @ {int(price):+d} ({bk[1]})")
    log(f"  kickoff: {late[1] or 'unknown'}")
    log(f"  thesis: {a.thesis.strip()[:100]}")
    log(f"  kill  : {a.kill.strip()[:100]}")
    if late[0] is False:
        log("  WARNING: logged AFTER kickoff - flagged in "
            "audit.bets_logged_after_kickoff")
    return 0


# --------------------------------------------------------------------------
# grade
# --------------------------------------------------------------------------

def sharp_price(con, game_id, market_id, side_key, line_value, gsis_id):
    """Best available sharp reference for a side, plus its opposite.

    Prefers a phase='close' snapshot; otherwise falls back to the most recent
    observation and reports the grade as provisional. Same book, same snapshot
    and same line for both sides, or the devig is meaningless.
    """
    for phase_filter, basis in (("s.phase = 'close'", "close"), ("TRUE", "reference")):
        row = con.execute(f"""
            SELECT l.book_id, l.snapshot_id, l.line_value, l.price_american,
                   b.clv_priority
            FROM odds.line l
            JOIN odds.snapshot s ON s.snapshot_id = l.snapshot_id
            JOIN ref.book b      ON b.book_id = l.book_id
            WHERE l.game_id = ? AND l.market_id = ? AND l.side_key = ?
              AND b.is_sharp AND {phase_filter}
              AND (? IS NULL OR l.gsis_id = ?)
            ORDER BY b.clv_priority NULLS LAST, l.captured_at DESC
            LIMIT 1
        """, [game_id, market_id, side_key, gsis_id, gsis_id]).fetchone()
        if not row:
            continue
        book_id, snap, close_line, close_price, _ = row
        opp = OPPOSITE.get(side_key)
        if opp is None:
            continue
        orow = con.execute("""
            SELECT price_american FROM odds.line
            WHERE snapshot_id = ? AND game_id = ? AND market_id = ?
              AND book_id = ? AND side_key = ?
              AND (line_value IS NULL OR abs(line_value) = abs(?))
            ORDER BY captured_at DESC LIMIT 1
        """, [snap, game_id, market_id, book_id, opp, close_line]).fetchone()
        if not orow:
            continue
        return book_id, close_line, close_price, orow[0], basis
    return None


def clv_points(market_id, side_key, our_line, close_line):
    """Line CLV in points. Positive = we hold the better number."""
    if our_line is None or close_line is None:
        return None
    if market_id in ("spread_game", "spread_1h", "spread_alt"):
        # works for both favorite (negative) and dog (positive) handicaps
        return round(our_line - close_line, 2)
    if market_id in ("total_game", "total_1h", "total_alt", "team_total") \
            or market_id.startswith("player_"):
        if side_key in ("over", "yes"):
            return round(close_line - our_line, 2)
        if side_key in ("under", "no"):
            return round(our_line - close_line, 2)
    return None


def _load_margin_model(con):
    """Returns (margin_params, totals_params); either may be None if unbuilt."""
    mm = tm = None
    try:
        from margin_model import load_params
        mm = load_params(con)
    except Exception:
        pass
    try:
        from totals_model import load_params as tload
        tm = tload(con)
    except Exception:
        pass
    return (mm, tm) if (mm or tm) else None


def _fair_prob_via_margin_model(con, mm, market_id, side, our_line, close_line):
    """Fair probability of OUR bet under the sharp line's margin distribution.

    Conditioning uses nflverse spread_line convention (positive = home
    favored); odds.line stores the odds-feed handicap (home favored ->
    negative), so the two are negations. close_line is the handicap for OUR
    side, so the home-perspective spread_line depends on which side we took.
    """
    if mm is None or close_line is None or our_line is None:
        return None
    margin_p, totals_p = mm

    if market_id in ("total_game", "total_alt", "total_1h") and totals_p:
        # The totals model validates at 0.97pp against the market's own
        # over/under prices, and totals key numbers are weak enough that the
        # conditional distortion which disqualifies the margin model here does
        # not bite: predicted over rate stays ~50% at every line, as a
        # market-anchored model should.
        from totals_model import over_prob, pmf_for_total
        sig, mult = totals_p
        pmf = pmf_for_total(round(float(close_line) * 2) / 2, sig, mult)
        o, push = over_prob(pmf, float(our_line))
        if push >= 1.0:
            return None
        p_over = o / (1.0 - push)
        return p_over if side in ("over", "yes") else 1.0 - p_over

    if market_id in ("spread_game", "spread_alt") and margin_p:
        # DELIBERATELY DISABLED. margin_model reproduces the unconditional
        # key-number distribution (0.40pp) and the moneyline (2.70pp), but at
        # a specific half-point its cover probability is off by 4.10pp against
        # 2006+ history - it swings +5.3pp across the 3 where reality swings
        # -2.0pp. Grading a spread through it would manufacture CLV. Points
        # only until conditional multipliers are estimated.
        return None
    return None


def cmd_grade(con, a) -> int:
    mm = _load_margin_model(con)
    if mm is None:
        log("note: margin model not built - line differences will be graded in")
        log("      points only. Run: py -3 scripts/margin_model.py build\n")
    pending = con.execute("""
        SELECT w.bet_id, w.game_id, w.market_id, w.side_key, w.line_value,
               w.price_american, w.gsis_id
        FROM bet.wager w LEFT JOIN bet.clv c ON c.bet_id = w.bet_id
        WHERE c.bet_id IS NULL
    """).fetchall()
    if not pending:
        log("nothing to grade")
        return 0

    log(f"grading {len(pending)} decision(s)\n")
    graded = skipped = 0
    for bet_id, gid, mid, side, our_line, our_price, gsis in pending:
        found = sharp_price(con, gid, mid, side, our_line, gsis)
        if not found:
            log(f"  bet {bet_id}: no sharp reference for {mid}/{side} - skipped")
            skipped += 1
            continue
        book_id, close_line, close_price, opp_price, basis = found

        method = default_method("side", 2)
        try:
            fair = devig_american([close_price, opp_price], method)
        except ValueError as e:
            log(f"  bet {bet_id}: devig failed ({e}) - skipped")
            skipped += 1
            continue
        close_fair = fair[0]
        our_imp = american_to_prob(our_price)
        pts = clv_points(mid, side, our_line, close_line)

        # CRITICAL: a probability comparison is only valid when both sides
        # refer to the SAME bet. SEA -3.5 and SEA -4.5 are different bets, so
        # differencing their probabilities is meaningless - and because our
        # price still carries the book's vig while the reference is de-vigged,
        # the difference lands near minus-half-the-hold on every wager and
        # looks like a uniform loss.
        #
        # So: when the lines match, measure price (prob delta at equal line).
        # When they differ, measure the LINE (points), which is the thing that
        # actually differs. Converting a line difference into a probability
        # needs a points->probability model built on the key-number
        # distribution; until that exists, do not fake it.
        same_line = (
            our_line is None and close_line is None
        ) or (
            our_line is not None and close_line is not None
            and abs(float(our_line) - float(close_line)) < 1e-9
        )

        if same_line:
            prob_delta = close_fair - our_imp
            beat = prob_delta > 0
            metric = "prob_at_same_line"
        else:
            # Lines differ. Price OUR handicap under the margin distribution
            # implied by the SHARP line, which makes the comparison a like-for-
            # like probability again. Falls back to points when no model covers
            # the market (totals have no distribution model yet).
            prob_delta = None
            metric = "line_pts"
            fair_ours = _fair_prob_via_margin_model(
                con, mm, mid, side, our_line, close_line)
            if fair_ours is not None:
                prob_delta = fair_ours - our_imp
                metric = "prob_via_margin_model"
            beat = ((prob_delta > 0) if prob_delta is not None
                    else (pts is not None and pts > 0))

        con.execute("""
            INSERT INTO bet.clv
                (bet_id, close_line, close_price_am, close_book_id,
                 close_novig_prob, clv_line_pts, clv_prob_delta, clv_cents,
                 beat_close, graded_at, grade_basis, devig_method,
                 our_implied_prob, clv_metric)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [bet_id, close_line, int(close_price), book_id, close_fair, pts,
              prob_delta, float(our_price) - float(close_price),
              beat, datetime.now(timezone.utc), basis, method,
              our_imp, metric])
        graded += 1
        flag = "BEAT" if beat else "lost"
        if same_line:
            detail = (f"ours {our_price:+d} ({our_imp:.4f}) vs fair "
                      f"{close_fair:.4f} -> {flag} {prob_delta*100:+.2f}pp")
        elif prob_delta is not None:
            detail = (f"our {our_line} vs {book_id} {close_line} -> {flag} "
                      f"{pts:+.1f} pts = {prob_delta*100:+.2f}pp")
        else:
            detail = (f"our line {our_line} vs {book_id} {close_line} "
                      f"-> {flag} {pts:+.1f} pts")
        log(f"  bet {bet_id}: {mid}/{side}  {detail}  "
            f"[{basis}/{metric}, hold {hold_pct([close_price, opp_price]):.2f}%]")

    log(f"\ngraded {graded}, skipped {skipped}")
    if skipped:
        log("  skipped decisions stay in bet.v_ungraded and can be re-graded "
            "once a close snapshot exists")
    return 0


# --------------------------------------------------------------------------
# report / show
# --------------------------------------------------------------------------

def _f(v, width: int, dash: str = "-") -> str:
    """Format a possibly-NULL aggregate. stddev over n=1 is NULL, and
    clv_prob_delta is NULL whenever the grade was measured in line points."""
    return f"{dash:>{width}}" if v is None else f"{v:>{width}}"


def cmd_report(con, a) -> int:
    log("=" * 74)
    log("PLACED vs PASSED")
    log("=" * 74)
    log("If passes beat the close as often as placements, the selection")
    log("filter is adding nothing and is only costing volume.\n")
    rows = con.execute("SELECT * FROM audit.placed_vs_passed").fetchall()
    if not rows:
        log("  no graded decisions yet")
    else:
        log(f"  {'decision':<10}{'n':>4}{'avg pts':>9}{'avg pp':>9}"
            f"{'by pts':>8}{'by prob':>9}{'beat':>6}{'beat %':>9}")
        for r in rows:
            log(f"  {r[0]:<10}{r[1]:>4}{_f(r[2],9)}{_f(r[3],9)}"
                f"{r[4]:>8}{r[5]:>9}{r[6]:>6}{_f(r[7],9)}")

    log("\n" + "=" * 74)
    log("CLV BY MARKET")
    log("=" * 74)
    rows = con.execute("SELECT * FROM audit.clv_by_market").fetchall()
    if not rows:
        log("  none")
    else:
        log(f"  {'decision':<9}{'family':<12}{'market':<16}{'n':>4}"
            f"{'CLV pp':>9}{'pts':>8}{'beat %':>9}")
        for r in rows:
            log(f"  {r[0]:<9}{r[1]:<12}{r[2]:<16}{r[3]:>4}{_f(r[4],9)}"
                f"{_f(r[5],8)}{_f(r[7],9)}")

    log("\n" + "=" * 74)
    log("DISCIPLINE AUDITS  (all must read 0)")
    log("=" * 74)
    for view in ("bets_missing_thesis", "bets_logged_after_kickoff",
                 "bets_bad_decision", "unplaceable_bets"):
        n = con.execute(f"SELECT count(*) FROM audit.{view}").fetchone()[0]
        log(f"  {'ISSUE' if n else 'ok   '} {view:<30}{n}")

    n = con.execute("SELECT count(*) FROM bet.v_ungraded").fetchone()[0]
    log(f"\n  ungraded decisions: {n}")

    log("\n" + "-" * 74)
    log("  READING THIS")
    log("-" * 74)
    log("  'CLV pp' is the fair probability of the exact bet under the sharp")
    log("  book's implied margin distribution, MINUS the probability we paid")
    log("  for. It is expected edge, not line movement.")
    log("")
    log("  A -110 price implies 52.38%, so a coin-flip bet starts at -2.38pp.")
    log("  Most decisions will therefore be negative, and that is correct -")
    log("  the vig is real. The benchmark is AVERAGE CLV pp above zero over a")
    log("  meaningful sample, NOT a 50% hit rate.")
    log("")
    log("  'avg pts' is the classic line-movement view and is shown alongside")
    log("  because the two can disagree: a bet can win a full point of line")
    log("  value and still be -EV if the price paid does not cover the vig.")
    return 0


def cmd_show(con, a) -> int:
    rows = con.execute("""
        SELECT w.bet_id, w.decision, w.game_id, w.market_id, w.side_key,
               w.line_value, w.price_american, w.book_id,
               coalesce(round(c.clv_prob_delta * 100, 2), NULL) AS clv_pp,
               c.beat_close, w.thesis
        FROM bet.wager w LEFT JOIN bet.clv c ON c.bet_id = w.bet_id
        ORDER BY w.bet_id
    """).fetchall()
    if not rows:
        log("no decisions logged")
        return 0
    log(f"{'id':<4}{'decision':<9}{'game':<18}{'market':<14}{'side':<7}"
        f"{'line':>6}{'price':>7}{'book':<12}{'CLV pp':>8}")
    for r in rows:
        log(f"{r[0]:<4}{r[1]:<9}{r[2]:<18}{r[3]:<14}{r[4]:<7}"
            f"{str(r[5]) if r[5] is not None else '':>6}{r[6]:>+7d}{r[7]:<12}"
            f"{str(r[8]) if r[8] is not None else '-':>8}")
        log(f"     thesis: {r[10][:96] if r[10] else ''}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Bet decision log + CLV grading.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("log", help="record a decision")
    p.add_argument("--game", required=True)
    p.add_argument("--market", required=True)
    p.add_argument("--side", required=True)
    p.add_argument("--book", required=True)
    p.add_argument("--decision", choices=["placed", "passed"], default="placed")
    p.add_argument("--thesis", required=True)
    p.add_argument("--kill", required=True, help="kill condition")
    p.add_argument("--price", type=int, default=None)
    p.add_argument("--line", type=float, default=None)
    p.add_argument("--player", default=None, help="gsis_id for prop markets")
    p.add_argument("--stake", type=float, default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--fair-prob", type=float, default=None, dest="fair_prob")
    p.add_argument("--ev", type=float, default=None)
    p.add_argument("--note", default=None)
    p.add_argument("--real", action="store_true",
                   help="real money (default is paper)")

    sub.add_parser("grade", help="grade all ungraded decisions")
    sub.add_parser("report", help="CLV report")
    sub.add_parser("show", help="list decisions")

    a = ap.parse_args()
    if not a.db.exists():
        log(f"database not found: {a.db}")
        return 1
    con = connect(a.db, read_only=(a.cmd in ("report", "show")))
    try:
        return {"log": cmd_log, "grade": cmd_grade,
                "report": cmd_report, "show": cmd_show}[a.cmd](con, a)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
