"""season_log.py - append what each build produced, so the season compounds.

WHY. Every artifact so far described one slate, and the slate payload is
overwritten on the next run. By week three there would be no way to answer
"what did the system say in week one, and was it right" - the one question that
gets more valuable as the season runs rather than less.

`log.week_build` is APPEND-ONLY. A build never edits an earlier week's row,
because the point of the record is that it was written before the games were
played. Results arrive separately, from raw.games, and the season view joins
the two.

Usage:
    py -3 scripts/season_log.py append -i week01_slate.json
    py -3 scripts/season_log.py show
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
SEASON_SQL = ROOT / "sql" / "14_season.sql"


def log(m=""):
    print(m, flush=True)


def current_week(con, season: int) -> int:
    row = con.execute(
        "SELECT week FROM mart.current_week WHERE season = ?", [season]
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 1


def append(con, payload) -> int:
    meta = payload.get("meta") or {}
    games = payload.get("games") or []
    season = int(meta.get("season") or 2026)
    week = int(meta.get("week") or 1)

    n_markets = sum(len(g.get("markets") or []) for g in games)
    # market status is the slate's own vocabulary: play / pass / thin /
    # unpriceable / unavailable. Guessing at `verdict` or `is_play` logged zeros
    # against a slate that had five plays.
    mk = [m for g in games for m in (g.get("markets") or [])]
    n_plays = sum(1 for m in mk if m.get("status") == "play")
    n_thin = sum(1 for m in mk if m.get("status") == "thin")
    n_mid = sum(len(g.get("middles") or []) for g in games)
    props = [x for g in games for x in (g.get("props") or [])]
    n_priced = sum(1 for x in props if x.get("status") == "priced")

    nxt = con.execute(
        "SELECT coalesce(max(build_id), 0) + 1 FROM log.week_build").fetchone()[0]
    con.execute("""
        INSERT INTO log.week_build VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, [nxt, season, week, datetime.now().replace(microsecond=0),
          len(games), n_markets, n_plays, n_thin, n_mid, len(props),
          n_priced, str(meta.get("captured") or "")[:19],
          None])
    log(f"  logged build {nxt}: {season} week {week} - {len(games)} games, "
        f"{n_plays} plays, {n_mid} middles, {n_priced}/{len(props)} props priced")
    return 0


def show(con, season: int) -> int:
    cw = current_week(con, season)
    log("=" * 78)
    log(f"SEASON {season} - current week {cw}")
    log("=" * 78)
    rows = con.execute("""
        SELECT week, games, played, complete, cast(last_built AS VARCHAR),
               builds, n_plays, n_middles, decisions, placed, passed
        FROM mart.season_week WHERE season = ? ORDER BY week
    """, [season]).fetchall()
    log(f"  {'wk':>3}{'games':>7}{'played':>8}{'builds':>8}{'plays':>7}"
        f"{'mid':>5}{'decisions':>11}{'placed':>8}   last build")
    for r in rows:
        mark = " <" if r[0] == cw else "  "
        log(f"  {r[0]:>3}{r[1]:>7}{r[2]:>8}{r[5]:>8}"
            f"{(r[6] if r[6] is not None else 0):>7}"
            f"{(r[7] if r[7] is not None else 0):>5}{r[8]:>11}{r[9]:>8}   "
            f"{(r[4] or '')[:16]}{mark}")
    tot = con.execute("""
        SELECT count(*), count(*) FILTER (WHERE decision='placed'),
               count(*) FILTER (WHERE decision='passed')
        FROM bet.wager WHERE season = ?
    """, [season]).fetchone()
    log("")
    log(f"  decisions on record: {tot[0]} ({tot[1]} placed, {tot[2]} passed)")
    if not any(r[5] for r in rows):
        log("  no builds logged yet - run `append` after each slate build")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["append", "show"])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("-i", "--inp", default="week01_slate.json")
    ap.add_argument("--season", type=int, default=2026)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    try:
        con.execute(SEASON_SQL.read_text(encoding="utf-8"))
        if a.cmd == "append":
            return append(con, json.loads(
                Path(a.inp).read_text(encoding="utf-8")))
        return show(con, a.season)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
