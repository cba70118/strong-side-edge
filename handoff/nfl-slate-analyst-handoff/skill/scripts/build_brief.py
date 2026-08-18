"""
build_brief.py — render the weekly NFL brief as a self-contained HTML one-pager.

Organized game-by-game, with market sub-sections (Side / Total / Prop) inside each
game, plus a market-type summary strip at the top. Editorial rules live in
references/brief-format.md; this file only renders.

    python3 build_brief.py --sample -o sample_brief.html
    python3 build_brief.py week.json -o week07_brief.html

---------------------------------------------------------------------------- SCHEMA

{
  "meta": {
    "title":        "The Sunday Number",       # brief name
    "season":       2026,
    "week":         7,
    "data_as_of":   "Sat Oct 17, 6:40pm CT",   # REQUIRED - staleness is a real risk
    "sample":       false                      # true -> renders a SAMPLE DATA banner
  },
  "season_stats": {
    "record":         "31-27-2",
    "units":          4.8,
    "roi_pct":        3.9,
    "bets":           60,
    "beat_close_pct": 61.7,
    "avg_clv_pts":    1.4                      # probability points
  },
  "lede": "2-4 sentences on the week as a whole.",
  "markets": [                                  # the by-market-type strip
    {"type": "Sides",  "plays": 2, "season_clv": 1.9, "read": "one line"},
    {"type": "Totals", "plays": 1, "season_clv": -0.3, "read": "one line"},
    {"type": "Props",  "plays": 3, "season_clv": 2.6, "read": "one line"}
  ],
  "games": [{
    "kickoff":   "Sun 12:00 CT",
    "away":      "Houston Texans",
    "home":      "Indianapolis Colts",
    "spread":    -3.5,                          # home perspective, negative = home fav
    "total":     45.5,
    "narrative": "2-4 sentences. Market read, football read, the disagreement, the risk.",
    "markets": [{
      "type":      "Side",                      # Side | Total | Prop
      "status":    "play",                      # play | pass
      "selection": "Houston +3.5",
      "price":     -108,
      "book":      "Pinnacle",
      "taken_at":  "Sat 6:05pm",
      "fair":      -124,                        # your fair price, American
      "edge_pct":  3.4,
      "stake_units": 1.1,
      "thesis":    "one sentence",
      "kill":      "one sentence - what makes this wrong before kickoff",
      "reason":    ""                           # used instead of the above when status=pass
    }]
  }],
  "passed": [{"game": "NYJ at NE", "reason": "one line"}],
  "footer_note": "optional extra line"
}
"""

from __future__ import annotations
import argparse
import html
import json
import sys
from pathlib import Path

# ------------------------------------------------------------------ validation

REQUIRED = {
    "meta": ["season", "week", "data_as_of"],
    "season_stats": ["units", "roi_pct", "bets", "beat_close_pct", "avg_clv_pts"],
}
MARKET_TYPES = {"Side", "Total", "Prop"}


def validate(d: dict) -> None:
    errs: list[str] = []
    for section, keys in REQUIRED.items():
        if section not in d:
            errs.append(f"missing top-level section: {section}")
            continue
        for k in keys:
            if k not in d[section]:
                errs.append(f"missing {section}.{k}")
    if not d.get("lede"):
        errs.append("missing lede - the brief needs a written opening")
    for i, g in enumerate(d.get("games", [])):
        for k in ("away", "home", "narrative", "markets"):
            if k not in g:
                errs.append(f"games[{i}] missing {k}")
        if not g.get("narrative", "").strip():
            errs.append(f"games[{i}] has an empty narrative - every game block needs one")
        for j, m in enumerate(g.get("markets", [])):
            if m.get("type") not in MARKET_TYPES:
                errs.append(f"games[{i}].markets[{j}].type must be one of {sorted(MARKET_TYPES)}")
            if m.get("status") == "play":
                for k in ("selection", "price", "book", "thesis", "kill"):
                    if not m.get(k):
                        errs.append(f"games[{i}].markets[{j}] is a play but missing {k}"
                                    + (" - every play needs a kill condition" if k == "kill" else ""))
            elif m.get("status") == "pass":
                if not m.get("reason"):
                    errs.append(f"games[{i}].markets[{j}] is a pass but has no reason")
            else:
                errs.append(f"games[{i}].markets[{j}].status must be 'play' or 'pass'")
    if errs:
        raise SystemExit("Brief JSON is invalid:\n  - " + "\n  - ".join(errs))


