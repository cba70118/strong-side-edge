"""team_profile.py - compose and append the running narrative log per club.

THE RULE THIS MODULE OBEYS. Every sentence is built from a number in the
warehouse or quoted from an attributed source. Nothing is characterized that
cannot be pointed at. A season-long log of confident prose that turns out to be
invented is worse than no log at all, because it reads exactly like the real
thing and gets trusted the same way.

So the vocabulary is bounded: descriptions like "explosive" or "grinding" are
chosen by threshold against the league distribution of that same season, not by
impression, and the thresholds are in the code where they can be argued with.

Entries accumulate. `seed` writes the week-0 preseason read; `week` appends a
dated entry once results exist; `note` lets a human add their own, attributed.
Re-running a command rewrites its own (team, season, week, kind) row and leaves
every earlier week untouched, so the log is a record rather than a snapshot.

Usage:
    py -3 scripts/team_profile.py seed --season 2026
    py -3 scripts/team_profile.py week --season 2026 --week 3
    py -3 scripts/team_profile.py note --team SEA --season 2026 --week 3 \\
        --headline "..." --body "..."
    py -3 scripts/team_profile.py show --team SEA
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
SQL_FILE = ROOT / "sql" / "09_team_log.sql"


def log(m: str = "") -> None:
    print(m, flush=True)


def pct_rank(value, pool) -> float:
    """Where a value sits in its own season's distribution, 0-1."""
    pool = [x for x in pool if x is not None]
    if not pool or value is None:
        return 0.5
    return sum(1 for x in pool if x < value) / len(pool)


def band(p: float, words) -> str:
    """Pick a word by percentile. `words` runs worst-to-best in five steps."""
    idx = min(int(p * len(words)), len(words) - 1)
    return words[idx]


# Vocabulary is fixed and threshold-driven so the same numbers always produce
# the same characterization, in any week, for any club.
OFF_WORDS = ["one of the worst offenses in the league", "a poor offense",
             "a middling offense", "a good offense", "an elite offense"]
DEF_WORDS = ["one of the worst defenses in the league", "a poor defense",
             "a middling defense", "a good defense", "an elite defense"]


def fetch_season(con, season: int):
    # Play-weighted, not a mean of per-game rates: some games contain as few
    # as two neutral-down plays and equal weighting let those dominate. The
    # headline strength numbers are then overwritten below with the
    # opponent-adjusted rating, which is what the rest of the system uses.
    rows = con.execute("""
        SELECT team,
               sum(off_epa_play_neutral * off_plays_neutral)
                 / nullif(sum(off_plays_neutral), 0) oepa,
               sum(def_epa_play_neutral * def_plays_neutral)
                 / nullif(sum(def_plays_neutral), 0) depa,
               sum(off_success * off_plays) / nullif(sum(off_plays), 0) succ,
               sum(off_proe_neutral * off_plays_neutral)
                 / nullif(sum(off_plays_neutral), 0) proe,
               avg(off_sec_per_play_neutral) pace,
               sum(off_explosive_rate * off_plays)
                 / nullif(sum(off_plays), 0) expl,
               sum(def_explosive_rate * def_plays)
                 / nullif(sum(def_plays), 0) dexpl,
               avg(points_for) pf, avg(points_against) pa,
               sum(CASE WHEN margin > 0 THEN 1 ELSE 0 END) w,
               sum(CASE WHEN margin < 0 THEN 1 ELSE 0 END) l,
               sum(CASE WHEN margin = 0 THEN 1 ELSE 0 END) t,
               count(*) g
        FROM mart.team_game WHERE season = ? AND game_type = 'REG'
        GROUP BY 1
    """, [season]).fetchall()
    cols = ["oepa", "depa", "succ", "proe", "pace", "expl", "dexpl",
            "pf", "pa", "w", "l", "t", "g"]
    out = {r[0]: dict(zip(cols, r[1:])) for r in rows}
    mx = con.execute("SELECT max(week) FROM mart.team_rating WHERE season = ?",
                     [season]).fetchone()[0]
    for team, o, d in con.execute("""
        SELECT team, off_rating, def_rating FROM mart.team_rating
        WHERE season = ? AND week = ?
    """, [season, mx]).fetchall():
        if team in out:
            out[team].update(oepa=o, depa=d)
    return out


