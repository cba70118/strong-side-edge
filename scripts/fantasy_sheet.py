"""fantasy_sheet.py - a draft cheat sheet for someone who does not watch football.

WHAT THIS IS. Our season projections turned into fantasy points, ranked, put
into tiers, and set beside where real drafters are actually taking each player.
Every row carries a one-line reason in plain English, built from the numbers
rather than written by hand.

WHY ADP MATTERS MORE THAN THE RANKING. A cheat sheet that only ranks players is
useless in a draft, because everyone else has a ranking too. What you need is
where our number disagrees with the room: a player we like who is going late is
the only actionable thing on the page. ADP comes from
fantasyfootballcalculator.com, which publishes it free from real mock drafts
(7,000+ this week) and permits programmatic use.

SCORING is standard PPR, which is what most beginner leagues use by default:

    passing     1 point per 25 yards, 4 per touchdown
    rushing     1 point per 10 yards, 6 per touchdown
    receiving   1 point per 10 yards, 6 per touchdown, 1 per catch

WHAT THIS CANNOT DO, said once rather than hedged everywhere. It does not know
about holdouts, suspensions, or a coach saying something in August. It projects
a full 17-game season for anyone the depth chart lists as a starter, so it will
never predict an injury. And touchdowns barely repeat year to year, so anyone
whose value is mostly touchdowns is the least reliable name on the page.

Usage:
    py -3 scripts/fantasy_sheet.py
    py -3 scripts/fantasy_sheet.py --out handoff/cheat_sheet.html
"""

from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
import urllib.request
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
ADP_URL = ("https://fantasyfootballcalculator.com/api/v1/adp/ppr"
           "?teams=12&year={year}&position=all")

# Standard PPR. Kept as plain constants so a reader can check the arithmetic.
PTS = {"pass_yd": 1 / 25, "pass_td": 4, "rush_yd": 1 / 10, "rush_td": 6,
       "rec_yd": 1 / 10, "rec_td": 6, "rec": 1, "int": -2}

# REPLACEMENT LEVEL, and this is the whole reason a cheat sheet exists.
#
# Ranking by raw fantasy points puts quarterbacks at the top, because they
# throw for 4,000 yards and everyone else does not. Ranking that way told the
# first draft of this sheet to take Jacoby Brissett in the second round, which
# would be a catastrophe. The reason is that a 12-team league starts ONE
# quarterback, so the gap between the best and the twelfth-best is what you
# actually gain by drafting him early - and that gap is small. At running back
# you start two plus a flex, so the same comparison runs 30 deep and the gap
# is enormous.
#
# So every player is scored against the last starter at HIS position. That is
# the number that decides a draft.
LEAGUE_TEAMS = 12
REPLACEMENT = {"QB": 12, "RB": 30, "WR": 40, "TE": 12}

# How deep each position is worth listing.
DEPTH = {"QB": 20, "RB": 45, "WR": 55, "TE": 20}

TEAM_FIX = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI"}


def log(m=""):
    print(m, flush=True)


def e(s):
    return html.escape("" if s is None else str(s), quote=True)


