"""
probe_sgo.py - evaluate SportsGameOdds against the ONLY questions that matter.

We already have DraftKings, FanDuel, BetMGM and Pinnacle working on The Odds
API for $30/mo. A switch to SportsGameOdds at $99/mo is justified by exactly
one thing: whether it actually delivers the three placeable books we cannot
see today - bet365, Caesars, Fanatics - with real NFL market depth.

A bookmaker appearing in a vendor's published list is a CLAIM. This measures
it. Questions, in order of decision weight:

  1. Which of the six Louisiana-placeable books return NFL odds at all?
  2. What market depth per book - sides/totals only, or player props too?
  3. Are the sharp references (Pinnacle, Circa) present?
  4. How does quota accounting actually work against the trial plan?

Discovery-first: endpoints and response shapes are probed and reported rather
than assumed, so a shape change surfaces as a finding instead of a traceback.

Usage:
    py -3 scripts/probe_sgo.py
    py -3 scripts/probe_sgo.py --dump          # raw structure for one event
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import require  # noqa: E402

BASE = "https://api.sportsgameodds.com/v2"

# What we actually care about, from ref.book
PLACEABLE = {"draftkings", "fanduel", "betmgm", "bet365", "caesars", "fanatics"}
SHARP = {"pinnacle", "circa"}


def log(m: str = "") -> None:
    print(m, flush=True)


def api(path: str, params: dict | None = None):
    key = require("SPORTSGAMEODDS_API_KEY")
    try:
        r = requests.get(f"{BASE}{path}", params=params or {},
                         headers={"X-Api-Key": key}, timeout=60)
    except requests.RequestException as e:
        log(f"    request failed: {e}")
        return None, None
    hdr = {k.lower(): v for k, v in r.headers.items()
           if "ratelimit" in k.lower() or "quota" in k.lower()
           or "usage" in k.lower() or "remaining" in k.lower()}
    if r.status_code != 200:
        log(f"    [{r.status_code}] {path}  {r.text[:200]}")
        return None, hdr
    try:
        return r.json(), hdr
    except ValueError:
        log(f"    [{r.status_code}] {path}: non-JSON response")
        return None, hdr


def unwrap(payload):
    """SGO wraps results as {success, data}. Tolerate either shape."""
    if isinstance(payload, dict):
        for k in ("data", "results", "events"):
            if k in payload and isinstance(payload[k], list):
                return payload[k]
        return [payload]
    return payload if isinstance(payload, list) else []


def collect_books(ev: dict) -> dict[str, Counter]:
    """bookmakerID -> Counter of market families seen on this event."""
    out: dict[str, Counter] = defaultdict(Counter)
    odds = ev.get("odds")
    if not isinstance(odds, dict):
        return out
    for odd_id, o in odds.items():
        if not isinstance(o, dict):
            continue
        # Classify on statEntityID, not on a guess from the oddID string.
        # Player props use the PERIOD 'game' too, so period is useless here;
        # what separates them is whether the entity is a team/side or a player.
        stat = o.get("statID") or (odd_id.split("-")[0] if odd_id else "?")
        entity = str(o.get("statEntityID", ""))
        is_player = entity not in ("all", "home", "away", "side1", "side2", "")
        fam = f"prop:{stat}" if is_player else f"game:{stat}"
        bb = o.get("byBookmaker")
        if isinstance(bb, dict):
            for bid, val in bb.items():
                if isinstance(val, dict):
                    out[bid][fam] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true",
                    help="print raw structure of one event and exit")
    ap.add_argument("--events", type=int, default=6,
                    help="how many events to inspect for prop depth")
    a = ap.parse_args()

    log("=" * 78)
    log("1. ACCOUNT / QUOTA")
    log("=" * 78)
    for path in ("/account/usage/", "/account/usage"):
        js, hdr = api(path)
        if js:
            log("  " + json.dumps(js, indent=2)[:900])
            break
    else:
        log("  usage endpoint not reachable; will infer from headers")

    log("\n" + "=" * 78)
    log("2. NFL EVENTS")
    log("=" * 78)
    events = []
    # type=match matters: without it the feed returns novelty prop events
    # (the first result was Puppy Bowl XX), which carry no teams and no
    # per-bookmaker breakdown. Always constrain to real games.
    attempts = [
        ("/events/", {"leagueID": "NFL", "season": 2026, "week": 1,
                      "type": "match", "oddsAvailable": "true"}),
        ("/events/", {"leagueID": "NFL", "season": 2026, "week": 1,
                      "type": "match"}),
        ("/events/", {"leagueID": "NFL", "season": 2026, "type": "match"}),
        ("/events/", {"leagueID": "NFL", "type": "match"}),
    ]
    used = None
    for path, params in attempts:
        js, hdr = api(path, params)
        rows = unwrap(js) if js else []
        if rows:
            events, used = rows, params
            log(f"  {path} {params} -> {len(rows)} events")
            if hdr:
                log(f"  quota headers: {hdr}")
            break
        log(f"  {path} {params} -> 0")
    if not events:
        log("\n  No events returned. Cannot evaluate. Check key/plan/league id.")
        return 1

    ev0 = events[0]
    log(f"\n  event keys: {sorted(ev0.keys())[:18]}")
    teams = ev0.get("teams") or {}
    if isinstance(teams, dict):
        try:
            log(f"  sample: {teams.get('away', {}).get('names', {}).get('short')}"
                f" @ {teams.get('home', {}).get('names', {}).get('short')}")
        except Exception:
            pass

    if a.dump:
        log("\n--- RAW EVENT (truncated) ---")
        log(json.dumps(ev0, indent=2)[:4000])
        return 0

    log("\n" + "=" * 78)
    log("3. BOOKMAKER COVERAGE  (the decision)")
    log("=" * 78)
    totals: dict[str, Counter] = defaultdict(Counter)
    ev_with_odds = 0
    inspected = 0
    for ev in events:
        if inspected >= a.events:
            break
        got = collect_books(ev)
        if not got:
            # odds may need a per-event fetch
            eid = ev.get("eventID") or ev.get("id")
            js, _ = api(f"/events/", {"eventID": eid})
            rows = unwrap(js) if js else []
            if rows:
                got = collect_books(rows[0])
        if got:
            ev_with_odds += 1
            for bid, c in got.items():
                totals[bid].update(c)
        inspected += 1

    log(f"  inspected {inspected} events, {ev_with_odds} carried odds\n")
    if not totals:
        log("  NO BOOKMAKER ODDS RETURNED.")
        log("  Either the trial tier withholds odds, or odds require a")
        log("  different endpoint. Re-run with --dump to inspect the shape.")
        return 1

    log(f"  {'bookmakerID':<20}{'markets':>9}{'game':>8}{'props':>8}   status")
    rows = []
    for bid, c in totals.items():
        game = sum(v for k, v in c.items() if k.startswith("game:"))
        prop = sum(v for k, v in c.items() if k.startswith("prop:"))
        rows.append((bid, sum(c.values()), game, prop))
    for bid, tot, game, prop in sorted(rows, key=lambda r: -r[1]):
        if bid in PLACEABLE:
            status = "PLACEABLE"
        elif bid in SHARP:
            status = "sharp ref"
        else:
            status = ""
        log(f"  {bid:<20}{tot:>9,}{game:>8,}{prop:>8,}   {status}")

    seen = set(totals)
    log("\n  --- the six placeable books ---")
    for b in sorted(PLACEABLE):
        c = totals.get(b)
        if c:
            prop = sum(v for k, v in c.items() if k.startswith("prop:"))
            log(f"    {b:<14} PRESENT   {sum(c.values()):,} markets "
                f"({prop:,} prop)")
        else:
            log(f"    {b:<14} ABSENT")
    log("\n  --- sharp references ---")
    for b in sorted(SHARP):
        log(f"    {b:<14} {'PRESENT' if b in seen else 'ABSENT'}")

    missing_today = {"bet365", "caesars", "fanatics"}
    gained = missing_today & seen
    log("\n" + "=" * 78)
    log("4. VERDICT vs THE ODDS API ($30/mo)")
    log("=" * 78)
    log(f"  books we cannot see today : {', '.join(sorted(missing_today))}")
    log(f"  delivered here            : "
        f"{', '.join(sorted(gained)) if gained else 'NONE'}")
    log(f"  circa (sharp openers)     : "
        f"{'PRESENT' if 'circa' in seen else 'ABSENT'}")
    if len(gained) == 3:
        log("\n  All three missing placeable books are covered. The $69/mo")
        log("  delta buys a complete six-book board for prop shopping.")
    elif gained:
        log(f"\n  Partial: {len(gained)}/3. Weigh against $69/mo.")
    else:
        log("\n  None of the missing books appeared. On this evidence the")
        log("  upgrade does NOT solve the problem it was meant to solve.")

    log("\n  market families seen (top 15):")
    fam = Counter()
    for c in totals.values():
        fam.update(c)
    for k, v in fam.most_common(15):
        log(f"    {k:<34}{v:>8,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
