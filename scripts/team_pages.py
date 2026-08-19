"""team_pages.py - one page per team, built to accumulate across the season.

WHY A THIRD SURFACE. The weekly brief is a narrative about a slate; the
dashboard is the state of the board right now. Neither answers "what is this
team, and what has happened to it so far" - a question that only gets more
useful as the season runs. mart.team_rating is already point-in-time and
weekly, so the trend is a query rather than a model; ref.team_log is
append-only, so the narrative accumulates without anything being rewritten.

It is NOT a standalone product. `build_pages()` returns the nav and panes so
slate_report.py can mount the whole thing as the brief's Teams tab, next to the
betting data, sharing one copy of the fonts and logo sprites.

TWO THINGS THIS GOT WRONG FIRST TIME, both worth keeping in mind:

  1. The QB health flag rendered as a bare chip reading "ankle rehab" with no
     subject attached, which reads as stray player data on a team page. Status
     without its subject is not context. Coach and quarterback are now always
     named, and the status hangs off the name.
  2. The net rating was drawn as an unlabeled sparkline - no scale, no zero,
     no value - so the single most important number on the page could not
     actually be read. A trend line without a value is a texture.

Usage:
    py -3 scripts/team_pages.py -o team_pages.html
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import team_viz as TV                                    # noqa: E402
import design_system as DS                               # noqa: E402

try:
    from logo_assets import LOGOS, COLORS
except Exception:
    LOGOS, COLORS = {}, {}
try:
    from font_assets import FONT_CSS
except Exception:
    FONT_CSS = ""

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
SEASON = 2026
HIST = 2025          # the last completed season

# Points of margin per unit of rating, measured in build_mart.py --evaluate.
# RATINGS ARE DISPLAYED IN POINTS, not in raw rating units. A blended rating of
# +0.1529 is seven glyphs of false precision that mean nothing at a glance;
# +6.0 is three glyphs in the unit a bettor already thinks in. The raw value
# stays available underneath for anyone who wants it.
PTS_PER_RATING = 39.18

# (label, team_game column, weight column, direction, stability r)
#
# `r` is that metric's measured year-over-year correlation from stability.py.
# Printing it beside the value is the honest way to show seven numbers at once:
# pass rate over expected carries at 0.46 and defensive EPA at 0.21, and
# drawing them the same size without saying so implies a parity the data
# refuses.
#
# `direction` is 'high', 'low', or 'style'. A style metric has no better end -
# a pass-leaning offense is not a good one - so it gets a value and no
# percentile bar. A bar would assert a direction that does not exist.
METRICS = [
    ("off EPA/play", "off_epa_play_neutral", "off_plays_neutral", "high", 0.366),
    ("off success rate", "off_success", "off_plays", "high", 0.404),
    ("explosive rate", "off_explosive_rate", "off_plays", "high", 0.281),
    ("def EPA/play", "def_epa_play_neutral", "def_plays_neutral", "low", 0.206),
    ("def success rate", "def_success", "def_plays", "low", 0.275),
    ("pass rate over exp", "off_proe_neutral", "off_plays_neutral", "style", 0.459),
    ("sec per play", "off_sec_per_play_neutral", "off_plays_neutral", "style", 0.397),
]


def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def log(m=""):
    print(m, flush=True)


def logo_css() -> str:
    """One background-image rule per team, referenced by class.

    Inlining each logo at every use site is what pushed an earlier artifact to
    1.1 MB and a 502 on deploy. One copy, many references.
    """
    return "".join(
        f'.lg-{k}{{background-image:url({v})}}' for k, v in sorted(LOGOS.items()))


def team_color(t):
    """(primary, secondary, text) for a team, text picked for contrast.

    logo_assets.COLORS holds a LIST per team, not a string. Interpolating it
    straight into a style attribute emitted `background:['#003594', '#FFD100']`
    - invalid CSS that every browser silently drops, so the team color never
    rendered at all. It looked like a design that had not landed; it was a type
    error.

    Text is chosen by measured contrast, not assumed white. Two of the 32
    primaries are light enough that white fails 4.5:1 and ink wins; with the
    pick made per team, all 32 clear it.
    """
    c = COLORS.get(t) or []
    if isinstance(c, str):
        c = [c]
    primary = (c[0] if len(c) > 0 else "#222629")
    secondary = (c[1] if len(c) > 1 else primary)
    return primary, secondary, ("#FFFFFF" if _cr(primary, "#FFFFFF")
                                >= _cr(primary, "#0B0D10") else "#0B0D10")


def _lin(v):
    v = v / 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _rl(h):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _cr(a, b):
    x, y = sorted([_rl(a), _rl(b)], reverse=True)
    return (x + 0.05) / (y + 0.05)


def rank_map(rows, key, best_high=True):
    ordered = sorted([r for r in rows if r.get(key) is not None],
                     key=lambda r: r[key], reverse=best_high)
    return {r["team"]: i + 1 for i, r in enumerate(ordered)}


def fetch(con):
    d = {}
    teams = {}
    for r in con.execute("""
        SELECT p.team, p.win_total, p.market_rating, p.rating_2025,
               p.blended_rating, p.blended_off, p.blended_def,
               c.power_rank, c.tier, c.record_2025, c.head_coach,
               c.coach_is_new, c.starting_qb, c.qb_is_new, c.qb_health_flag,
               c.context_note, t.full_name, t.conference, t.division
        FROM mart.preseason_prior p
        LEFT JOIN ref.team_context c ON c.team = p.team AND c.season = ?
        LEFT JOIN ref.team t ON t.team_id = p.team
    """, [SEASON]).fetchall():
        k = ["team", "win_total", "market_rating", "rating_2025", "blended",
             "off", "def", "power_rank", "tier", "record_2025", "hc", "hc_new",
             "qb", "qb_new", "qb_flag", "note", "name", "conf", "div"]
        teams[r[0]] = dict(zip(k, r))
    d["teams"] = teams

    d["trend"] = {}
    for t, w, n in con.execute(
            "SELECT team, week, net_rating FROM mart.team_rating "
            "WHERE season = ? ORDER BY team, week", [HIST]).fetchall():
        d["trend"].setdefault(t, []).append((w, n))

    # end-of-season net rating per team per season - the long arc
    d["seasons"] = {}
    for t, s, n in con.execute("""
        SELECT team, season, net_rating FROM (
          SELECT team, season, net_rating,
                 row_number() OVER (PARTITION BY team, season
                                    ORDER BY week DESC) rn
          FROM mart.team_rating) WHERE rn = 1 ORDER BY team, season
    """).fetchall():
        d["seasons"].setdefault(t, []).append((s, n))

    d["logs"] = {}
    for r in con.execute("""
        SELECT team, season, week, kind, cast(as_of AS VARCHAR), headline, body
        FROM ref.team_log ORDER BY team, season, week
    """).fetchall():
        d["logs"].setdefault(r[0], []).append(dict(zip(
            ["team", "season", "week", "kind", "as_of", "headline", "body"], r)))

    d["games"] = {}
    d["sched"] = {}
    for gid, wk, home, away, day, tm, sp, tot in con.execute("""
        SELECT game_id, week, home_team, away_team, gameday, gametime,
               spread_line, total_line
        FROM raw.games WHERE season = ? AND game_type = 'REG'
        ORDER BY week
    """, [SEASON]).fetchall():
        for me, opp, at, s in ((home, away, "vs", sp),
                               (away, home, "at",
                                -sp if sp is not None else None)):
            row = {"week": wk, "opp": opp, "at": at, "day": day, "time": tm,
                   "spread": s, "total": tot}
            d["sched"].setdefault(me, []).append(row)
            if wk == 1:
                d["games"][me] = row

    # Charting tendencies, ranked WITHIN the season. Levels are not comparable
    # across seasons (the league man rate swings 29->49->31 on identical
    # charted volume), but a rank inside one season is sound.
    d["chart"] = {}
    try:
        rows = con.execute("""
            WITH s AS (
                SELECT team,
                       avg(play_action_rate) AS pa, avg(screen_rate) AS scr,
                       avg(rpo_rate) AS rpo, avg(motion_rate) AS mot,
                       avg(no_huddle_rate) AS nh, avg(shotgun_rate) AS sg,
                       avg(blitz_sent) AS blz, avg(man_rate) AS man,
                       sum(plays) AS n
                FROM mart.team_charting
                WHERE season = (SELECT max(season) FROM mart.team_charting)
                GROUP BY team
            )
            SELECT team, pa, scr, rpo, mot, nh, sg, blz, man, n,
                   rank() OVER (ORDER BY pa DESC)  AS r_pa,
                   rank() OVER (ORDER BY scr DESC) AS r_scr,
                   rank() OVER (ORDER BY rpo DESC) AS r_rpo,
                   rank() OVER (ORDER BY mot DESC) AS r_mot,
                   rank() OVER (ORDER BY nh DESC)  AS r_nh,
                   rank() OVER (ORDER BY sg DESC)  AS r_sg,
                   rank() OVER (ORDER BY blz DESC) AS r_blz,
                   rank() OVER (ORDER BY man DESC) AS r_man
            FROM s
        """).fetchall()
        keys = ["team", "pa", "scr", "rpo", "mot", "nh", "sg", "blz", "man",
                "n", "r_pa", "r_scr", "r_rpo", "r_mot", "r_nh", "r_sg",
                "r_blz", "r_man"]
        for r in rows:
            d["chart"][r[0]] = dict(zip(keys, r))
        d["chart_season"] = con.execute(
            "SELECT max(season) FROM mart.team_charting").fetchone()[0]
    except Exception:
        d["chart_season"] = None

    d["players"] = {}
    # Charting is joined on gsis_id from the most recent charted season. It is
    # last season's usage sitting beside a projection, so the header says so.
    for r in con.execute("""
        WITH ch AS (
            SELECT gsis_id,
                   sum(targets)                                  AS n,
                   sum(catchable_rate * targets)
                     / nullif(sum(targets), 0)                   AS catchable,
                   sum(tgt_vs_man)                               AS man,
                   sum(tgt_vs_zone)                              AS zone
            FROM mart.player_charting
            WHERE season = (SELECT max(season) FROM mart.player_charting)
            GROUP BY gsis_id
        )
        SELECT p.team, p.display_name, p.position, p.proj_targets,
               p.proj_rec_yards, p.proj_carries, p.proj_rush_yards,
               ch.catchable,
               ch.man / nullif(ch.man + ch.zone, 0) AS man_share,
               ch.man + ch.zone                     AS charted
        FROM mart.player_projection_pre p
        LEFT JOIN ch ON ch.gsis_id = p.gsis_id
        WHERE p.display_name IS NOT NULL
        ORDER BY coalesce(p.proj_targets,0) + coalesce(p.proj_carries,0) DESC
    """).fetchall():
        d["players"].setdefault(r[0], []).append(dict(zip(
            ["team", "name", "pos", "tgt", "rec_yds", "car", "rush_yds",
             "catchable", "man_share", "charted"], r)))

    # INJURY REPORT AS CONTEXT, NEVER AS A SIGNAL. h-0010 measured the
    # Out-count differential at partial +0.0425 against the closing spread on
    # 1,609 games - under the 0.05 bar and 1.7 SE from zero - while the market
    # visibly prices it (corr with the spread +0.157). So it belongs on the
    # page as description and in the projection as a correctness filter, and
    # nowhere near an edge claim.
    #
    # TRAP: season_type is NULL on 83% of rows. Filtering on it drops five
    # sixths of the table. game_type is the populated one, and season/week are
    # DOUBLE so they need casting to join raw.games.
    d["inj"] = {}
    d["inj_week"] = None
    row = con.execute("""
        SELECT cast(max(season) AS INT), cast(max(week) AS INT)
        FROM raw.injuries WHERE game_type = 'REG'
          AND cast(season AS INT) = ?
    """, [SEASON]).fetchone()
    if row and row[0] is not None:
        d["inj_week"] = (row[0], row[1])
        for team, status, name, pos, inj in con.execute("""
            SELECT team, report_status, full_name, position,
                   coalesce(report_primary_injury, practice_primary_injury)
            FROM raw.injuries
            WHERE game_type = 'REG' AND cast(season AS INT) = ?
              AND cast(week AS INT) = ? AND report_status IS NOT NULL
            ORDER BY team,
                     CASE report_status WHEN 'Out' THEN 0 WHEN 'Doubtful' THEN 1
                          WHEN 'Questionable' THEN 2 ELSE 3 END, full_name
        """, [row[0], row[1]]).fetchall():
            d["inj"].setdefault(team, []).append(
                {"status": status, "name": name, "pos": pos, "inj": inj})

    d["drives"] = {}
    for t, n, td, fg, dn, to in con.execute("""
        SELECT d.posteam, count(*),
               100.0*count(*) FILTER (WHERE d.outcome='TD')/count(*),
               100.0*count(*) FILTER (WHERE d.outcome='FG')/count(*),
               100.0*count(*) FILTER (WHERE d.outcome='DOWNS')/count(*),
               100.0*count(*) FILTER (WHERE d.outcome='TURNOVER')/count(*)
        FROM mart.drive d JOIN raw.games g USING (game_id)
        WHERE g.season = ? AND d.outcome <> 'OTHER'
        GROUP BY 1
    """, [HIST]).fetchall():
        d["drives"][t] = {"n": n, "td": td, "fg": fg, "dn": dn, "to": to}

    # play-weighted season value per team per metric, matching stability.py -
    # an unweighted mean of per-game rates lets a two-snap game count as much
    # as a fifty-snap one, which is the error that put a 3-14 team at +0.170.
    d["metrics"] = {}
    for label, col, wcol, _dir, _r in METRICS:
        d["metrics"][label] = dict(con.execute(f"""
            SELECT team, sum({col} * {wcol}) / nullif(sum({wcol}), 0)
            FROM mart.team_game
            WHERE season = ? AND game_type = 'REG' AND {col} IS NOT NULL
            GROUP BY 1
        """, [HIST]).fetchall())
    return d


def num(v, dp=2, sign=False):
    if v is None:
        return '<span class="na">--</span>'
    return (f"{{:+.{dp}f}}" if sign else f"{{:.{dp}f}}").format(v)


def pctc(v):
    """A charted rate, or a dash. Never a zero standing in for absent."""
    return f"{v * 100:.0f}%" if v else '<span class="na">--</span>'


def mz(share, n):
    """Man-coverage target share, suppressed under a usable sample.

    Under 20 charted targets the split is a handful of plays and would read as
    a tendency when it is a coin flip.
    """
    if share is None or not n or n < 20:
        return '<span class="na">--</span>'
    return f"{share * 100:.0f}%"



CHART_ROWS = [
    ("pa",  "r_pa",  "play action",   "share of dropbacks with play action"),
    ("sg",  "r_sg",  "shotgun",       "share of dropbacks from shotgun"),
    ("mot", "r_mot", "motion",        "share of plays with pre-snap motion"),
    ("rpo", "r_rpo", "RPO",           "run-pass option share of plays"),
    ("scr", "r_scr", "screens",       "share of dropbacks that are screens"),
    ("nh",  "r_nh",  "no huddle",     "share of plays run without a huddle"),
    ("blz", "r_blz", "blitzers sent", "average extra rushers this defense sends"),
    ("man", "r_man", "man coverage",  "share of charted dropbacks in man"),
]


def charting_block(c, season):
    """Coordinator tendencies as within-season ranks.

    A rank rather than a level because the league man rate swings twenty points
    between seasons on identical charted volume, which is a charting revision
    rather than a change in football. Ranks inside one season are sound.
    """
    if not c:
        return '<p class="empty">No charting data for this club.</p>'
    out = ""
    for key, rkey, label, why in CHART_ROWS:
        v, rk = c.get(key), c.get(rkey)
        if v is None or rk is None:
            continue
        # rank 1 is the most of a thing; the bar fills from the league bottom
        pct = round((33 - rk) / 32 * 100)
        val = f"{v:.2f}" if key == "blz" else f"{v * 100:.0f}%"
        out += (f'<div class="crow" title="{e(why)}">'
                f'<div class="cl">{e(label)}</div>'
                f'<div class="cb"><i style="width:{pct}%"></i></div>'
                f'<div class="cv">{val}</div>'
                f'<div class="cr">{rk}</div></div>')
    return (f'<div class="chart-note">Coordinator tendencies, {season}. '
            f'Bar is rank among 32, not a level. These are the most stable '
            f'numbers on the page (no huddle 0.91, motion 0.87 season to '
            f'season, against 0.55 for offensive EPA) because they carry '
            f'intent, not performance. They say what a team likes to do, not '
            f'how good it is.</div><div class="ctable">{out}</div>')



def sched_strip(rows, teams, ranks, pts_fn):
    """The season as a strength profile, one cell per week.

    rows are this team's games; teams carries every club's rating so the
    opponent can be valued; ranks gives the opponent's power-ranking place.
    Bars are scaled to the strongest opponent anyone faces, so a cell means the
    same thing on all 32 pages.
    """
    if not rows:
        return '<p class="empty">No schedule loaded.</p>'

    vals = [abs((teams.get(r["opp"], {}) or {}).get("blended") or 0.0)
            for r in rows]
    peak = max(vals) or 1.0
    by_week = {r["week"]: r for r in rows}
    last = max(by_week) if by_week else 18

    cells = ""
    for wk in range(1, last + 1):
        r = by_week.get(wk)
        if r is None:
            # The gap in the sequence IS the bye. Drawing it keeps week
            # numbers aligned with their position in the strip.
            cells += (f'<div class="wkc bye"><div class="wkn">{wk}</div>'
                      f'<div class="bar"></div><div class="opp">BYE</div>'
                      f'<div class="val">&nbsp;</div></div>')
            continue
        o = teams.get(r["opp"], {}) or {}
        raw = o.get("blended")
        v = raw or 0.0
        h = round(min(abs(v) / peak, 1.0) * 100)
        up = v >= 0
        rk = ranks["net"].get(r["opp"]) or "--"
        home = r["at"] == "vs"
        cells += (
            f'<div class="wkc{"" if raw is not None else " nod"}" '
            f'title="Week {wk}: {"vs" if home else "at"} {r["opp"]} '
            f'(rank {rk}), strength {pts_fn(raw)}">'
            f'<div class="wkn">{wk}</div>'
            f'<div class="bar"><i class="{"up" if up else "dn"}" '
            f'style="height:{h}%"></i></div>'
            f'<div class="opp">{"" if home else "@"}'
            f'<span class="lg lg-{r["opp"]}"></span>'
            f'<b>{r["opp"]}</b></div>'
            f'<div class="val">{pts_fn(raw)}</div></div>')

    played = [r for r in rows if (teams.get(r["opp"], {}) or {}).get("blended")
              is not None]
    avg = (sum((teams[r["opp"]]["blended"]) for r in played) / len(played)
           if played else None)
    hard = sum(1 for r in played if (teams[r["opp"]]["blended"] or 0) > 0)
    foot = (f'<div class="sfoot"><span><b>{pts_fn(avg)}</b> average opponent'
            f'</span><span><b>{hard}</b> of {len(played)} above average</span>'
            f'<span class="lgd"><i class="up"></i>tougher'
            f'<i class="dn"></i>easier</span></div>') if played else ""
    return f'<div class="strip">{cells}</div>{foot}'



def pts(v, flip=False):
    """A rating rendered in points above league average, signed, one decimal.

    `flip` for defense, which is stored so that NEGATIVE is good - flipping it
    here means a positive number is a good unit everywhere on the page, so the
    reader never has to hold a sign convention while looking at a picture.
    """
    if v is None:
        return '<span class="na">--</span>'
    return f"{(-v if flip else v) * PTS_PER_RATING:+.1f}"


def stat(label, value, sub=""):
    return (f'<div class="stat"><div class="lb">{e(label)}</div>'
            f'<div class="vl">{value}</div>'
            + (f'<div class="sb">{sub}</div>' if sub else "") + "</div>")


def metric_rows(t, d):
    """(label, value, percentile, rank, dp, r) for one team.

    Percentile is direction-corrected so a full bar always means 'best in the
    league'. Style metrics get None, which renders no bar at all.
    """
    out = []
    for label, _col, _w, direction, r in METRICS:
        vals = d["metrics"].get(label, {})
        v = vals.get(t)
        if v is None:
            continue
        others = [x for x in vals.values() if x is not None]
        n = len(others)
        if direction == "style":
            pct, rank = None, None
        else:
            worse = sum(1 for x in others
                        if (x < v if direction == "high" else x > v))
            pct = worse / (n - 1) if n > 1 else 0.5
            rank = sorted(others, reverse=(direction == "high")).index(v) + 1
        dp = 1 if "sec" in label else 3
        out.append((label, v, pct, rank, dp, r))
    return out


def render_team(t, d, ranks, open_=False):
    tm = d["teams"][t]
    primary, secondary, tcol = team_color(t)
    lg = f'<span class="lg lg-{e(t)}"></span>' if t in LOGOS else ""
    net_r = ranks["net"].get(t)

    delta = None
    if tm.get("market_rating") is not None and tm.get("rating_2025") is not None:
        delta = tm["market_rating"] - tm["rating_2025"]

    # CONTEXT, NOT LOOSE CHIPS. Both roles are always named and a status hangs
    # off the person it belongs to - "ankle rehab" on its own reads as stray
    # player data on a team page.
    hc = e(tm.get("hc") or "unknown")
    qb = e(tm.get("qb") or "unknown")
    hc_tag = '<em class="tag new">first year</em>' if tm.get("hc_new") else ""
    qb_tag = '<em class="tag new">new starter</em>' if tm.get("qb_new") else ""
    qb_st = (f'<em class="tag warn">{e(tm["qb_flag"])}</em>'
             if tm.get("qb_flag") else "")
    # THE NAMED DATA LAYER. Signage was picked for its shell, but the sketch
    # had dropped the head coach, the tier and the context note - the parts
    # that make the page worth reading rather than just looking at. Every
    # figure now carries its label and its rank.
    context = (
        '<dl class="roster">'
        f'<div><dt>Head coach</dt><dd>{hc}{hc_tag}</dd></div>'
        f'<div><dt>Starting quarterback</dt><dd>{qb}{qb_tag}{qb_st}</dd></div>'
        f'<div><dt>Win total</dt><dd class="fig">'
        f'{e(tm.get("win_total"))}</dd></div>'
        f'<div><dt>Power rank</dt><dd class="fig">'
        f'{e(tm.get("power_rank") or "--")}</dd></div>'
        '</dl>')

    game = d["games"].get(t)
    g = '<p class="empty">No Week 1 game found.</p>'
    if game:
        sp = game.get("spread")
        line = ("pick&rsquo;em" if sp is None or abs(sp) < 0.25 else
                (f"favored by {abs(sp):g}" if sp > 0
                 else f"getting {abs(sp):g}"))
        g = (f'<div class="mt">{e(game["at"])} <b>{e(game["opp"])}</b>'
             f'<span class="dt">{e(game["day"])} {e(game["time"])}</span></div>'
             f'<div class="sb">{line} &middot; total '
             f'{e(game.get("total")) if game.get("total") is not None else "--"}'
             f'</div>')

    prows = ""
    for p in (d["players"].get(t) or [])[:8]:
        if (p.get("tgt") or 0) + (p.get("car") or 0) < 1:
            continue
        prows += (f'<tr><td class="nm">{e(p["name"])}</td>'
                  f'<td class="ps">{e(p["pos"])}</td>'
                  f'<td class="n">{num(p.get("tgt"), 1)}</td>'
                  f'<td class="n">{num(p.get("rec_yds"), 0)}</td>'
                  f'<td class="n">{num(p.get("car"), 1)}</td>'
                  f'<td class="n">{num(p.get("rush_yds"), 0)}</td>'
                  f'<td class="n sep">{pctc(p.get("catchable"))}</td>'
                  f'<td class="n">{mz(p.get("man_share"), p.get("charted"))}</td>'
                  f'</tr>')
    ptable = (f'<table class="pl"><thead><tr><th>projected usage</th><th></th>'
              f'<th class="n">tgt</th><th class="n">rec yd</th>'
              f'<th class="n">car</th><th class="n">ru yd</th>'
              f'<th class="n sep" title="Share of 2025 targets the ball was '
              f'charted catchable. This tracks the quarterback\'s accuracy '
              f'more than the receiver\'s hands.">catch</th>'
              f'<th class="n" title="Share of his charted 2025 targets that '
              f'came against man coverage.">vs man</th></tr></thead>'
              f'<tbody>{prows}</tbody></table>'
              f'<div class="pnote">The last two columns are <b>2025 '
              f'regular-season charted usage</b>, not projections. Catch rate '
              f'falls with target depth (82% under 6 yards, 64% past 14), so '
              f'read it against the route tree rather than as hands. Drop rate '
              f'is deliberately absent: it measured 0.06 to 0.12 season over '
              f'season, which is noise.</div>') if prows else ""

    stable = sched_strip(d["sched"].get(t) or [], d["teams"],
                         ranks, pts)

    charting = charting_block(d["chart"].get(t),
                              d.get("chart_season") or "")

    dm = d["drives"].get(t)
    drive = (TV.drive_mix(dm["td"], dm["fg"], dm["dn"], dm["to"])
             if dm else '<p class="empty">No drive history.</p>')

    inj = d["inj"].get(t) or []
    if inj:
        wk = d["inj_week"]
        items = "".join(
            f'<li><span class="ist is-{i["status"][:1].lower()}">'
            f'{e(i["status"])}</span>'
            f'<span class="inm">{e(i["name"])}</span>'
            f'<span class="ipo">{e(i["pos"])}</span>'
            f'<span class="iin">{e(i["inj"] or "")}</span></li>' for i in inj)
        injury = (f'<div class="sb ipad">Official report, {SEASON} week '
                  f'{wk[1] if wk else "--"}.</div>'
                  f'<ul class="inj">{items}</ul>')
    elif d["inj_week"]:
        injury = ('<p class="empty ipad">No players listed on the latest '
                  'report.</p>')
    else:
        injury = ('<p class="empty ipad">The official report starts in game '
                  f'week. It is context only &mdash; a team&rsquo;s Out count '
                  f'carries a partial correlation of +0.04 against the closing '
                  f'spread, which the market already prices.</p>')

    entries = ""
    for L in d["logs"].get(t, []):
        wl = "preseason" if not L["week"] else f"week {L['week']}"
        entries += (f'<li><div class="when">{e(wl)}'
                    f'<span class="as">{e(L["as_of"])}</span></div>'
                    f'<div class="hd">{e(L["headline"])}</div>'
                    f'<p>{e(L["body"])}</p></li>')
    timeline = (f'<ol class="log">{entries}</ol>' if entries else
                '<p class="empty">No entries yet. The log fills as the season '
                'runs.</p>')

    return f'''<section class="team" id="t-{e(t)}" role="tabpanel"
 aria-labelledby="nav-{e(t)}"{"" if open_ else " hidden"}>
 <header class="hero" style="background:{primary};color:{tcol}">
  {lg}
  <div class="hid">
   <h2>{e(tm.get("name") or t)}</h2>
   <div class="hsub">{e(" ".join(x for x in [tm.get("conf"), tm.get("div")] if x))}
    &middot; {e(tm.get("record_2025") or "")} in {HIST}
    &middot; {e(tm.get("tier") or "")}</div>
  </div>
  <div class="hrk"><span class="n">{e(net_r or "--")}</span>
   <span class="l">of 32</span></div>
 </header>
 <div class="accent" style="background:{secondary}"></div>
 {context}
 <div class="grid">
  {stat("Team strength", pts(tm.get("blended")) + '<em>pts</em>', f"rank {net_r or '--'} of 32 &middot; rating {num(tm.get('blended'), 3, True)}")}
  {stat("Offense", pts(tm.get("off")) + '<em>pts</em>', f"rank {ranks['off'].get(t) or '--'} of 32")}
  {stat("Defense", pts(tm.get("def"), flip=True) + '<em>pts</em>', f"rank {ranks['def'].get(t) or '--'} of 32 &middot; higher is better")}
  {stat(f"{HIST} strength", pts(tm.get("rating_2025")) + '<em>pts</em>', "from play only, before the market")}
  {stat("Market re-rate", pts(delta) + '<em>pts</em>', f"market minus our {HIST} read")}
 </div>
 <div class="lb sec">Form</div>
 <div class="two">
  <div class="card">
   <div class="lb">Net rating through {HIST}</div>
   {TV.net_trend(d["trend"].get(t, []))}
  </div>
  <div class="card">
   <div class="lb">Where the strength sits</div>
   {TV.off_def(tm.get("off"), tm.get("def"), ranks["off"].get(t), ranks["def"].get(t))}
  </div>
 </div>
 <div class="lb sec">Profile</div>
 <div class="two">
  <div class="card">
   <div class="lb">{HIST} profile &middot; percentile in the league</div>
   {TV.metric_strip(metric_rows(t, d))}
   <div class="sb">r is the metric&rsquo;s own year-over-year stability.
    Style metrics have no better end, so they carry no bar.</div>
  </div>
  <div class="card">
   <div class="lb">{HIST} drive outcomes</div>
   {drive}
  </div>
 </div>
 <div class="two">
  <div class="card">
   <div class="lb">Net rating by season</div>
   {TV.season_line(d["seasons"].get(t, []))}
  </div>
  <div class="card">
   <div class="lb">Week 1</div>
   {g}
  </div>
 </div>
 <div class="lb sec">Injury report</div>
 {injury}
 <div class="note">{e(tm.get("note") or "")}</div>
 <div class="lb sec">People</div>
 {ptable}
 <div class="lb sec">Tendencies</div>
 {charting}
 <div class="lb sec">{SEASON} schedule</div>
 {stable}
 <div class="lb sec">Season log</div>
 {timeline}
</section>'''


LEDGER_CSS = """
/* Signage, applied to the ledger. Structure is rule-and-block; the only large
   field of color is the team's own, and that is data rather than decoration. */
