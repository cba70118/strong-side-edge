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
# label, column, weight column, direction, source, stability key, group.
# Sources: g = mart.team_game, p = mart.team_pressure, c = mart.team_charting.
# The stability figure is looked up from mart.metric_stability rather than
# hard-coded, so the number beside a metric is the one currently measured.
# "style" metrics have no better end and carry no percentile bar: a team is not
# better for motioning more, it is only different.
METRICS = [
    ("off EPA/play", "off_epa_play_neutral", "off_plays_neutral", "high",
     "g", "off EPA/play", "Offense"),
    ("off success rate", "off_success", "off_plays", "high",
     "g", "off success rate", "Offense"),
    ("explosive rate", "off_explosive_rate", "off_plays", "high",
     "g", "off explosive rate", "Offense"),
    ("pressure allowed", "pressure_rate_allowed", "dropbacks", "low",
     "p", "pressure allowed", "Offense"),
    ("def EPA/play", "def_epa_play_neutral", "def_plays_neutral", "low",
     "g", "def EPA/play", "Defense"),
    ("def success rate", "def_success", "def_plays", "low",
     "g", "def success rate", "Defense"),
    ("def explosive rate", "def_explosive_rate", "def_plays", "low",
     "g", "def explosive rate", "Defense"),
    ("pressure generated", "pressure_rate_made", "pass_snaps_faced", "high",
     "p", "pressure generated", "Defense"),
    ("pass rate over exp", "off_proe_neutral", "off_plays_neutral", "style",
     "g", "PROE", "Tendencies"),
    ("sec per play", "off_sec_per_play_neutral", "off_plays_neutral", "style",
     "g", "pace sec/play", "Tendencies"),
    ("play action", "play_action_rate", "plays", "style",
     "c", "play action rate", "Tendencies"),
    ("motion", "motion_rate", "plays", "style",
     "c", "motion rate", "Tendencies"),
    ("shotgun", "shotgun_rate", "plays", "style",
     "c", "shotgun rate", "Tendencies"),
    ("screens", "screen_rate", "plays", "style",
     "c", "screen rate", "Tendencies"),
    ("RPO", "rpo_rate", "plays", "style",
     "c", "RPO rate", "Tendencies"),
    ("blitzers sent", "blitz_sent", "plays", "style",
     "c", "blitzers sent", "Tendencies"),
    ("man coverage", "man_rate", "coverage_charted", "style",
     "c", "man coverage rate", "Tendencies"),
]

SRC_TABLE = {"g": "mart.team_game", "p": "mart.team_pressure",
             "c": "mart.team_charting"}