def norm(name):
    """Match names across two feeds that punctuate differently."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s.lower())
    return re.sub(r"[^a-z]", "", s)


def fetch_adp(year):
    """Real draft positions, or nothing. An invented ADP is worse than none."""
    try:
        req = urllib.request.Request(ADP_URL.format(year=year),
                                     headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=45).read())
        out = {}
        for p in d.get("players", []):
            out[norm(p.get("name"))] = {
                "adp": p.get("adp"), "pos": p.get("position"),
                "team": TEAM_FIX.get(p.get("team"), p.get("team")),
                "bye": p.get("bye"), "high": p.get("high"), "low": p.get("low"),
            }
        meta = d.get("meta", {})
        log(f"  ADP: {len(out)} players from {meta.get('total_drafts', 0):,} "
            f"mock drafts, {meta.get('start_date')} to {meta.get('end_date')}")
        return out, meta
    except Exception as exc:
        log(f"  ADP unavailable ({type(exc).__name__}); ranking only")
        return {}, {}


def load(con, season):
    rows = con.execute("""
        SELECT display_name, team, position, depth_rank,
               coalesce(proj_pass_yards, 0), coalesce(proj_pass_tds, 0),
               coalesce(pr_ints, 0),
               coalesce(proj_rush_yards, 0), coalesce(proj_rush_tds, 0),
               coalesce(proj_rec_yards, 0), coalesce(proj_rec_tds, 0),
               coalesce(proj_receptions, 0), coalesce(proj_targets, 0),
               coalesce(proj_carries, 0),
               pr_games, availability_flag, coalesce(team_moved, false),
               coalesce(pr_targets, 0) + coalesce(pr_carries, 0)
        FROM mart.player_season_projection
        WHERE season = ? AND position IN ('QB', 'RB', 'WR', 'TE')
    """, [season]).fetchall()
    out = []
    for r in rows:
        (nm, tm, pos, rank, py, ptd, ints, ry, rtd, cy, ctd, rec, tgt,
         car, pg, flag, moved, prior_touch) = r
        # WHERE OUR NUMBER IS BLIND. This model reads last season and nothing
        # else, so it cannot see a player who just took a starting job, missed
        # most of last year, or changed team. Those are exactly the players a
        # draft room prices differently, and on those the room is usually
        # right and we are behind. Flagged rather than presented as a fade.
        thin = ((pg or 0) <= 8
                or (rank == 1 and prior_touch < 150 and pos in ("RB", "WR"))
                or bool(moved))
        pts = (py * PTS["pass_yd"] + ptd * PTS["pass_td"] + ints * PTS["int"]
               + ry * PTS["rush_yd"] + rtd * PTS["rush_td"]
               + cy * PTS["rec_yd"] + ctd * PTS["rec_td"] + rec * PTS["rec"])
        out.append({"name": nm, "team": tm, "pos": pos, "rank": rank,
                    "pts": pts, "ppg": pts / 17.0,
                    "pass_yd": py, "pass_td": ptd, "rush_yd": ry,
                    "rush_td": rtd, "rec_yd": cy, "rec_td": ctd,
                    "rec": rec, "tgt": tgt, "car": car,
                    "games": pg, "flag": flag, "thin": thin,
                    "moved": bool(moved), "prior_touch": prior_touch})
    return out


def add_context(con, season, players):
    """Team share, so a reason can say WHY rather than repeat the projection."""
    tot = {}
    for p in players:
        if p["pos"] in ("WR", "TE", "RB"):
            t = tot.setdefault(p["team"], {"tgt": 0.0, "car": 0.0})
            t["tgt"] += p["tgt"] or 0
            t["car"] += p["car"] or 0
    for p in players:
        t = tot.get(p["team"], {"tgt": 0, "car": 0})
        p["tgt_share"] = (p["tgt"] / t["tgt"]) if t["tgt"] else 0
        p["car_share"] = (p["car"] / t["car"]) if t["car"] else 0
    # who throws them the ball
    qb = {}
    for p in players:
        if p["pos"] == "QB" and (p["rank"] or 9) == 1:
            qb[p["team"]] = p
    for p in players:
        # Store the FACT, not the player object. Holding the dict made a
        # quarterback's qb field point at himself, which is a cycle the moment
        # anything serializes it.
        starter = qb.get(p["team"])
        p["qb_name"] = starter["name"] if starter else None
        p["qb_td"] = starter["pass_td"] if starter else 0
    return players


def _ord(n):
    """1st reads badly next to "most", so the top of a list says so plainly."""
    if n == 1:
        return "the"
    suf = ("th" if 11 <= n % 100 <= 13
           else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))
    return f"{n}{suf}"


def reason(p, adp_row, val, ranks):
    """One sentence a person who does not watch football can act on.

    Raw counts with a league rank, never a share of the team. Share sounded
    friendlier but the denominator is only players who produced last season,
    so a team with one established back read "98% of the carries", which is a
    fact about our table rather than about football.
    """
    pos = p["pos"]
    r = ranks.get(id(p), {})
    bits = []
    if pos == "QB":
        bits.append(f"{p['pass_yd']:,.0f} passing yards and "
                    f"{p['pass_td']:.0f} touchdowns")
        if p["rush_yd"] >= 300:
            bits.append(f"plus {p['rush_yd']:,.0f} yards running, which counts "
                        f"the same and is why he is worth more than his arm")
    elif pos == "RB":
        bits.append(f"{p['car']:,.0f} carries, {_ord(r.get('car', 99))} most "
                    f"among running backs")
        if p["rec"] >= 45:
            bits.append(f"and he catches {p['rec']:.0f} passes, which most "
                        f"backs do not and which is worth a point each")
        elif p["rush_td"] >= 8:
            bits.append(f"with {p['rush_td']:.0f} touchdowns projected")
    else:
        bits.append(f"{p['rec_yd']:,.0f} receiving yards on {p['tgt']:.0f} "
                    f"throws his way, {_ord(r.get('rec_yd', 99))} most at his "
                    f"position")
        if p.get("qb_td", 0) >= 28 and p.get("qb_name"):
            bits.append(f"and {p['qb_name']} should throw about "
                        f"{p['qb_td']:.0f} touchdowns for someone to catch")
    s = ", ".join(bits[:2])
    s = s[0].upper() + s[1:] + "."
    if val is not None and val >= 12 and adp_row:
        s += (f" <b>Other drafters are letting him slide to pick "
              f"{adp_row['adp']:.0f}.</b> Take him a round earlier than that.")
    elif val is not None and val <= -12 and adp_row:
        if p.get("thin"):
            s += (f" People are taking him at pick {adp_row['adp']:.0f}, way "
                  f"ahead of our number &mdash; but we only read last season "
                  f"and his situation just changed. <b>Trust them, not us.</b>")
        else:
            s += (f" People are taking him around pick {adp_row['adp']:.0f}. "
                  f"That is earlier than he is worth &mdash; let someone else "
                  f"have him.")
    return s


def build(con, season, year):
    adp, meta = fetch_adp(year)
    players = add_context(con, season, load(con, season))
    # Only starters. A backup's projection assumes he starts, which he will not.
    starters = [p for p in players
                if (p["rank"] or 9) <= {"QB": 1, "RB": 2, "WR": 3,
                                        "TE": 1}.get(p["pos"], 1)]
    starters.sort(key=lambda p: -p["pts"])

    by_pos = {}
    for p in starters:
        by_pos.setdefault(p["pos"], []).append(p)

    # Points above the last startable player at the same position.
    base = {}
    for pos, lst in by_pos.items():
        lst.sort(key=lambda p: -p["pts"])
        n = REPLACEMENT.get(pos, 12)
        base[pos] = lst[min(n, len(lst)) - 1]["pts"] if lst else 0.0
        for i, p in enumerate(lst, 1):
            p["pos_rank"] = i
            p["edge"] = p["pts"] - base[pos]
            a = adp.get(norm(p["name"]))
            p["adp"] = a["adp"] if a else None
            p["bye"] = a["bye"] if a else None
    # Rank everyone by that, which is the order to actually draft in.
    starters.sort(key=lambda p: -p["edge"])
    for i, p in enumerate(starters, 1):
        p["our_pick"] = i
        p["value"] = (p["adp"] - i) if p["adp"] is not None else None
    # the sentence is written last, because it needs the final ranking
    # league ranks for the counting stats, so a reason can say "4th most"
    ranks = {}
    for pos, lst in by_pos.items():
        for key in ("car", "rec_yd", "rec", "pass_yd"):
            for i, q in enumerate(
                    sorted(lst, key=lambda x: -(x[key] or 0)), 1):
                ranks.setdefault(id(q), {})[key] = i
    for p in starters:
        p["why"] = reason(p, adp.get(norm(p["name"])), p["value"], ranks)
    return starters, by_pos, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "handoff" / "cheat_sheet.html")
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)
    try:
        starters, by_pos, meta = build(con, a.season, a.season)
    finally:
        con.close()
    log(f"  {len(starters)} starters ranked")
    log("  draft order (points above the last starter at that position):")
    for p in starters[:12]:
        log(f"    {p['our_pick']:>3}. {p['name'][:22]:<23}{p['pos']:<4}"
            f"+{p['edge']:>5.0f}   ADP "
            + (f"{p['adp']:.1f}" if p['adp'] else "--"))
    payload = {"players": starters, "meta": meta, "season": a.season,
               "replacement": REPLACEMENT}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.with_suffix(".json").write_text(
        json.dumps(payload, indent=1, default=str), encoding="utf-8")
    log(f"  wrote {a.out.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
