"""
sgo_history.py - pull historical player-prop lines from SportsGameOdds and test
the prop engine against them.

THE QUESTION THIS ANSWERS: does our usage-based projection beat the posted
line? Everything about the $99/mo decision, and about whether the prop engine
is worth betting at all, reduces to that.

The test mirrors the sides analysis: the posted line is the benchmark, exactly
as the closing spread was. If the market's number predicts the outcome better
than ours, we have no edge on props either - and better to learn that from 2025
history than from a season of losing tickets.

CRITICAL API NOTE: SportsGameOdds SILENTLY IGNORES the `season` and `week`
parameters - season=2024, 2025 and 2026 all return the same completed 2024
games with no error. Only startsAfter/startsBefore are honoured. Every request
here is date-filtered, and every returned event has its startsAt asserted
against the requested window.

Usage:
    py -3 scripts/sgo_history.py load --start 2025-09-04 --end 2026-01-05
    py -3 scripts/sgo_history.py load --start 2025-09-04 --end 2025-10-20 --books draftkings
    py -3 scripts/sgo_history.py resolve
    py -3 scripts/sgo_history.py evaluate
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import require  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "nfl.duckdb"
BASE = "https://api.sportsgameodds.com/v2"

# SGO statID -> (our stat key, mart.player_game_usage actual column)
STAT_MAP = {
    "receiving_yards":       ("rec_yards",   "rec_yards"),
    "rushing_yards":         ("rush_yards",  "rush_yards"),
    "receiving_receptions":  ("receptions",  "receptions"),
    "rushing+receiving_yards": ("rush_rec_yards", None),
}
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)
NONALPHA = re.compile(r"[^a-z ]")


def log(m: str = "") -> None:
    print(m, flush=True)


def norm(n: str) -> str:
    s = (n or "").lower().replace(".", " ").replace("'", "").replace("-", " ")
    return " ".join(NONALPHA.sub(" ", SUFFIX.sub(" ", s)).split())


def headers():
    return {"X-Api-Key": require("SPORTSGAMEODDS_API_KEY")}


def usage_entities():
    try:
        u = requests.get(f"{BASE}/account/usage/", headers=headers(),
                         timeout=45).json().get("data", {})
        return u.get("rateLimits", {}).get("per-month", {}).get("current-entities")
    except Exception:
        return None


def ddl(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS raw.sgo_prop_history (
            event_id        VARCHAR,
            starts_at       VARCHAR,
            away_short      VARCHAR,
            home_short      VARCHAR,
            book_id         VARCHAR,
            odd_id          VARCHAR,
            stat_id         VARCHAR,
            sgo_player_id   VARCHAR,
            player_name     VARCHAR,
            team_id         VARCHAR,
            period_id       VARCHAR,
            side_id         VARCHAR,
            line_value      DOUBLE,
            price_american  INTEGER,
            last_updated    VARCHAR,
            PRIMARY KEY (event_id, book_id, odd_id)
        )
    """)


def parse_price(v):
    if v is None:
        return None
    try:
        return int(str(v).replace("+", ""))
    except ValueError:
        return None


def parse_num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace("+", ""))
    except ValueError:
        return None


def cmd_load(con, a) -> int:
    ddl(con)
    books = [b.strip() for b in a.books.split(",")]
    start = datetime.strptime(a.start, "%Y-%m-%d").date()
    end = datetime.strptime(a.end, "%Y-%m-%d").date()
    e0 = usage_entities()
    log(f"window {start} -> {end} | books: {', '.join(books)}")
    log(f"entities before: {e0}\n")

    total = 0
    win = start
    while win < end:
        wend = min(win + timedelta(days=7), end)
        for b in books:
            try:
                r = requests.get(f"{BASE}/events/", headers=headers(), timeout=120,
                                 params={"leagueID": "NFL", "type": "match",
                                         "startsAfter": win.isoformat(),
                                         "startsBefore": wend.isoformat(),
                                         "bookmakerID": b, "limit": 30})
            except requests.RequestException as e:
                log(f"  {win} {b}: request failed {e}")
                continue
            if r.status_code != 200:
                log(f"  {win} {b}: [{r.status_code}] {r.text[:120]}")
                continue
            evs = r.json().get("data", [])
            rows, skipped = [], 0
            for ev in evs:
                st = ev.get("status") or {}
                sa = str(st.get("startsAt", ""))
                # assert the API honoured the window; it ignores season/week
                if not (win.isoformat() <= sa[:10] <= wend.isoformat()):
                    skipped += 1
                    continue
                teams = ev.get("teams") or {}
                away = ((teams.get("away") or {}).get("names") or {}).get("short")
                home = ((teams.get("home") or {}).get("names") or {}).get("short")
                players = ev.get("players") or {}
                for oid, o in (ev.get("odds") or {}).items():
                    if not isinstance(o, dict):
                        continue
                    if o.get("statID") not in STAT_MAP:
                        continue
                    if o.get("betTypeID") != "ou":
                        continue
                    bb = (o.get("byBookmaker") or {}).get(b)
                    if not isinstance(bb, dict):
                        continue
                    ent = str(o.get("statEntityID", ""))
                    if ent in ("all", "home", "away", ""):
                        continue
                    p = players.get(ent) or {}
                    line = parse_num(bb.get("overUnder") or o.get("bookOverUnder"))
                    price = parse_price(bb.get("odds"))
                    if line is None or price is None:
                        continue
                    rows.append((
                        ev.get("eventID"), sa, away, home, b, oid,
                        o.get("statID"), ent, p.get("name"), p.get("teamID"),
                        o.get("periodID"), o.get("sideID"), line, price,
                        bb.get("lastUpdatedAt")))
            if rows:
                con.executemany("""
                    INSERT INTO raw.sgo_prop_history VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT DO NOTHING
                """, rows)
            total += len(rows)
            note = f"  {win} {b:<12} events={len(evs):>3} rows={len(rows):>5}"
            if skipped:
                note += f"  (dropped {skipped} outside window)"
            log(note)
        win = wend

    e1 = usage_entities()
    n = con.execute("SELECT count(*) FROM raw.sgo_prop_history").fetchone()[0]
    log(f"\ninserted {total:,} this run | table now {n:,} rows")
    log(f"entities: {e0} -> {e1}")
    return 0