SRC_GATE = {"g": "AND game_type = 'REG'", "p": "AND native = 1", "c": ""}


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
    d["mweeks"] = {}
    d["stab"] = {}
    try:
        d["stab"] = dict(con.execute(
            "SELECT metric, n8 FROM mart.metric_stability").fetchall())
    except Exception:
        pass
    for label, col, wcol, _dir, src, _sk, _grp in METRICS:
        tbl, gate = SRC_TABLE[src], SRC_GATE[src]
        try:
            d["metrics"][label] = dict(con.execute(f"""
                SELECT team, sum({col} * {wcol}) / nullif(sum({wcol}), 0)
                FROM {tbl}
                WHERE season = ? {gate} AND {col} IS NOT NULL
                GROUP BY 1
            """, [HIST]).fetchall())
            # week by week, for the sparkline
            wk = {}
            for team, week, v in con.execute(f"""
                SELECT team, week, {col} FROM {tbl}
                WHERE season = ? {gate} AND {col} IS NOT NULL
                ORDER BY team, week
            """, [HIST]).fetchall():
                wk.setdefault(team, []).append(v)
            d["mweeks"][label] = wk
        except Exception:
            d["metrics"][label] = {}
            d["mweeks"][label] = {}
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
            # The gap in the sequence IS the bye. Drawing it keeps the week
            # numbers lined up with their place in the season.
            cells += (f'<div class="wkr bye"><div class="wkn">{wk}</div>'
                      f'<div class="opp">bye</div><div class="bar"></div>'
                      f'<div class="val"></div></div>')
            continue
        o = teams.get(r["opp"], {}) or {}
        raw = o.get("blended")
        v = raw or 0.0
        # Half the track sits on each side of the centre rule, so a full-scale
        # bar is 50% wide. Sizing it at 100% is exactly what sent the old
        # vertical bars over the labels.
        w = round(min(abs(v) / peak, 1.0) * 50, 1)
        up = v >= 0
        rk = ranks["net"].get(r["opp"]) or "--"
        home = r["at"] == "vs"
        cells += (
            f'<div class="wkr" title="Week {wk}: {"vs" if home else "at"} '
            f'{r["opp"]} (rank {rk}), strength {pts_fn(raw)}">'
            f'<div class="wkn">{wk}</div>'
            f'<div class="opp"><span class="ha">{"" if home else "@"}</span>'
            f'<span class="lg lg-{r["opp"]}"></span>'
            f'<b>{r["opp"]}</b></div>'
            f'<div class="bar"><i class="{"up" if up else "dn"}" '
            f'style="width:{w}%"></i></div>'
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


def spark(vals, lo, hi, w=64, h=15):
    """A week-by-week trace, scaled to the LEAGUE range for that metric.

    Scaling to the team's own range would make every team look equally
    volatile, which is the opposite of what the trace is for.
    """
    if not vals or len(vals) < 3 or hi <= lo:
        return ""
    n = len(vals)
    pts = " ".join(
        f"{i / (n - 1) * w:.1f},"
        f"{h - (max(lo, min(hi, v)) - lo) / (hi - lo) * h:.1f}"
        for i, v in enumerate(vals))
    return (f'<svg class="spk" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'aria-hidden="true"><polyline points="{pts}"/></svg>')


def metric_rows(t, d, opp=None):
    """One row per metric: trend, own value, rank, and the opponent's.

    Percentile is direction-corrected so a full bar always means best in the
    league. Style metrics get None, which renders no bar: a team is not better
    for motioning more, only different.
    """
    out = []
    for label, _col, _w, direction, src, skey, group in METRICS:
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

        ov = vals.get(opp) if opp else None
        orank = None
        if ov is not None and direction != "style":
            orank = sorted(others, reverse=(direction == "high")).index(ov) + 1

        wk = (d["mweeks"].get(label) or {}).get(t) or []
        flat = [x for team_vals in (d["mweeks"].get(label) or {}).values()
                for x in team_vals]
        if flat:
            flat = sorted(flat)
            lo, hi = flat[int(.05 * (len(flat) - 1))], flat[int(.95 * (len(flat) - 1))]
        else:
            lo = hi = 0
        # Format by SOURCE, not by matching words in the label. Matching on
        # "rate" caught pass rate over expected, which is already in
        # percentage POINTS, and printed +3.45 as 345%.
        dp = 1 if ("sec" in label or "blitz" in label
                   or label == "pass rate over exp") else 3
        pctv = src == "c" and label != "blitzers sent"
        out.append({"label": label, "group": group, "v": v, "pct": pct,
                    "rank": rank, "dp": dp, "r": d["stab"].get(skey),
                    "spark": spark(wk, lo, hi), "ov": ov, "orank": orank,
                    "aspct": pctv})
    return out


def metric_table(rows, opp):
    """The applied metrics section. Full width, because a matchup column and a
    trend do not fit in a 330px card."""
    if not rows:
        return '<p class="empty">No metrics available.</p>'
    head = (f'<div class="mh"><span></span><span>trend</span><span>2025</span>'
            f'<span>rk</span><span></span>'
            f'<span class="oc">{e(opp) if opp else ""}</span>'
            f'<span class="oc">rk</span></div>')
    out, seen = "", None
    for r in rows:
        if r["group"] != seen:
            seen = r["group"]
            out += f'<div class="mgrp">{e(seen)}</div>'
        p = max(0.0, min(1.0, r["pct"] if r["pct"] is not None else 0.0))
        bar = (f'<span class="mb"><i style="width:{p * 100:.1f}%"></i></span>'
               if r["pct"] is not None else '<span class="mb none"></span>')
        fmt = (lambda x: f"{x * 100:.0f}%") if r["aspct"] else (
            lambda x: num(x, r["dp"]))
        out += (
            f'<div class="mrow"><span class="ml">{e(r["label"])}'
            f'<em title="year-over-year stability, measured not assumed">'
            f'r{("%.2f" % r["r"])[1:] if r["r"] is not None else "--"}</em>'
            f'</span>'
            f'<span class="msp">{r["spark"]}</span>'
            f'<span class="mv">{fmt(r["v"]) if r["v"] is not None else "--"}</span>'
            f'<span class="mr">{("#%s" % r["rank"]) if r["rank"] else ""}</span>'
            f'{bar}'
            f'<span class="mv oc">'
            f'{fmt(r["ov"]) if r["ov"] is not None else "--"}</span>'
            f'<span class="mr oc">'
            f'{("#%s" % r["orank"]) if r["orank"] else ""}</span></div>')
    return f'<div class="mtable">{head}{out}</div>'


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

    opp1 = (d["games"].get(t) or {}).get("opp")
    mtable = metric_table(metric_rows(t, d, opp1), opp1)
    profile_note = ('<div class="sb">Season shape above; the measured metrics are in the Metrics section below.</div>')

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
   {profile_note}
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
 <div class="lb sec">Metrics</div>
 {mtable}
 <div class="note sb">Each row carries <b>r</b>, its own measured
  year-over-year stability, because motion at 0.87 and defensive EPA at 0.37
  are not the same kind of number. Tendencies have no better end so they carry
  no bar. The right-hand column is week 1&rsquo;s opponent. None of these beat
  the closing spread, so this explains a line rather than beating one.</div>
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
/* Applied metrics. Full width because a trend and a matchup column do not
   fit a 330px card. Grid columns, so every row lines up without a table. */
.ledger .mtable{margin:8px 16px 0}
.ledger .mh,.ledger .mrow{display:grid;
  grid-template-columns:150px 66px 58px 30px minmax(56px,1fr) 58px 30px;
  align-items:center;gap:9px}
.ledger .mh{font-size:9px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink3);padding-bottom:5px;border-bottom:1px solid var(--rule)}
.ledger .mh span,.ledger .mrow .mv,.ledger .mrow .mr{text-align:right}
.ledger .mh span:first-child{text-align:left}
.ledger .mgrp{font-size:9px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink3);padding:10px 0 3px}
.ledger .mrow{padding:2px 0;font-size:11.5px;
  border-bottom:1px solid var(--line)}
