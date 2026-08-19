"""capture_news.py - injury status and headlines, stored rather than fetched live.

WHY STORE IT. The published page is a static encrypted artifact with no network
of its own, so anything it shows has to be baked in at build time. Storing also
gives a history: an injury that appears on Wednesday and clears on Friday is a
fact about the week, and a page that only ever shows "now" throws that away.

WHAT THIS IS FOR, AND WHAT IT IS NOT. The official report already measured out
at a partial correlation of +0.0425 against the closing spread on 1,609 games,
under the 0.05 bar, while the market visibly prices it (+0.157 with the
spread). So this is CONTEXT and a correctness filter, never a signal. Nothing
here feeds a price.

THIRD-PARTY CONTENT. Headlines and a one-line description are stored with the
source link, the way any reader does it. Full article text is not copied.

Sources, both public and unauthenticated:
    injuries   site.api.espn.com/.../nfl/injuries      800 rows, all 32 teams
    news       site.api.espn.com/.../nfl/news          rolling headlines

Usage:
    py -3 scripts/capture_news.py
    py -3 scripts/capture_news.py --show
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
UA = {"User-Agent": "Mozilla/5.0"}
BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

SQL = """
CREATE SCHEMA IF NOT EXISTS ref;

-- Point-in-time injury status. Keyed by player and the timestamp we saw it, so
-- a status that changes during the week leaves both rows behind.
CREATE TABLE IF NOT EXISTS ref.injury_status (
    captured_at  TIMESTAMP NOT NULL,
    team         VARCHAR,
    player       VARCHAR,
    position     VARCHAR,
    status       VARCHAR,
    injury       VARCHAR,
    location     VARCHAR,
    reported_at  VARCHAR,
    comment      VARCHAR
);

CREATE TABLE IF NOT EXISTS ref.news_item (
    captured_at  TIMESTAMP NOT NULL,
    published    VARCHAR,
    headline     VARCHAR,
    description  VARCHAR,
    link         VARCHAR,
    news_id      VARCHAR
);

-- The newest status per player, which is what a page should show.
CREATE OR REPLACE VIEW ref.injury_current AS
SELECT team, player, position, status, injury, location, reported_at, comment,
       captured_at
FROM (
    SELECT *, row_number() OVER (PARTITION BY team, player
                                 ORDER BY captured_at DESC) AS rn
    FROM ref.injury_status
) WHERE rn = 1;
"""


def log(m=""):
    print(m, flush=True)


def api(path: str):
    req = urllib.request.Request(f"{BASE}{path}", headers=UA)
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())


def capture(con) -> int:
    now = datetime.now().replace(microsecond=0)

    inj_rows = []
    try:
        d = api("/injuries")
        for team in d.get("injuries") or []:
            tname = team.get("displayName")
            for x in team.get("injuries") or []:
                ath = x.get("athlete") or {}
                det = x.get("details") or {}
                inj_rows.append((
                    now, tname, ath.get("displayName"),
                    ((ath.get("position") or {}).get("abbreviation")),
                    x.get("status"), det.get("type"), det.get("location"),
                    str(x.get("date") or "")[:19],
                    str(x.get("shortComment") or "")[:300]))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log(f"  injuries unavailable ({type(exc).__name__}); keeping the last "
            f"capture")

    news_rows = []
    try:
        d = api("/news?limit=40")
        for a in d.get("articles") or []:
            link = (((a.get("links") or {}).get("web") or {}).get("href")
                    or (a.get("links") or {}).get("api", {}).get("news", {})
                    .get("href"))
            news_rows.append((
                now, str(a.get("published") or "")[:19],
                str(a.get("headline") or "")[:300],
                str(a.get("description") or "")[:600],
                link, str(a.get("id") or "")))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log(f"  news unavailable ({type(exc).__name__}); keeping the last "
            f"capture")

    if inj_rows:
        con.executemany("INSERT INTO ref.injury_status VALUES (?,?,?,?,?,?,?,?,?)",
                        inj_rows)
    # De-duplicate headlines by id: the feed repeats the same story for hours
    # and a page that shows it four times looks broken.
    if news_rows:
        seen = set(r[0] for r in con.execute(
            "SELECT DISTINCT news_id FROM ref.news_item").fetchall())
        fresh = [r for r in news_rows if r[5] not in seen]
        if fresh:
            con.executemany("INSERT INTO ref.news_item VALUES (?,?,?,?,?,?)",
                            fresh)
        log(f"  news: {len(news_rows)} returned, {len(fresh)} new")
    log(f"  injuries: {len(inj_rows)} rows across "
        f"{len({r[1] for r in inj_rows})} teams")
    return 0 if (inj_rows or news_rows) else 1


def show(con) -> int:
    r = con.execute("SELECT count(*), cast(max(captured_at) AS VARCHAR) "
                    "FROM ref.injury_status").fetchone()
    log(f"injury rows {r[0]:,}, last capture {str(r[1])[:19]}")
    for x in con.execute("""
        SELECT status, count(*) FROM ref.injury_current
        GROUP BY 1 ORDER BY 2 DESC LIMIT 6
    """).fetchall():
        log(f"  {str(x[0]):<16}{x[1]:>5}")
    n = con.execute("SELECT count(*) FROM ref.news_item").fetchone()[0]
    log(f"news items {n}")
    for x in con.execute("""
        SELECT published, headline FROM ref.news_item
        ORDER BY published DESC LIMIT 5
    """).fetchall():
        log(f"  {str(x[0])[:16]}  {str(x[1])[:66]}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    try:
        con.execute(SQL)
        return show(con) if a.show else capture(con)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
