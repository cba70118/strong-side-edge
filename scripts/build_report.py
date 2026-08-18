"""
build_report.py - render the weekly brief as an institutional research note.

Replaces the handoff renderer for our own use. Differences that matter:

  * Every game gets a DATA SHEET, not just a betting read: 2025 team profile
    with league ranks, market-implied team totals, and the model's forecast
    distribution. A game with no position still has a page worth reading.
  * PROJECTIONS are first-class - projected score, win probability, P(over),
    and the margin bands the distribution implies.
  * The prose is written for a reader, not for a model. No jargon in the
    summary; the mechanics live in the methodology note at the foot.

Everything is queried. Nothing is invented - in particular there are no
injury, weather or personnel claims anywhere, because we have no source for
them and a research note that invents them is worthless.

Usage:
    py -3 scripts/build_report.py --week 1 -o week01_report.html
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devig import american_to_prob, devig_american, prob_to_american  # noqa: E402
import margin_model as MM  # noqa: E402
import totals_model as TM  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
OPP = {"home": "away", "away": "home", "over": "under", "under": "over"}

TEAM_NAME = {}
TEAM_NICK = {}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1:'st',2:'nd',3:'rd'}.get(n % 10, 'th') }"


def rank_word(rank: int, n: int = 32) -> str:
    if rank <= 3:
        return "elite"
    if rank <= 8:
        return "strong"
    if rank <= 16:
        return "above average"
    if rank <= 24:
        return "below average"
    return "poor"


# --------------------------------------------------------------------- data

def load(con, season: int, week: int):
    global TEAM_NAME, TEAM_NICK
    TEAM_NAME = dict(con.execute(
        "SELECT team_id, full_name FROM ref.team WHERE is_current").fetchall())
    # Nicknames for repeat mentions - naming a team in full three times in one
    # sentence reads like a database dump, not like prose.
    TEAM_NICK = dict(con.execute(
        "SELECT team_id, coalesce(nickname, full_name) FROM ref.team "
        "WHERE is_current").fetchall())

    games = con.execute("""
        SELECT game_id, away_team, home_team, gameday, gametime, roof, surface,
               spread_line, total_line, away_rest, home_rest, div_game
        FROM raw.games WHERE season=? AND week=? ORDER BY gameday, gametime
    """, [season, week]).fetchall()

    prof = {}
    rows = con.execute("""
        SELECT team,
               avg(off_epa_play_neutral) oepa, avg(def_epa_play_neutral) depa,
               avg(off_success) succ, avg(off_proe_neutral) proe,
               avg(off_sec_per_play_neutral) pace, avg(off_explosive_rate) expl,
               avg(points_for) pf, avg(points_against) pa, count(*) g
        FROM mart.team_game WHERE season=2025 GROUP BY 1
    """).fetchall()
    cols = ["oepa", "depa", "succ", "proe", "pace", "expl", "pf", "pa"]
    for r in rows:
        prof[r[0]] = dict(zip(cols, r[1:9]), g=r[9])
    # ranks: higher is better except depa/pa/pace where lower is better
    for c in cols:
        rev = c not in ("depa", "pa", "pace")
        order = sorted(prof, key=lambda t: prof[t][c], reverse=rev)
        for i, t in enumerate(order, 1):
            prof[t][c + "_rk"] = i

    quotes = con.execute("""
        WITH r AS (SELECT l.game_id, l.market_id, l.book_id, l.side_key,
               l.line_value, l.price_american, b.is_placeable, b.is_sharp,
               b.book_name,
               row_number() OVER (PARTITION BY l.game_id,l.market_id,l.book_id,
                    l.side_key ORDER BY l.captured_at DESC) rn
            FROM odds.line_pregame l JOIN ref.book b ON b.book_id=l.book_id
            WHERE l.game_id IS NOT NULL
              AND l.market_id IN ('spread_game','total_game','ml_game'))
        SELECT game_id, market_id, book_id, book_name, side_key, line_value,
               price_american, is_placeable, is_sharp FROM r WHERE rn=1
    """).fetchall()

    captured = con.execute(
        "SELECT cast(max(captured_at) AS VARCHAR) FROM odds.line").fetchone()[0]

    ctx = {}
    try:
        for r in con.execute("""
            SELECT c.team, c.power_rank, c.record_2025, c.win_total,
                   c.head_coach, c.coach_is_new, c.starting_qb, c.qb_is_new,
                   c.qb_health_flag, c.context_note,
                   p.blended_rating, p.market_rating, p.rating_2025
            FROM ref.team_context c
            LEFT JOIN mart.preseason_prior p ON p.team = c.team
            WHERE c.season = 2026
        """).fetchall():
            ctx[r[0]] = dict(zip(
                ["rank","rec","wt","coach","coach_new","qb","qb_new",
                 "qb_flag","note","blend","mkt","r25"], r[1:]))
    except Exception:
        pass

    proj = {}
    try:
        for r in con.execute("""
            SELECT team, display_name, position, proj_targets, proj_receptions,
                   proj_rec_yards, proj_carries, proj_rush_yards,
                   proj_target_share, proj_carry_share, n_games
            FROM mart.player_projection_pre
            WHERE display_name IS NOT NULL
        """).fetchall():
            proj.setdefault(r[0], []).append(r)
    except Exception:
        pass
    return games, prof, quotes, captured, proj, ctx


def sharp_and_best(quotes):
    per = {}
    for g, m, b, bn, side, line, price, place, sharp in quotes:
        per.setdefault((g, m, b), {})[side] = (line, price, place, sharp, bn)
    ref, best = {}, {}
    for (g, m, b), sides in per.items():
        if len(sides) != 2:
            continue
        k0 = next(iter(sides)); k1 = OPP.get(k0)
        if k1 not in sides:
            continue
        try:
            fair = devig_american([sides[k0][1], sides[k1][1]], "power")
        except Exception:
            continue
        if sides[k0][3]:
            ref[(g, m)] = {k0: (sides[k0][0], fair[0]), k1: (sides[k1][0], fair[1])}
        for k in (k0, k1):
            line, price, place, _, bn = sides[k]
            if not place:
                continue
            cur = best.get((g, m, k))
            if cur is None or price > cur["price"]:
                best[(g, m, k)] = {"price": price, "line": line, "book": bn}
    return ref, best


# ---------------------------------------------------------------- rendering

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --paper:#f6f7f8; --sheet:#ffffff; --band:#eef1f3;
  --ink:#11161b; --ink2:#3f4a54; --muted:#79848d;
  --rule:#dde2e6; --hair:#e9edf0;
  --accent:#1b4a6b; --accent-soft:#e3ecf3;
  --pos:#0e7a55; --neg:#a33a22;
  --shadow:0 1px 2px rgba(17,22,27,.05);
}
:root:not([data-theme="light"]){ color-scheme:light }
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --paper:#0d1013; --sheet:#151a1e; --band:#1b2228;
    --ink:#e9edf0; --ink2:#aab5bd; --muted:#727d86;
    --rule:#242c33; --hair:#1e262c;
    --accent:#7db3d8; --accent-soft:#16242e;
    --pos:#3cb388; --neg:#dd7150;
    --shadow:none;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --paper:#0d1013; --sheet:#151a1e; --band:#1b2228;
  --ink:#e9edf0; --ink2:#aab5bd; --muted:#727d86;
  --rule:#242c33; --hair:#1e262c;
  --accent:#7db3d8; --accent-soft:#16242e;
  --pos:#3cb388; --neg:#dd7150;
  --shadow:none;
}

body{margin:0;background:var(--paper);color:var(--ink);
  font:400 16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1040px;margin:0 auto;padding:40px 22px 80px}
.serif{font-family:Georgia,"Iowan Old Style","Times New Roman",serif}
.num{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.pos{color:var(--pos)} .neg{color:var(--neg)}

/* masthead */
.mast{border-bottom:2px solid var(--ink);padding-bottom:14px;
  display:flex;justify-content:space-between;align-items:flex-end;gap:24px;
  flex-wrap:wrap}
.mast h1{font-family:Georgia,"Iowan Old Style",serif;font-size:38px;
  line-height:1.05;margin:0;letter-spacing:-.02em;text-wrap:balance}
.mast .issue{font-size:12px;color:var(--muted);text-align:right;
  text-transform:uppercase;letter-spacing:.09em;line-height:1.7}
.strip{display:flex;gap:0;border-bottom:1px solid var(--rule);
  margin-bottom:34px;flex-wrap:wrap}
.strip div{padding:12px 20px 12px 0;margin-right:20px;
  border-right:1px solid var(--hair)}
.strip div:last-child{border-right:0}
.strip .k{font-size:10.5px;text-transform:uppercase;letter-spacing:.09em;
  color:var(--muted);display:block;margin-bottom:3px}
.strip .v{font-size:19px}

h2.sec{font-family:Georgia,serif;font-size:13px;text-transform:uppercase;
  letter-spacing:.13em;color:var(--accent);margin:44px 0 14px;
  padding-bottom:7px;border-bottom:1px solid var(--rule);font-weight:700}
p{margin:0 0 15px;max-width:66ch;color:var(--ink2)}
p.lede{font-size:18.5px;line-height:1.62;color:var(--ink)}
p.lede:first-letter{font-family:Georgia,serif;font-size:52px;float:left;
  line-height:.82;padding:4px 9px 0 0;color:var(--accent)}

/* slate table */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:13.5px;min-width:660px}
th{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);font-weight:600;text-align:right;padding:0 0 8px 14px;
  border-bottom:1px solid var(--rule);white-space:nowrap}
th:first-child{text-align:left;padding-left:0}
td{padding:9px 0 9px 14px;border-bottom:1px solid var(--hair);text-align:right;
  white-space:nowrap}
td:first-child{text-align:left;padding-left:0}
tbody tr:hover{background:var(--band)}
.tag{display:inline-block;font-size:10px;text-transform:uppercase;
  letter-spacing:.07em;padding:2px 7px;border-radius:2px;font-weight:600}
.tag.play{background:var(--accent);color:var(--sheet)}
.tag.none{background:var(--band);color:var(--muted)}

/* game sheet */
.sheet{background:var(--sheet);border:1px solid var(--rule);
  box-shadow:var(--shadow);margin:0 0 22px}
.sheet > .hd{display:flex;justify-content:space-between;align-items:baseline;
  gap:18px;padding:16px 20px;border-bottom:1px solid var(--rule);
  background:var(--band);flex-wrap:wrap}
.sheet .idx{font-size:11px;color:var(--muted);letter-spacing:.09em;
  margin-right:10px}
.sheet h3{font-family:Georgia,serif;font-size:22px;margin:0;
  letter-spacing:-.01em;display:inline}
.sheet .when{font-size:12px;color:var(--muted);text-transform:uppercase;
  letter-spacing:.06em;text-align:right}
.panels{display:grid;grid-template-columns:1fr 1fr;gap:0}
@media (max-width:760px){.panels{grid-template-columns:1fr}}
.panel{padding:18px 20px;border-right:1px solid var(--hair)}
.panel:last-child{border-right:0}
@media (max-width:760px){.panel{border-right:0;border-bottom:1px solid var(--hair)}}
.panel h4{font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;
  color:var(--muted);margin:0 0 12px;font-weight:600}
.kv{display:flex;justify-content:space-between;gap:12px;padding:5px 0;
  border-bottom:1px solid var(--hair);font-size:13.5px}
.kv:last-child{border-bottom:0}
.kv .k{color:var(--ink2)} .kv .v{text-align:right}
.rk{color:var(--muted);font-size:11.5px;margin-left:5px}

.bars{margin-top:12px}
.bar{display:grid;grid-template-columns:104px 1fr 44px;align-items:center;
  gap:9px;padding:3px 0;font-size:12px}
.bar .t{color:var(--muted)}
.bar .track{background:var(--band);height:7px;border-radius:1px;overflow:hidden}
.bar .fill{display:block;background:var(--accent);height:100%}
.bar .p{text-align:right;color:var(--ink2)}

.projwrap{padding:18px 20px;border-top:1px solid var(--rule)}
.projwrap h4{font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;
  color:var(--muted);margin:0 0 12px;font-weight:600}
.pnote{text-transform:none;letter-spacing:0;color:var(--ink2);font-weight:400}
.projgrid{display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media (max-width:760px){.projgrid{grid-template-columns:1fr}}
.projcol h5{font-family:Georgia,serif;font-size:14px;margin:0 0 6px;
  font-weight:400;color:var(--ink2)}
table.proj{min-width:0;font-size:12.5px}
table.proj th{padding:0 0 5px 9px;font-size:9.5px}
table.proj td{padding:5px 0 5px 9px}
.note{padding:16px 20px;border-top:1px solid var(--rule);font-size:14.5px;
  color:var(--ink2)}
.note p{max-width:none;margin:0}
.pos-block{padding:16px 20px;border-top:2px solid var(--accent);
  background:var(--accent-soft)}
.pos-block .row{display:flex;justify-content:space-between;gap:16px;
  align-items:baseline;flex-wrap:wrap;margin-bottom:9px}
.pos-block .sel{font-family:Georgia,serif;font-size:19px;color:var(--ink)}
.pos-block .meta{font-size:12.5px;color:var(--ink2)}
.pos-block dl{margin:8px 0 0;font-size:13.5px;color:var(--ink2)}
.pos-block dt{font-size:10px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);margin-top:9px}
.pos-block dd{margin:2px 0 0}
.noact{padding:13px 20px;border-top:1px solid var(--rule);
  font-size:13px;color:var(--muted)}

.foot{margin-top:52px;padding-top:18px;border-top:1px solid var(--rule);
  font-size:12.5px;color:var(--muted)}
.foot p{max-width:none;color:var(--muted);font-size:12.5px}
.foot b{color:var(--ink2)}
"""


