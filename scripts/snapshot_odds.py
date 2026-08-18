"""
snapshot_odds.py - capture the odds path into odds.line (append-only).

Build order step 3. Missed snapshots are UNRECOVERABLE at any sane price, so
this runs on a cadence: opener (Sun night/Mon) -> post_wed (first injury
report) -> post_fri (final report) -> close.

Credit discipline, learned from a live probe on 2026-08-16:
  * /sports and /events are FREE (0 credits).
  * Cost = [markets RETURNED] x [regions]. Requesting a market a book has not
    posted yet costs nothing, so over-requesting props is safe.
  * Player props are per-event calls and are the expensive path.
  * Free tier is 500 credits/month, which covers main lines comfortably but
    CANNOT fund in-season prop capture. See RUNBOOK.

Everything is written inside one transaction per snapshot: either the whole
snapshot lands or none of it does, so a half-written phase never looks complete.

Usage:
    py -3 scripts/snapshot_odds.py --phase opener
    py -3 scripts/snapshot_odds.py --phase close --props
    py -3 scripts/snapshot_odds.py --phase adhoc --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get, require  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"
SOURCE = "the_odds_api"

MAIN_MARKETS = "h2h,spreads,totals"
PROP_MARKETS = ",".join([
    "player_pass_yds", "player_pass_tds", "player_rush_yds",
    "player_reception_yds", "player_receptions", "player_anytime_td",
])

# API market key -> ref.market.market_id
MARKET_MAP = {
    "h2h": "ml_game", "spreads": "spread_game", "totals": "total_game",
    "player_pass_yds": "player_pass_yds", "player_pass_tds": "player_pass_tds",
    "player_rush_yds": "player_rush_yds",
    "player_reception_yds": "player_rec_yds",
    "player_receptions": "player_receptions",
    "player_anytime_td": "player_anytime_td",
}

_credits = {"used": 0, "left": None}


def log(m: str = "") -> None:
    print(m, flush=True)


def api(path: str, params: dict, label: str):
    p = dict(params)
    p["apiKey"] = require("THE_ODDS_API_KEY")
    r = requests.get(f"{BASE}{path}", params=p, timeout=60)
    last = r.headers.get("x-requests-last")
    left = r.headers.get("x-requests-remaining")
    if last and last.isdigit():
        _credits["used"] += int(last)
    if left and left.replace(".", "").isdigit():
        _credits["left"] = left
    if r.status_code != 200:
        log(f"    [{r.status_code}] {label}: {r.text[:200]}")
        return None, r.status_code
    return r.json(), 200


def american_to_decimal(a: int) -> float:
    return 1 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def implied(a: int) -> float:
    return 100.0 / (a + 100.0) if a > 0 else abs(a) / (abs(a) + 100.0)


def resolve_games(con, events: list[dict]) -> dict[str, str | None]:
    """Map Odds API event ids onto the nflverse game_id spine.

    Match on (season, week-bearing date window, home, away) via team aliases.
    Anything unresolved is recorded with game_id NULL so it lands in
    audit.unresolved_games rather than vanishing.
    """
    alias = {a.lower(): t for a, t in con.execute("""
        SELECT alias, team_id FROM ref.team_alias WHERE source IN ('the_odds_api','nflverse')
    """).fetchall()}
    sched = con.execute("""
        SELECT game_id, season, gameday, home_team, away_team FROM raw.games
        WHERE season >= 2026
    """).fetchall()
    by_key = {(str(r[2]), r[3], r[4]): r[0] for r in sched}

    out: dict[str, str | None] = {}
    rows = []
    now = datetime.now(timezone.utc)
    for ev in events:
        home = alias.get(ev["home_team"].lower())
        away = alias.get(ev["away_team"].lower())
        ct = ev["commence_time"]
        gid, method = None, "unresolved"
        if home and away:
            d = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            # kickoff is UTC; nflverse gameday is local, so a Sunday-night or
            # Monday-night game can land on the following UTC date
            for delta in (0, -1, 1):
                day = str(d.date() + timedelta(days=delta))
                if (day, home, away) in by_key:
                    gid, method = by_key[(day, home, away)], "teams_date"
                    break
        out[ev["id"]] = gid
        rows.append((SOURCE, ev["id"], gid, ct, ev["home_team"], ev["away_team"],
                     method, now))
    con.executemany("""
        INSERT INTO odds.game_map
            (source, source_game_id, game_id, commence_time,
             source_home_raw, source_away_raw, match_method, resolved_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT (source, source_game_id) DO UPDATE SET
            game_id = excluded.game_id, match_method = excluded.match_method,
            resolved_at = excluded.resolved_at
    """, rows)
    n_ok = sum(1 for v in out.values() if v)
    log(f"    game resolution: {n_ok}/{len(out)} matched to nflverse spine")
    return out


def extract_lines(payload, gmap, book_alias, snapshot_id, captured_at):
    """Flatten the nested API payload into odds.line rows."""
    rows, unmapped_books, unknown_markets = [], set(), set()
    for ev in payload:
        gid = gmap.get(ev["id"])
        for bk in ev.get("bookmakers", []):
            bid = book_alias.get(bk["key"])
            if bid is None:
                unmapped_books.add(bk["key"])
                bid = bk["key"]      # keep the row; audit will surface it
            for mk in bk.get("markets", []):
                mid = MARKET_MAP.get(mk["key"])
                if mid is None:
                    unknown_markets.add(mk["key"])
                    continue
                is_player = mk["key"].startswith("player_")
                for o in mk.get("outcomes", []):
                    price = o.get("price")
                    if price is None:
                        continue
                    if is_player:
                        side = o["name"].lower()           # over / under / yes / no
                        player_name = o.get("description")
                    else:
                        nm = o["name"]
                        if nm.lower() in ("over", "under"):
                            side = nm.lower()
                        elif nm == ev["home_team"]:
                            side = "home"
                        elif nm == ev["away_team"]:
                            side = "away"
                        else:
                            side = nm
                        player_name = None
                    rows.append((
                        snapshot_id, gid, ev["id"], bid, mid, side,
                        player_name, o.get("point"), int(price),
                        american_to_decimal(int(price)), implied(int(price)),
                        captured_at, False,
                    ))
    return rows, unmapped_books, unknown_markets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--phase", required=True,
                    choices=["opener", "post_wed", "post_fri", "close", "adhoc"])
    ap.add_argument("--props", action="store_true",
                    help="also pull player props (per-event, expensive)")
    ap.add_argument("--max-events", type=int, default=None,
                    help="cap prop events pulled, for credit control")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report, write nothing")
    ap.add_argument("--pinnacle-only", action="store_true",
                    help="ROLE SPLIT: SGO carries the placeable board, so this "
                         "feed is reduced to the Pinnacle CLV anchor. Forces "
                         "regions=eu and main markets only: 3 markets x 1 "
                         "region = 3 credits/snapshot, ~52/month, which fits "
                         "the FREE tier.")
    ap.add_argument("--reresolve", action="store_true",
                    help="re-run game resolution against current aliases and "
                         "backfill odds.line.game_id; costs 0 credits")
    args = ap.parse_args()

    if not args.db.exists():
        log(f"database not found: {args.db}")
        return 1

    if args.reresolve:
        # /events is free, so repairing resolution after an alias fix is free.
        con = duckdb.connect(str(args.db))
        log("re-resolving games against current ref.team_alias (0 credits)")
        events, _ = api(f"/sports/{SPORT}/events", {}, "events")
        if events is None:
            return 1
        con.execute("BEGIN TRANSACTION")
        try:
            resolve_games(con, events)
            n = con.execute("""
                UPDATE odds.line l
                   SET game_id = m.game_id
                  FROM odds.game_map m
                 WHERE m.source = ? AND m.source_game_id = l.source_game_id
                   AND m.game_id IS NOT NULL
                   AND (l.game_id IS NULL OR l.game_id <> m.game_id)
            """, [SOURCE]).fetchone()
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        still = con.execute(
            "SELECT count(*) FROM odds.line WHERE game_id IS NULL").fetchone()[0]
        log(f"    odds.line rows still unresolved: {still:,}")
        log("\naudit:")
        for name, k in con.execute(
                "SELECT check_name, n_issues FROM audit.run_all").fetchall():
            flag = "ISSUE" if k else "ok   "
            log(f"  {flag} {name:<28} {k}")
        con.close()
        return 0

    regions = "eu" if args.pinnacle_only else get("ODDS_API_REGIONS", "us,us2")
    budget = int(get("ODDS_API_MAX_CREDITS_PER_RUN", "500"))
    if args.pinnacle_only:
        args.props = False
    con = duckdb.connect(str(args.db))
    captured_at = datetime.now(timezone.utc)

    log(f"phase   : {args.phase}")
    log(f"regions : {regions}")
    log(f"budget  : {budget} credits max this run")
    log(f"dry run : {args.dry_run}\n")

    # 1. events (free)
    log("[1/4] fetching events (free)")
    events, _ = api(f"/sports/{SPORT}/events", {}, "events")
    if events is None:
        return 1
    log(f"    {len(events)} events")

    # 2. main markets
    log(f"\n[2/4] main markets: {MAIN_MARKETS}")
    est = 3 * len(regions.split(","))
    log(f"    estimated cost {est} credits")
    payload, status = api(f"/sports/{SPORT}/odds",
                          {"regions": regions, "markets": MAIN_MARKETS,
                           "oddsFormat": "american"}, "main odds")
    if payload is None:
        return 1
    log(f"    {len(payload)} events priced   (credits used so far: {_credits['used']}, "
        f"remaining: {_credits['left']})")

    # 3. props (per event, optional)
    prop_payloads = []
    if args.props:
        log(f"\n[3/4] player props (per-event)")
        targets = events[:args.max_events] if args.max_events else events
        log(f"    {len(targets)} events; cost = markets RETURNED x regions each")
        for i, ev in enumerate(targets, 1):
            if _credits["used"] >= budget:
                log(f"    budget reached ({budget}); stopping after {i-1} events")
                break
            pr, st = api(f"/sports/{SPORT}/events/{ev['id']}/odds",
                         {"regions": regions, "markets": PROP_MARKETS,
                          "oddsFormat": "american"}, f"props {ev['id'][:8]}")
            if pr:
                prop_payloads.append(pr)
        log(f"    props pulled for {len(prop_payloads)} events "
            f"(credits used: {_credits['used']}, remaining: {_credits['left']})")
    else:
        log("\n[3/4] props skipped (pass --props)")

    # 4. persist
    log("\n[4/4] persisting")
    book_alias = dict(con.execute(
        "SELECT alias, book_id FROM ref.book_alias WHERE source = ?",
        [SOURCE]).fetchall())

    if args.dry_run:
        gmap = {ev["id"]: None for ev in events}
        rows, unmapped, unknown = extract_lines(
            payload, gmap, book_alias, -1, captured_at)
        log(f"    would write {len(rows):,} main-line rows")
        if unmapped:
            log(f"    UNMAPPED BOOKS: {', '.join(sorted(unmapped))}")
        if unknown:
            log(f"    unknown markets (ignored): {', '.join(sorted(unknown))}")
        log("    dry run - nothing written")
        con.close()
        return 0

    con.execute("BEGIN TRANSACTION")
    try:
        gmap = resolve_games(con, events)

        season = con.execute(
            "SELECT max(season) FROM raw.games WHERE season >= 2026").fetchone()[0]
        sid = con.execute("""
            INSERT INTO odds.snapshot
                (captured_at, source, season, week, phase, markets_pulled,
                 credits_used, credits_left, http_status, note)
            VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 200, ?)
            RETURNING snapshot_id
        """, [captured_at, SOURCE, int(season), args.phase,
              MAIN_MARKETS + (("," + PROP_MARKETS) if args.props else ""),
              _credits["used"],
              int(_credits["left"]) if (_credits["left"] or "").isdigit() else None,
              f"regions={regions}"]).fetchone()[0]

        all_rows, unmapped, unknown = extract_lines(
            payload, gmap, book_alias, sid, captured_at)
        for pr in prop_payloads:
            r2, u2, k2 = extract_lines([pr], gmap, book_alias, sid, captured_at)
            all_rows += r2
            unmapped |= u2
            unknown |= k2

        # gsis_id is resolved later by the player-resolver; store the raw name
        # in side_key-adjacent form now by leaving gsis_id NULL and keeping the
        # description on the note-free path. Player props therefore land with
        # gsis_id NULL and are picked up by audit.unresolved_players.
        con.executemany("""
            INSERT INTO odds.line
                (snapshot_id, game_id, source_game_id, book_id, market_id,
                 side_key, gsis_id, line_value, price_american, price_decimal,
                 implied_prob, captured_at, is_alt)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [(r[0], r[1], r[2], r[3], r[4], r[5], None, r[7], r[8], r[9],
               r[10], r[11], r[12]) for r in all_rows])

        # stash prop player names so resolution has something to work from
        prop_names = {(r[3], r[6]) for r in all_rows if r[6]}
        if prop_names:
            con.executemany("""
                INSERT INTO ref.player_xref
                    (source, source_player_key, gsis_id, source_name_raw,
                     match_method, match_confidence, is_manual, resolved_at)
                VALUES (?, ?, NULL, ?, 'unresolved', NULL, FALSE, NULL)
                ON CONFLICT DO NOTHING
            """, [(SOURCE, n.lower(), n) for _, n in prop_names])

        con.execute("COMMIT")
        log(f"    snapshot_id {sid}: {len(all_rows):,} lines written")
        if prop_names:
            log(f"    {len(prop_names)} distinct prop player names queued for resolution")
        if unmapped:
            log(f"    UNMAPPED BOOKS (rows kept, see audit): {', '.join(sorted(unmapped))}")
        if unknown:
            log(f"    unknown markets (skipped): {', '.join(sorted(unknown))}")
    except Exception:
        con.execute("ROLLBACK")
        raise

    log(f"\ncredits: used {_credits['used']} this run, {_credits['left']} remaining")
    log("\naudit:")
    for name, n in con.execute(
            "SELECT check_name, n_issues FROM audit.run_all").fetchall():
        if n:
            log(f"  ISSUE {name:<28} {n}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
