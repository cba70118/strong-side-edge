"""
verify_sgo.py - the three checks that decide whether SportsGameOdds becomes
the primary board.

  1. Does byBookmaker populate WITHOUT a bookmakerID filter?
     Decides whether one pull covers four books or needs four pulls, which
     drives both quota burn and snapshot latency.

  2. Caesars prop depth vs DraftKings, market by market.
     708 quotes vs 1,618 could be August thinness or a real coverage gap.
     Breadth (how many statIDs) matters more than raw count.

  3. SGO playerID -> nflverse gsis_id match rate.
     Entity resolution is a first-class module; a feed we cannot join to the
     player spine cannot price a prop.

Usage:
    py -3 scripts/verify_sgo.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import require  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "nfl.duckdb"
BASE = "https://api.sportsgameodds.com/v2"
H = {"X-Api-Key": require("SPORTSGAMEODDS_API_KEY")}
BOOKS = ["draftkings", "fanduel", "betmgm", "caesars"]

SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.I)
NONALPHA = re.compile(r"[^a-z ]")


def log(m: str = "") -> None:
    print(m, flush=True)


def norm(name: str) -> str:
    s = (name or "").lower().replace(".", " ").replace("'", "").replace("-", " ")
    s = SUFFIX.sub(" ", s)
    s = NONALPHA.sub(" ", s)
    return " ".join(s.split())


def fetch(params):
    r = requests.get(f"{BASE}/events/", params=params, headers=H, timeout=90)
    if r.status_code != 200:
        log(f"    [{r.status_code}] {r.text[:160]}")
        return []
    return r.json().get("data", [])


def usage():
    try:
        u = requests.get(f"{BASE}/account/usage/", headers=H, timeout=45
                         ).json().get("data", {})
        m = u.get("rateLimits", {}).get("per-month", {})
        return m.get("current-entities"), m.get("max-entities")
    except Exception:
        return None, None


def main() -> int:
    base_params = {"leagueID": "NFL", "season": 2026, "week": 1, "type": "match"}

    # ---------------------------------------------------------------- 1
    log("=" * 78)
    log("1. DOES byBookmaker POPULATE WITHOUT A BOOK FILTER?")
    log("=" * 78)
    e0 = usage()[0]
    unf = fetch(dict(base_params))
    books_unf = Counter()
    obj_unf = 0
    for ev in unf:
        for o in (ev.get("odds") or {}).values():
            if not isinstance(o, dict):
                continue
            obj_unf += 1
            for bid in (o.get("byBookmaker") or {}):
                books_unf[bid] += 1
    e1 = usage()[0]
    log(f"  unfiltered pull: {len(unf)} events, {obj_unf:,} odds objects")
    log(f"  distinct books in byBookmaker: {len(books_unf)}")
    log(f"  our four: " + ", ".join(
        f"{b}={books_unf.get(b, 0):,}" for b in BOOKS))
    if e0 is not None and e1 is not None:
        log(f"  entities consumed: {e1 - e0:,}")
    if all(books_unf.get(b) for b in BOOKS):
        log("\n  VERDICT: one unfiltered pull carries all four books.")
        log("  -> single request per snapshot; no per-book fan-out needed.")
    else:
        missing = [b for b in BOOKS if not books_unf.get(b)]
        log(f"\n  VERDICT: unfiltered payload is INCOMPLETE (missing {missing}).")
        log("  -> must fan out one request per bookmakerID.")

    # ---------------------------------------------------------------- 2
    log("\n" + "=" * 78)
    log("2. CAESARS PROP DEPTH vs DRAFTKINGS")
    log("=" * 78)
    per_book: dict[str, Counter] = {}
    per_book_period: dict[str, Counter] = {}
    # A bookmakerID filter still returns the FULL market universe; it does not
    # restrict rows to markets that book prices. Counting odds objects
    # therefore reports an identical number for every book, which is an
    # artefact, not coverage. Real coverage = the book has an entry in
    # byBookmaker for that market.
    for b in BOOKS:
        evs = fetch(dict(base_params, bookmakerID=b))
        stats, periods = Counter(), Counter()
        for ev in evs:
            for o in (ev.get("odds") or {}).values():
                if not isinstance(o, dict):
                    continue
                if b not in (o.get("byBookmaker") or {}):
                    continue          # this book does not price it
                ent = str(o.get("statEntityID", ""))
                if ent in ("all", "home", "away", "side1", "side2", ""):
                    continue          # player props only
                stats[o.get("statID")] += 1
                periods[o.get("periodID")] += 1
        per_book[b] = stats
        per_book_period[b] = periods

    log(f"  {'book':<14}{'prop objs':>11}{'distinct statIDs':>19}{'periods':>10}")
    for b in BOOKS:
        log(f"  {b:<14}{sum(per_book[b].values()):>11,}"
            f"{len(per_book[b]):>19}{len(per_book_period[b]):>10}")

    dk = set(per_book["draftkings"])
    cz = set(per_book["caesars"])
    log(f"\n  DraftKings statIDs   : {len(dk)}")
    log(f"  Caesars statIDs      : {len(cz)}")
    log(f"  Caesars covers       : {len(dk & cz)}/{len(dk)} of DK's markets")
    only_dk = sorted(dk - cz)
    if only_dk:
        log(f"  DK-only markets ({len(only_dk)}): {', '.join(only_dk[:12])}")
    only_cz = sorted(cz - dk)
    if only_cz:
        log(f"  Caesars-only ({len(only_cz)}): {', '.join(only_cz[:8])}")
    log("\n  core yardage markets, objects per book:")
    core = ["passing_yards", "rushing_yards", "receiving_yards",
            "receiving_receptions", "passing_touchdowns", "touchdowns",
            "firstTouchdown", "rushing+receiving_yards"]
    log(f"    {'market':<28}" + "".join(f"{b[:9]:>11}" for b in BOOKS))
    for s in core:
        log(f"    {s:<28}" + "".join(f"{per_book[b].get(s, 0):>11,}"
                                     for b in BOOKS))

    # ---------------------------------------------------------------- 3
    log("\n" + "=" * 78)
    log("3. SGO playerID -> nflverse gsis_id MATCH RATE")
    log("=" * 78)
    players: dict[str, dict] = {}
    for ev in unf:
        for pid, p in (ev.get("players") or {}).items():
            if isinstance(p, dict):
                players[pid] = p
    log(f"  distinct SGO players across {len(unf)} events: {len(players)}")

    con = duckdb.connect(str(DB), read_only=True)
    rows = con.execute("""
        SELECT gsis_id, display_name, first_name, last_name, latest_team,
               position, last_season
        FROM raw.players
        WHERE gsis_id IS NOT NULL AND coalesce(last_season, 0) >= 2024
    """).fetchall()
    by_full: dict[str, list] = defaultdict(list)
    by_lastfi: dict[str, list] = defaultdict(list)
    for gid, disp, fn, ln, team, pos, ls in rows:
        n = norm(disp)
        by_full[n].append((gid, team, pos))
        if fn and ln:
            by_lastfi[f"{norm(ln)}|{norm(fn)[:1]}"].append((gid, team, pos))
    log(f"  nflverse candidates (last_season >= 2024): {len(rows):,}")

    exact = initial = ambiguous = unmatched = 0
    misses = []
    for pid, p in players.items():
        nm = norm(p.get("name") or
                  f"{p.get('firstName','')} {p.get('lastName','')}")
        cands = by_full.get(nm, [])
        if len(cands) == 1:
            exact += 1
            continue
        if len(cands) > 1:
            ambiguous += 1
            continue
        ln, fn = norm(p.get("lastName", "")), norm(p.get("firstName", ""))
        c2 = by_lastfi.get(f"{ln}|{fn[:1]}", []) if ln and fn else []
        if len(c2) == 1:
            initial += 1
        elif len(c2) > 1:
            ambiguous += 1
        else:
            unmatched += 1
            misses.append(f"{p.get('name')} ({p.get('teamID','?')})")

    tot = len(players)
    log(f"\n  {'exact name match':<26}{exact:>5}  {100*exact/tot:>5.1f}%")
    log(f"  {'last+first-initial':<26}{initial:>5}  {100*initial/tot:>5.1f}%")
    log(f"  {'ambiguous (needs team)':<26}{ambiguous:>5}  {100*ambiguous/tot:>5.1f}%")
    log(f"  {'unmatched':<26}{unmatched:>5}  {100*unmatched/tot:>5.1f}%")
    resolved = exact + initial
    log(f"\n  auto-resolved: {resolved}/{tot} = {100*resolved/tot:.1f}%")
    if misses:
        log(f"\n  unmatched sample: {'; '.join(misses[:10])}")
    log("\n  Note ambiguous cases are resolvable with teamID, which SGO")
    log("  supplies on every player object - they are work, not blockers.")

    cur, mx = usage()
    log(f"\n  quota after all checks: {cur}/{mx} entities")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
