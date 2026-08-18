"""kill_check.py - do the kill conditions actually fire?

A kill condition that is never evaluated is a comment. Five decisions were
logged with mandatory, non-empty kills and not one had ever been checked,
because checking meant re-reading five sentences against a board that moves
hourly. This turns that into a command.

WHAT IT DOES NOT DO. It does not parse the English. A regex that is right most
of the time on a control surface is worse than nothing, because it reads as
coverage. Kills with a structured trigger (`kill_line` / `kill_dir` /
`kill_book`) are evaluated mechanically and fire into bet.kill_event. Kills
without one are printed next to the actual line movement, so the judgement is
made against numbers instead of memory - and the report says plainly how many
are in each bucket.

DIRECTION IS THE SUBTLE PART. "Worse" depends on which side you are on. For a
spread, a home favorite at -3.5 gets worse as the number falls further below
-3.5; a home dog at +1.5 gets worse as the number shrinks. For a total, an
under gets worse as the number falls and an over as it rises. `kill_dir` is
stated relative to the POSITION, and the comparison is derived from the side.

Usage:
    py -3 scripts/kill_check.py                # report
    py -3 scripts/kill_check.py --arm          # backfill structured triggers
    py -3 scripts/kill_check.py --fire         # record events for fired kills
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
KILL_SQL = ROOT / "sql" / "13_kill.sql"

# Structured triggers backfilled from the prose already on record. Each one is
# a reading of a sentence a human wrote, recorded here so it can fire without
# that human re-reading it. bet_id -> (kill_line, kill_dir, kill_book)
BACKFILL = {
    # "Line moves to -4.5 or worse at DK" - laying more points is worse.
    2: (-4.5, "at_or_worse", "draftkings"),
    # "Would revisit if DK moves to -3.0 while the sharp number holds" - this
    # is a RE-ENTRY trigger on a pass, not an exit.
    3: (-3.0, "at_or_better", "draftkings"),
    # "Revisit only if the gap widens beyond a full point before Friday" - the
    # gap is not a single book's line, so this one stays unstructured. Left out
    # deliberately rather than forced into a shape it does not have.
    # "Pinnacle moves up to 44+ ... i.e. the gap closes" - an under at 44.5
    # gets worse as the total rises. NAMES PINNACLE, which is a sharp reference
    # and deliberately not placeable. Pointing this at the placeable board
    # fired a kill that had not happened.
    5: (44.0, "at_or_worse", "pinnacle"),
    # "If the NE/SEA position earns positive CLV by close" - depends on another
    # decision's grade, not on a line. Cannot be a line trigger.
}


def log(m: str = "") -> None:
    print(m, flush=True)


def worse_is_lower(market_id: str, side_key: str) -> bool:
    """Does the position get worse as the number FALLS?

    spread: holding the home side at -3.5, a fall to -4.5 means laying more,
            which is worse. Holding a dog at +1.5, a fall to +0.5 is also
            worse. So for a spread, worse is always lower for the side you
            hold, because line_value is quoted from that side's perspective.
    total : an under gets worse as the total RISES; an over as it falls.
    """
    if market_id.startswith("total"):
        return side_key.lower() == "over"
    return True


def arm(con) -> int:
    n = 0
    for bet_id, (kl, kd, kb) in BACKFILL.items():
        con.execute("""UPDATE bet.wager SET kill_line=?, kill_dir=?, kill_book=?
                       WHERE bet_id=?""", [kl, kd, kb, bet_id])
        n += 1
    tot = con.execute("SELECT count(*) FROM bet.wager").fetchone()[0]
    log(f"armed {n} of {tot} decisions with a structured trigger")
    log(f"{tot - n} stay prose-only and are adjudicated by hand against the")
    log("line movement printed below. That is a real gap, not a rounding error.")
    return 0


def check(con, fire: bool) -> int:
    rows = con.execute("""
        SELECT w.bet_id, w.decision, w.game_id, w.market_id, w.side_key,
               w.line_value, w.kill_line, w.kill_dir, w.kill_book,
               w.kill_condition
        FROM bet.wager w ORDER BY w.bet_id
    """).fetchall()
    if not rows:
        log("no decisions logged")
        return 0

    log("=" * 78)
    log("KILL CHECK")
    log("=" * 78)
    fired, armed, prose = [], 0, 0

    for (bid, dec, gid, mkt, side, logged, kl, kd, kb, prose_txt) in rows:
        # current line at the relevant book(s)
        q = """
            SELECT l.book_id, l.line_value, l.price_american,
                   cast(l.captured_at AS VARCHAR) AS captured_at,
                   l.snapshot_id
            FROM odds.line_pregame l JOIN ref.book b USING (book_id)
            WHERE l.game_id=? AND l.market_id=? AND l.side_key=?
        """
        p = [gid, mkt, side]
        if kb:
            # A kill written against a NAMED book is checked at that book even
            # when it is not placeable. A Pinnacle trigger is the whole point
            # of keeping a sharp reference, and filtering to placeable books
            # silently evaluated it against the wrong number - which fired a
            # kill on decision #5 that had not actually happened.
            q += " AND l.book_id=?"
            p.append(kb)
        else:
            q += " AND b.is_placeable"
        q += " QUALIFY row_number() OVER (PARTITION BY l.book_id ORDER BY l.captured_at DESC)=1"
        cur = con.execute(q, p).fetchall()
        if not cur:
            log(f"\n  #{bid} {dec:<6} {gid} {mkt} {side} @ {logged}")
            log(f"     no current placeable line{' at ' + kb if kb else ''}"
                f" - cannot evaluate")
            continue

        low = worse_is_lower(mkt, side)
        # the worst line currently available is the one a kill would fire on
        worst = min(cur, key=lambda r: r[1]) if low else max(cur, key=lambda r: r[1])
        best = max(cur, key=lambda r: r[1]) if low else min(cur, key=lambda r: r[1])
        move = worst[1] - logged

        log(f"\n  #{bid} {dec:<6} {gid} {mkt} {side}")
        log(f"     logged {logged:+g}   now {best[1]:+g} best / {worst[1]:+g} worst"
            f"   ({len(cur)} book{'s' if len(cur) != 1 else ''})   move {move:+g} pts")

        if kl is None:
            prose += 1
            log(f"     PROSE ONLY - judge by hand: {prose_txt[:66]}")
            continue

        armed += 1
        # An exit reads the WORST line on offer - that is what you would be
        # stuck with. A re-entry reads the BEST - that is what you could take.
        # `low` says which direction hurts. Written out as two cases rather
        # than folded into one conditional expression: the folded version was
        # unreadable enough to hide a false fire.
        if kd == "at_or_worse":
            ref = worst[1]
            hit = ref <= kl if low else ref >= kl
        else:
            ref = best[1]
            hit = ref >= kl if low else ref <= kl
        if hit:
            fired.append((bid, worst, logged, move, kd))
            verb = "RE-ENTRY" if kd == "at_or_better" else "KILL"
            log(f"     >>> {verb} FIRED at {kl:+g} ({kd}) - "
                f"{worst[0]} is {worst[1]:+g}")
        else:
            log(f"     armed at {kl:+g} ({kd}) - not triggered")

    log("\n" + "=" * 78)
    log(f"  {armed} armed, {prose} prose-only, {len(fired)} fired")
    if prose:
        log(f"  {prose} kills cannot fire on their own. Arm them or accept that")
        log("  they are notes.")
    if fire and fired:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        mx = con.execute(
            "SELECT coalesce(max(kill_event_id),0) FROM bet.kill_event").fetchone()[0]
        for i, (bid, w, logged, move, kd) in enumerate(fired, 1):
            con.execute("""INSERT INTO bet.kill_event VALUES (?,?,?,?,?,?,?,?,?)""",
                        [mx + i, bid, now, w[0], w[1], logged, move, w[4],
                         "re-entry" if kd == "at_or_better" else "kill"])
        log(f"  recorded {len(fired)} event(s) to bet.kill_event")
    elif fired:
        log("  re-run with --fire to record these to bet.kill_event")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--arm", action="store_true")
    ap.add_argument("--fire", action="store_true")
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    try:
        con.execute(KILL_SQL.read_text(encoding="utf-8"))
        if a.arm:
            return arm(con)
        return check(con, a.fire)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