def bar(label, pct, color=None):
    """One horizontal bar. Width and color share a single style attribute -
    emitting two `style=` on the same element makes the browser drop one."""
    w = max(0.0, min(100.0, pct))
    decl = f"width:{w:.1f}%" + (f";background:{color}" if color else "")
    return (f'<div class="bar"><span class="t">{esc(label)}</span>'
            f'<span class="track"><span class="fill" style="{decl}"></span>'
            f'</span><span class="p num">{pct:.1f}%</span></div>')


def kv(k, v, rk=None):
    r = f'<span class="rk">{ordinal(rk)}</span>' if rk else ""
    return (f'<div class="kv"><span class="k">{esc(k)}</span>'
            f'<span class="v num">{v}{r}</span></div>')


def build(con, season, week, min_edge):
    games, prof, quotes, captured, proj, ctx = load(con, season, week)
    ref, best = sharp_and_best(quotes)
    mm = MM.load_params(con)
    tm = TM.load_params(con)

    sheets, slate, n_play = [], [], 0

    for i, (gid, away, home, gday, gtime, roof, surf, sl, tl,
            arest, hrest, div) in enumerate(games, 1):
        pa, ph = prof.get(away, {}), prof.get(home, {})
        # market-implied team totals
        it_home = (tl / 2) + (sl / 2) if (tl is not None and sl is not None) else None
        it_away = (tl / 2) - (sl / 2) if (tl is not None and sl is not None) else None

        pmf_m = MM.pmf_for_spread(round(float(sl) * 2) / 2, *mm)
        p_home = MM.win_prob(pmf_m, "home")
        pmf_t = TM.pmf_for_total(round(float(tl) * 2) / 2, *tm)
        p_over = TM.fair_prob_over(pmf_t, float(tl))

        # Unit matchup, expressed as league percentile from 2025 ranks.
        # This replaced a margin-of-victory band chart: those bands came out of
        # the margin model, which is gated for exactly this kind of conditional
        # read, so charting it implied a confidence we had already disclaimed.
        # Offense against the defense it actually faces is the real matchup and
        # it is measured, not modeled.
        def pct_of(rk):
            return (33 - rk) / 32 * 100
        bands = []
        if pa and ph:
            bands = [
                (f"{TEAM_NICK.get(away,away)[:11]} off", pct_of(pa["oepa_rk"])),
                (f"{TEAM_NICK.get(home,home)[:11]} def", pct_of(ph["depa_rk"])),
                (f"{TEAM_NICK.get(home,home)[:11]} off", pct_of(ph["oepa_rk"])),
                (f"{TEAM_NICK.get(away,away)[:11]} def", pct_of(pa["depa_rk"])),
            ]

        # position: totals via validated model; sides matched-line only
        play = None
        tref = ref.get((gid, "total_game"))
        for side in ("under", "over"):
            bq = best.get((gid, "total_game", side))
            if not bq or not tref or side not in tref:
                continue
            rl = tref[side][0]
            if rl is None or bq["line"] is None or abs(bq["line"] - rl) > 1.0:
                continue
            o, push = TM.over_prob(TM.pmf_for_total(round(rl * 2) / 2, *tm),
                                   float(bq["line"]))
            if push >= 1.0:
                continue
            fp = (o / (1 - push)) if side == "over" else 1 - (o / (1 - push))
            edge = (fp - american_to_prob(bq["price"])) * 100
            if edge >= min_edge:
                play = {
                    "kind": "Total", "sel": f"{side.capitalize()} {bq['line']:g}",
                    "price": bq["price"], "book": bq["book"], "fair": fp,
                    "edge": edge, "refline": rl,
                    "thesis": (
                        f"Every book we can bet is posting {bq['line']:g} while "
                        f"the sharp reference sits at {rl:g}, in the same "
                        f"capture. We are buying a full point of number on the "
                        f"{side}, and the scoring model says that point is "
                        f"worth about {edge:.1f} percent."),
                    "kill": (
                        f"The sharp number climbs to {bq['line']:g}, or the "
                        f"books we can bet drop back to {rl:g}. Either way the "
                        f"gap closed against us and the reason for the position "
                        f"is gone."),
                }
                break
        if play is None:
            sref = ref.get((gid, "spread_game"))
            for side in ("home", "away"):
                bq = best.get((gid, "spread_game", side))
                if not bq or not sref or side not in sref:
                    continue
                rl, rfair = sref[side]
                if rl is None or bq["line"] is None or abs(bq["line"] - rl) > 1e-9:
                    continue
                edge = (rfair - american_to_prob(bq["price"])) * 100
                if edge >= min_edge:
                    tm_ = home if side == "home" else away
                    play = {
                        "kind": "Side", "sel": f"{TEAM_NAME.get(tm_, tm_)} {bq['line']:+g}",
                        "price": bq["price"], "book": bq["book"], "fair": rfair,
                        "edge": edge, "refline": rl,
                        "thesis": (
                            f"The same number as the sharp book, at a better "
                            f"price  {bq['price']:+d} against a true price of "
                            f"about {prob_to_american(rfair):+.0f}. This is "
                            f"shopping, not a forecast."),
                        "kill": (
                            f"The sharp price shortens past {bq['price']:+d}, or "
                            f"either book moves off {bq['line']:+g}."),
                    }
                    break
        if play:
            n_play += 1

        # ---- narrative, plain English, from measured facts only ----------
        def team_line(t, p):
            if not p:
                return f"{TEAM_NAME.get(t,t)} have no 2025 profile on file"
            return (f"{TEAM_NAME.get(t,t)} finished 2025 with "
                    f"{rank_word(p['oepa_rk'])} offense "
                    f"({ordinal(p['oepa_rk'])} in expected points per play) and "
                    f"{rank_word(p['depa_rk'])} defense "
                    f"({ordinal(p['depa_rk'])})")
        pace_note = ""
        if pa and ph:
            fast = min([(pa["pace"], away), (ph["pace"], home)])
            slow = max([(pa["pace"], away), (ph["pace"], home)])
            if slow[0] - fast[0] > 1.5:
                pace_note = (f" The {TEAM_NICK.get(fast[1],fast[1])} played "
                             f"noticeably faster than the "
                             f"{TEAM_NICK.get(slow[1],slow[1])} last year "
                             f"({fast[0]:.1f} seconds a snap against "
                             f"{slow[0]:.1f}), which is the kind of difference "
                             f"that shows up in play count rather than points.")
        nh, na = TEAM_NICK.get(home, home), TEAM_NICK.get(away, away)

        def change_line(t, nick):
            c = ctx.get(t)
            if not c:
                return ""
            bits = []
            if c["coach_new"]:
                bits.append(f"{c['coach']} takes over as head coach")
            if c["qb_new"]:
                bits.append(f"{c['qb']} is the new starter")
            elif c["qb_flag"]:
                bits.append(f"{c['qb']} is working back ({c['qb_flag']})")
            if not bits:
                return ""
            return f" For the {nick}, {' and '.join(bits)}."

        def prior_line(t, nick):
            c = ctx.get(t)
            if not c or c["mkt"] is None or c["r25"] is None:
                return ""
            gap = c["mkt"] - c["r25"]
            if abs(gap) < 0.05:
                return ""
            dirn = "higher" if gap > 0 else "lower"
            return (f" The market has set the {nick} a {c['wt']:g}-win total, "
                    f"which rates them materially {dirn} than their 2025 play "
                    f"does - the kind of gap that says last season no longer "
                    f"describes this roster.")
        narr = (
            f"{team_line(away, pa)}. {team_line(home, ph)}. "
            f"The market makes the {nh} a "
            f"{abs(sl):g}-point {'favorite' if sl > 0 else 'underdog'} and "
            f"expects about {tl:g} points, which splits into roughly "
            f"{it_home:.0f} for the {nh} and {it_away:.0f} "
            f"for the {na}.{pace_note} "
            + change_line(away, na) + change_line(home, nh)
            + prior_line(away, na) + prior_line(home, nh)
            + f" Our own strength read blends the market's preseason view with "
              f"last season's play; we do not act on small disagreements with "
              f"the closing line, because last season's ratings carry no "
              f"information it has not already priced."
        )

        # ---- panels -----------------------------------------------------
        market_panel = (
            '<div class="panel"><h4>Market</h4>'
            + kv("Spread", f"{TEAM_NAME.get(home,home)[:3].upper()} {sl:+g}")
            + kv("Total", f"{tl:g}")
            + kv(f"Implied  {home}", f"{it_home:.1f}")
            + kv(f"Implied  {away}", f"{it_away:.1f}")
            + kv("Rest (home / away)", f"{hrest} / {arest} d")
            + kv("Venue", f"{(roof or '?').title()} · {(surf or '?')}")
            + '</div>'
        )
        forecast_panel = (
            '<div class="panel"><h4>Forecast</h4>'
            + kv(f"{home} win", f"{p_home*100:.1f}%")
            + kv("Over hits", f"{p_over*100:.1f}%")
            + kv("Model total", f"{tl:g}")
            + '<div class="bars"><h4 style="margin:16px 0 8px">Unit matchup <span style="text-transform:none;letter-spacing:0;color:var(--ink2)">2025 percentile</span></h4>'
            + "".join(bar(l, v) for l, v in bands)
            + '</div></div>'
        )
        prof_rows = ""
        if pa and ph:
            def two(label, key, fmt="{:+.3f}"):
                return (f'<div class="kv"><span class="k">{esc(label)}</span>'
                        f'<span class="v num">'
                        f'{fmt.format(pa[key])}<span class="rk">'
                        f'{ordinal(pa[key+"_rk"])}</span>'
                        f' &nbsp;|&nbsp; {fmt.format(ph[key])}'
                        f'<span class="rk">{ordinal(ph[key+"_rk"])}</span>'
                        f'</span></div>')
            prof_rows = (
                two("EPA per play, offense", "oepa")
                + two("EPA per play allowed", "depa")
                + two("Success rate", "succ", "{:.1%}")
                + two("Pass rate over expected", "proe", "{:+.1f}")
                + two("Seconds per snap", "pace", "{:.1f}")
                + two("Explosive play rate", "expl", "{:.1%}")
                + two("Points scored", "pf", "{:.1f}")
                + two("Points allowed", "pa", "{:.1f}")
            )
        profile_panel = (
            f'<div class="panel" style="grid-column:1/-1;border-right:0;'
            f'border-top:1px solid var(--hair)"><h4>2025 team profile &nbsp;'
            f'<span style="color:var(--ink2);text-transform:none;'
            f'letter-spacing:0">{esc(away)} &nbsp;|&nbsp; {esc(home)}</span>'
            f'</h4>{prof_rows}</div>'
        )

        # ---- player projections, both teams -------------------------
        def proj_rows(t):
            rs = [r for r in proj.get(t, []) if r[5] is not None]
            rec = sorted(rs, key=lambda r: -r[5])[:5]
            run = sorted([r for r in proj.get(t, []) if r[7] is not None],
                         key=lambda r: -r[7])[:3]
            out = ""
            for r in rec:
                out += (f'<tr><td>{esc(r[1])}</td><td class="num">{esc(r[2])}</td>'
                        f'<td class="num">{r[3]:.1f}</td>'
                        f'<td class="num">{r[4]:.1f}</td>'
                        f'<td class="num">{r[5]:.0f}</td>'
                        f'<td class="num">{r[8]*100:.0f}%</td></tr>')
            for r in run:
                out += (f'<tr><td>{esc(r[1])}</td><td class="num">{esc(r[2])}</td>'
                        f'<td class="num">{r[6]:.1f} car</td><td class="num"></td>'
                        f'<td class="num">{r[7]:.0f} ru</td>'
                        f'<td class="num">{r[9]*100:.0f}%</td></tr>')
            return out or '<tr><td colspan="6">no projection available</td></tr>'

        ctx_rows = ""
        for t in (away, home):
            c = ctx.get(t)
            if not c:
                continue
            flags = []
            if c["coach_new"]:
                flags.append("new HC")
            if c["qb_new"]:
                flags.append("new QB")
            if c["qb_flag"]:
                flags.append(esc(c["qb_flag"]))
            ctx_rows += (
                f'<tr><td><strong>{esc(TEAM_NICK.get(t,t))}</strong></td>'
                f'<td class="num">#{c["rank"]}</td><td class="num">{esc(c["rec"])}</td>'
                f'<td class="num">{c["wt"]:g}</td><td>{esc(c["coach"])}</td>'
                f'<td>{esc(c["qb"])}</td>'
                f'<td>{" · ".join(flags) if flags else "none"}</td></tr>')
        ctx_panel = ""
        if ctx_rows:
            ctx_panel = (
                '<div class="projwrap"><h4>Season context '
                '<span class="pnote">power rank, 2025 record, market win total, '
                'and the changes our ratings cannot see</span></h4>'
                '<div class="scroll"><table class="proj"><thead><tr>'
                '<th>Team</th><th>Rk</th><th>2025</th><th>Win tot</th>'
                '<th>Head coach</th><th>QB</th><th>Flags</th></tr></thead>'
                f'<tbody>{ctx_rows}</tbody></table></div></div>')

        proj_panel = ""
        if proj:
            proj_panel = (
                '<div class="projwrap"><h4>Historical usage baseline '
                '<span class="pnote">2025 rates carried onto 2026 rosters. '
                'A starting point for the season, not a Week 1 forecast.</span>'
                '</h4><div class="projgrid">'
                + "".join(
                    f'<div class="projcol"><h5>{esc(TEAM_NICK.get(t,t))}</h5>'
                    f'<div class="scroll"><table class="proj"><thead><tr>'
                    f'<th>Player</th><th>Pos</th><th>Tgt</th><th>Rec</th>'
                    f'<th>Yds</th><th>Share</th></tr></thead>'
                    f'<tbody>{proj_rows(t)}</tbody></table></div></div>'
                    for t in (away, home))
                + '</div></div>')

        if play:
            body = (
                '<div class="pos-block">'
                f'<div class="row"><span class="sel">{esc(play["sel"])} '
                f'<span class="num">{play["price"]:+d}</span></span>'
                f'<span class="meta num">{esc(play["book"])} · fair '
                f'{prob_to_american(play["fair"]):+.0f} · '
                f'<b class="pos">+{play["edge"]:.1f}%</b> edge</span></div>'
                f'<dl><dt>Why</dt><dd>{esc(play["thesis"])}</dd>'
                f'<dt>What kills it</dt><dd>{esc(play["kill"])}</dd></dl>'
                '</div>')
        else:
            body = ('<div class="noact">No position. The prices we can bet '
                    'agree with the sharp number inside our threshold.</div>')

        sheets.append(
            f'<section class="sheet"><div class="hd"><div>'
            f'<span class="idx num">{i:02d}</span>'
            f'<h3>{esc(TEAM_NAME.get(away,away))} at '
            f'{esc(TEAM_NAME.get(home,home))}</h3></div>'
            f'<div class="when">{esc(gday)}{" · " + esc(gtime) if gtime else ""}'
            f'{" · divisional" if div else ""}</div></div>'
            f'<div class="note"><p>{esc(narr)}</p></div>'
            f'<div class="panels">{market_panel}{forecast_panel}'
            f'{profile_panel}</div>{ctx_panel}{proj_panel}{body}</section>')

        slate.append(
            f'<tr><td>{esc(away)} at {esc(home)}</td>'
            f'<td class="num">{sl:+g}</td><td class="num">{tl:g}</td>'
            f'<td class="num">{it_away:.1f}-{it_home:.1f}</td>'
            f'<td class="num">{p_home*100:.0f}%</td>'
            f'<td class="num">{p_over*100:.0f}%</td>'
            f'<td>{"<span class=\'tag play\'>position</span>" if play else "<span class=\'tag none\'>no action</span>"}</td></tr>')

    missing = (
        "Two things are missing from this issue and both matter. There is no "
        "injury information, because no football has been played and the "
        "reports do not exist yet. There is no player-level projection, "
        "because the books have not posted yardage markets this far out. Team "
        "profiles here are last season's, and last season is a weak guide to "
        "Week 1. They describe the matchup; they do not price it."
    )

    return f"""<title>The Sunday Number</title>
<style>{CSS}</style>
<div class="wrap">
  <header class="mast">
    <h1>The Sunday Number</h1>
    <div class="issue">NFL {season} &middot; Week {week}<br>
      Prices as of {esc(captured[:16])} UTC<br>
      Research note &middot; not advice</div>
  </header>
  <div class="strip">
    <div><span class="k">Games</span><span class="v num">{len(games)}</span></div>
    <div><span class="k">Positions</span><span class="v num">{n_play}</span></div>
    <div><span class="k">No action</span><span class="v num">{len(games)-n_play}</span></div>
    <div><span class="k">Books priced</span><span class="v num">4 + sharp</span></div>
  </div>


  <h2 class="sec">The slate</h2>
  <div class="scroll"><table>
    <thead><tr><th>Game</th><th>Spread</th><th>Total</th>
      <th>Implied score</th><th>Home win</th><th>Over</th><th>Status</th></tr></thead>
    <tbody>{''.join(slate)}</tbody>
  </table></div>

  <h2 class="sec">Game sheets</h2>
  {''.join(sheets)}

  <div class="foot">
    <p><b>What is missing.</b> {esc(missing)}</p>
    <p><b>Season context.</b> Power ranks, records, win totals, coaching and
    quarterback changes come from the 2026 season preview. They are BACKGROUND,
    not positions: nothing in that document is treated as a bet here. Their
    purpose is to explain why a team sits where it does at a point in the season
    when our own ratings cannot see roster change. Where the market's win total
    and last season's play disagree sharply, the narrative says so, because that
    gap is the most useful early-season signal available.</p>
    <p><b>Historical usage baseline.</b> This is context, not a forecast. It
    answers "what did these players do last year, and who is on the roster to
    do it now", which is the only honest question available before a snap has
    been played. It becomes a real projection once in-season usage replaces the
    prior, and it will be updated weekly as that happens. Team assignment comes
    from the real 2026 Week 1 roster. Each player's rate is their last six games of 2025, shrunk
    toward a positional baseline, then renormalized across the players actually
    on the roster now, because every target goes to someone. That step matters:
    only 71 per cent of last season's regular skill players are on the same team
    this year, 21 per cent moved and 8 per cent are not rostered at all.
    Efficiency is a positional baseline, not the player's own, because
    yards per target correlates 0.14 week to week and RACR 0.07. Anyone without
    2025 NFL usage, rookies included, gets no projection rather than an invented
    one. These numbers carry no 2026 information at all.</p>
    <p><b>How to read this.</b> Implied score splits the market total by the
    spread. Home win and Over come from our scoring models, both anchored on
    the market's own number. They describe the shape of the outcome, not a
    disagreement with the price. Unit matchup shows each offense against the defense it actually faces, as a percentile of last season's league.</p>
    <p><b>What we price and what we don't.</b> Totals run through a model
    validated to within one percentage point of the market's own over/under
    prices. Spreads are limited to buying the same number at a better price:
    our margin model reproduces the season-long shape of results well but is
    off by four percentage points at any single half-point, which is more
    than any edge it could find, so it is not used to price them. Team ranks are out of 32 across the 2025 season.</p>
    <p>Prices from DraftKings, FanDuel, BetMGM and Caesars, with Pinnacle as
    the sharp reference, vig removed by the power method. Every figure is
    queried from the warehouse. Nothing here is illustrative and nothing is
    advice.</p>
  </div>
</div>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--min-edge", type=float, default=1.0)
    ap.add_argument("-o", "--out", type=Path, required=True)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)
    try:
        a.out.write_text(build(con, a.season, a.week, a.min_edge),
                         encoding="utf-8")
        print(f"wrote {a.out} ({a.out.stat().st_size:,} bytes)")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