def fetch_split(con, season: int):
    """First half of the season against the second, as average point margin.

    A season average hides a club that started 1-5 and finished 7-1. Margin is
    used rather than neutral-down EPA because that rate is measured over a play
    count which itself collapses in blowouts - averaging it by game put Arizona
    at +0.170 through week nine of a 3-14 season. Margin has no such sample
    problem and needs no explaining to a reader.
    """
    rows = con.execute("""
        SELECT team,
               avg(CASE WHEN week <= 9 THEN margin END) AS h1,
               avg(CASE WHEN week > 9  THEN margin END) AS h2
        FROM mart.team_game WHERE season = ? AND game_type = 'REG'
        GROUP BY 1
    """, [season]).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def compose_preseason(team, s, split, ctx, prior, takes, pools, measured):
    """The week-0 read: what we measured, what the market now says, the gap."""
    o_p = pct_rank(s["oepa"], pools["oepa"])
    d_p = pct_rank(-(s["depa"] or 0), [-x for x in pools["depa"]])
    rec = f'{s["w"]}-{s["l"]}' + (f'-{s["t"]}' if s["t"] else "")

    bits = [f'In {measured} {team} went {rec} with '
            f'{band(o_p, OFF_WORDS)} ({s["oepa"]:+.3f} opponent-adjusted EPA '
            f'per play) and {band(d_p, DEF_WORDS)} ({s["depa"]:+.3f}).']

    # Style, stated only when it is genuinely unusual for that season.
    style = []
    pr = pct_rank(s["proe"], pools["proe"])
    pc = pct_rank(s["pace"], pools["pace"])
    if pr > 0.85:
        style.append("threw well above expectation for the situation")
    elif pr < 0.15:
        style.append("ran well above expectation for the situation")
    if pc < 0.15:
        style.append("played fast")
    elif pc > 0.85:
        style.append("played slow")
    ex = pct_rank(s["expl"], pools["expl"])
    if ex > 0.85:
        style.append("generated explosive plays at a top-five rate")
    elif ex < 0.15:
        style.append("rarely generated an explosive play")
    if style:
        bits.append("They " + ", ".join(style) + ".")

    h1, h2 = split.get(team, (None, None))
    if h1 is not None and h2 is not None:
        d = h2 - h1
        if abs(d) >= 4.0:
            bits.append(
                f'The season was not flat: average margin ran {h1:+.1f} points '
                f'a game through week nine and {h2:+.1f} after, so they '
                f'{"improved as it went on" if d > 0 else "fell away"}.')

    if prior:
        wt = prior.get("win_total")
        move = (prior.get("market_rating") or 0) - (prior.get("rating_2025") or 0)
        direction = ("higher than" if move > 0.02 else
                     "lower than" if move < -0.02 else "in line with")
        bits.append(
            f'For 2026 the market sets the win total at {wt:g}, which implies a '
            f'team rating {direction} what we measured '
            f'({move:+.3f} on the same scale).')
        if abs(move) > 0.08:
            bits.append(
                'That is one of the larger disagreements on the board, and it '
                'is the market pricing something our ratings cannot see - they '
                'end at the final whistle of last season.')

    if ctx:
        changes = []
        if ctx.get("coach_is_new"):
            changes.append(f'a new head coach in {ctx["head_coach"]}')
        if ctx.get("qb_is_new"):
            changes.append(f'{ctx["starting_qb"]} at quarterback')
        if changes:
            bits.append("They open the season with " + " and ".join(changes) + ".")
        if ctx.get("context_note"):
            bits.append(f'Preview note: {ctx["context_note"]}.')

    def clip(s, n=190):
        """Truncate on a word boundary - a quote cut mid-word reads as a bug."""
        s = s.strip()
        if len(s) <= n:
            return s
        cut = s[:n].rsplit(" ", 1)[0].rstrip(".,;:")
        return cut + "..."

    if takes:
        t0 = takes[0]
        sel = " ".join(str(x) for x in (t0.get("selection"), t0.get("line"))
                       if x is not None)
        bits.append(
            f'Move The Line\'s recorded position is a {t0["stance"]} on '
            f'{t0["market"]} {sel}'
            f'{" at " + t0["price"] if t0.get("price") else ""} - '
            f'"{clip(t0["thesis"])}" That is their view, not ours, and it '
            f'prices nothing here.')

    off_w, def_w = band(o_p, OFF_WORDS), band(d_p, DEF_WORDS)
    if off_w == def_w:
        head = f'{off_w.capitalize()} on both sides of the ball in {measured}'
    else:
        head = f'{off_w.capitalize()}, {def_w} in {measured}'
    return head[0].upper() + head[1:], " ".join(bits)


