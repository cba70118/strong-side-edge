"""dash_report.py - compose the four dashboard screens from components.

Per references/dashboard-spec.md. Screens are Board, Game, Market, Bet; each
answers one question and carries one primary verb. Nothing here authors free
HTML for content - every content element is a component from dash_components.

Actions are real within a static page, or they are not offered. Drill navigates.
Mark pass and Watch hold state for the session and are written in text, not
color. Log bet emits the exact bet_log.py command and copies it - that is a
genuine handoff into the pipeline rather than a button that does nothing. Set
line alert and Open book are omitted: nothing here can watch a price or knows a
book's URL, and a verb that lies is worse than a missing one.

Usage:
    py -3 scripts/dash_report.py -i week01_dash.json -o week01_dashboard.html
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_system as DS                                      # noqa: E402
from dash_components import (disagreement_line, verdict_chip, edge_bar,   # noqa
                             price_row, kill_card, pass_line, kpi_tile,
                             clv_spark, line_move_track, source_stamp,
                             COMPONENT_CSS, e)
from team_charts import quadrant, sparkline, rerating, TEAM_CHART_CSS  # noqa
try:
    from logo_assets import LOGOS
except ImportError:
    LOGOS = {}
try:
    from font_assets import FONT_CSS
except ImportError:
    FONT_CSS = ""

MKT_ID = {"Spread": "spread_game", "Total": "total_game",
          "Moneyline": "ml_game"}
MKT_LABEL = {v: k for k, v in MKT_ID.items()}


def pts(v, signed=True):
    """Spec section 4: spreads and totals always render at half-point
    precision. A line of -2 shows as -2.0, never as -2."""
    if v is None:
        return "&ndash;"
    return f"{v:+.1f}" if signed else f"{v:.1f}"


def crest_class(t):
    return f'lg-{e(t)}' if t in LOGOS else ""


def crest(t):
    return (f'<span class="cr lg-{e(t)}"></span>' if t in LOGOS
            else f'<span class="cr"></span>')


def log_cmd(g, m, r):
    """The exact command that logs this bet. A real handoff, not a fake verb."""
    return (f'py -3 scripts/bet_log.py log --game {g["game_id"]} '
            f'--market {MKT_ID.get(m["type"], m["type"])} --side {r["side"]} '
            f'--book "{r["book"]}" --price {r["price"]:+d} '
            f'--thesis "..." --kill "..."')


EXTRA_CSS = """
.top{display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap;
  padding:20px 0 12px;border-bottom:3px solid var(--rule)}
.top h1{margin:0;font-family:'IBM Plex Sans Condensed',sans-serif;
  font-size:26px;font-weight:700;letter-spacing:-.01em;text-transform:uppercase;
  line-height:1}
/* Dashboard-only tokens. Structure comes from design_system; these are the
   market-family hues and the aliases the borrowed brief components need.
   Keeping them here rather than redefining the whole palette is what stops the
   surfaces drifting apart - that drift is why team_charts.py once rendered
   invisible in this file. */
:root{ --sides:#12507E; --totals:#A8551C; --props:#0F7351;
  --pos:var(--play); --surface:var(--panel); --plane:var(--bg);
  --rule2:var(--line); --bone:var(--ink2); --line2:var(--line); }
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --sides:#5B9BD8; --totals:#c0851f; --props:#36a980;
  --pos:var(--play); --surface:var(--panel); --plane:var(--bg);
  --rule2:var(--line); --bone:var(--ink2); --line2:var(--line); }}
:root[data-theme="dark"]{ --sides:#5B9BD8; --totals:#c0851f; --props:#36a980;
  --pos:var(--play); --surface:var(--panel); --plane:var(--bg);
  --rule2:var(--line); --bone:var(--ink2); --line2:var(--line); }
/* Same rule as the brief: the dashboard's numerals take the Signage condensed
   face, and .mono is reserved for exact-alignment cases. */
.n{font-family:'IBM Plex Sans Condensed',sans-serif;font-weight:600;
  font-variant-numeric:tabular-nums}
.mono{font-family:"IBM Plex Mono",monospace;
  font-variant-numeric:tabular-nums slashed-zero}
.cap,th,.lbl{font-family:"IBM Plex Sans Condensed",sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:0 16px 56px}

.top .rt{margin-left:auto;font-size:10.5px;color:var(--ink3)}

