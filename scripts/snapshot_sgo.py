"""
snapshot_sgo.py - capture the placeable board from SportsGameOdds.

ROLE: SGO carries DraftKings, FanDuel, BetMGM and Caesars - four of the six
Louisiana-placeable books, including Caesars which The Odds API cannot see at
any tier. The Odds API is reduced to Pinnacle-only for CLV grading
(scripts/snapshot_odds.py --pinnacle-only), which fits its free tier.

Writes into the same odds.snapshot / odds.line tables with
source = 'sportsgameodds', so every downstream consumer is unchanged.

THREE MEASURED BEHAVIOURS THIS HANDLES
  1. `season` and `week` are SILENTLY IGNORED. Only startsAfter/startsBefore
     work, and returned events are asserted against the window.
  2. byBookmaker does NOT populate without a bookmakerID filter, so books are
     fetched one at a time (4 requests per snapshot).
  3. The feed reports the book's own lastUpdatedAt. A quote updated at or after
     kickoff is IN-PLAY - 98% of sampled 2025 history was. It is captured but
     flagged, and odds.line_pregame is what models must read.

Usage:
    py -3 scripts/snapshot_sgo.py --phase opener --days 8
    py -3 scripts/snapshot_sgo.py --phase close --days 3 --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import require  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
BASE = "https://api.sportsgameodds.com/v2"
SOURCE = "sportsgameodds"

BOOKS = ["draftkings", "fanduel", "betmgm", "caesars"]

PERIOD_LABEL = {"game": "game", "1h": "1H", "2h": "2H",
                "1q": "1Q", "2q": "2Q", "3q": "3Q", "4q": "4Q", "reg": "game"}

# (sgo_stat_id, bet_type, scope) -> canonical market_id.  scope: game|team|player
CANON = {
    ("points", "sp", "team"): "spread_game",
    ("points", "ou", "game"): "total_game",
    ("points", "ml", "team"): "ml_game",
    ("points", "ou", "team"): "team_total",
    ("passing_yards", "ou", "player"): "player_pass_yds",
    ("rushing_yards", "ou", "player"): "player_rush_yds",
    ("receiving_yards", "ou", "player"): "player_rec_yds",
    ("receiving_receptions", "ou", "player"): "player_receptions",
    ("passing_touchdowns", "ou", "player"): "player_pass_tds",
    ("touchdowns", "yn", "player"): "player_anytime_td",
    ("rushing+receiving_yards", "ou", "player"): "player_rush_rec_yds",
    ("passing+rushing_yards", "ou", "player"): "player_pass_rush_yds",
    ("rushing_attempts", "ou", "player"): "player_rush_att",
    ("passing_attempts", "ou", "player"): "player_pass_att",
    ("passing_completions", "ou", "player"): "player_pass_comp",
    ("passing_interceptions", "ou", "player"): "player_pass_int",
    ("receiving_longestReception", "ou", "player"): "player_longest_rec",
    ("rushing_longestRush", "ou", "player"): "player_longest_rush",
    ("firstTouchdown", "yn", "player"): "player_first_td",
    ("defense_combinedTackles", "ou", "player"): "player_tackles",
    ("defense_sacks", "ou", "player"): "player_sacks",
}
PERIOD_SUFFIX = {"1h": "_1h", "2h": "_2h", "1q": "_1q",
                 "2q": "_2q", "3q": "_3q", "4q": "_4q"}


_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)
_NONALPHA = re.compile(r"[^a-z ]")


def _norm(n: str) -> str:
    s = (n or "").lower().replace(".", " ").replace("'", "").replace("-", " ")
    return " ".join(_NONALPHA.sub(" ", _SUFFIX.sub(" ", s)).split())


def log(m: str = "") -> None:
    print(m, flush=True)


def headers():
    return {"X-Api-Key": require("SPORTSGAMEODDS_API_KEY")}


def entities_used():
    try:
        u = requests.get(f"{BASE}/account/usage/", headers=headers(),
                         timeout=45).json().get("data", {})
        return u.get("rateLimits", {}).get("per-month", {}).get("current-entities")
    except Exception:
        return None


def scope_of(entity: str) -> str:
    if entity == "all":
        return "game"
    if entity in ("home", "away"):
        return "team"
    return "player"


def market_for(stat: str, bet_type: str, period: str, scope: str):
    """Canonical id where we have one, else a deterministic derived id."""
    base = CANON.get((stat, bet_type, scope))
    if base:
        if period in ("game", "reg"):
            return base, False
        suffix = PERIOD_SUFFIX.get(period, f"_{period}")
        # spread_game -> spread_1h etc. keeps the existing naming
        if base.endswith("_game"):
            return base[:-5] + suffix, True
        return base + suffix, True
    derived = f"{scope}_{stat}_{period}_{bet_type}".lower()
    return derived.replace("+", "_plus_"), True


def american(v):
    if v is None:
        return None
    try:
        return int(str(v).replace("+", ""))
    except ValueError:
        return None


def number(v):
    if v is None:
        return None
    try:
        return float(str(v).replace("+", ""))
    except ValueError:
        return None


def to_decimal(a: int) -> float:
    return 1 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def implied(a: int) -> float:
    return 100.0 / (a + 100.0) if a > 0 else abs(a) / (abs(a) + 100.0)


def fetch_book(book: str, start: str, end: str):
    r = requests.get(f"{BASE}/events/", headers=headers(), timeout=120,
                     params={"leagueID": "NFL", "type": "match",
                             "startsAfter": start, "startsBefore": end,
                             "bookmakerID": book, "limit": 30})
    if r.status_code != 200:
        log(f"    {book}: [{r.status_code}] {r.text[:140]}")
        return []
    return r.json().get("data", [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--phase", required=True,
                    choices=["opener", "post_wed", "post_fri", "close", "adhoc"])
    ap.add_argument("--days", type=int, default=8,
                    help="capture games kicking off within N days")
    ap.add_argument("--books", default=",".join(BOOKS))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not a.db.exists():
        log(f"database not found: {a.db}")
        return 1

    today = datetime.now(timezone.utc).date()
    start, end = today.isoformat(), (today + timedelta(days=a.days)).isoformat()
    books = [b.strip() for b in a.books.split(",")]

    con = duckdb.connect(str(a.db))
    captured_at = datetime.now(timezone.utc)
    e0 = entities_used()

    log(f"source  : {SOURCE}")
    log(f"phase   : {a.phase}")
    log(f"window  : {start} -> {end}  (date filter; season/week are ignored by "
        f"this API)")
    log(f"books   : {', '.join(books)}")
    log(f"entities: {e0}\n")

    alias = dict(con.execute(
        "SELECT alias, book_id FROM ref.book_alias WHERE source = ?",
        [SOURCE]).fetchall())
    known_markets = set(r[0] for r in con.execute(
        "SELECT market_id FROM ref.market").fetchall())

    # Player resolution, self-maintaining: anything new gets matched on the
    # spot and written to ref.player_xref, resolved or not. Leaving gsis_id
    # NULL would make every player's prop indistinguishable downstream.
    xref = dict(con.execute("""
        SELECT source_player_key, gsis_id FROM ref.player_xref WHERE source = ?
    """, [SOURCE]).fetchall())
    name_to_gsis: dict[str, list] = {}
    for gid, disp in con.execute("""
        SELECT gsis_id, display_name FROM raw.players
        WHERE gsis_id IS NOT NULL AND coalesce(last_season, 0) >= 2024
    """).fetchall():
        name_to_gsis.setdefault(_norm(disp), []).append(gid)
    new_xref: dict[str, tuple] = {}

    # nflverse spine for game resolution
    sched = {(r[1], r[2], str(r[3])): r[0] for r in con.execute("""
        SELECT game_id, away_team, home_team, gameday FROM raw.games
        WHERE season >= 2025
    """).fetchall()}

    rows, new_markets, unresolved, inplay = [], {}, 0, 0
    events_seen, outside = set(), 0

    for b in books:
        bid = alias.get(b, b)
        evs = fetch_book(b, start, end)
        n_before = len(rows)
        for ev in evs:
            st = ev.get("status") or {}
            sa = str(st.get("startsAt", ""))
            if not (start <= sa[:10] <= end):
                outside += 1
                continue          # the API ignores season/week; verify the window
            teams = ev.get("teams") or {}
            away = ((teams.get("away") or {}).get("names") or {}).get("short")
            home = ((teams.get("home") or {}).get("names") or {}).get("short")
            gid = None
            for delta in (0, -1, 1):
                d = (datetime.fromisoformat(sa.replace("Z", "+00:00")).date()
                     + timedelta(days=delta)).isoformat()
                if (away, home, d) in sched:
                    gid = sched[(away, home, d)]
                    break
            if gid is None:
                unresolved += 1
            events_seen.add(ev.get("eventID"))
            players = ev.get("players") or {}

            for oid, o in (ev.get("odds") or {}).items():
                if not isinstance(o, dict):
                    continue
                bb = (o.get("byBookmaker") or {}).get(b)
                if not isinstance(bb, dict):
                    continue
                stat = o.get("statID")
                bt = o.get("betTypeID")
                period = o.get("periodID") or "game"
                entity = str(o.get("statEntityID", ""))
                scope = scope_of(entity)
                mid, auto = market_for(stat, bt, period, scope)
                if mid not in known_markets and mid not in new_markets:
                    new_markets[mid] = (
                        mid, o.get("marketName") or mid,
                        ("player_prop" if scope == "player" else
                         "derivative" if period not in ("game", "reg") else
                         {"sp": "side", "ou": "total", "ml": "moneyline"}
                         .get(bt, "other")),
                        PERIOD_LABEL.get(period, period),
                        scope == "player", stat, auto, stat, bt)
                gsis = None
                if scope == "player":
                    if entity in xref:
                        gsis = xref[entity]
                    else:
                        p = players.get(entity) or {}
                        cand = name_to_gsis.get(_norm(p.get("name") or ""), [])
                        gsis = cand[0] if len(cand) == 1 else None
                        xref[entity] = gsis
                        new_xref[entity] = (
                            SOURCE, entity, gsis, p.get("name") or entity,
                            p.get("teamID"),
                            "exact" if gsis else "unresolved",
                            1.0 if gsis else None, False,
                            captured_at.isoformat())
                price = american(bb.get("odds"))
                if price is None:
                    continue
                line = number(bb.get("overUnder") or bb.get("spread")
                              or o.get("bookOverUnder") or o.get("bookSpread"))
                lu = bb.get("lastUpdatedAt")
                if lu and sa and str(lu) >= sa:
                    inplay += 1
                rows.append((
                    gid, ev.get("eventID"), bid, mid, o.get("sideID"), entity,
                    gsis, line, price, to_decimal(price), implied(price),
                    captured_at, False, lu, sa))
        log(f"  {b:<12} events={len(evs):>3}  rows+={len(rows)-n_before:>6}")

    log(f"\n  events: {len(events_seen)} | unresolved to spine: {unresolved}")
    if outside:
        log(f"  dropped {outside} events outside the requested window")
    log(f"  new markets to register: {len(new_markets)}")
    log(f"  new players seen: {len(new_xref)} "
        f"({sum(1 for v in new_xref.values() if v[2]) } resolved)")
    log(f"  quotes already IN-PLAY (kept, flagged): {inplay:,}")

    if a.dry_run:
        log(f"\n  would write {len(rows):,} rows - dry run, nothing written")
        if new_markets:
            for m in list(new_markets.values())[:12]:
                log(f"    new market: {m[0]:<40}{m[2]:<14}{m[3]}")
        con.close()
        return 0

    con.execute("BEGIN TRANSACTION")
    try:
        if new_xref:
            con.executemany("""
                INSERT INTO ref.player_xref
                  (source, source_player_key, gsis_id, source_name_raw,
                   source_team, match_method, match_confidence, is_manual,
                   resolved_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT (source, source_player_key) DO UPDATE SET
                  gsis_id = excluded.gsis_id,
                  match_method = excluded.match_method
            """, list(new_xref.values()))

        if new_markets:
            con.executemany("""
                INSERT INTO ref.market
                  (market_id, market_name, family, period, is_player, stat_key,
                   is_auto, sgo_stat_id, sgo_bet_type)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING
            """, list(new_markets.values()))

        sid = con.execute("""
            INSERT INTO odds.snapshot
                (captured_at, source, season, week, phase, markets_pulled,
                 credits_used, credits_left, http_status, note)
            VALUES (?, ?, 2026, NULL, ?, 'sgo:all', ?, NULL, 200, ?)
            RETURNING snapshot_id
        """, [captured_at, SOURCE, a.phase,
              (entities_used() or 0) - (e0 or 0),
              f"window {start}..{end}; books {','.join(books)}"]).fetchone()[0]

        # An empty pull is a NORMAL outcome, not an error. The board does not
        # move every hour, and executemany refuses an empty parameter list -
        # which crashed the scheduled capture on the first run that found
        # nothing new. The snapshot row is still written so the gap is visible
        # as "we looked and there was nothing", not as a missing capture.
        if rows:
            con.executemany("""
                INSERT INTO odds.line
                  (snapshot_id, game_id, source_game_id, book_id, market_id,
                   side_key, entity_key, gsis_id, line_value, price_american,
                   price_decimal, implied_prob, captured_at, is_alt,
                   book_last_updated, event_starts_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, [(sid,) + r for r in rows])
        else:
            log("  no new quotes in this pull; snapshot row written empty")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    e1 = entities_used()
    log(f"\n  snapshot_id {sid}: {len(rows):,} lines written")
    log(f"  entities: {e0} -> {e1}")

    log("\naudit:")
    for name, n in con.execute(
            "SELECT check_name, n_issues FROM audit.run_all").fetchall():
        if n:
            log(f"  ISSUE {name:<28} {n}")
    ip = con.execute("SELECT count(*) FROM audit.inplay_contamination").fetchone()[0]
    pg = con.execute("SELECT count(*) FROM odds.line_pregame").fetchone()[0]
    tot = con.execute("SELECT count(*) FROM odds.line").fetchone()[0]
    log(f"\n  odds.line total {tot:,} | pregame {pg:,} | in-play {ip:,}")
    log("  models must read odds.line_pregame, never odds.line")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