# --------------------------------------------------------------------- helpers

def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def am(v) -> str:
    """Format an American price."""
    if v in (None, ""):
        return "—"
    v = float(v)
    return f"{v:+.0f}".replace("+-", "-")


def signed(v, dp=1, suffix="") -> str:
    if v in (None, ""):
        return "—"
    return f"{float(v):+.{dp}f}{suffix}"


def cls_for(v) -> str:
    if v in (None, ""):
        return ""
    return "pos" if float(v) > 0 else ("neg" if float(v) < 0 else "")


MARKET_SLOT = {"Side": "1", "Total": "2", "Prop": "3", "Sides": "1", "Totals": "2", "Props": "3"}


def team_totals(total, spread) -> tuple[float, float] | tuple[None, None]:
    if total in (None, "") or spread in (None, ""):
        return None, None
    return round(total / 2 - spread / 2, 1), round(total / 2 + spread / 2, 1)


# ----------------------------------------------------------------------- CSS

CSS = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
.brief{
  color-scheme:light;
  --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --rule:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a;
  --good:#0ca30c; --crit:#d03b3b; --warn:#fab219; --goodtext:#006300;
  --track:#eceae4;
}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])) .brief{
    color-scheme:dark;
    --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
    --rule:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70;
    --goodtext:#0ca30c; --track:#262624;
  }
}
:root[data-theme="dark"] .brief{
  color-scheme:dark;
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --rule:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70;
  --goodtext:#0ca30c; --track:#262624;
}

.brief{
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
  background:var(--plane); color:var(--ink);
  margin:0; padding:28px 20px 56px; line-height:1.5;
}
.wrap{max-width:940px;margin:0 auto}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:18px 20px;margin-bottom:14px}
h1{font-size:30px;line-height:1.15;margin:0 0 4px;letter-spacing:-.02em}
h2{font-size:12px;letter-spacing:.10em;text-transform:uppercase;color:var(--muted);margin:0 0 12px;font-weight:600}
h3{font-size:17px;margin:0;letter-spacing:-.01em}
p{margin:0 0 10px}
.sub{color:var(--ink2);font-size:13px}
.num{font-variant-numeric:tabular-nums}
.pos{color:var(--goodtext)} .neg{color:var(--crit)}

/* banner */
.banner{background:var(--warn);color:#0b0b0b;border-radius:8px;padding:9px 14px;
  font-size:13px;font-weight:600;margin-bottom:14px;display:flex;gap:9px;align-items:center}

/* masthead */
.mast{display:flex;justify-content:space-between;align-items:flex-end;gap:18px;flex-wrap:wrap;
  border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:16px}
.recline{font-size:13px;color:var(--ink2);text-align:right}
.recline b{color:var(--ink);font-size:15px}
.toggle{background:none;border:1px solid var(--ring);color:var(--ink2);border-radius:6px;
  padding:4px 9px;font-size:11px;cursor:pointer;font-family:inherit}

/* KPI tiles */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:14px}
.kpi{background:var(--surface);border:1px solid var(--ring);border-radius:10px;padding:13px 15px}
.kpi .lab{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);margin-bottom:5px}
.kpi .val{font-size:26px;font-weight:600;letter-spacing:-.02em;line-height:1.1}
.kpi .note{font-size:11px;color:var(--muted);margin-top:3px}
.kpi.flag{border-color:var(--axis);border-width:1px;box-shadow:inset 3px 0 0 var(--s1)}