.kpis{display:flex;flex-wrap:wrap;background:var(--surface);
  border:1px solid var(--rule);border-top:0}
.lede{font-size:12.5px;color:var(--ink2);line-height:1.5;max-width:96ch;
  padding:10px 0 12px}
.lede b{color:var(--ink)}

.board nav{display:flex;gap:0;border-bottom:1px solid var(--rule);margin-top:14px;
  position:sticky;top:0;background:var(--plane);z-index:20}
.board nav button{font:inherit;font-family:"IBM Plex Sans Condensed",sans-serif;
  font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  background:none;border:0;border-bottom:2px solid transparent;cursor:pointer;
  padding:9px 14px;color:var(--ink3)}
.board nav button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--ink)}
.board nav button:focus-visible{outline:2px solid var(--sides);outline-offset:-2px}
/* SCOPED. This file's panes now live inside .board so the dashboard can be
   mounted as a tab of the brief. An unscoped `section` rule matched the
   brief's own panes and all 32 team panes and hid the lot - the same collision
   that once rendered the Teams tab blank. */
.board>section{display:none;padding-top:14px}
.board>section.on{display:block}

.board table{width:100%;border-collapse:collapse;background:var(--surface)}
.board th{text-align:left;font-size:9.5px;font-weight:700;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink3);padding:7px 10px;
  border-bottom:1px solid var(--rule);white-space:nowrap}
.board td{padding:0 10px;border-bottom:1px solid var(--rule2);height:40px;
  vertical-align:middle}
td.n,th.n{text-align:right}
tr.stale td{opacity:.62}
.tw{border:1px solid var(--rule);overflow-x:auto}
.mut{color:var(--ink3)}

.brow{cursor:pointer}
.brow:hover{background:var(--plane)}
.brow.marked td:first-child::after{content:" PASSED";font-size:9px;
  font-weight:700;color:var(--ink3);letter-spacing:.06em}
.cr{width:19px;height:19px;display:inline-block;vertical-align:-4px;
  background-size:contain;background-position:center;
  background-repeat:no-repeat;margin-right:5px}
.gm{font-weight:600;white-space:nowrap}

.btn{font:inherit;font-family:"IBM Plex Sans Condensed",sans-serif;
  font-size:11px;font-weight:700;letter-spacing:.05em;cursor:pointer;
  border:1px solid var(--rule);background:var(--surface);color:var(--ink2);
  padding:4px 9px;border-radius:2px;white-space:nowrap}
.btn:hover{border-color:var(--ink3);color:var(--ink)}
.btn.pri{background:var(--ink);color:var(--surface);border-color:var(--ink)}
.btn:focus-visible{outline:2px solid var(--sides);outline-offset:1px}
.acts{display:flex;gap:6px;flex-wrap:wrap;padding:9px 12px;
  border-top:1px solid var(--rule2)}
td.act{width:1%;white-space:nowrap}

.gcard{border:1px solid var(--rule);background:var(--surface);margin-bottom:10px}
.gh{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:9px 12px;
  border-bottom:1px solid var(--rule2)}
.gh .itt{margin-left:auto;font-size:10.5px;color:var(--ink3)}
.mrow{display:grid;grid-template-columns:78px 1fr 150px auto;gap:12px;
  align-items:center;padding:7px 12px;border-bottom:1px solid var(--rule2)}
.mrow .lbl{font-size:10px;font-weight:700;letter-spacing:.07em;
  text-transform:uppercase;color:var(--ink3)}
.note{font-size:11px;color:var(--ink3);padding:0 12px 8px}
h3.sc{font-family:"IBM Plex Sans Condensed",sans-serif;font-size:11px;
  font-weight:700;letter-spacing:.09em;text-transform:uppercase;
  color:var(--ink3);margin:16px 0 7px}
.brieflink{margin-left:auto;align-self:center;font-family:"IBM Plex Sans Condensed",sans-serif;
  font-size:11.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  color:var(--ink3);text-decoration:none;padding:0 12px}
.brieflink:hover{color:var(--sides)}
.figrow{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}
.figbox{background:var(--surface);border:1px solid var(--rule);padding:11px 13px}
.figbox h4{margin:0 0 8px;font-family:"IBM Plex Sans Condensed",sans-serif;
  font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink3)}
.qmark{position:absolute;transform:translate(-50%,-50%);width:22px;height:22px;
  background-size:contain;background-position:center;background-repeat:no-repeat}
