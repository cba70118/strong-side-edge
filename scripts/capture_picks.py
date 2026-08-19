"""capture_picks.py - published expert picks, stored and graded by us.

WHAT THIS IS FOR, STATED FIRST. It is not a signal. The best-known picker on
the CBS grid shows 141-138-3 lifetime against the spread, which is 50.5%, and
this project has now measured three independent times that nothing beats the
closing line. Expert picks are ingested for three honest uses:

    DISAGREEMENT   when our number differs from a line, knowing seven writers
                   are all on the other side is context for why the line sits
                   where it does
    OUR OWN GRADE  every pick is graded here against the CLOSING line, not
                   against the record the outlet publishes for itself
    NARRATIVE      a read beside a price, which is what the page asked for

SOURCES, both free public pages, read the way a reader reads them. The pick,
the number, the source and the link are stored. Article text is not.

    CBS   cbssports.com/nfl/picks/experts/against-the-spread/<week>/
          Seven named writers, every game, AGAINST THE SPREAD with the number
          they took. This is the useful one.
    ESPN  espn.com/nfl/picks
          Eleven named analysts, every game, but STRAIGHT UP. A straight-up
          pick is almost always the favorite, so it carries information only
          on near-pick'em games. Stored with that caveat attached, and the
          loader records pick_type so nothing can average the two together.

Usage:
    py -3 scripts/capture_picks.py --week 1
    py -3 scripts/capture_picks.py --week 20 --season 2025   # a played week
    py -3 scripts/capture_picks.py --show
"""

from __future__ import annotations

import argparse
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CBS = "https://www.cbssports.com/nfl/picks/experts/against-the-spread/{week}/"
ESPN = "https://www.espn.com/nfl/picks/_/week/{week}/seasontype/2"

SQL = """
CREATE SCHEMA IF NOT EXISTS ref;

-- One row per expert per game per capture. Append-only: a pick made on
-- Wednesday and changed on Sunday is two facts about the week, not one.
CREATE TABLE IF NOT EXISTS ref.expert_pick (
    captured_at TIMESTAMP NOT NULL,
    source      VARCHAR,      -- cbs | espn
    season      INTEGER,
    week        INTEGER,
    expert      VARCHAR,
    role        VARCHAR,
    away        VARCHAR,
    home        VARCHAR,
    pick_type   VARCHAR,      -- ats | straight_up
    pick_team   VARCHAR,
    pick_line   DOUBLE,       -- NULL for straight up
    raw_pick    VARCHAR,
    url         VARCHAR
);

-- The newest pick per expert per game, which is what a page should show.
CREATE OR REPLACE VIEW ref.expert_pick_current AS
SELECT * EXCLUDE (rn) FROM (
    SELECT *, row_number() OVER (
        PARTITION BY source, season, week, expert, away, home
        ORDER BY captured_at DESC) AS rn
    FROM ref.expert_pick
) WHERE rn = 1;
"""

TAG = re.compile(r"<[^>]+>")


def log(m=""):
    print(m, flush=True)


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8", "replace")


def strip(s):
    return re.sub(r"\s+", " ", TAG.sub(" ", s)).strip()


def parse_cbs(html, season, week, url, now):
    """The grid is a plain table: experts in the header, one row per game.

    Column order is the contract. The nth pick cell in a row belongs to the
    nth expert in the header, so the two are zipped rather than matched by any
    identifier, and a length mismatch is treated as a parse failure rather
    than silently truncated.
    """
    head = html[:html.index("<tbody")] if "<tbody" in html else html
    experts = [(f"{a} {b}", strip(c)) for a, b, c in re.findall(
        r'AuthorHeadshotAndName-firstName">([^<]+)</span>'
        r'<span class="AuthorHeadshotAndName-lastName">([^<]+)</span></div>'
        r'<div class="AuthorHeadshotAndName-byLine">([^<]*)</div>', head)]
    if not experts:
        return []

    rows = []
    body = html[html.index("<tbody"):] if "<tbody" in html else ""
    for tr in re.findall(r'<tr class="TableBase-bodyTr">(.*?)</tr>', body,
                         re.S):
        teams = re.findall(r'GameMatchup-teamAbbr">([A-Z]{2,3})</span>', tr)
        if len(teams) != 2:
            continue
        away, home = teams
        picks = [re.sub(r"\s+", " ", x).strip() for x in re.findall(
            r'TableExpertPicks-pick">(.*?)</p>', tr, re.S)]
        if len(picks) != len(experts):
            log(f"  CBS: {away}@{home} has {len(picks)} picks for "
                f"{len(experts)} experts; skipped rather than guessed")
            continue
        for (name, role), raw in zip(experts, picks):
            m = re.match(r"([A-Z]{2,3})\s*([+-]?\d+(?:\.\d+)?)?", raw or "")
            if not m:
                continue
            rows.append((now, "cbs", season, week, name, role, away, home,
                         "ats", m.group(1),
                         float(m.group(2)) if m.group(2) else None,
                         raw, url))
    return rows