def cmd_resolve(con, a) -> int:
    """Resolve SGO player ids to gsis_id and SGO events to nflverse game_id."""
    log("resolving players")
    players = con.execute("""
        SELECT DISTINCT sgo_player_id, any_value(player_name), any_value(team_id)
        FROM raw.sgo_prop_history GROUP BY sgo_player_id
    """).fetchall()
    cands = con.execute("""
        SELECT gsis_id, display_name, position
        FROM raw.players WHERE gsis_id IS NOT NULL
          AND coalesce(last_season, 0) >= 2023
    """).fetchall()
    by_name = defaultdict(list)
    for gid, disp, pos in cands:
        by_name[norm(disp)].append(gid)

    rows, unres = [], 0
    for pid, nm, tid in players:
        c = by_name.get(norm(nm or ""), [])
        gid = c[0] if len(c) == 1 else None
        if gid is None:
            unres += 1
        rows.append(("sportsgameodds", pid, gid, nm, tid,
                     "exact" if gid else "unresolved",
                     1.0 if gid else None, False,
                     datetime.now().isoformat()))
    con.executemany("""
        INSERT INTO ref.player_xref
          (source, source_player_key, gsis_id, source_name_raw, source_team,
           match_method, match_confidence, is_manual, resolved_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT (source, source_player_key) DO UPDATE SET
          gsis_id = excluded.gsis_id, match_method = excluded.match_method
    """, rows)
    log(f"  {len(players) - unres}/{len(players)} resolved "
        f"({100*(len(players)-unres)/max(len(players),1):.1f}%)")

    log("resolving events to nflverse game_id")
    con.execute("""
        CREATE OR REPLACE TABLE mart.sgo_event_map AS
        SELECT DISTINCT h.event_id, g.game_id, h.starts_at, h.away_short, h.home_short
        FROM raw.sgo_prop_history h
        LEFT JOIN raw.games g
          ON g.away_team = h.away_short AND g.home_team = h.home_short
         AND abs(date_diff('day', cast(g.gameday AS DATE),
                           cast(h.starts_at[1:10] AS DATE))) <= 1
    """)
    r = con.execute("""
        SELECT count(*), count(game_id) FROM mart.sgo_event_map
    """).fetchone()
    log(f"  {r[1]}/{r[0]} events matched to the nflverse spine")
    return 0