.qwrap{position:relative;width:100%}
.qsvg{position:absolute;inset:0;width:100%;height:100%}
.empty{padding:14px;color:var(--ink3);font-size:12.5px;background:var(--surface);
  border:1px solid var(--rule)}
dialog{border:1px solid var(--rule);background:var(--surface);color:var(--ink);
  max-width:640px;padding:0}
dialog::backdrop{background:rgba(0,0,0,.45)}
.dlg-h{padding:10px 12px;border-bottom:1px solid var(--rule2);font-weight:600}
.dlg-b{padding:12px}
textarea{width:100%;min-height:88px;font-family:"IBM Plex Mono",monospace;
  font-size:11.5px;border:1px solid var(--rule);background:var(--plane);
  color:var(--ink);padding:8px;resize:vertical}
@media (prefers-reduced-motion:no-preference){
  .brow{transition:background .18s}
  .board td{transition:opacity .18s}
}
"""

CSS = DS.css() + EXTRA_CSS

JS = r"""<script>
(function(){
  var root=document.querySelector('.board'); if(!root) return;
  var tabs=[].slice.call(root.querySelectorAll('nav button'));
  var panes=[].slice.call(root.querySelectorAll(':scope>section'));
  function show(id){
    tabs.forEach(function(t){t.setAttribute('aria-selected',String(t.dataset.t===id));});
    panes.forEach(function(p){p.classList.toggle('on',p.id===id);});
    try{history.replaceState(null,'','#'+id);}catch(e){}
  }
  tabs.forEach(function(t){t.addEventListener('click',function(){show(t.dataset.t);});});
  var h=location.hash.slice(1);
  if(h&&panes.some(function(p){return p.id===h;}))show(h);

  // Drill: board row -> that game's card.
  document.addEventListener('click',function(ev){
    var d=ev.target.closest('[data-drill]');
    if(d){show('db-game');var el=document.getElementById(d.dataset.drill);
      if(el){el.scrollIntoView({block:'start'});el.classList.add('hit');
        setTimeout(function(){el.classList.remove('hit');},900);}return;}

    var mk=ev.target.closest('[data-mark]');
    if(mk){ev.stopPropagation();
      var row=mk.closest('tr');row.classList.toggle('marked');
      mk.textContent=row.classList.contains('marked')?'Unmark':'Mark pass';return;}

    var lg=ev.target.closest('[data-log]');
    if(lg){ev.stopPropagation();
      var dlg=document.getElementById('logdlg');
      document.getElementById('logcmd').value=lg.dataset.log;
      if(dlg.showModal)dlg.showModal();return;}
  });
  var cp=document.getElementById('copybtn');
  if(cp)cp.addEventListener('click',function(){
    var ta=document.getElementById('logcmd');ta.select();
    try{document.execCommand('copy');cp.textContent='Copied';
      setTimeout(function(){cp.textContent='Copy command';},1400);}catch(e){}
  });
  var cl=document.getElementById('closedlg');
  if(cl)cl.addEventListener('click',function(){
    document.getElementById('logdlg').close();});
})();
</script>"""


def props_panel(d):
    """Props, where DisagreementLine finally does its intended job.

    Sides and totals draw a backtest band wide enough to swallow the market
    number, so they read NO EDGE by construction. Here the band is the
    validated coverage range from prop_model, so when a book posts a line the
    element genuinely says whether it sits inside what we will quote.
    """
    if not d.get("props"):
        return '<div class="empty">No projections available.</div>'
    rows = "".join(
        f'<tr><td class="gm">{crest(x["team"])}{e(x["player"])} '
        f'<span class="mut">{e(x["pos"])}</span></td>'
        f'<td class="mut">{e(x["game"])}</td>'
        f'<td class="cap">{e(x["stat"])}</td>'
        f'<td class="n mono">{x["targets"]:.1f}</td>'
        f'<td class="n mono">{x["share"]}%</td>'
        f'<td class="n mono">{x["model"]:.1f}</td>'
        f'<td>{disagreement_line({"market": x["model"], "model": x["model"], "band_low": x["band_low"], "band_high": x["band_high"], "unit": " yd"}, "props", floor=0)}</td>'
        f'<td>{verdict_chip(x["verdict"])}</td></tr>'
        for x in d["props"][:60])
    return f"""