# ESPN team codes differ from nflverse in a handful of places. Left
# unmapped these become teams that do not exist and join to nothing.
ESPN_TEAM = {"WSH": "WAS", "LAR": "LA"}


def parse_espn(html, season, week, url, now):
    """ESPN publishes straight-up winners, so there is no number to store.

    The page is two parallel tables: a fixed left table carrying the games,
    and a scrolling right table carrying the experts and their picks. They are
    joined by ROW ORDER and by the game id on each <tr>, and the pick itself is
    encoded only in the team logo URL of each cell.

    Recorded as pick_type 'straight_up' so nothing downstream can pool these
    with a pick that took a price. A straight-up pick is nearly always the
    favorite, which is why it carries information mainly on close games.
    """
    if "Table__Scroller" not in html:
        return []
    left = html[:html.index("Table__Scroller")]
    right = html[html.index("Table__Scroller"):]

    games = []
    for gid, matchup in re.findall(
            r'<tr[^>]*id="(\d+)"[^>]*>.*?>([A-Z]{2,3} VS [A-Z]{2,3})<',
            left, re.S):
        a, _, hm = matchup.partition(" VS ")
        games.append((gid, ESPN_TEAM.get(a, a), ESPN_TEAM.get(hm, hm)))
    if not games:
        return []

    head = right[:right.index("Table__TBODY")]
    experts = [n for n in re.findall(r'<img alt="([^"]+)"', head)
               if n.lower() != "logo team"]
    body = right[right.index("Table__TBODY"):]

    rows = []
    for gid, away, home in games:
        m = re.search(r'<tr[^>]*id="' + gid + r'"[^>]*>(.*?)</tr>', body, re.S)
        if not m:
            continue
        # Parse CELL BY CELL, not by scanning the row for logos. An analyst
        # who sat a game out renders a cell with no logo at all, so counting
        # logos across the row silently shifts every later pick onto the wrong
        # name. The guard caught exactly that: 10 logos against 11 experts.
        cells = re.findall(r'<td class="Table__TD">(.*?)</td>', m.group(1),
                           re.S)
        if len(cells) != len(experts):
            log(f"  ESPN: {away}@{home} has {len(cells)} cells for "
                f"{len(experts)} experts; skipped rather than guessed")
            continue
        for name, cell in zip(experts, cells):
            g = re.search(r'teamlogos/nfl/500/(\w+)\.png', cell)
            if not g:
                continue          # no pick from this analyst on this game
            pick = ESPN_TEAM.get(g.group(1).upper(), g.group(1).upper())
            rows.append((now, "espn", season, week, name, "ESPN analyst",
                         away, home, "straight_up", pick, None, pick, url))
    return rows


def capture(con, season, week):
    now = datetime.now().replace(microsecond=0)
    total = 0
    for label, url, parser in (
            ("CBS ", CBS.format(week=week), parse_cbs),
            ("ESPN", ESPN.format(week=week), parse_espn)):
        try:
            rows = parser(get(url), season, week, url, now)
            if rows:
                con.executemany(
                    "INSERT INTO ref.expert_pick "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                ex = len({r[4] for r in rows})
                gm = len({(r[6], r[7]) for r in rows})
                log(f"  {label} {len(rows)} picks, {ex} experts, {gm} games")
                total += len(rows)
            else:
                log(f"  {label} nothing published for week {week} yet")
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            log(f"  {label} unavailable ({type(exc).__name__}); "
                f"keeping the last capture")
    return total


def show(con):
    try:
        n, s = con.execute(
            "SELECT count(*), cast(max(captured_at) AS VARCHAR) "
            "FROM ref.expert_pick").fetchone()
    except Exception:
        log("no picks captured yet")
        return 0
    log(f"{n:,} picks stored, last capture {str(s)[:19]}")
    for r in con.execute("""
        SELECT source, expert, count(*) n, count(DISTINCT week) wk
        FROM ref.expert_pick GROUP BY 1, 2 ORDER BY 1, n DESC
    """).fetchall():
        log(f"  {r[0]:<6}{r[1]:<20}{r[2]:>5} picks over {r[3]} week(s)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    try:
        con.execute(SQL)
        if a.show:
            return show(con)
        week = a.week
        if week is None:
            week = con.execute(
                "SELECT week FROM mart.current_week").fetchone()[0]
        log(f"capturing published picks, {a.season} week {week}")
        capture(con, a.season, week)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