def cmd_evaluate(con, a) -> int:
    log("=" * 78)
    log("PROP ENGINE vs THE POSTED LINE")
    log("=" * 78)
    log("The line is the benchmark, exactly as the closing spread was for")
    log("sides. If the market predicts the outcome better than we do, there")
    log("is no edge here regardless of how good the model looks alone.\n")

    con.execute("""
        CREATE OR REPLACE TEMP TABLE ev AS
        WITH lines AS (
            SELECT h.event_id, m.game_id, h.stat_id, x.gsis_id,
                   h.book_id,
                   avg(h.line_value) AS line,
                   max(CASE WHEN h.side_id='over'  THEN h.price_american END) AS over_px,
                   max(CASE WHEN h.side_id='under' THEN h.price_american END) AS under_px
            FROM raw.sgo_prop_history h
            JOIN mart.sgo_event_map m ON m.event_id = h.event_id
            JOIN ref.player_xref x
              ON x.source='sportsgameodds' AND x.source_player_key = h.sgo_player_id
            WHERE m.game_id IS NOT NULL AND x.gsis_id IS NOT NULL
              AND h.period_id = 'game'
            GROUP BY ALL
        )
        SELECT l.*,
               CASE l.stat_id
                 WHEN 'receiving_yards'      THEN u.rec_yards
                 WHEN 'rushing_yards'        THEN u.rush_yards
                 WHEN 'receiving_receptions' THEN u.receptions
               END AS actual,
               CASE l.stat_id
                 WHEN 'receiving_yards'      THEN p.proj_rec_yards
                 WHEN 'rushing_yards'        THEN p.proj_rush_yards
                 WHEN 'receiving_receptions' THEN p.proj_receptions
               END AS projection,
               p.n_prior, u.position
        FROM lines l
        JOIN mart.player_game_usage u
          ON u.game_id = l.game_id AND u.gsis_id = l.gsis_id
        LEFT JOIN mart.player_usage_projection p
          ON p.game_id = l.game_id AND p.gsis_id = l.gsis_id
        WHERE l.stat_id IN ('receiving_yards','rushing_yards','receiving_receptions')
    """)
    n, npro = con.execute(
        "SELECT count(*), count(projection) FROM ev").fetchone()
    log(f"  prop lines joined to actuals : {n:,}")
    log(f"  of which we have a projection: {npro:,}")
    if npro < 100:
        log("\n  Not enough overlap to conclude. Load more weeks, or check")
        log("  the resolve step.")
        return 1

    log(f"\n  {'stat':<24}{'n':>6}{'line MAE':>11}{'model MAE':>11}"
        f"{'line corr':>11}{'model corr':>12}")
    for s in ("receiving_yards", "rushing_yards", "receiving_receptions"):
        r = con.execute("""
            SELECT count(*), avg(abs(actual-line)), avg(abs(actual-projection)),
                   corr(actual, line), corr(actual, projection)
            FROM ev WHERE stat_id = ? AND projection IS NOT NULL
              AND actual IS NOT NULL AND n_prior >= 3
        """, [s]).fetchone()
        if r[0] and r[0] > 20:
            log(f"  {s:<24}{r[0]:>6}{r[1]:>11.3f}{r[2]:>11.3f}"
                f"{(r[3] or 0):>11.3f}{(r[4] or 0):>12.3f}")

    log("\n" + "-" * 78)
    log("  INCREMENTAL SIGNAL: does the model know anything the line does not?")
    log("-" * 78)
    r = con.execute("""
        SELECT corr(actual, projection), corr(actual, line), corr(projection, line),
               count(*)
        FROM ev WHERE projection IS NOT NULL AND actual IS NOT NULL AND n_prior >= 3
    """).fetchone()
    ry, rl, rml, cnt = r
    if None not in (ry, rl, rml):
        den = ((1 - rl ** 2) * (1 - rml ** 2)) ** 0.5
        partial = (ry - rl * rml) / den if den else float("nan")
        log(f"  n = {cnt:,}")
        log(f"    corr(actual, line)       : {rl:+.4f}   <- benchmark")
        log(f"    corr(actual, projection) : {ry:+.4f}")
        log(f"    corr(projection, line)   : {rml:+.4f}   <- overlap")
        log(f"    PARTIAL corr(actual, projection | line) : {partial:+.4f}")
        log("")
        if abs(partial) < 0.05:
            log("    VERDICT: no incremental signal. The projection is a")
            log("    noisier restatement of the line - same result as sides.")
        elif partial > 0:
            log("    VERDICT: POSITIVE incremental signal. The model carries")
            log("    information the line does not. This is the first place in")
            log("    the project that has happened - verify on held-out weeks")
            log("    before believing it.")
        else:
            log("    VERDICT: negative - where we disagree, the line is right.")

    log("\n  disagreement buckets (|projection - line|):")
    log(f"    {'bucket':<14}{'n':>7}{'over hit%':>11}{'note':>8}")
    for lo, hi in ((0, 5), (5, 10), (10, 20), (20, 999)):
        r = con.execute("""
            SELECT count(*),
                   avg(CASE WHEN actual > line THEN 1.0 ELSE 0.0 END)
            FROM ev
            WHERE projection IS NOT NULL AND actual IS NOT NULL AND n_prior >= 3
              AND stat_id = 'receiving_yards'
              AND abs(projection - line) > ? AND abs(projection - line) <= ?
              AND projection > line
        """, [lo, hi]).fetchone()
        if r[0] and r[0] > 15:
            log(f"    proj>line {lo}-{hi:<4}{r[0]:>7}{r[1]*100:>10.1f}%"
                f"{'   (50% = no edge)' if lo == 0 else ''}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["load", "resolve", "evaluate"])
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--start", default="2025-09-04")
    ap.add_argument("--end", default="2025-11-01")
    ap.add_argument("--books", default="draftkings")
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    try:
        return {"load": cmd_load, "resolve": cmd_resolve,
                "evaluate": cmd_evaluate}[a.cmd](con, a)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