/* market strip */
.mkt{display:grid;grid-template-columns:auto auto 1fr;gap:12px;align-items:baseline;
  padding:11px 0;border-bottom:1px solid var(--rule)}
.mkt:last-child{border-bottom:none}
.chip{display:inline-flex;align-items:center;gap:7px;font-size:13px;font-weight:600;min-width:74px}
.dot{width:10px;height:10px;border-radius:3px;flex:none}
.d1{background:var(--s1)} .d2{background:var(--s2)} .d3{background:var(--s3)}
.mkt .co{font-size:12px;color:var(--muted);white-space:nowrap}
.mkt .rd{font-size:13px;color:var(--ink2)}

/* game blocks */
.game{background:var(--surface);border:1px solid var(--ring);border-radius:10px;
  padding:16px 18px;margin-bottom:12px}
.ghead{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;
  align-items:baseline;border-bottom:1px solid var(--rule);padding-bottom:10px;margin-bottom:11px}
.kick{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.lines{font-size:12.5px;color:var(--ink2);display:flex;gap:14px;flex-wrap:wrap}
.lines span b{color:var(--ink)}
.narr{font-size:14.5px;color:var(--ink);margin-bottom:13px}

/* market row inside a game */
.row{border-left:3px solid var(--axis);padding:9px 0 9px 13px;margin-bottom:9px}
.row.s1{border-left-color:var(--s1)} .row.s2{border-left-color:var(--s2)} .row.s3{border-left-color:var(--s3)}
.row.passrow{border-left-color:var(--axis);opacity:.72}
.rtop{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:4px}
.tag{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}
.sel{font-size:15px;font-weight:600}
.px{font-size:13px;color:var(--ink2)}
.passtag{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
  border:1px solid var(--axis);border-radius:4px;padding:1px 6px;font-weight:600}
.meta{font-size:12.5px;color:var(--ink2);margin-top:3px}
.meta b{color:var(--ink);font-weight:600}
.kill{font-size:12px;color:var(--muted);margin-top:3px}
.kill::before{content:"Kill: ";font-weight:600;letter-spacing:.03em}

/* edge bar */
.edge{display:flex;align-items:center;gap:8px;margin-top:6px}
.track{width:118px;height:7px;background:var(--track);border-radius:4px;overflow:hidden;flex:none}
.fill{height:100%;border-radius:4px}
.edge .v{font-size:12.5px;font-weight:600}

/* card table */
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);font-weight:600;padding:0 10px 8px 0;border-bottom:1px solid var(--axis)}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:none}
td.n{font-variant-numeric:tabular-nums;white-space:nowrap}

ul.passed{list-style:none;padding:0;margin:0;font-size:13.5px}
ul.passed li{padding:7px 0;border-bottom:1px solid var(--rule);color:var(--ink2)}
ul.passed li:last-child{border-bottom:none}
ul.passed b{color:var(--ink)}

footer{font-size:11.5px;color:var(--muted);margin-top:18px;padding-top:14px;
  border-top:1px solid var(--rule);line-height:1.65}