.ledger{display:grid;grid-template-columns:196px minmax(0,1fr);gap:20px;
  align-items:start}
.ledger nav.idx{position:sticky;top:44px;max-height:calc(100vh - 58px);
  overflow-y:auto;background:var(--panel);border:1px solid var(--line)}
.ledger nav.idx .ih{padding:10px 12px 8px;border-bottom:2px solid var(--rule);
  font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink3)}
.ledger nav.idx button{display:flex;align-items:center;gap:8px;width:100%;
  background:none;border:0;border-bottom:1px solid var(--line);cursor:pointer;
  padding:6px 10px;font:inherit;font-size:12px;color:var(--ink2);
  text-align:left}
.ledger nav.idx button:last-child{border-bottom:0}
.ledger nav.idx button:hover{background:var(--row);color:var(--ink)}
.ledger nav.idx button[aria-selected="true"]{background:var(--ink);
  color:var(--panel)}
.ledger nav.idx button[aria-selected="true"] .rn,
.ledger nav.idx button[aria-selected="true"] .rt{color:var(--panel);
  opacity:.72}
.ledger nav.idx button:focus-visible{outline:2px solid var(--accent);
  outline-offset:-2px}
.ledger nav.idx .rn{font-family:'IBM Plex Sans Condensed',sans-serif;
  font-weight:700;font-size:11px;color:var(--ink3);width:17px;text-align:right;
  font-variant-numeric:tabular-nums}
