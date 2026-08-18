"""
probe_odds_api.py - discover what The Odds API actually returns before we
build a pipeline against assumptions.

Answers four questions, cheapest call first:
  1. What is our quota and plan? (/v4/sports is FREE - 0 credits)
  2. Which NFL sport keys are live right now?
  3. What are the REAL bookmaker keys for us,us2? (our book_aliases.json is
     seeded from documented values and unverified)
  4. Which markets exist, and what do they cost?

Credit accounting is read from the x-requests-used / x-requests-remaining
response headers and printed after every call, so nothing is spent blind.

Usage:
    py -3 scripts/probe_odds_api.py            # free + one cheap odds call
    py -3 scripts/probe_odds_api.py --props    # + one event's prop markets
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get, require  # noqa: E402

BASE = "https://api.the-odds-api.com/v4"
ROOT = Path(__file__).resolve().parent.parent


def call(path: str, params: dict, label: str):
    p = dict(params)
    p["apiKey"] = require("THE_ODDS_API_KEY")
    r = requests.get(f"{BASE}{path}", params=p, timeout=45)
    used = r.headers.get("x-requests-used", "?")
    left = r.headers.get("x-requests-remaining", "?")
    last = r.headers.get("x-requests-last", "?")
    print(f"  [{r.status_code}] {label}   cost={last}  used={used}  remaining={left}")
    if r.status_code != 200:
        print(f"        body: {r.text[:300]}")
        return None
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--props", action="store_true",
                    help="also probe player prop markets on one event (costs more)")
    args = ap.parse_args()

    regions = get("ODDS_API_REGIONS", "us,us2")

    print("=" * 74)
    print("1. QUOTA + SPORT KEYS  (/v4/sports is free)")
    print("=" * 74)
    sports = call("/sports", {"all": "true"}, "GET /sports")
    if sports is None:
        return 1
    nfl = [s for s in sports if "americanfootball_nfl" in s["key"]]
    print()
    for s in nfl:
        print(f"    {s['key']:<38} active={s['active']}  {s['title']}")

    live_keys = [s["key"] for s in nfl if s["active"]]
    if not live_keys:
        print("\n  No active NFL sport key right now.")
        return 0

    target = ("americanfootball_nfl" if "americanfootball_nfl" in live_keys
              else live_keys[0])

    print()
    print("=" * 74)
    print(f"2. EVENTS on {target}  (/events is free)")
    print("=" * 74)
    events = call(f"/sports/{target}/events", {}, "GET /events")
    if events:
        print(f"\n    {len(events)} events")
        for e in events[:5]:
            print(f"    {e['commence_time']}  {e['away_team']} @ {e['home_team']}")
        if len(events) > 5:
            print(f"    ... +{len(events) - 5} more")

    print()
    print("=" * 74)
    print(f"3. BOOK KEYS + MAIN MARKETS   regions={regions}")
    print("=" * 74)
    print("    cost = [markets] x [regions] = 3 x "
          f"{len(regions.split(','))} = {3 * len(regions.split(','))} credits")
    odds = call(f"/sports/{target}/odds",
                {"regions": regions, "markets": "h2h,spreads,totals",
                 "oddsFormat": "american"},
                "GET /odds h2h,spreads,totals")
    if not odds:
        return 1

    books = Counter()
    markets = Counter()
    for ev in odds:
        for bk in ev.get("bookmakers", []):
            books[(bk["key"], bk["title"])] += 1
            for mk in bk.get("markets", []):
                markets[mk["key"]] += 1

    print(f"\n    {len(odds)} events with prices\n")
    print(f"    {'book key':<26}{'title':<26}{'events':>7}")
    for (k, t), n in books.most_common():
        print(f"    {k:<26}{t:<26}{n:>7}")

    print(f"\n    markets returned: {dict(markets)}")

    # Compare against what we seeded, so unmapped books surface now, not later.
    alias_path = ROOT / "ref" / "overrides" / "book_aliases.json"
    seeded = json.loads(alias_path.read_text(encoding="utf-8")).get("the_odds_api", {})
    live = {k for k, _ in books}
    print("\n    --- reconciliation vs ref/overrides/book_aliases.json ---")
    unmapped = sorted(live - set(seeded))
    stale = sorted(set(seeded) - live)
    print(f"    live but UNMAPPED ({len(unmapped)}): "
          f"{', '.join(unmapped) if unmapped else 'none'}")
    print(f"    mapped but ABSENT ({len(stale)}): "
          f"{', '.join(stale) if stale else 'none'}")

    if odds:
        ev = odds[0]
        print(f"\n    sample event: {ev['away_team']} @ {ev['home_team']}")
        for bk in ev.get("bookmakers", [])[:2]:
            for mk in bk.get("markets", []):
                outs = ", ".join(
                    f"{o['name']}"
                    + (f" {o.get('point')}" if o.get("point") is not None else "")
                    + f" @ {o['price']}"
                    for o in mk["outcomes"])
                print(f"      {bk['key']:<16}{mk['key']:<10}{outs}")

    if args.props and events:
        print()
        print("=" * 74)
        print("4. PLAYER PROP MARKETS on one event")
        print("=" * 74)
        eid = events[0]["id"]
        prop_markets = ("player_pass_yds,player_rush_yds,player_reception_yds,"
                        "player_receptions,player_anytime_td")
        print(f"    event {eid}: {events[0]['away_team']} @ {events[0]['home_team']}")
        print(f"    cost = 5 markets x {len(regions.split(','))} regions "
              f"= {5 * len(regions.split(','))} credits")
        pr = call(f"/sports/{target}/events/{eid}/odds",
                  {"regions": regions, "markets": prop_markets,
                   "oddsFormat": "american"},
                  "GET /events/{id}/odds props")
        if pr:
            pm = Counter()
            pb = Counter()
            for bk in pr.get("bookmakers", []):
                pb[bk["key"]] += 1
                for mk in bk.get("markets", []):
                    pm[mk["key"]] += len(mk.get("outcomes", []))
            print(f"\n    books offering props: {sorted(pb)}")
            print(f"    outcomes per market : {dict(pm)}")
            for bk in pr.get("bookmakers", [])[:1]:
                for mk in bk.get("markets", [])[:2]:
                    print(f"\n      {bk['key']} / {mk['key']}")
                    for o in mk.get("outcomes", [])[:4]:
                        print(f"        {o.get('description', o['name']):<26}"
                              f"{o['name']:<6}{o.get('point')}  @ {o['price']}")

    print("\ndone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