@media print{
  .brief{background:#fff;--plane:#fff;--surface:#fff;--ink:#000;--ink2:#333;padding:0}
  .toggle{display:none}
  .game,.card,.kpi{break-inside:avoid;border-color:#ccc}
}
@media (max-width:620px){
  .mkt{grid-template-columns:1fr;gap:4px}
  h1{font-size:24px}
  .kpi .val{font-size:22px}
}
"""

TOGGLE_JS = """
(function(){
 var b=document.getElementById('tt');if(!b)return;
 b.addEventListener('click',function(){
   var r=document.documentElement,
       cur=r.getAttribute('data-theme'),
       dark=cur?cur==='dark':matchMedia('(prefers-color-scheme:dark)').matches;
   r.setAttribute('data-theme',dark?'light':'dark');
 });
})();
"""


# ------------------------------------------------------------------- rendering

def edge_bar(edge_pct, slot: str, scale: float = 8.0) -> str:
    if edge_pct in (None, ""):
        return ""
    v = float(edge_pct)
    pct = min(abs(v) / scale, 1.0) * 100
    color = f"var(--s{slot})" if v > 0 else "var(--crit)"
    return (f'<div class="edge"><div class="track">'
            f'<div class="fill" style="width:{pct:.0f}%;background:{color}"></div></div>'
            f'<span class="v num {cls_for(v)}">{signed(v, 1, "%")} edge</span></div>')


def render_market_row(m: dict) -> str:
    slot = MARKET_SLOT.get(m.get("type", ""), "1")
    if m.get("status") == "pass":
        return (f'<div class="row passrow"><div class="rtop">'
                f'<span class="tag">{e(m.get("type"))}</span>'
                f'<span class="passtag">Pass</span></div>'
                f'<div class="meta">{e(m.get("reason"))}</div></div>')
    parts = [f'<div class="row s{slot}"><div class="rtop">',
             f'<span class="tag">{e(m.get("type"))}</span>',
             f'<span class="sel">{e(m.get("selection"))}</span>',
             f'<span class="px num">{am(m.get("price"))} · {e(m.get("book"))}</span>',
             '</div>']
    meta = []
    if m.get("fair") not in (None, ""):
        meta.append(f'fair <b class="num">{am(m["fair"])}</b>')
    if m.get("stake_units") not in (None, ""):
        meta.append(f'stake <b class="num">{float(m["stake_units"]):.2f}u</b>')
    if m.get("taken_at"):
        meta.append(f'taken {e(m["taken_at"])}')
    if meta:
        parts.append(f'<div class="meta">{" · ".join(meta)}</div>')
    parts.append(edge_bar(m.get("edge_pct"), slot))
    if m.get("thesis"):
        parts.append(f'<div class="meta">{e(m["thesis"])}</div>')
    if m.get("kill"):
        parts.append(f'<div class="kill">{e(m["kill"])}</div>')
    parts.append('</div>')
    return "".join(parts)


def render_game(g: dict) -> str:
    hm, aw = team_totals(g.get("total"), g.get("spread"))
    lines = []
    if g.get("spread") not in (None, ""):
        lines.append(f'<span>Spread <b class="num">{e(g["home"])} {signed(g["spread"])}</b></span>')
    if g.get("total") not in (None, ""):
        lines.append(f'<span>Total <b class="num">{float(g["total"]):.1f}</b></span>')
    if hm is not None:
        lines.append(f'<span>Implied <b class="num">{e(g["away"])} {aw} · {e(g["home"])} {hm}</b></span>')
    rows = "".join(render_market_row(m) for m in g.get("markets", []))
    return f"""<div class="game">
 <div class="ghead">
  <div><div class="kick">{e(g.get("kickoff",""))}</div>
   <h3>{e(g.get("away"))} at {e(g.get("home"))}</h3></div>
  <div class="lines">{"".join(lines)}</div>
 </div>
 <p class="narr">{e(g.get("narrative"))}</p>
 {rows}
</div>"""


def render_card_table(games: list) -> str:
    rows = []
    for g in games:
        for m in g.get("markets", []):
            if m.get("status") != "play":
                continue
            rows.append(f"""<tr>
 <td>{e(g.get("away"))} @ {e(g.get("home"))}</td>
 <td><span class="dot d{MARKET_SLOT.get(m.get("type"),"1")}" style="display:inline-block;
   margin-right:6px;vertical-align:middle"></span>{e(m.get("type"))}</td>
 <td><b>{e(m.get("selection"))}</b></td>
 <td class="n">{am(m.get("price"))}</td>
 <td class="n">{e(m.get("book"))}</td>
 <td class="n">{am(m.get("fair"))}</td>
 <td class="n {cls_for(m.get("edge_pct"))}">{signed(m.get("edge_pct"),1,"%")}</td>
 <td class="n">{float(m["stake_units"]):.2f}u</td>
</tr>""")
    if not rows:
        return '<p class="sub">No plays this week. That is a result, not a failure.</p>'
    return f"""<table><thead><tr>
 <th>Game</th><th>Market</th><th>Play</th><th>Price</th><th>Book</th>
 <th>Fair</th><th>Edge</th><th>Stake</th></tr></thead>
 <tbody>{"".join(rows)}</tbody></table>"""


def build(d: dict) -> str:
    validate(d)
    meta, ss = d["meta"], d["season_stats"]
    title = meta.get("title", "The Weekly Number")

    banner = ""
    if meta.get("sample"):
        banner = ('<div class="banner">⚠ SAMPLE DATA — every line, price, and figure below '
                  'is illustrative. Do not bet from this page.</div>')

    kpis = [
        ("Units", signed(ss["units"], 2), ss.get("record", ""), cls_for(ss["units"]), False),
        ("ROI", signed(ss["roi_pct"], 1, "%"), f'{ss["bets"]} bets', cls_for(ss["roi_pct"]), False),
        ("Beat the close", f'{float(ss["beat_close_pct"]):.1f}%', "the real scorecard",
         "pos" if float(ss["beat_close_pct"]) > 50 else "neg", True),
        ("Avg CLV", signed(ss["avg_clv_pts"], 2, " pts"), "probability points",
         cls_for(ss["avg_clv_pts"]), True),
    ]
    kpi_html = "".join(
        f'<div class="kpi{" flag" if flag else ""}"><div class="lab">{e(l)}</div>'
        f'<div class="val num {c}">{v}</div><div class="note">{e(n)}</div></div>'
        for l, v, n, c, flag in kpis)

    mkt_html = "".join(
        f'<div class="mkt"><span class="chip"><span class="dot d{MARKET_SLOT.get(m["type"],"1")}"></span>'
        f'{e(m["type"])}</span>'
        f'<span class="co num">{m.get("plays",0)} play(s) · season CLV '
        f'<span class="{cls_for(m.get("season_clv"))}">{signed(m.get("season_clv"),1," pts")}</span></span>'
        f'<span class="rd">{e(m.get("read",""))}</span></div>'
        for m in d.get("markets", []))

    games_html = "".join(render_game(g) for g in d.get("games", []))
    passed_html = "".join(
        f'<li><b>{e(p.get("game"))}</b> — {e(p.get("reason"))}</li>' for p in d.get("passed", []))

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} — {e(meta["season"])} Week {e(meta["week"])}</title>
<style>{CSS}</style></head>
<body class="brief"><div class="wrap">
{banner}
<div class="mast">
 <div>
  <h1>{e(title)}</h1>
  <div class="sub">{e(meta["season"])} · Week {e(meta["week"])} · data as of {e(meta["data_as_of"])}</div>
 </div>
 <div class="recline">
  <div><b class="num">{e(ss.get("record","—"))}</b> season</div>
  <div class="num">{signed(ss["units"],2)}u · {signed(ss["roi_pct"],1,"%")} ROI</div>
  <button class="toggle" id="tt" type="button">Toggle theme</button>
 </div>
</div>

<div class="kpis">{kpi_html}</div>

<div class="card"><h2>The Week</h2><p>{e(d["lede"])}</p></div>

<div class="card"><h2>By Market</h2>{mkt_html}</div>

<h2 style="margin:22px 0 12px">The Board</h2>
{games_html}

{'<div class="card"><h2>Passed On Entirely</h2><ul class="passed">' + passed_html + '</ul></div>' if passed_html else ''}

<div class="card"><h2>The Card</h2>{render_card_table(d.get("games", []))}</div>

<footer>
 Every play above is graded at the <b>closing line</b>, not at the final score. A week that
 beat the close and lost is a good week; a week that won against every closing number was luck.
 Prices shown are what was available at the book and time listed — shop your own number before
 you take it.<br>
 {e(d.get("footer_note",""))}<br>
 Generated {e(meta.get("data_as_of"))}. For entertainment and analysis. Bet only what you can
 afford to lose. If gambling stops being fun, call 1-800-GAMBLER.
</footer>
</div><script>{TOGGLE_JS}</script></body></html>"""


# ------------------------------------------------------------------- sample

SAMPLE = {
    "meta": {"title": "The Sunday Number", "season": 2026, "week": 7,
             "data_as_of": "Sat Oct 17, 6:40pm CT", "sample": True},
    "season_stats": {"record": "31-27-2", "units": 4.82, "roi_pct": 3.9, "bets": 60,
                     "beat_close_pct": 61.7, "avg_clv_pts": 1.4},
    "lede": ("The board tightened all week and there is not much left on sides — books opened "
             "close and the market agreed with them, which is usually a sign to look elsewhere. "
             "Elsewhere this week is receiving props, where three games have injury-driven target "
             "redistribution that the prop lines have moved maybe half as much as they should. "
             "Two plays, both unders on yardage, both for the same structural reason. We're passing "
             "on eight of eleven games entirely."),
    "markets": [
        {"type": "Sides", "plays": 1, "season_clv": 1.9,
         "read": "Efficient board. One play, and it's a stale-number grab rather than a model call."},
        {"type": "Totals", "plays": 1, "season_clv": -0.4,
         "read": "Season CLV is negative here — we are not good at totals yet. Sizing stays small until that turns."},
        {"type": "Props", "plays": 2, "season_clv": 2.6,
         "read": "Best CLV of any market by a wide margin. This is where the process is actually working."},
    ],
    "games": [
        {"kickoff": "Sun 12:00 CT", "away": "Houston Texans", "home": "Indianapolis Colts",
         "spread": -2.5, "total": 44.5,
         "narrative": ("The market has Indianapolis as a field-goal-ish home favorite, which prices "
                       "these two as near-equals with home field doing the work. Our read agrees on "
                       "the teams and disagrees on the pass rush: Houston's pressure rate over the "
                       "last five games is well clear of where it sat early, and Indianapolis is "
                       "starting a backup left tackle for the third straight week. Pressure rate "
                       "stabilizes fast enough that five games is a real sample, and the line has not "
                       "moved on the tackle news. The risk is obvious — if the tackle is activated "
                       "and plays, the reason for this bet walks onto the field with him."),
         "markets": [
             {"type": "Side", "status": "play", "selection": "Houston +2.5", "price": -105,
              "book": "Pinnacle", "taken_at": "Sat 6:05pm", "fair": -122, "edge_pct": 3.6,
              "stake_units": 1.10,
              "thesis": "Backup LT for a third week is not in the number; Houston's pressure rate is real over a stabilized sample.",
              "kill": "Starting LT upgraded to full participation, or the line moves to +1.5 before kickoff."},
             {"type": "Total", "status": "pass",
              "reason": "44.5 sits right on our number. No disagreement, no bet."},
             {"type": "Prop", "status": "pass",
              "reason": "Receiving lines already repriced after Wednesday's report — we're late."},
         ]},
        {"kickoff": "Sun 12:00 CT", "away": "Green Bay Packers", "home": "Chicago Bears",
         "spread": 1.5, "total": 41.5,
         "narrative": ("A 41.5 in Chicago in late October is the market telling you it expects wind, "
                       "and the forecast currently has sustained 18-22 mph off the lake. That is the "
                       "one weather condition with a consistent, sizable effect on passing and "
                       "kicking, and it is the rare case where we think the market has under-adjusted "
                       "rather than over-adjusted. Both offenses are middling in neutral-script "
                       "passing efficiency to begin with. The risk is that the forecast softens — wind "
                       "predictions inside 24 hours move a lot, and if it drops under 12 this is a "
                       "no-bet, not a smaller bet."),
         "markets": [
             {"type": "Total", "status": "play", "selection": "Under 41.5", "price": -110,
              "book": "FanDuel", "taken_at": "Sat 6:12pm", "fair": -128, "edge_pct": 3.1,
              "stake_units": 0.60,
              "thesis": "Sustained 18-22 mph wind is the one weather effect that reliably suppresses scoring, and 41.5 has not fully absorbed it.",
              "kill": "Forecast wind drops below 12 mph sustained at kickoff."},
             {"type": "Side", "status": "pass",
              "reason": "Pick-em game between two teams we rate as a pick-em. Nothing to say."},
             {"type": "Prop", "status": "pass",
              "reason": "Correlated with the under we already have. Sizing it separately would double the same bet."},
         ]},
        {"kickoff": "Sun 3:25 CT", "away": "Los Angeles Chargers", "home": "Denver Broncos",
         "spread": -1.5, "total": 47.5,
         "narrative": ("This is the props game. Denver's WR1 is out, and the market has moved the "
                       "obvious beneficiary — the WR2 — while barely touching the tight end, who "
                       "actually absorbed the larger share of vacated targets the last time this "
                       "happened. But the play here is the other direction: the Chargers' RB "
                       "receiving line is priced off a mean projection, and receiving yards are "
                       "right-skewed enough that a mean clearing the line still leaves the under as "
                       "the correct side. Our distribution puts P(over) at 0.46 against a line priced "
                       "near even. The risk is script — if Denver jumps ahead early, checkdown volume "
                       "goes up and the skew argument weakens."),
         "markets": [
             {"type": "Prop", "status": "play", "selection": "LAC RB Under 34.5 receiving yards",
              "price": -112, "book": "DraftKings", "taken_at": "Sat 6:20pm", "fair": -132,
              "edge_pct": 3.8, "stake_units": 0.75,
              "thesis": "Gamma-fit distribution puts P(over) at 0.46 despite a mean that clears the line — the market is pricing the mean.",
              "kill": "Denver opens as a larger favorite than -3, which flips the script assumption."},
             {"type": "Prop", "status": "play", "selection": "DEN TE Over 3.5 receptions",
              "price": +104, "book": "Caesars", "taken_at": "Sat 6:22pm", "fair": -118,
              "edge_pct": 5.1, "stake_units": 0.85,
              "thesis": "Vacated target share historically flows to this TE, not the WR2 the market repriced; receptions are far less skewed than yards, so the over is live here.",
              "kill": "TE appears on the injury report Saturday night, or the line moves to 4.5."},
             {"type": "Side", "status": "pass",
              "reason": "Our number is Denver -2. The market is at -1.5. That is noise, not edge."},
             {"type": "Total", "status": "pass",
              "reason": "47.5 matches our projection within half a point."},
         ]},
    ],
    "passed": [
        {"game": "NYJ at NE", "reason": "Both offenses below our confidence threshold to project at all. No number we trust."},
        {"game": "MIA at BUF", "reason": "Line moved three points on Thursday's news. Whatever was there is gone."},
        {"game": "SEA at ARI", "reason": "Our spread and the market's agree to within 0.4 points."},
        {"game": "TB at ATL", "reason": "Divisional rematch with a QB questionable — waiting for inactives was correct and we did not get the number."},
    ],
    "footer_note": "Prices shopped across five books. Best available number shown; we took the best one.",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_file", nargs="?", help="week JSON (omit with --sample)")
    ap.add_argument("--sample", action="store_true", help="render the demo week")
    ap.add_argument("-o", "--out", default="brief.html")
    a = ap.parse_args()

    if a.sample:
        data = SAMPLE
    elif a.json_file:
        data = json.loads(Path(a.json_file).read_text())
    else:
        ap.error("give a JSON file or use --sample")

    Path(a.out).write_text(build(data))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