.ledger nav.idx .rt{margin-left:auto;font-family:'IBM Plex Sans Condensed',
  sans-serif;font-weight:600;font-size:11px;color:var(--ink3);
  font-variant-numeric:tabular-nums}
.ledger .lg{display:inline-block;width:20px;height:20px;flex:0 0 20px;
  background-size:contain;background-repeat:no-repeat;
  background-position:center}
.ledger .team{background:var(--panel);border:1px solid var(--line)}
.ledger .team[hidden]{display:none}

/* HERO. The team color is the page's one large field. Text color is chosen by
   measured contrast per team, not assumed white - two of the 32 primaries need
   ink instead. */
.ledger .hero{display:flex;align-items:flex-end;gap:16px;padding:20px 20px 16px}
.ledger .hero .lg{width:52px;height:52px;flex:0 0 52px;
  filter:drop-shadow(0 1px 3px rgba(0,0,0,.30))}
.ledger .hero h2{margin:0;font-size:40px;font-weight:700;line-height:.92;
  letter-spacing:-.02em;text-transform:uppercase;text-wrap:balance}
.ledger .hero .hsub{font-size:10.5px;letter-spacing:.16em;
  text-transform:uppercase;opacity:.86;margin-top:7px}
.ledger .hero .hrk{margin-left:auto;text-align:right}
.ledger .hero .hrk .n{display:block;font-size:56px;font-weight:700;
  line-height:.82;font-variant-numeric:tabular-nums}