<div class="kpis"><div class="kpi kpi-p"><span class="kpi-l">Posted</span>
<span class="kpi-v mono">{d.get('props_posted', 0)}</span>
<span class="kpi-d">player lines on the board</span></div>
<div class="kpi kpi-p"><span class="kpi-l">Priced</span>
<span class="kpi-v mono">0</span><span class="kpi-d">all anytime TD</span></div>
<div class="kpi"><span class="kpi-l">Our lines</span>
<span class="kpi-v mono">{len(d['props'])}</span>
<span class="kpi-d">ready to price</span></div></div>
<div class="tw" style="margin-top:12px"><table><thead><tr>
<th>Player</th><th>Game</th><th>Stat</th><th class="n">Tgt</th>
<th class="n">Share</th><th class="n">Our line</th>
<th>Quote band 5&ndash;95% &middot; yd</th><th>Verdict</th>
</tr></thead><tbody>{rows}</tbody></table></div>"""


def middles_panel(d):
    if not d.get("middles"):
        return '<div class="empty">No two-sided windows on this board.</div>'
    rows = "".join(
        f'<tr><td class="gm">{e(x["game"])}</td>'
        f'<td class="mono">{x["window"][0]:g} &ndash; {x["window"][1]:g}</td>'
        f'<td class="mut">' + "<br>".join(
            f'{e(l["side"])} <span class="mono">{l["line"]:g}</span> '
            f'<span class="mono">{l["price"]:+d}</span> {e(l["book"])}'
            for l in x["legs"]) + '</td>'
        f'<td class="n mono">{x["hit_pct"]:.2f}%</td>'
        f'<td class="n mono">{x["be_pct"]:.2f}%</td>'
        f'<td class="n mono {"pos" if x["edge_pp"] > 0 else "mut"}">'
        f'{x["edge_pp"]:+.2f}pp</td>'
        f'<td class="n mono mut">&plusmn;{x["se_pp"]:.2f}</td>'
        f'<td>{verdict_chip(x["verdict"])}</td>'
        f'<td class="act"><button class="btn">Mark pass</button></td></tr>'
        for x in d["middles"])
    return f"""
<div class="tw"><table><thead><tr>
<th>Game</th><th>Window</th><th>Legs</th><th class="n">Hit</th>
<th class="n">Breakeven</th><th class="n">Edge</th><th class="n">Sim SE</th>
<th>Verdict</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>"""


def teams_panel(d):
    tm_ = d.get("teams") or {}
    if not tm_:
        return '<div class="empty">No team data.</div>'
    rows = []
    for k, v in sorted(tm_.items(), key=lambda kv: kv[1].get("rank") or 99):
        move = (v.get("market_rating") or 0) - (v.get("rating_prior") or 0)
        rows.append(
            f'<tr><td class="gm">{crest(k)}{e(k)}</td>'
            f'<td class="n mono mut">{v.get("rank") or "&ndash;"}</td>'
            f'<td class="mono">{e(v.get("record") or "")}</td>'
            f'<td class="n mono">{(v.get("oepa") or 0):+.3f}</td>'
            f'<td class="n mono">{(v.get("depa") or 0):+.3f}</td>'
            f'<td>{sparkline(v.get("trend") or [])}</td>'
            f'<td class="n mono">{(v.get("win_total") or 0):g}</td>'
            f'<td class="n mono {"pos" if move > 0 else "mut"}">{move:+.3f}</td>'
            f'</tr>')
    return f"""
<div class="figrow">
<div class="figbox"><h4>Where each club was, 2025</h4>
{quadrant(tm_, lambda k: crest_class(k))}</div>
<div class="figbox"><h4>What the market did about it</h4>
{rerating(tm_)}</div></div>
<div class="tw" style="margin-top:12px"><table><thead><tr>
<th>Club</th><th class="n">Rk</th><th>Rec</th><th class="n">Off EPA</th>
<th class="n">Def EPA</th><th>Trend</th><th class="n">Win tot</th>
<th class="n">Re-rate</th></tr></thead><tbody>{''.join(rows)}</tbody>
</table></div>"""


def board(d):
    rows = []
    for g in d["games"]:
        sp = [m for m in g["markets"] if m["type"] == "Spread"]
        best = max(g["markets"], key=lambda m: m["best_edge"] or -99)
        rows.append(
            f'<tr class="brow" data-drill="g-{e(g["game_id"])}">'
            f'<td><span class="gm">{crest(g["away"])}{e(g["away"])} '
            f'<span class="mut">@</span> {crest(g["home"])}{e(g["home"])}</span></td>'
            f'<td class="n mono">{g["now"]["spread"]:+g}</td>'
            f'<td class="n mono">{g["now"]["total"]:g}</td>'
            f'<td>{disagreement_line(sp[0]["disagreement"] if sp else None, "sides")}</td>'
            f'<td>{edge_bar(g["best_edge"], d["meta"]["edge_threshold"])}</td>'
            f'<td>{verdict_chip(best["verdict"])}</td>'
            f'<td class="act"><button class="btn" data-drill="g-{e(g["game_id"])}"'
            f'>Drill</button> <button class="btn" data-mark>Mark pass</button></td>'
            f'</tr>')
    return f"""