.ledger .mrow:last-child{border-bottom:0}
.ledger .mrow .ml{color:var(--ink2);text-align:left;display:flex;
  align-items:baseline;gap:6px}
.ledger .mrow .ml em{font-style:normal;font-size:9px;color:var(--ink3);
  letter-spacing:.04em}
.ledger .mrow .mv{font-weight:700;font-variant-numeric:tabular-nums}
.ledger .mrow .mr{color:var(--ink3);font-size:10px;
  font-variant-numeric:tabular-nums}
.ledger .mrow .mb{height:8px;background:var(--row);display:block}
.ledger .mrow .mb.none{background:transparent}
.ledger .mrow .mb i{display:block;height:100%;background:var(--accent)}
.ledger .msp{display:flex;align-items:center;height:15px}
.ledger svg.spk{overflow:visible}
.ledger svg.spk polyline{fill:none;stroke:var(--ink3);stroke-width:1.2;
  stroke-linejoin:round;vector-effect:non-scaling-stroke}
/* the opponent columns are secondary: same data, quieter */
.ledger .oc{color:var(--ink3)}
.ledger .mrow .mv.oc{font-weight:400}

/* Season strength strip, PORTRAIT. One week per row, bar diverging from a
   centre rule. The landscape version sized bars as a percentage of the box but
   drew them from the midpoint, so anything past half ran over the labels.
   Here each side owns half the track and a full-scale bar is 50% wide. */
.ledger .strip{margin:10px 16px 0;border:1px solid var(--line);
  background:var(--panel)}
.ledger .wkr{display:grid;grid-template-columns:22px 76px minmax(0,1fr) 46px;
  align-items:center;gap:8px;padding:3px 9px;
  border-bottom:1px solid var(--line)}
.ledger .wkr:last-child{border-bottom:0}
.ledger .wkr .wkn{font-size:9.5px;color:var(--ink3);text-align:right;
  font-variant-numeric:tabular-nums}
.ledger .wkr .opp{display:flex;align-items:center;gap:4px;font-size:11.5px;
  font-weight:700;line-height:1;overflow:hidden}
.ledger .wkr .opp .ha{color:var(--ink3);font-weight:400;width:7px;flex:0 0 7px}
.ledger .wkr .opp .lg{width:14px;height:14px;flex:0 0 14px}
.ledger .wkr .bar{position:relative;height:11px;background:var(--row)}
.ledger .wkr .bar::before{content:"";position:absolute;left:50%;top:-1px;
  bottom:-1px;width:1px;background:var(--ink3);opacity:.5}
.ledger .wkr .bar i{position:absolute;top:0;bottom:0;display:block}
.ledger .wkr .bar i.up{left:50%;background:var(--warn)}
.ledger .wkr .bar i.dn{right:50%;background:var(--play)}
.ledger .wkr .val{text-align:right;font-size:10.5px;color:var(--ink3);
  font-variant-numeric:tabular-nums}
.ledger .wkr.bye{opacity:.45}
.ledger .wkr.bye .opp{font-size:9.5px;letter-spacing:.12em;font-weight:400;
  text-transform:uppercase;color:var(--ink3)}
.ledger .sfoot{display:flex;flex-wrap:wrap;gap:16px;padding:8px 16px 0;
  font-size:11px;color:var(--ink3)}
.ledger .sfoot .lgd{display:flex;align-items:center;gap:5px}
.ledger .sfoot .lgd i{width:9px;height:9px;display:inline-block}
.ledger .sfoot .lgd i.up{background:var(--warn)}
.ledger .sfoot .lgd i.dn{background:var(--play);margin-left:7px}
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