.ledger .hero .hrk .l{font-size:9.5px;letter-spacing:.16em;
  text-transform:uppercase;opacity:.8}
.ledger .accent{height:6px}

/* NAMED DATA LAYER. Labelled, not inferred. */
.ledger dl.roster{display:grid;
  grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1px;margin:0;
  background:var(--line);border-bottom:3px solid var(--rule)}
.ledger dl.roster div{background:var(--panel);padding:11px 16px}
.ledger dl.roster dd{margin:2px 0 0;font-size:14px;display:flex;
  align-items:center;gap:7px;flex-wrap:wrap;line-height:1.35}
.ledger dl.roster dd.fig{font-family:'IBM Plex Sans Condensed',sans-serif;
  font-weight:700;font-size:22px;line-height:1.05;
  font-variant-numeric:tabular-nums}

.ledger .grid{display:grid;
  grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:1px;
  background:var(--line);border-bottom:3px solid var(--rule)}
.ledger .stat{background:var(--panel);padding:11px 16px}
.ledger .vl{font-family:'IBM Plex Sans Condensed',sans-serif;font-weight:700;
  font-size:26px;line-height:1.08;margin-top:3px;
  font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.ledger .sb{font-size:10.5px;color:var(--ink3);margin-top:3px;line-height:1.45}
.ledger .na{color:var(--ink3)}

.ledger .two{display:grid;
  grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1px;
  background:var(--line);border-bottom:1px solid var(--line)}
.ledger .card{background:var(--panel);padding:13px 16px 15px}
.ledger .mt{font-family:'IBM Plex Sans Condensed',sans-serif;font-weight:700;
  font-size:22px;margin-top:5px;text-transform:uppercase}
.ledger .dt{color:var(--ink3);font-size:11px;margin-left:9px;font-weight:400;
  text-transform:none;letter-spacing:0}
.ledger .note{padding:14px 16px;color:var(--ink2);font-size:13px;
  max-width:80ch;border-bottom:3px solid var(--rule)}
.ledger table.pl th:first-child,.ledger table.pl td:first-child{
  padding-left:16px}
.ledger table.pl td.ps{color:var(--ink3);font-size:10.5px;
  letter-spacing:.06em;text-transform:uppercase}
.ledger table.pl td.nm{font-weight:600}
/* Fixed layout so the numeric columns hold position instead of floating
   beside a stretched opponent name. */
.ledger table.pl .sep{border-left:1px solid var(--line)}
.ledger .pnote{padding:6px 16px 0;font-size:10.5px;color:var(--ink3);
  max-width:72ch;line-height:1.5}
.ledger .chart-note{padding:8px 16px 2px;font-size:11px;color:var(--ink3);
  max-width:74ch;line-height:1.5}
.ledger .ctable{padding:6px 16px 2px;display:flex;flex-direction:column;gap:4px}
.ledger .crow{display:grid;grid-template-columns:104px minmax(0,1fr) 46px 34px;
  align-items:center;gap:9px;font-size:11.5px}
.ledger .crow .cl{color:var(--ink2)}
.ledger .crow .cb{height:9px;background:var(--row);border:1px solid var(--line)}
.ledger .crow .cb i{display:block;height:100%;background:var(--accent)}
.ledger .crow .cv{text-align:right;font-weight:700;
  font-variant-numeric:tabular-nums}
.ledger .crow .cr{text-align:right;color:var(--ink3);font-size:10px;
  font-variant-numeric:tabular-nums}

/* Season strength strip. Grid, not a table: there is no header row to drift
   out of alignment, and grid tracks cannot desynchronize the way table columns
   did across 32 pages. */
.ledger .strip{display:grid;
  grid-template-columns:repeat(auto-fill,minmax(58px,1fr));
  gap:1px;background:var(--line);border:1px solid var(--line);margin:10px 16px 0}
.ledger .wkc{background:var(--panel);padding:7px 2px 6px;text-align:center;
  display:flex;flex-direction:column;align-items:center;gap:3px}
.ledger .wkc .wkn{font-size:9px;letter-spacing:.12em;color:var(--ink3)}
.ledger .wkc .bar{height:38px;width:100%;display:flex;flex-direction:column;
  justify-content:center;align-items:center;position:relative}
.ledger .wkc .bar::before{content:"";position:absolute;left:12%;right:12%;
  top:50%;height:1px;background:var(--line)}
.ledger .wkc .bar i{width:11px;display:block;position:absolute}
.ledger .wkc .bar i.up{bottom:50%;background:var(--warn)}
.ledger .wkc .bar i.dn{top:50%;background:var(--play)}
.ledger .wkc .opp{display:flex;align-items:center;justify-content:center;
  gap:3px;font-size:11px;font-weight:700;line-height:1}
.ledger .wkc .opp .lg{width:13px;height:13px;flex:0 0 13px}
.ledger .wkc .val{font-size:9.5px;color:var(--ink3);
  font-variant-numeric:tabular-nums}
.ledger .wkc.bye{opacity:.45}
.ledger .wkc.bye .opp{font-size:9px;letter-spacing:.1em;color:var(--ink3)}
.ledger .sfoot{display:flex;flex-wrap:wrap;gap:16px;padding:8px 16px 0;
  font-size:11px;color:var(--ink3)}
.ledger .sfoot .lgd{display:flex;align-items:center;gap:5px}
.ledger .sfoot .lgd i{width:9px;height:9px;display:inline-block}
.ledger .sfoot .lgd i.up{background:var(--warn)}
.ledger .sfoot .lgd i.dn{background:var(--play);margin-left:7px}
.ledger .tw{overflow-x:auto}
.ledger .sec{padding:16px 16px 0;color:var(--ink);font-weight:600}

.ledger ol.log{list-style:none;margin:0;padding:10px 16px 18px}
.ledger ol.log li{border-left:3px solid var(--line);padding:0 0 14px 14px}
.ledger ol.log li:last-child{padding-bottom:4px}
.ledger ol.log .when{font-size:9.5px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--ink3)}
.ledger ol.log .as{margin-left:8px;letter-spacing:0;text-transform:none}
.ledger ol.log .hd{font-weight:600;margin-top:3px}
.ledger ol.log p{margin:3px 0 0;color:var(--ink2);font-size:12.5px;
  max-width:76ch}