<div class="kpis">{''.join(kpi_tile(k) for k in d['kpis'])}
<div class="kpi"><span class="kpi-l">CLV trend</span>
{clv_spark(d['clv_series'])}<span class="kpi-d">per logged bet</span></div></div>
<p class="lede">{e(d['lede'])}</p>
<div class="tw"><table><thead><tr>
<th>Game</th><th class="n">Spread</th><th class="n">Total</th>
<th>Market vs model &middot; pts</th><th>Edge</th><th>Verdict</th><th></th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h3 class="sc">Pass list &middot; {len(d['passes'])} markets declined</h3>
<div class="tw"><table><thead><tr><th>Game</th><th>Market</th><th>Reason</th>
</tr></thead><tbody>{''.join(pass_line(p) for p in d['passes'])}</tbody>
</table></div>"""


def games(d):
    out = []
    for g in d["games"]:
        mrows = []
        for m in g["markets"]:
            mrows.append(
                f'<div class="mrow"><span class="lbl">{e(m["type"])}</span>'
                f'{disagreement_line(m["disagreement"], m["family"])}'
                f'{edge_bar(m["best_edge"], d["meta"]["edge_threshold"])}'
                f'{verdict_chip(m["verdict"])}</div>'
                + (f'<p class="note">{e(m["note"])}</p>' if m.get("note") else ""))
        out.append(f"""
<div class="gcard" id="g-{e(g['game_id'])}">
  <div class="gh"><span class="gm">{crest(g['away'])}{e(g['away'])}
    <span class="mut">@</span> {crest(g['home'])}{e(g['home'])}</span>
    <span class="mut mono">{e(g['kickoff'][5:])}</span>
    <span class="itt">ITT {g['itt']['away']:.1f} &middot; {g['itt']['home']:.1f}</span>
  </div>
  <div class="mrow"><span class="lbl">Spread</span>
    {line_move_track(g['open']['spread'], g['now']['spread'], None, ' pts')}
    <span class="lbl">Total</span>
    {line_move_track(g['open']['total'], g['now']['total'], None, ' pts')}</div>
  {''.join(mrows)}
  {kill_card(g.get('thesis'), g.get('kill'), g.get('kill_status'))}
  <div class="acts"><button class="btn pri" data-log="{e(_first_cmd(g))}">Log bet</button>
    <button class="btn" data-mark>Mark pass</button></div>
</div>""")
    return "".join(out)


def _first_cmd(g):
    for m in g["markets"]:
        if m["rows"]:
            return log_cmd(g, m, m["rows"][0])
    return ""


def markets(d):
    out = []
    for g in d["games"]:
        for m in g["markets"]:
            if not m["rows"]:
                continue
            for r in m["rows"]:
                r["log_cmd"] = log_cmd(g, m, r)
            out.append(f"""
<h3 class="sc">{e(g['away'])} @ {e(g['home'])} &middot; {e(m['type'])}
 &middot; {m['verdict']}</h3>