def cmd_seed(con, a) -> int:
    measured = a.measured or (a.season - 1)
    s_all = fetch_season(con, measured)
    if not s_all:
        log(f"  no completed games for {measured}; nothing to seed")
        return 1
    split = fetch_split(con, measured)
    pools = {k: [v[k] for v in s_all.values() if v[k] is not None]
             for k in ("oepa", "depa", "proe", "pace", "expl")}

    prior = {r[0]: dict(zip(["win_total", "market_rating", "rating_2025"], r[1:]))
             for r in con.execute("""
                 SELECT team, win_total, market_rating, rating_2025
                 FROM mart.preseason_prior""").fetchall()}
    ctx = {}
    try:
        ctx = {r[0]: dict(zip(
            ["head_coach", "coach_is_new", "starting_qb", "qb_is_new",
             "context_note"], r[1:]))
            for r in con.execute("""
                SELECT team, head_coach, coach_is_new, starting_qb, qb_is_new,
                       context_note FROM ref.team_context WHERE season = ?
            """, [a.season]).fetchall()}
    except Exception as exc:
        log(f"  WARN team_context unavailable: {exc}")
    takes = {}
    try:
        for r in con.execute("""
            SELECT team, market, selection, line, price, stance, thesis
            FROM ref.analyst_take WHERE team IS NOT NULL
            ORDER BY conviction DESC, take_id
        """).fetchall():
            takes.setdefault(r[0], []).append(dict(zip(
                ["market", "selection", "line", "price", "stance", "thesis"],
                r[1:])))
    except Exception:
        pass

    n = 0
    for team, s in sorted(s_all.items()):
        head, body = compose_preseason(
            team, s, split, ctx.get(team), prior.get(team),
            takes.get(team, []), pools, measured)
        metrics = json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                              for k, v in s.items()})
        con.execute("""
            INSERT OR REPLACE INTO ref.team_log
            (team, season, week, kind, as_of, headline, body, metrics, source)
            VALUES (?, ?, 0, 'preseason', ?, ?, ?, ?, 'generated')
        """, [team, a.season, date.today(), head, body, metrics])
        n += 1
    log(f"  seeded {n} preseason entries for {a.season} "
        f"(measured on {measured})")
    return 0


def cmd_week(con, a) -> int:
    """Append a dated entry once the week has been played.

    Deliberately refuses to write anything when no result exists - an empty
    week would otherwise produce a confident paragraph about nothing, which is
    the failure mode this whole module is built to avoid.
    """
    rows = con.execute("""
        SELECT team, opponent, is_home, points_for, points_against, margin,
               off_epa_play_neutral, def_epa_play_neutral, spread_line_team
        FROM mart.team_game
        WHERE season = ? AND week = ? AND points_for IS NOT NULL
    """, [a.season, a.week]).fetchall()
    if not rows:
        log(f"  no completed games in {a.season} week {a.week}; nothing to log")
        return 1
    n = 0
    for (team, opp, home, pf, pa, margin, oepa, depa, spread) in rows:
        res = "beat" if margin > 0 else ("lost to" if margin < 0 else "tied")
        where = "at home to" if home else "away at"
        ats = ""
        if spread is not None:
            cover = margin + spread
            ats = (f' They {"covered" if cover > 0 else "did not cover"} as a '
                   f'{abs(spread):g}-point {"favorite" if spread < 0 else "underdog"}.')
        head = f'Week {a.week}: {res} {opp} {pf:.0f}-{pa:.0f}'
        body = (f'{team} {res} {opp} {where.split()[0]} {pf:.0f}-{pa:.0f} in '
                f'week {a.week}.{ats} They generated {oepa:+.3f} EPA per play '
                f'on neutral downs and allowed {depa:+.3f}.')
        con.execute("""
            INSERT OR REPLACE INTO ref.team_log
            (team, season, week, kind, as_of, headline, body, metrics, source)
            VALUES (?, ?, ?, 'weekly', ?, ?, ?, ?, 'generated')
        """, [team, a.season, a.week, date.today(), head, body,
              json.dumps({"pf": pf, "pa": pa, "margin": margin,
                          "oepa": round(oepa or 0, 4),
                          "depa": round(depa or 0, 4)})])
        n += 1
    log(f"  logged {n} week-{a.week} entries")
    return 0


def cmd_note(con, a) -> int:
    if not (a.team and a.headline and a.body):
        log("  --team, --headline and --body are all required")
        return 1
    con.execute("""
        INSERT OR REPLACE INTO ref.team_log
        (team, season, week, kind, as_of, headline, body, metrics, source)
        VALUES (?, ?, ?, 'manual', ?, ?, ?, NULL, 'user')
    """, [a.team, a.season, a.week, date.today(), a.headline, a.body])
    log(f"  noted {a.team} {a.season} week {a.week}")
    return 0


def cmd_show(con, a) -> int:
    rows = con.execute("""
        SELECT week, kind, as_of, headline, body FROM ref.team_log
        WHERE team = ? AND season = ? ORDER BY week, kind
    """, [a.team, a.season]).fetchall()
    if not rows:
        log(f"  no log entries for {a.team} {a.season}")
        return 1
    for wk, kind, as_of, head, body in rows:
        log(f"\n  [{'preseason' if wk == 0 else 'week ' + str(wk)}] "
            f"{kind} - {as_of}")
        log(f"  {head}")
        for i in range(0, len(body), 96):
            log(f"    {body[i:i + 96]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["seed", "week", "note", "show"])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--measured", type=int, default=None)
    ap.add_argument("--week", type=int, default=0)
    ap.add_argument("--team")
    ap.add_argument("--headline")
    ap.add_argument("--body")
    a = ap.parse_args()

    con = duckdb.connect(str(a.db), read_only=(a.cmd == "show"))
    try:
        if a.cmd != "show":
            con.execute(SQL_FILE.read_text(encoding="utf-8"))
        return {"seed": cmd_seed, "week": cmd_week, "note": cmd_note,
                "show": cmd_show}[a.cmd](con, a)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