.ledger p.empty{margin:0;padding:4px 16px 14px}
.ledger .ipad{padding:6px 16px 0}
.ledger ul.inj{list-style:none;margin:6px 0 0;padding:0 16px 16px;
  display:flex;flex-direction:column;gap:3px}
.ledger ul.inj li{display:grid;
  grid-template-columns:86px minmax(0,1fr) 38px minmax(0,1.2fr);
  gap:9px;align-items:baseline;font-size:12px}
.ledger .ist{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
  padding:2px 7px;border:1px solid currentColor;justify-self:start}
.ledger .is-o{color:var(--neg)}
.ledger .is-d{color:var(--warn)}
.ledger .is-q{color:var(--ink3)}
.ledger .inm{font-weight:600}
.ledger .ipo{color:var(--ink3);font-size:10.5px;letter-spacing:.06em;
  text-transform:uppercase}
.ledger .iin{color:var(--ink2);font-size:11.5px}
.ledger .mstrip{display:flex;flex-direction:column;gap:5px;margin-top:4px}
.ledger .stat .vl em{font-style:normal;font-size:10px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink3);margin-left:5px;font-weight:400}
.ledger .lb.sec{padding:15px 16px 7px;background:var(--row);
  border-top:1px solid var(--line);border-bottom:1px solid var(--line);
  color:var(--ink);font-weight:600;letter-spacing:.16em}