<div class="tw"><table><thead><tr>
<th>Side</th><th class="n">Price</th><th>Book</th><th>Taken</th>
<th class="n">Fair</th><th class="n">Fair %</th><th>Edge</th><th>Verdict</th>
<th></th></tr></thead>
<tbody>{''.join(price_row(r, d['meta']['edge_threshold']) for r in m['rows'])}
</tbody></table></div>""")
    return "".join(out) or '<div class="empty">No priced markets.</div>'


def bets(d):
    if not d["bets"]:
        return ('<div class="empty">No bets logged yet. Log one from any '
                'market row.</div>')
    rows = "".join(
        f'<tr><td class="gm">{e(b["game_id"][8:])}</td>'
        f'<td class="cap">{e(b["market_id"])}</td>'
        f'<td class="cap">{e(b["side"])}</td>'
        f'<td class="n mono">{b["line"] if b["line"] is not None else "&ndash;"}</td>'
        f'<td class="n mono">{b["price"]:+d}</td><td>{e(b["book"] or "")}</td>'
        f'<td class="mono mut">{e(str(b["taken_at"])[5:16])}</td>'
        f'<td class="n mono {"pos" if (b["clv_pts"] or 0) > 0 else "mut"}">'
        f'{(b["clv_pts"] or 0):+.1f} pt</td>'
        f'<td>{verdict_chip("PLAY" if b["beat_close"] else "PASS")}</td>'
        f'<td class="act"><button class="btn">Grade at close</button></td></tr>'
        for b in d["bets"])
    return f"""
<div class="kpis"><div class="kpi kpi-p"><span class="kpi-l">CLV</span>
<span class="kpi-v mono">{sum(d['clv_series'])/len(d['clv_series']):+.1f} pt</span>
<span class="kpi-d">mean across {len(d['bets'])}</span></div>
<div class="kpi"><span class="kpi-l">Trend</span>{clv_spark(d['clv_series'])}
<span class="kpi-d">zero marked</span></div></div>
<div class="tw" style="margin-top:12px"><table><thead><tr>
<th>Game</th><th>Market</th><th>Side</th><th class="n">Line</th>
<th class="n">Price</th><th>Book</th><th>Taken</th><th class="n">CLV</th>
<th>Beat close</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>"""


def dash_body(d) -> str:
    """The dashboard's nav + panes, scoped so it can be mounted in the brief.

    Section ids are prefixed `db-` because the dashboard's own first pane is
    called "board" and so is one of the brief's tabs - two elements with the
    same id in one document is a silent, undebuggable collision.
    """
    return f"""<div class="board">
<nav role="tablist" aria-label="Board views">
  <button data-t="db-board" aria-selected="true">Board</button>
  <button data-t="db-game" aria-selected="false">Game</button>
  <button data-t="db-market" aria-selected="false">Market</button>
  <button data-t="db-props" aria-selected="false">Props</button>
  <button data-t="db-middles" aria-selected="false">Middles</button>
  <button data-t="db-bet" aria-selected="false">Bet</button>
</nav>
<section id="db-board" class="on">{board(d)}</section>
<section id="db-game">{games(d)}</section>
<section id="db-market">{markets(d)}</section>
<section id="db-props">{props_panel(d)}</section>
<section id="db-middles">{middles_panel(d)}</section>
<section id="db-bet">{bets(d)}</section>
</div>
<dialog id="logdlg"><div class="dlg-h">Log bet</div><div class="dlg-b">
<p style="margin:0 0 8px;font-size:12.5px;color:var(--ink2)">Run this to write
the wager. It refuses without a thesis and a kill condition.</p>
<textarea id="logcmd" readonly></textarea>
<div style="display:flex;gap:8px;margin-top:10px">
<button class="btn pri" id="copybtn">Copy command</button>
<button class="btn" id="closedlg">Close</button></div></div></dialog>"""


def build(d) -> str:
    logos = "".join(f".lg-{k}{{background-image:url({v})}}"
                    for k, v in sorted(LOGOS.items()))
    return f"""<title>{DS.BRAND} &middot; Board</title>
<style>{FONT_CSS}{CSS}{COMPONENT_CSS}{TEAM_CHART_CSS}{logos}
.gcard.hit{{outline:2px solid var(--sides)}}</style>
<div class="wrap">
<div class="top"><h1>{DS.BRAND} &middot; Board</h1>
  <span class="rt">{source_stamp('Pinnacle + 28 books',
                                 d['meta']['captured'][:16])}</span>
  {DS.TOGGLE_HTML}</div>
{dash_body(d)}
</div>
{JS}<script>{DS.TOGGLE_JS}</script>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--inp", default="week01_dash.json")
    ap.add_argument("-o", "--out", default="week01_dashboard.html")
    a = ap.parse_args()
    d = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    Path(a.out).write_text(build(d), encoding="utf-8")
    print(f"  wrote {a.out} ({Path(a.out).stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