@media (max-width:820px){
  .ledger{grid-template-columns:1fr}
  .ledger nav.idx{position:static;max-height:250px}
  .ledger .hero h2{font-size:30px}
  .ledger .hero .hrk .n{font-size:40px}
}
"""

LEDGER_JS = """
(function(){
  var root=document.querySelector('.ledger'); if(!root) return;
  var btns=[].slice.call(root.querySelectorAll('nav.idx button'));
  var panes=[].slice.call(root.querySelectorAll('section.team'));
  if(!btns.length) return;
  function show(team){
    btns.forEach(function(b){
      b.setAttribute('aria-selected', b.dataset.team===team?'true':'false');});
    panes.forEach(function(p){p.hidden = p.id!=='t-'+team;});
  }
  btns.forEach(function(b){
    b.addEventListener('click',function(){show(b.dataset.team);});});
  // The league table is open in the markup; only act when a hash asks for a
  // team. A row in that table is the primary way in.
  var start=(location.hash||'').replace('#','');
  if(btns.some(function(b){return b.dataset.team===start;})) show(start);
  window.showTeam=show;
})();
"""


def build_pages(con):
    """Return (nav, panes, n_teams, n_logs) so the brief can mount this."""
    d = fetch(con)
    rows = [dict(v, team=k) for k, v in d["teams"].items()]
    ranks = {"net": rank_map(rows, "blended", True),
             "off": rank_map(rows, "off", True),
             # defense is signed so that NEGATIVE is good
             "def": rank_map(rows, "def", False)}
    # Power-ranking order, falling back to blended strength so a missing rank
    # cannot silently reorder the list.
    order = sorted(d["teams"],
                   key=lambda t: (d["teams"][t].get("power_rank") or 99,
                                  -(d["teams"][t].get("blended") or -9)))
    # GROUPED BY DIVISION, not a flat list of 32. Strength order is the table's
    # job; a rail exists to FIND a team, and nobody looks a team up by rating.
    rank_of = {tm: i + 1 for i, tm in enumerate(order)}
    # A FLAT LIST IN POWER-RANKING ORDER. No division grouping and no league
    # table in front of it. `power_rank` is the ranking on record in
    # ref.team_context; it tracks our own blended strength order closely - only
    # LAC differs by five places or more - so the two agree, and this is the one
    # that is explicitly a ranking.
    nav = "".join(
        f'<button role="tab" id="nav-{e(tm)}" data-team="{e(tm)}" '
        f'aria-selected="{"true" if i == 0 else "false"}" '
        f'aria-controls="t-{e(tm)}">'
        f'<span class="rn">{e(d["teams"][tm].get("power_rank") or i + 1)}</span>'
        f'<span class="lg lg-{e(tm)}"></span><span>{e(tm)}</span>'
        f'<span class="rt">{pts(d["teams"][tm].get("blended"))}</span>'
        f'</button>' for i, tm in enumerate(order))
    panes = "".join(render_team(t, d, ranks, open_=(i == 0))
                    for i, t in enumerate(order))
    return nav, panes, len(order), sum(len(v) for v in d["logs"].values())


def ledger_html(nav, panes):
    return (f'<div class="ledger">'
            f'<nav class="idx" role="tablist" aria-label="Teams in power-ranking order">'
            f'<div class="ih">Power ranking</div>{nav}</nav>'
            f'<main>{panes}</main></div>')


STANDALONE_CSS = DS.css() + """
.mast .sub{color:var(--ink2);font-size:12.5px}
.foot{margin-top:24px;padding-top:12px;border-top:1px solid var(--line);
  color:var(--ink3);font-size:11.5px;max-width:92ch}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("-o", "--out", default="team_pages.html")
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)
    nav, panes, n_teams, n_logs = build_pages(con)
    con.close()

    doc = f"""<title>{DS.BRAND} &middot; Teams</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{FONT_CSS}{STANDALONE_CSS}{LEDGER_CSS}{TV.CHART_CSS}{logo_css()}</style>
<div class="wrap">
 <header class="mast">
  <h1>{DS.BRAND} &middot; Teams</h1>
  <div class="sub">32 teams, ordered by blended Week 1 strength
   &middot; {SEASON}</div>
 </header>
 {ledger_html(nav, panes)}
 <p class="foot">Blended rating is 70% market win total and 30% our {HIST}
  opponent-adjusted rating, split into offense and defense by allocating the
  re-rate 0.560/0.440 &mdash; the measured share of a year-over-year move.
  Defense is stored so negative is good and is flipped in the chart so both
  bars point right when the unit is good. The weekly line and the season log
  both extend as {SEASON} is played; nothing here is rewritten in place.</p>
</div>
<script>{LEDGER_JS}</script>"""

    out = Path(a.out)
    out.write_text(doc, encoding="utf-8")
    log(f"  {n_teams} team pages, {n_logs} log entries -> {out} "
        f"({len(doc) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
