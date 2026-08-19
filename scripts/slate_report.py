"""slate_report.py - render a week's slate JSON as a self-contained HTML tool.

STRUCTURE. Seven panels behind real tabs, not one scroll of everything. The
first version put market prices, simulation output, team context, player
projections and outside commentary inside one card per game, which reads as a
dump: those layers have different jobs and very different reliability, and
stacking them implies they are the same kind of claim.

  Board        every game, every market, one row each - the scan
  Plays        only what cleared the threshold, with the reasoning
  Simulation   distribution output and how far the tuning actually got
  Projections  player usage, explicitly unpriced
  Middles      the two-sided scan
  Outside      other people's recorded positions, which price nothing here
  Method       what the numbers mean and what they cannot say

Tables sort, because that is how this kind of page is used - by edge, by
kickoff, by total. Sorting is a view: it reorders rows and recomputes nothing.

Usage:
    py -3 scripts/slate_report.py -i week01_slate.json -o week01_brief.html
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from logo_assets import LOGOS, COLORS
except ImportError:                                    # logos are optional
    LOGOS, COLORS = {}, {}
from slate_charts import (margin_chart, book_strip, proj_bullet,  # noqa: E402
                          CHART_CSS)
import duckdb                                                   # noqa: E402
import team_pages as TP                                        # noqa: E402
import team_viz as TV                                          # noqa: E402
import design_system as DS                                     # noqa: E402
import dash_report as DR                                       # noqa: E402
from team_charts import (quadrant, sparkline, rerating,           # noqa: E402
                         TEAM_CHART_CSS)
try:
    from font_assets import FONT_CSS
except ImportError:                                    # fonts are optional
    FONT_CSS = ""


def e(s) -> str:
    return html.escape(str(s), quote=True)


def sgn(v, dp=1) -> str:
    return f"{v:+.{dp}f}"


def am(p) -> str:
    return f"{int(p):+d}" if p is not None else "&mdash;"


def ln(v, signed=True) -> str:
    """Spreads carry a sign, totals do not. "Under +44.5" is not a thing."""
    if v is None:
        return ""
    if not signed:
        return f"{v:g}"
    return f"{v:+g}" if v else "PK"


def implied(price):
    dec = (price / 100.0 + 1) if price > 0 else (100.0 / -price + 1)
    return 100.0 / dec


def crest(team, size=20):
    """A team's mark, referenced by class rather than inlined.

    Each crest appears about six times across the page. Inlining the data URI
    at every use took the file to 1.2 MB for 160 KB of actual image; emitting
    one CSS rule per club and referencing it costs nothing per use. A club with
    no crest in the pack degrades to a color chip rather than a broken image.
    """
    if team in LOGOS:
        return f'<span class="crest lg-{e(team)}" aria-hidden="true"></span>'
    c = (COLORS.get(team) or ["#666"])[0]
    return (f'<span class="crest chip" style="background:{e(c)}"'
            f' aria-hidden="true">{e(team[:2])}</span>')


def crest_class(team) -> str:
    """Just the class, for callers that position their own element."""
    return f'lg-{e(team)}' if team in LOGOS else ""


def logo_css() -> str:
    return "".join(f".lg-{k}{{background-image:url({v})}}"
                   for k, v in sorted(LOGOS.items()))


def tm(team, nick=None):
    return (f'<span class="tm">{crest(team)}'
            f'<span class="tmn">{e(nick or team)}</span></span>')


CSS = DS.css() + """
.mast .sub{margin-top:4px}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin:14px 0 4px}
h4.nh{margin:22px 0 0;font-family:'IBM Plex Sans Condensed',sans-serif;
  font-size:17px;font-weight:700;text-transform:uppercase;letter-spacing:.01em}
.ist{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
  padding:2px 7px;border:1px solid currentColor;white-space:nowrap}
.is-o{color:var(--neg)} .is-d{color:var(--warn)}
.is-i{color:var(--neg)} .is-s{color:var(--warn)}
.is-q{color:var(--ink3)}
ol.log.news{list-style:none;margin:10px 0 0;padding:0;display:flex;
  flex-direction:column;gap:0}
ol.log.news li{border-left:3px solid var(--line);padding:0 0 14px 14px}
ol.log.news .when{font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink3)}
ol.log.news .hd{font-weight:600;margin-top:3px;font-size:14px}
ol.log.news p{margin:3px 0 0;color:var(--ink2);font-size:12.5px;max-width:78ch}
.posfilter{display:flex;gap:0;margin:14px 0 0;width:fit-content;
  border:1px solid var(--line)}
.posfilter button{font:inherit;font-size:10px;letter-spacing:.14em;
  text-transform:uppercase;font-weight:600;cursor:pointer;background:none;
  border:0;border-right:1px solid var(--line);padding:8px 15px;
  color:var(--ink3)}
.posfilter button:last-child{border-right:0}
.posfilter button[aria-pressed="true"]{background:var(--ink);color:var(--panel)}
.posfilter button:hover{color:var(--ink)}
.posfilter button[aria-pressed="true"]:hover{color:var(--panel)}
.posfilter .s-hi{font-weight:700}
.s-mid{color:var(--ink2)} .s-lo{color:var(--ink3)}
tr.grp td{font-size:10px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink3);padding-top:14px;border-bottom:1px solid var(--line)}
.vd{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;
  padding:2px 7px;border:1px solid currentColor;white-space:nowrap}
.v-fast{color:var(--pos)} .v-slow{color:var(--warn)}
.v-weak{color:var(--ink3)} .v-noise{color:var(--neg)}
.v-noedge{color:var(--ink3)} .v-withinnoise{color:var(--warn)}
.v-candidate{color:var(--pos)} .v-wrongsign{color:var(--neg)}
.dim{color:var(--ink3)}
.rate{color:var(--ink2)}
.method{margin-top:26px;padding-top:16px;border-top:2px solid var(--rule)}
.method .lede{max-width:82ch}
.ct{margin-left:7px;font-weight:400;letter-spacing:0;opacity:.7}
/* LAST SEASON vs PROJECTION. The two column groups get their own spanning
   header and different weights - the panel previously gave the reader no way
   to tell which numbers were history and which were output. */
table.pseason tr.grp th{border-bottom:0;padding-bottom:2px;font-size:9px}
table.pseason tr.grp th.gwas{color:var(--ink3);
  border-bottom:2px solid var(--line)}
table.pseason tr.grp th.gproj{color:var(--ink);
  border-bottom:2px solid var(--rule)}
table.pseason td.was{color:var(--ink3);background:var(--row)}
table.pseason th.was{color:var(--ink3)}
table.pseason td.proj{color:var(--ink2)}
table.pseason td.strong{color:var(--ink);font-weight:700}
table.pseason td.band{color:var(--ink3);font-size:11px;
  font-family:'IBM Plex Mono',monospace;font-weight:400}
table.pseason td.pos{font-weight:600;color:var(--ink2)}
table.pseason tbody tr[hidden]{display:none}
.skpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:1px;background:var(--line);border-top:1px solid var(--line);
  border-bottom:3px solid var(--rule);margin:14px 0 0}
.skpi>div{background:var(--panel);padding:11px 14px}
.skpi .v{font-family:'IBM Plex Sans Condensed',sans-serif;font-weight:700;
  font-size:26px;line-height:1.08;margin-top:3px;
  font-variant-numeric:tabular-nums}
.skpi .v em{font-style:normal;font-size:10px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink3);margin-left:6px;font-weight:400}
tr.cur td{background:var(--play-bg)}
tr.cur td:first-child{box-shadow:inset 3px 0 0 var(--play)}
/* Brief-only tokens. Everything structural comes from design_system so the
   three surfaces cannot drift apart again - that drift is why team_charts.py
   once rendered invisible inside the dashboard. */
:root{ --line2:#E7EAEF; --thin:#8A5B10; --thin-bg:#F7EEDC;
  --pass:#69748A; --gate:#4E4A86; --gate-bg:#E8E7F3; }
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --line2:#1C222B; --thin:#D9A94E; --thin-bg:#2E2612;
  --pass:#7E8AA0; --gate:#9A92D8; --gate-bg:#1F1D36; }}
:root[data-theme="dark"]{ --line2:#1C222B; --thin:#D9A94E;
  --thin-bg:#2E2612; --pass:#7E8AA0; --gate:#9A92D8; --gate-bg:#1F1D36; }
.wrap{max-width:1300px;margin:0 auto;padding:0 16px 60px}
/* Numerals in tables take the Signage condensed face from design_system;
   .mono stays for the places where exact character alignment matters more than
   voice - prices, timestamps, ids. Overriding td.n here is what made the brief
   and the ledger disagree about what a number looks like. */
.mono{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;
  font-variant-numeric:tabular-nums slashed-zero;letter-spacing:-.015em}


.dashlink{margin-left:auto;align-self:center;font-size:12px;font-weight:600;
  color:var(--ink3);text-decoration:none;padding:0 12px}
.dashlink:hover{color:var(--accent)}
/* SCOPED ON PURPOSE. The team ledger mounts its own role=tabpanel sections
   inside one of these panes; an unscoped `section[role=tabpanel]` rule matched
   all 32 of them, pinned them to display:none, and the ledger's own `hidden`
   toggle could not override a display rule. The whole tab rendered blank. */
section.pane[role=tabpanel]{display:none;padding-top:15px}
section.pane[role=tabpanel].on{display:block}

.lede{color:var(--ink2);font-size:12.5px;max-width:100ch;margin:0 0 13px;
  padding-left:9px;border-left:3px solid var(--line)}
.lede b{color:var(--ink)}

.tw{overflow-x:auto;background:var(--panel);border:1px solid var(--line)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
tbody td{line-height:1.4}
caption{text-align:left;padding:8px 11px;font-weight:600;font-size:11.5px;
  color:var(--ink2);border-bottom:1px solid var(--line2);
  text-transform:uppercase;letter-spacing:.05em}
th{background:var(--hd);text-align:left;padding:7px 9px;font-size:10px;
  font-weight:600;text-transform:uppercase;letter-spacing:.07em;
  color:var(--ink2);border-bottom:2px solid var(--line);white-space:nowrap;
  position:sticky;top:0;z-index:5}
th.s{cursor:pointer;user-select:none}
th.s:hover{color:var(--ink)}
th.s::after{content:"\\2195";opacity:.28;margin-left:4px;font-size:10px}
th.s[data-dir="1"]::after{content:"\\2191";opacity:.95}
th.s[data-dir="-1"]::after{content:"\\2193";opacity:.95}
td{padding:6px 9px;border-bottom:1px solid var(--line2);vertical-align:top}
tbody tr:nth-child(even){background:var(--row)}
tbody tr:hover{background:var(--play-bg)}
td.n,th.n{text-align:right}

.tm{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.crest{width:20px;height:20px;flex:none;background-size:contain;
  background-position:center;background-repeat:no-repeat}
.crest.chip{display:inline-flex;align-items:center;justify-content:center;
  color:#fff;font-size:9px;font-weight:700;border-radius:3px}
.tmn{font-weight:600}
.vs{display:flex;flex-direction:column;gap:3px}

.pill{display:inline-block;font-size:9.5px;font-weight:700;padding:1px 6px;
  border-radius:3px;text-transform:uppercase;letter-spacing:.04em}
.p-play{background:var(--play);color:var(--panel)}
.p-thin{background:var(--thin-bg);color:var(--thin)}
.p-pass{background:transparent;color:var(--pass);border:1px solid var(--line)}
.p-gate{background:var(--gate-bg);color:var(--gate)}
.up{color:var(--play);font-weight:700}
.dn{color:var(--neg)}
.mut{color:var(--ink3)}
.tag{display:inline-block;font-size:9.5px;padding:0 5px;border:1px solid var(--line);
  color:var(--ink2);border-radius:3px;margin-left:4px;white-space:nowrap}

.play{background:var(--panel);border:1px solid var(--line);
  border-left:3px solid var(--play);margin-bottom:10px}
.play.thinbox{border-left-color:var(--thin)}
.phd{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:9px 13px;
  border-bottom:1px solid var(--line2);background:var(--hd)}
.phd .big{font-size:15px;font-weight:700}
.phd .edge{margin-left:auto;font-size:19px;font-weight:700}
.pbd{padding:11px 13px;display:grid;gap:14px;
  grid-template-columns:repeat(auto-fit,minmax(230px,1fr))}
.pbd h5{margin:0 0 4px;font-size:10.5px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--ink3)}
.pbd p{margin:0;font-size:12.5px;color:var(--ink2)}

.bart{display:inline-block;width:86px;height:8px;background:var(--line2);
  border-radius:3px;vertical-align:middle;overflow:hidden}
.bar{display:block;height:8px;background:var(--accent);border-radius:3px}

.note{background:var(--panel);border:1px solid var(--line);
  border-left:3px solid var(--gate);padding:12px 15px;margin:0 0 13px}
.note h3{margin:0 0 7px;font-size:13px}
.note ul{margin:0;padding-left:17px;color:var(--ink2);font-size:12.5px}
.note li{margin:4px 0}
.note li b{color:var(--ink)}
.note p{margin:0;font-size:12.5px;color:var(--ink2)}
.cols{display:grid;gap:13px;grid-template-columns:repeat(auto-fit,minmax(290px,1fr))}
.projg{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(600px,1fr))}
.projg td:first-child{white-space:nowrap}
.projg td,.projg th{padding-left:7px;padding-right:7px}
.projg th{font-size:9.5px}
details.prof{background:var(--panel);border:1px solid var(--line);
  margin-bottom:6px}
details.prof>summary{cursor:pointer;list-style:none;padding:9px 12px;
  display:flex;align-items:center;gap:10px;flex-wrap:wrap}
details.prof>summary::-webkit-details-marker{display:none}
details.prof>summary::before{content:"+";color:var(--accent);font-weight:700;
  width:10px;font-family:"IBM Plex Mono",monospace}
details.prof[open]>summary::before{content:"–"}
details.prof>summary:hover{background:var(--row)}
.prof .ph{font-size:12.5px;color:var(--ink2)}
.prof .pn{margin-left:auto;font-size:10.5px;color:var(--ink3)}
.profb{padding:2px 12px 12px 34px;border-top:1px solid var(--line2)}
.logrow{display:flex;gap:11px;padding:9px 0;border-bottom:1px solid var(--line2)}
.logrow:last-child{border-bottom:0}
.logwk{flex:none;width:34px;font-size:10px;font-weight:600;color:var(--accent);
  padding-top:2px}
.logrow p{margin:2px 0 0;font-size:12.5px;color:var(--ink2);line-height:1.55;
  max-width:88ch}
.logrow b{font-size:12.5px}
.src{font-size:10.5px;color:var(--ink3);text-transform:uppercase;
  letter-spacing:.05em}
.profgrid{margin-top:8px}
td.fg{width:1%;min-width:190px;padding:3px 9px}
.simgrid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(410px,1fr))}
.simcard{background:var(--panel);border:1px solid var(--line)}
.sch{display:flex;gap:8px;align-items:center;padding:7px 11px;
  background:var(--hd);border-bottom:1px solid var(--line2);font-size:12.5px}
.scb{padding:9px 11px}
table.kv{width:100%;border-collapse:collapse;font-size:12px;margin-top:7px}
table.kv td{padding:2px 0;border-bottom:1px solid var(--line2)}
table.kv td:nth-child(odd){color:var(--ink3)}
table.kv td:nth-child(even){text-align:right;padding-right:12px}
h3.sec{font-size:12px;margin:17px 0 8px;padding-bottom:5px;
  border-bottom:1px solid var(--line);text-transform:uppercase;
  letter-spacing:.05em;color:var(--ink2)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
@media (max-width:700px){th{position:static}.mast .rt{margin-left:0;text-align:left}}
"""

JS = r"""<script>
(function(){
  var tabs=[].slice.call(document.querySelectorAll('nav.tabs button'));
  var panes=[].slice.call(document.querySelectorAll('section.pane[role=tabpanel]'));
  function show(id){
    tabs.forEach(function(t){t.setAttribute('aria-selected',String(t.dataset.tab===id));});
    panes.forEach(function(p){p.classList.toggle('on',p.id===id);});
    try{history.replaceState(null,'','#'+id);}catch(err){}
    requestAnimationFrame(function(){window.scrollTo(0,0);});
  }
  tabs.forEach(function(t){t.addEventListener('click',function(){show(t.dataset.tab);});});
  var h=location.hash.slice(1);
  if(h&&panes.some(function(p){return p.id===h;})){show(h);}

  // Sorting is a view, never a recalculation. Comparison uses data-v so it is
  // on the underlying number, not on a formatted string like "+2.06%".
  [].forEach.call(document.querySelectorAll('table'),function(tb){
    var ths=[].slice.call(tb.querySelectorAll('th.s'));
    ths.forEach(function(th){
      th.addEventListener('click',function(){
        var idx=[].indexOf.call(th.parentNode.children,th);
        var dir=th.getAttribute('data-dir')==='1'?-1:1;
        ths.forEach(function(o){o.removeAttribute('data-dir');});
        th.setAttribute('data-dir',String(dir));
        var body=tb.tBodies[0];
        var rows=[].slice.call(body.rows);
        rows.sort(function(a,b){
          var x=a.cells[idx],y=b.cells[idx];
          if(!x||!y)return 0;
          var xv=x.getAttribute('data-v'),yv=y.getAttribute('data-v');
          if(xv!==null&&yv!==null){
            var xn=parseFloat(xv),yn=parseFloat(yv);
            if(!isNaN(xn)&&!isNaN(yn))return (xn-yn)*dir;
            return String(xv).localeCompare(String(yv))*dir;
          }
          return x.textContent.trim().localeCompare(y.textContent.trim())*dir;
        });
        rows.forEach(function(r){body.appendChild(r);});
      });
    });
  });
})();
</script>"""

# The ledger brings its own club-switching behavior; it is scoped to .ledger
# so it cannot collide with the brief's own tab handler.
JS_LEDGER = "<script>" + TP.LEDGER_JS + "</script>"
JS_THEME = "<script>" + DS.TOGGLE_JS + "</script>"
JS_POS = r"""<script>
(function(){
  var wrap=document.querySelector('.posfilter'); if(!wrap) return;
  var tbl=document.querySelector('table.pseason'); if(!tbl) return;
  var btns=[].slice.call(wrap.querySelectorAll('button'));
  var rows=[].slice.call(tbl.querySelectorAll('tbody tr'));
  /* Which column group each position cares about. A QB and an RB share no
     primary stat, so showing every column for everyone leaves the table two
     thirds empty. */
  var SHOW={QB:'cpass', WR:'crec', TE:'crec', RB:'crush', ALL:'crec'};
  function apply(pos){
    var keep=SHOW[pos]||'crec';
    ['cpass','crec','crush'].forEach(function(cls){
      var on=(cls===keep);
      [].forEach.call(tbl.querySelectorAll('.'+cls),function(cell){
        cell.style.display = on ? '' : 'none';
      });
    });
    var i=0;
    rows.forEach(function(r){
      var on = (pos==='ALL') || (r.dataset.pos===pos);
      r.hidden = !on;
      if(on){ i++; r.cells[0].textContent = i; }
    });
  }
  btns.forEach(function(b){
    b.addEventListener('click',function(){
      btns.forEach(function(x){x.setAttribute('aria-pressed',String(x===b));});
      apply(b.dataset.pos);
    });
  });
  apply('ALL');
})();
</script>"""


# ------------------------------------------------------------------- helpers

def best_offer(m):
    for want in ("play", "thin"):
        for o in m.get("offers", []):
            if o.get("verdict") == want:
                return o, want
    return None, m.get("status", "pass")


def status_pill(m):
    _, kind = best_offer(m)
    if kind == "play":
        return '<span class="pill p-play">Play</span>'
    if kind == "thin":
        return '<span class="pill p-thin">Thin</span>'
    lab = {"unavailable": "No market", "no_reference": "No sharp",
           "unpriceable": "Unpriced"}.get(m.get("status"))
    if lab:
        return f'<span class="pill p-gate">{lab}</span>'
    return '<span class="pill p-pass">Pass</span>'


def offer_text(m):
    o, _ = best_offer(m)
    if not o:
        return '<span class="mut">no edge</span>'
    signed = m.get("market_id") == "spread_game"
    sel = f'{o["side"].title()} {ln(o.get("line"), signed)}'.strip()
    return (f'{e(sel)} <span class="mono">{am(o["price"])}</span> '
            f'<span class="mut">{e(o["book"])}</span>')


def edge_cell(m):
    o, kind = best_offer(m)
    if not o or o.get("edge_pct") is None:
        return '<td class="n mut">&mdash;</td>'
    cls = "up" if kind == "play" else ("" if o["edge_pct"] > 0 else "dn")
    return (f'<td class="n {cls}" data-v="{o["edge_pct"]}">'
            f'{sgn(o["edge_pct"], 2)}%</td>')


# -------------------------------------------------------------------- panels

def panel_board(games):
    rows = []
    for g in games:
        mk = {m["type"]: m for m in g["markets"]}
        side, tot, ml = mk["Side"], mk["Total"], mk["Moneyline"]
        tags = ""
        if g["neutral"]:
            tags += '<span class="tag">neutral</span>'
        if g["div_game"]:
            tags += '<span class="tag">div</span>'
        if g.get("shape_gated"):
            tags += '<span class="tag">shape gated</span>'
        rows.append(f"""<tr>
<td data-v="{e(g['away'])}"><div class="vs">{tm(g['away'], g['away_nick'])}
  <span>{tm(g['home'], g['home_nick'])}{tags}</span></div></td>
<td class="mono mut" data-v="{e(g['kickoff'])}">{e(g['kickoff'][5:])}</td>
<td class="n mono" data-v="{side['market_line']}">{e(g['home'])} {ln(side['market_line'])}</td>
<td class="n mono" data-v="{tot['market_line']}">{tot['market_line']:g}</td>
<td class="fg">{book_strip(g.get('book_lines', {}).get('spread', []),
   (side.get('sharp') or {}).get('home', {}).get('line'))}</td>
<td>{status_pill(side)}<br>{offer_text(side)}</td>{edge_cell(side)}
<td>{status_pill(tot)}<br>{offer_text(tot)}</td>{edge_cell(tot)}
<td>{status_pill(ml)}<br>{offer_text(ml)}</td>{edge_cell(ml)}
</tr>""")
    return f"""
<p class="lede">Every game and every market in the capture, including the ones
we passed. <b>A pass is a decision</b> &mdash; the sharp number was there and
nothing beat it. Any column head with an arrow sorts; sorting reorders the
view and recomputes nothing.</p>
<div class="tw"><table>
<thead><tr>
  <th class="s">Game</th><th class="s">Kickoff</th>
  <th class="s n">Line</th><th class="s n">Total</th>
  <th>Books per spread</th>
  <th>Side</th><th class="s n">Edge</th>
  <th>Total</th><th class="s n">Edge</th>
  <th>Moneyline</th><th class="s n">Edge</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"""


# Margin frequencies 2006+, from raw.games. A line's distance from these is
# what decides whether half a point is worth chasing.
KEY_NUMBERS = {3: 14.6, 7: 8.9, 6: 6.3, 10: 5.3, 4: 5.1, 14: 5.0, 1: 4.2,
               17: 3.3, 2: 3.2, 13: 3.1}


def key_note(market_id, line, side=None, total_line=None):
    """Where a handicap sits relative to the margins games actually land on.

    NOT round(). Python rounds 3.5 to 4, which described the most important
    handicap in football against 4 (5.1% of games) instead of 3 (14.6%). A 3.5
    matters precisely because it clears 3, so the note names the number the line
    clears and which side of it the bettor is on.
    """
    import math
    if line is None:
        # A moneyline has no handicap. There is no half point to buy, so the
        # key-number arithmetic does not apply and the price is the whole bet.
        if market_id == "ml_game":
            return ("A moneyline carries no handicap, so there is no half point "
                    "to buy and no key number to clear &mdash; the entire bet is "
                    "the price against the sharp no-vig number.")
        return ""
    if market_id.startswith("total"):
        a = abs(float(line))
        return (f"Totals have no key numbers worth chasing &mdash; scoring is "
                f"spread across many values, and the totals model null-tests to "
                f"within 0.97pp. At {a:g} half a point is worth roughly what it "
                f"costs.")
    if market_id not in ("spread_game", "ml_game"):
        return ""
    a = abs(float(line))
    lo, hi = math.floor(a + 1e-9), math.ceil(a - 1e-9)
    if lo == hi:                                   # a whole number
        pct = KEY_NUMBERS.get(int(a))
        if pct is None:
            return (f"A whole number at {a:g}, which is not a common margin, so "
                    f"the push risk is small.")
        return (f"This sits <b>on</b> {int(a)} &mdash; {pct:g}% of games since "
                f"2006 land exactly there. The push is live and it is the "
                f"single most valuable number to have right.")
    pct_lo = KEY_NUMBERS.get(int(lo))
    if pct_lo is None:
        return (f"At {a:g} the handicap sits between {int(lo)} and {int(hi)}, "
                f"neither a common margin, so the half point is worth little.")
    getting = (side or "").lower() in ("away", "under", "dog") or float(line) > 0
    if getting:
        return (f"At {a:g} this is <b>getting past {int(lo)}</b>, the margin in "
                f"{pct_lo:g}% of games since 2006 &mdash; an exact {int(lo)}-point "
                f"loss still wins. That half point is most of why this number "
                f"differs from the sharp one.")
    return (f"At {a:g} this is <b>laying past {int(lo)}</b>, the margin in "
            f"{pct_lo:g}% of games since 2006 &mdash; winning by exactly "
            f"{int(lo)} loses. That is the half point being paid for.")


def play_category(market_id, has_line_value):
    """The honest name for what a play is.

    Sides and totals: the drive simulator backtests at -0.030 partial
    correlation against the closing line over 816 games, and the ratings at
    -0.04. So neither carries incremental signal over the close and a side
    "play" is a price, not an opinion. Props are the exception - the prop
    distribution passed a coverage test, so there the model has a view.
    """
    if market_id.startswith("player_"):
        return ("A model view.", "The prop distribution passed a randomised "
                "coverage test per stat, so this is our number against theirs.")
    if has_line_value:
        return ("Line value, not a view.",
                "We hold a better number than the sharp book. This system has "
                "no measured edge on who wins &mdash; the simulator backtests at "
                "&minus;0.030 partial correlation against the close over 816 "
                "games &mdash; so the claim is about the handicap, not the game.")
    return ("Price only, not a view.",
            "Same number as the sharp book, better price. Nothing here is an "
            "opinion on the outcome; it is the hold being smaller at one book.")


def panel_plays(games, min_edge, teams=None):
    out = []
    for g in games:
        for m in g["markets"]:
            for o in m.get("offers", []):
                if o.get("verdict") not in ("play", "thin"):
                    continue
                signed = m.get("market_id") == "spread_game"
                sel = f'{o["side"].title()} {ln(o.get("line"), signed)}'.strip()
                thin = o["verdict"] == "thin"
                why = []
                if o.get("model_fair_pct") is not None:
                    gap = abs(o["line_gap"])
                    why.append(
                        f'The number is {gap:g} point{"s" if gap != 1 else ""} better '
                        f'than the sharp {ln(o["sharp_line"], signed)}, worth '
                        f'<span class="mono">{o["pts_of_line_value_pp"]:+.1f}pp</span>. '
                        f'That puts fair at <span class="mono">'
                        f'{o["model_fair_pct"]:.1f}%</span> against a price implying '
                        f'<span class="mono">{implied(o["price"]):.1f}%</span>.')
                elif o.get("fair_price") is not None:
                    fp = 100.0 / ((o["fair_price"] / 100 + 1)
                                  if o["fair_price"] > 0
                                  else (100 / -o["fair_price"] + 1))
                    why.append(
                        f'Matched line, so this is pure price. Sharp no-vig is '
                        f'<span class="mono">{fp:.2f}%</span> '
                        f'(<span class="mono">{am(o["fair_price"])}</span>) against '
                        f'a board price implying <span class="mono">'
                        f'{implied(o["price"]):.2f}%</span> '
                        f'(<span class="mono">{am(o["price"])}</span>).')
                if o.get("middle"):
                    why.append("Also one leg of a two-sided window &mdash; see Middles.")
                sharp = " &middot; ".join(
                    f'{k}{" " + ln(v["line"], signed) if v.get("line") is not None else ""} '
                    f'<span class="mono">{v["fair_pct"]:.1f}%</span>'
                    for k, v in (m.get("sharp") or {}).items())
                kill = ('The sharp number moves to this number or through it, or '
                        'the book comes back to the sharp line.'
                        if o.get("model_fair_pct") is not None else
                        'The sharp no-vig price moves past the board price, or '
                        'the book shortens.')
                cat_head, cat_body = play_category(
                    m.get("market_id", ""),
                    o.get("model_fair_pct") is not None)
                keyn = key_note(m.get("market_id", ""), o.get("line"),
                                o.get("side"))

                # TEAM CONTEXT from the per-game payload, which carries both
                # the ratings and the notes. The market's re-rate against our
                # 2025 rating is the one number that says what the market can
                # see and we cannot: ten new head coaches and nine new starting
                # quarterbacks a 2025 rating is blind to.
                ctx_bits = []
                pri = g.get("prior") or {}
                con_ = g.get("context") or {}
                for side_key in ("away", "home"):
                    tk = g[side_key]
                    pr = pri.get(side_key) or {}
                    cx = con_.get(side_key) or {}
                    if pr.get("mkt_rating") is not None \
                            and pr.get("r25") is not None:
                        rr = (pr["mkt_rating"] - pr["r25"]) * 39.18
                        if abs(rr) < 0.5:
                            ctx_bits.append(
                                f'<b>{e(tk)}</b> priced where our 2025 rating '
                                f'has them')
                        else:
                            word = "up" if rr > 0 else "down"
                            ctx_bits.append(
                                f'<b>{e(tk)}</b> re-rated {word} '
                                f'<span class="mono">{abs(rr):.1f}</span> pts by '
                                f'the market against our 2025 rating')
                    tags = []
                    if cx.get("coach_new"):
                        tags.append(f'new HC {e(cx.get("coach") or "")}')
                    if cx.get("qb_new"):
                        tags.append(f'new QB {e(cx.get("qb") or "")}')
                    if cx.get("qb_flag"):
                        tags.append(e(str(cx["qb_flag"])))
                    if tags:
                        ctx_bits.append(f'{e(tk)}: ' + ", ".join(tags))
                note_bits = [f'{e(g[s])} &mdash; {e(str((con_.get(s) or {}).get("note"))[:110])}'
                             for s in ("away", "home")
                             if (con_.get(s) or {}).get("note")]
                ctx = ". ".join(ctx_bits)
                if ctx:
                    ctx += "."
                if note_bits:
                    ctx += " " + " ".join(note_bits) + "."

                # OUTSIDE READS are recorded positions, never inputs. They can
                # raise a question about a game; they never price one.
                rd = []
                for side_key in ("away", "home"):
                    for tk in (g.get("takes") or {}).get(side_key, []) or []:
                        if tk.get("market") in ("spread", "win_total",
                                                "prop", "structural"):
                            rd.append(
                                f'{e(tk.get("stance", "note"))} '
                                f'{e(tk.get("selection") or tk.get("market"))}'
                                f' &mdash; {e(str(tk.get("thesis") or "")[:110])}')
                reads = " &middot; ".join(rd[:2])

                out.append(f"""
<div class="play{' thinbox' if thin else ''}">
  <div class="phd">{tm(g['away'], g['away_nick'])}<span class="mut">at</span>
    {tm(g['home'], g['home_nick'])}
    <span class="big">{e(m['type'])} &middot; {e(sel)}
      <span class="mono">{am(o['price'])}</span></span>
    <span class="tag">{e(o['book'])}</span>
    {'<span class="pill p-thin">Thin</span>' if thin
     else '<span class="pill p-play">Play</span>'}
    <span class="edge {'' if thin else 'up'}">{sgn(o['edge_pct'], 2)}%</span>
  </div>
  <div class="pbd">
    <div><h5>What this is</h5><p><b>{cat_head}</b> {cat_body}</p></div>
    <div><h5>Why it prices</h5><p>{' '.join(why) or 'Price advantage at a matched number.'}</p></div>
    <div><h5>Where the number sits</h5><p>{keyn or '&mdash;'}</p></div>
    <div><h5>Team context</h5><p>{ctx or '&mdash;'}</p></div>
    <div><h5>Outside reads</h5><p>{reads or 'Nobody on record took a position on this game.'}</p></div>
    <div><h5>Sharp reference</h5><p>{sharp or '&mdash;'}</p></div>
    <div><h5>Kill condition</h5><p>{kill}</p></div>
  </div></div>""")
    return (f'<p class="lede">Anything clearing <b>{min_edge:g}% of stake</b> is a '
            f'play; below that it is reported as <b>thin</b> and not counted, '
            f'because an edge that small sits inside the error of the de-vig '
            f'itself. Prices are one snapshot and must be re-verified at the '
            f'book.</p>'
            f'<p class="lede" style="margin-top:10px"><b>Read the category '
            f'first.</b> On sides and totals this system has no measured edge on '
            f'who wins &mdash; the simulator backtests at &minus;0.030 partial '
            f'correlation against the closing line over 816 games and the '
            f'ratings at &minus;0.04. So a side listed here is a better '
            f'<em>number</em> or a better <em>price</em>, not a prediction. '
            f'Props are the one market where the model holds a view, because the '
            f'prop distribution passed a per-stat coverage test.</p>' + ("".join(out) or '<p class="lede">Nothing cleared.</p>'))


def panel_sim(games, meta):
    cards = []
    for g in games:
        s, tn = g["sim"], g["sim"]["tuning"]
        gated = bool(g.get("shape_gated"))
        side = [m for m in g["markets"] if m["type"] == "Side"][0]
        # The shape fields are now genuinely absent for a gated game rather
        # than merely hidden here, so read them only inside the live branch.
        if gated:
            fig = (f'<p class="why mut">{e(g["shape_gated"])}</p>')
            stats = ""
        else:
            k = s["key_numbers"]
            fig = margin_chart(s.get("margin_pmf") or {}, side["market_line"],
                               g["home"], g["away"])
            fig += ('<p class="figleg"><i><span class="sw key"></span>3 and 7</i>'
                    '<i><span class="sw oth"></span>other margins</i>'
                    '<i><span style="color:var(--neg)">- -</span> cover line</i>'
                    '</p>')
            stats = (
                f'<table class="kv">'
                f'<tr><td>Home win</td><td class="mono">{s["home_win"]*100:.1f}%</td>'
                f'<td>Tie</td><td class="mono">{s["tie"]*100:.1f}%</td></tr>'
                f'<tr><td>Margin = 3</td><td class="mono">{k["3"]*100:.1f}%</td>'
                f'<td>= 7</td><td class="mono">{k["7"]*100:.1f}%</td></tr>'
                f'<tr><td>Margin sd</td><td class="mono">{s["margin_sd"]:.2f}</td>'
                f'<td>Push</td><td class="mono">{side.get("push_prob", 0):.1f}%</td></tr>'
                f'</table>')
        flag = ('' if tn["converged"] else
                '<span class="tag" style="color:var(--gate)">clamped</span>')
        cards.append(f"""
<div class="simcard">
  <div class="sch">{tm(g['away'], g['away_nick'])}<span class="mut">at</span>
    {tm(g['home'], g['home_nick'])}{flag}
    <span class="mut mono" style="margin-left:auto">
      {sgn(tn['achieved_margin'])} / {tn['achieved_total']:.1f}</span></div>
  <div class="scb">{fig}{stats}</div>
</div>""")
    return f"""
<p class="lede">The simulator supplies <b>shape only</b>. Its own spread has a
partial correlation of &minus;0.030 with the realised margin once the closing
line is held fixed, across 816 games, so the center comes from the market and
only the distribution around it is ours. Each chart is the full simulated
margin distribution with the cover line drawn on it &mdash; whether the line
sits on a spike or beside one is the entire question, and a table of four
percentages could not answer it.</p>
<div class="simgrid">{''.join(cards)}</div>
<p class="lede" style="margin-top:13px"><b>Known bias.</b> Scoring happens only
in 3s and 7s here &mdash; no missed extra point, no two-point try &mdash; so
totals awkward to build from those run thin. Measured against 5,199 games the
simulator is about 1pp light at a total of 46. Margin key numbers do not share
this; they validate to 2.5pp.</p>"""


def panel_proj(games):
    """Usage projections expressed as LINES, so a posted market compares directly.

    The previous table carried ten numeric columns and scrolled sideways inside
    every block. Four of them - rec yards, our line, the band bounds - are one
    idea, so they collapse into a single bullet figure that says it better:
    where our number sits inside the range we are willing to quote.
    """
    seen, blocks = set(), []
    posted = sum(len(g.get("props", [])) for g in games)
    priced = sum(1 for g in games for x in g.get("props", [])
                 if x["status"] == "priced")
    refused = {}
    market_by_player = {}
    for g in games:
        for x in g.get("props", []):
            if x["status"] != "priced":
                refused[x["status"]] = refused.get(x["status"], 0) + 1
            if x.get("line") is not None and x.get("gsis_id"):
                market_by_player[x["gsis_id"]] = x["line"]

    for g in games:
        for side in ("away", "home"):
            tcode = g[side]
            if tcode in seen or not g["projections"][side]:
                continue
            seen.add(tcode)
            pl = g["projections"][side]
            scale = max([p_.get("rec_band", [0, 0])[1] for p_ in pl] + [1]) * 1.05
            rows = []
            for p_ in pl:
                bullet = proj_bullet(p_.get("rec_line"), p_.get("rec_band"),
                                     None, scale_max=scale)
                rows.append(
                    f'<tr><td>{e(p_["name"])} '
                    f'<span class="mut">{e(p_["pos"])}</span></td>'
                    f'<td class="n mono" data-v="{p_["tgt"] or 0}">'
                    f'{(p_["tgt"] or 0):.1f}</td>'
                    f'<td class="n mono" data-v="{p_["tgt_share"] or 0}">'
                    f'{(p_["tgt_share"] or 0)*100:.0f}%</td>'
                    f'<td class="n mono up" data-v="{p_.get("rec_line") or 0}">'
                    f'{("%.1f" % p_["rec_line"]) if p_.get("rec_line") else "&mdash;"}'
                    f'</td>'
                    f'<td class="fg">{bullet}</td></tr>')
            blocks.append(
                f'<div class="tw"><table><caption>{tm(tcode, tcode)}</caption>'
                f'<thead><tr><th class="s">Player</th>'
                f'<th class="s n">Tgt</th><th class="s n">Shr</th>'
                f'<th class="s n">Our line</th>'
                f'<th>Quote band &middot; rec yds</th>'
                f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')

    refuse_txt = ", ".join(f"{v} {k.replace('_', ' ')}"
                           for k, v in sorted(refused.items())) or "none"
    return f"""
<p class="lede">Each posted line is joined to the projection for that
player, so a model number and a book number sit in the same row.
<span class="mono">Our line</span> is the median of the fitted distribution
&mdash; the number at which we would be indifferent, in the units a book posts.
The bar is the range where the distribution passes its coverage test &mdash;
5th&ndash;95th percentile for yardage, tightened to 25th&ndash;75th for
receptions, each set by measurement rather than assumed. When a book posts a line it is drawn on
the same bar, and a line landing outside is refused rather than priced off the
part of the model that does not hold.</p>
<div class="note" style="border-left-color:var(--accent)">
<h3>Live market status</h3>
<p><b>{posted}</b> player lines posted across the slate, <b>{priced}</b>
priced. Unpriced: {e(refuse_txt)}. Everything posted so far is anytime- or
first-touchdown, which have no distribution here. Receiving and rushing yards
are <b>cleared</b> by the coverage test at 0.3pp and 1.1pp in-band error, over
the full 5&ndash;95% range, and price automatically the moment a book posts
them. Receptions clears too, on a tighter 25&ndash;75% band. An earlier
non-monotone survival function had made all three look 10pp worse than they
are and refused receptions outright.</p>
</div>
<p class="lede">Week 1 has no current-season data, so every projection is
maximally shrunk toward baseline and a genuine alpha receiver reads low. The
band is wide for the same reason, and the pricer uses that: a book posting a
number past the 95th percentile is refused rather than bet against a
projection that cannot yet see the season.</p>
<div class="projg">{''.join(blocks)}</div>"""


def panel_middles(games):
    rows = []
    for g in games:
        for x in g.get("middles", []):
            legs = "<br>".join(
                f'{e(l["side"])} <span class="mono">{l["line"]:g}</span> '
                f'<span class="mono">{am(l["price"])}</span> '
                f'<span class="mut">{e(l["book"])}</span>' for l in x["legs"])
            cls = {"positive": "up", "negative": "dn", "line ball": "mut"}[x["verdict"]]
            rows.append(f"""<tr>
<td data-v="{e(g['away'])}">{tm(g['away'], g['away_nick'])}
  <span class="mut">at</span> {tm(g['home'], g['home_nick'])}</td>
<td class="mono">{x['window'][0]:g} &ndash; {x['window'][1]:g}</td>
<td>{legs}</td>
<td class="n mono" data-v="{x['hit_prob_pct']}">{x['hit_prob_pct']:.2f}%</td>
<td class="n mono" data-v="{x['breakeven_prob_pct']}">{x['breakeven_prob_pct']:.2f}%</td>
<td class="n mono {cls}" data-v="{x['edge_pp']}">{x['edge_pp']:+.2f}pp</td>
<td class="n mono mut">&plusmn;{x['se_pp']:.2f}</td>
<td class="{cls}" data-v="{e(x['verdict'])}">{e(x['verdict'])}</td></tr>""")
    return f"""
<p class="lede">Where two placeable books disagree enough that both sides can
win. This is the one product the simulator prices outright, because a middle is
purely a question of <b>how much probability sits between two numbers</b> and
needs no view on who wins, which this model does not have.
An edge under twice the simulation standard error is reported as <b>line
ball</b> rather than a find. Breakeven is computed from <b>both</b> tails
separately, weighted by how likely each leg is to be the one that survives
&mdash; assuming the better-priced leg always won understated it badly and
turned Buffalo at Houston, the closest window on the board, from a real
&minus;1.47pp into an apparent &plus;0.11pp.</p>
<div class="tw"><table><thead><tr>
  <th class="s">Game</th><th>Window</th><th>Legs</th>
  <th class="s n">Hit</th><th class="s n">Breakeven</th>
  <th class="s n">Edge</th><th class="n">Sim&nbsp;SE</th><th class="s">Verdict</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"""


def panel_reads(games):
    seen, rows = set(), []
    for g in games:
        for side in ("away", "home"):
            t = g[side]
            if t in seen:
                continue
            seen.add(t)
            for k in g["takes"][side]:
                line = f' {k["line"]:g}' if k.get("line") is not None else ""
                st = ("play" if k["stance"] == "bet"
                      else "thin" if k["stance"] == "lean" else "pass")
                rows.append((k.get("conviction") or 0, f"""<tr>
<td data-v="{e(t)}">{tm(t, t)}</td>
<td data-v="{e(k['stance'])}"><span class="pill p-{st}">{e(k['stance'])}</span></td>
<td class="mono">{e(k['market'])} {e(k.get('selection') or '')}{line}
  {f'<span class="mut">{e(k["price"])}</span>' if k.get('price') else ''}</td>
<td class="n mono" data-v="{k.get('conviction') or 0}">{k.get('conviction') or 0}</td>
<td>{e(k['thesis'])}
  {f'<br><span class="mut">Timing: {e(k["timing"])}</span>' if k.get('timing') else ''}
  {'<span class="tag">revised later</span>' if k.get('status') == 'revised' else ''}</td>
<td class="mono mut" data-v="{e(k['published'])}">{e(k['published'])}</td></tr>"""))
    rows.sort(key=lambda r: -r[0])
    return f"""
<p class="lede"><b>Not our opinions, and not inputs to any price on this page.</b>
Move The Line's recorded positions from ten August shows, kept so a model output
can be checked against a human read. Only <em>bet</em> means money down; a
<em>pass</em> with a stated entry plan is a market-timing claim, which is
testable. Prices are as quoted on air and have moved.</p>
<div class="tw"><table><thead><tr>
  <th class="s">Team</th><th class="s">Stance</th><th>Position</th>
  <th class="s n">Conv</th><th>Thesis</th><th class="s">Aired</th>
</tr></thead><tbody>{''.join(r[1] for r in rows)}</tbody></table></div>"""


def panel_teams(teams, games):
    """Season-level profile for all 32, measured play beside market view."""
    if not teams:
        return '<p class="lede">No team data available.</p>'
    measured = next(iter(teams.values())).get("season_measured", "last season")
    played = {g[s] for g in games for s in ("away", "home")}

    rows = []
    for k, v in sorted(teams.items(),
                       key=lambda kv: -(kv[1].get("oepa") or 0)
                       + (kv[1].get("depa") or 0)):
        rec = v.get("record") or f'{v.get("w", 0)}-{v.get("l", 0)}'
        net = (v.get("oepa") or 0) - (v.get("depa") or 0)
        move = ((v.get("market_rating") or 0) - (v.get("rating_prior") or 0))
        rows.append(
            f'<tr><td>{tm(k, k)}{"" if k in played else ""}</td>'
            f'<td class="mono mut" data-v="{v.get("rank") or 99}">'
            f'{v.get("rank") or "&mdash;"}</td>'
            f'<td class="mono" data-v="{rec}">{e(rec)}</td>'
            f'<td class="n mono" data-v="{v.get("oepa") or 0}">'
            f'{(v.get("oepa") or 0):+.3f}</td>'
            f'<td class="n mono" data-v="{-(v.get("depa") or 0)}">'
            f'{(v.get("depa") or 0):+.3f}</td>'
            f'<td class="n mono" data-v="{net}">{net:+.3f}</td>'
            f'<td class="n mono" data-v="{v.get("succ") or 0}">'
            f'{(v.get("succ") or 0)*100:.1f}%</td>'
            f'<td class="n mono" data-v="{v.get("proe") or 0}">'
            f'{(v.get("proe") or 0):+.1f}</td>'
            f'<td class="n mono" data-v="{v.get("pace") or 0}">'
            f'{(v.get("pace") or 0):.1f}s</td>'
            f'<td>{sparkline(v.get("trend") or [])}</td>'
            f'<td class="n mono" data-v="{v.get("win_total") or 0}">'
            f'{(v.get("win_total") or 0):g}</td>'
            f'<td class="n mono {"up" if move > 0 else "dn" if move < 0 else "mut"}" '
            f'data-v="{move}">{move:+.3f}</td>'
            f'<td class="mut">{e((v.get("note") or "")[:78])}</td></tr>')

    # Narrative log, newest entry first. Built by team_profile.py from the
    # warehouse - every sentence traces to a number or an attributed quote.
    cards = []
    for k, v in sorted(teams.items(), key=lambda kv: kv[1].get("rank") or 99):
        entries = v.get("log") or []
        if not entries:
            continue
        head = entries[0]
        older = entries[1:]
        hist = "".join(
            f'<div class="logrow"><span class="logwk mono">'
            f'{"PRE" if x["week"] == 0 else "W" + str(x["week"])}</span>'
            f'<div><b>{e(x["headline"])}</b><p>{e(x["body"])}</p></div></div>'
            for x in older)
        cards.append(
            f'<details class="prof"><summary>'
            f'{tm(k, k)}<span class="ph">{e(head["headline"])}</span>'
            f'<span class="pn mono">{len(entries)} '
            f'entr{"y" if len(entries) == 1 else "ies"}</span></summary>'
            f'<div class="profb"><div class="logrow">'
            f'<span class="logwk mono">'
            f'{"PRE" if head["week"] == 0 else "W" + str(head["week"])}</span>'
            f'<div><p>{e(head["body"])}</p>'
            f'<span class="src">{e(head["source"])} &middot; '
            f'{e(head["as_of"])}</span></div></div>{hist}</div></details>')
    profiles = ""
    if cards:
        profiles = (
            '<h3 class="sec" style="margin-top:18px">Narrative log &mdash; '
            'one running record per club</h3>'
            '<p class="lede">Composed from the warehouse and appended to as the '
            'season goes, never rewritten. Every sentence traces to a measured '
            'number or an attributed quote; the descriptive words are chosen by '
            'threshold against that season\'s own league distribution, so the '
            'same numbers always produce the same characterization. Add a human '
            'entry with <span class="mono">team_profile.py note</span>, and a '
            'result-based one each week with '
            '<span class="mono">team_profile.py week</span>.</p>'
            f'<div class="profgrid">{"".join(cards)}</div>')

    return f"""
<p class="lede">Every club as we <b>measured</b> it in {measured}, beside what
the market <b>prices</b> for 2026. Those describe different
seasons, so the gap between them is where ten new head coaches and nine new
starting quarterbacks sit, none of which our
ratings can see. Sort by any column.</p>

<div class="figrow">
  <div class="figbox">
    <h4>Measured team performance, {measured}</h4>
    <p class="cap">Opponent-adjusted EPA per play on neutral downs. Defensive
    EPA is inverted so up and right is better on both axes.</p>
    {quadrant(teams, lambda k: crest_class(k))}
  </div>
  <div class="figbox">
    <h4>What the market did about it</h4>
    <p class="cap">Small dot: our {measured} rating. Large dot: the rating
    implied by the 2026 win total. The line is the re-rating &mdash; and the
    long ones are almost all coaching or quarterback changes.</p>
    {rerating(teams)}
  </div>
</div>

{profiles}
<div class="tw" style="margin-top:14px"><table>
<caption>All 32 &middot; measured {measured} play, 2026 market view</caption>
<thead><tr>
  <th class="s">Club</th><th class="s n">Rk</th><th class="s">Rec</th>
  <th class="s n">Off EPA</th><th class="s n">Def EPA</th><th class="s n">Net</th>
  <th class="s n">Succ</th><th class="s n">PROE</th><th class="s n">Pace</th>
  <th>Rating trend</th>
  <th class="s n">Win tot</th><th class="s n">Re-rate</th>
  <th>2026 context</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="lede" style="margin-top:12px"><b>Re-rate</b> is the market's implied
rating minus ours. Positive means the market thinks a club improved more than
last season's play alone would suggest. It is not an edge &mdash; the market
can see the offseason and we cannot, which is exactly why
<span class="mono">mart.preseason_prior</span> weights it 70/30 in the market's
favor for Week 1.</p>"""


def panel_method(meta, games):
    gated_names = [f"{g['away']} at {g['home']}" for g in games
                   if g.get("shape_gated")]
    n_prop_posted = sum(len(g.get("props", [])) for g in games)
    n_prop_priced = sum(1 for g in games for x in g.get("props", [])
                        if x.get("status") == "priced")
    return f"""
<div class="note"><h3>What this brief cannot tell you</h3><ul>
<li><b>It has no opinion on any side.</b> The simulator was backtested against
816 closing lines: partial correlation &minus;0.030, and betting its
disagreement went 46.8% against the spread &mdash; below 50%, so not merely
uninformative. Sides here are price shopping at a matched number.</li>
<li><b>Every number here survived an adversarial code review.</b> It found the
prop model's survival function was non-monotone below zero, which had
manufactured a fictitious 10pp tail miss and was gating the pricer to a
needlessly narrow band; and that the middle calculator assumed the
better-priced leg always survived, which flipped one window's sign. Both are
fixed and the figures on this page are post-fix.</li>
<li><b>Player props: {n_prop_posted} posted, {n_prop_priced} priced.</b>
Receiving and rushing yards are cleared by the coverage test and price
automatically; everything currently on the board is anytime- or
first-touchdown, which has no distribution here.</li>
<li><b>No injuries and no weather.</b> The season has not started and kickoff is
three weeks out. There is no source, so there is no claim.</li>
<li><b>{len(gated_names)} game{'' if len(gated_names) == 1 else 's'}
shape-gated:</b> {e(', '.join(gated_names)) or 'none'}. Priced
beyond what the simulator's fitted strength range can express, so its shape
output is suppressed rather than quoted from a distribution sitting around the
wrong center.</li>
<li><b>Prices move.</b> One snapshot, captured {e(meta['captured'][:19])}.
Re-verify at the book before placing anything.</li>
</ul></div>

<h3 class="sec">How a price is judged</h3>
<div class="cols">
<div class="note" style="border-left-color:var(--accent)">
<h3>Sharp reference</h3><p>Pinnacle, de-vigged with the <b>power</b> method
&mdash; the correct routing for a two-outcome market. Best available is the most
favorable <em>number</em> at a placeable book, with price only as the tiebreak,
because half a point is worth far more than a few cents of juice.</p></div>
<div class="note" style="border-left-color:var(--accent)">
<h3>When the numbers differ</h3><p>The model supplies the <b>increment</b> a line
move is worth, added to the sharp fair &mdash; never a level of its own.
Anchoring on the model invented 5.3pp of edge on one game before that was
caught. The margin model stays gated for half-point pricing entirely: it carries
a 4.10pp conditional error.</p></div>
<div class="note" style="border-left-color:var(--accent)">
<h3>The simulator</h3><p>Possession-level Monte Carlo on 36,595 real drives,
{meta['n_sims']:,} simulations a game, home field {meta['hfa_points']:.2f} points
and zero at neutral sites. It passes a null test &mdash; fed league-average
inputs it reproduces real margin spread and key numbers to 2.5pp. That validates
<em>shape</em>, a far weaker claim than getting a game right, which is why it
never supplies a center.</p></div>
</div>"""


# --------------------------------------------------------------------- build

def panel_season(con, season, week):
    """The season spine. THIS IS NOT A WEEK 1 REPORT.

    Every other panel describes one slate, and the slate payload is overwritten
    on the next run. This panel reads log.week_build - append-only, written
    BEFORE the games are played - joined to results as they land, so by week
    six it answers "what did we say, and were we right" rather than only "what
    do we think now".
    """
    if con is None:
        return '<p class="lede">Season state needs a database connection.</p>'
    rows = con.execute("""
        SELECT week, games, played, complete, cast(last_built AS VARCHAR),
               builds, n_plays, n_middles, n_props_priced,
               decisions, placed, passed, mean_margin, mean_total,
               cast(first_game AS VARCHAR)
        FROM mart.season_week WHERE season = ? ORDER BY week
    """, [season]).fetchall()
    body = ""
    for r in rows:
        (wk, games, played, complete, built, builds, plays, mids, priced,
         dec, placed, passed, mm, mt, first) = r
        state = ("complete" if complete else
                 ("in progress" if played else
                  ("current" if wk == week else "scheduled")))
        body += (
            f'<tr class="{"cur" if wk == week else ""}">'
            f'<td class="n" data-v="{wk}">{wk}</td>'
            f'<td class="ps" data-v="{e(state)}">{e(state)}</td>'
            f'<td class="ps" data-v="{e(first or "")}">{e((first or "")[:10])}</td>'
            f'<td class="n" data-v="{games}">{games}</td>'
            f'<td class="n" data-v="{played}">{played}</td>'
            f'<td class="n" data-v="{plays if plays is not None else -1}">'
            f'{plays if plays is not None else "&mdash;"}</td>'
            f'<td class="n" data-v="{mids if mids is not None else -1}">'
            f'{mids if mids is not None else "&mdash;"}</td>'
            f'<td class="n" data-v="{priced if priced is not None else -1}">'
            f'{priced if priced is not None else "&mdash;"}</td>'
            f'<td class="n" data-v="{dec or 0}">{dec or "&mdash;"}</td>'
            f'<td class="n" data-v="{placed or 0}">{placed or "&mdash;"}</td>'
            f'<td class="n" data-v="{mm if mm is not None else -1}">'
            f'{("%.1f" % mm) if mm is not None else "&mdash;"}</td>'
            f'<td class="n" data-v="{mt if mt is not None else -1}">'
            f'{("%.1f" % mt) if mt is not None else "&mdash;"}</td>'
            f'<td class="ps" data-v="{e(built or "")}">'
            f'{e((built or "")[:16])}</td></tr>')

    tot = con.execute("""
        SELECT count(*), count(*) FILTER (WHERE decision='placed'),
               count(*) FILTER (WHERE decision='passed')
        FROM bet.wager WHERE season = ?
    """, [season]).fetchone()
    # clv_line_pts is the points measure; clv_prob_delta is only valid when the
    # handicaps match, which is why the points column is the one summarised.
    clv = con.execute("""
        SELECT count(*), avg(c.clv_line_pts),
               count(*) FILTER (WHERE c.beat_close)
        FROM bet.clv c JOIN bet.wager w USING (bet_id)
        WHERE w.season = ? AND c.clv_line_pts IS NOT NULL
    """, [season]).fetchone()
    builds_n = sum(1 for r in rows if r[5])
    played_n = sum(r[2] for r in rows)

    kpi = (
        f'<div class="skpi">'
        f'<div><div class="lb">Weeks built</div><div class="v">{builds_n}'
        f'<em>of {len(rows)}</em></div></div>'
        f'<div><div class="lb">Games played</div><div class="v">{played_n}'
        f'<em>of {sum(r[1] for r in rows)}</em></div></div>'
        f'<div><div class="lb">Decisions logged</div><div class="v">{tot[0]}'
        f'<em>{tot[1]} placed</em></div></div>'
        f'<div><div class="lb">Mean CLV</div><div class="v">'
        f'{("%+.2f" % clv[1]) if clv[1] is not None else "&mdash;"}'
        f'<em>pts, n={clv[0]}</em></div></div>'
        f'<div><div class="lb">Beat close</div><div class="v">'
        f'{clv[2] if clv[0] else "&mdash;"}'
        f'<em>of {clv[0]}</em></div></div>'
        f'</div>')

    return f'''<p class="lede">A rolling record, and <b>deliberately thin right
now</b> &mdash; one week built, no games played. It fills in weekly and is the
review surface rather than the betting surface, which is why it sits back here.
<b>log.week_build</b> is appended once per build and never edited, so each row
is what the system produced <em>before</em> that week was played; results join
in from the schedule as they land. CLV grades against Pinnacle in points, which
is the only valid comparison when the handicaps differ.</p>
{kpi}
<div class="tw"><table><thead><tr>
 <th class="s n">wk</th><th class="s">state</th><th class="s">opens</th>
 <th class="s n">games</th><th class="s n">played</th>
 <th class="s n">plays</th><th class="s n">middles</th>
 <th class="s n">props</th>
 <th class="s n">decisions</th><th class="s n">placed</th>
 <th class="s n">margin</th><th class="s n">total</th>
 <th class="s">last build</th>
</tr></thead><tbody>{body}</tbody></table></div>
<p class="lede" style="margin-top:12px">Blank cells are weeks that have not been
built or played yet. Nothing on this page is back-filled.</p>'''


def panel_player_season(con, season):
    """Season projections: QBs, skill players, touchdowns, per-stat bands.

    The position filter drives COLUMN visibility as well as rows, because a
    quarterback and a running back share no primary stat and one table showing
    every column for everyone would be two thirds empty.
    """
    if con is None:
        return '<p class="lede">Season projections need a database connection.</p>'
    try:
        rows = con.execute("""
            SELECT display_name, team, position, pr_games,
                   pr_targets, pr_yards, pr_rec_tds, pr_carries, pr_rush_yards,
                   pr_attempts, pr_pass_yards, pr_pass_tds, pr_ints,
                   proj_targets, proj_rec_yards, p10_rec_yards, p90_rec_yards,
                   proj_rec_tds, p10_rec_tds, p90_rec_tds,
                   proj_carries, proj_rush_yards, p10_rush_yards, p90_rush_yards,
                   proj_pass_yards, p10_pass_yards, p90_pass_yards,
                   proj_pass_tds, p10_pass_tds, p90_pass_tds,
                   availability_flag, proj_games, proj_games_rush
            FROM mart.player_season_projection WHERE season = ?
            ORDER BY coalesce(proj_pass_yards, 0) * 0.35
                   + coalesce(proj_rec_yards, 0)
                   + coalesce(proj_rush_yards, 0) DESC
        """, [season]).fetchall()
    except Exception:
        return ('<p class="lede">No season projections built yet &mdash; run '
                '<span class="mono">build_season_proj.py</span>.</p>')
    if not rows:
        return '<p class="lede">No season projections for this season.</p>'

    def c(v, dp=0):
        if v is None or v == 0:
            return "&mdash;"
        return f"{v:,.{dp}f}"

    def per(v, g):
        """Per-game rate. The comparable number when the totals sit on
        different games counts."""
        if not v or not g:
            return 0
        return v / g

    def band(lo, hi, dp=0):
        if lo is None or hi is None or (lo == 0 and hi == 0):
            return "&mdash;"
        return f"{lo:,.{dp}f}&ndash;{hi:,.{dp}f}"

    body = ""
    for i, r in enumerate(rows):
        (nm, tm, pos, prg, prtg, pry, prrtd, prc, prry,
         prat, prpy, prptd, prints,
         ptg, pry2, p10y, p90y, prtd2, p10td, p90td,
         pc, prush, p10r, p90r,
         ppy, p10py, p90py, pptd, p10ptd, p90ptd, flag, pgm, rgm) = r
        f = f'<em class="tag warn">{prg} gm</em>' if flag else ""
        body += (
            f'<tr data-pos="{e(pos)}">'
            f'<td class="n" data-v="{i + 1}">{i + 1}</td>'
            f'<td class="nm" data-v="{e(nm)}">{e(nm)}{f}</td>'
            f'<td class="ps" data-v="{e(tm)}">{e(tm)}</td>'
            f'<td class="ps pos" data-v="{e(pos)}">{e(pos)}</td>'
            f'<td class="n was" data-v="{prg or 0}">{c(prg)}</td>'
            # --- passing, QB only
            f'<td class="n was cpass" data-v="{prat or 0}">{c(prat)}</td>'
            f'<td class="n was cpass" data-v="{prpy or 0}">{c(prpy)}</td>'
            f'<td class="n was cpass" data-v="{prptd or 0}">{c(prptd)}</td>'
            f'<td class="n was cpass" data-v="{prints or 0}">{c(prints)}</td>'
            f'<td class="n proj strong cpass" data-v="{ppy or 0}">{c(ppy)}</td>'
            f'<td class="n proj rate cpass" data-v="{per(ppy, pgm)}">'
            f'{c(per(ppy, pgm), 1)}</td>'
            f'<td class="n band cpass" data-v="{p10py or 0}">'
            f'{band(p10py, p90py)}</td>'
            f'<td class="n proj strong cpass" data-v="{pptd or 0}">{c(pptd, 1)}</td>'
            f'<td class="n band cpass" data-v="{p10ptd or 0}">'
            f'{band(p10ptd, p90ptd, 1)}</td>'
            # --- receiving
            f'<td class="n was crec" data-v="{prtg or 0}">{c(prtg)}</td>'
            f'<td class="n was crec" data-v="{pry or 0}">{c(pry)}</td>'
            f'<td class="n was crec" data-v="{prrtd or 0}">{c(prrtd)}</td>'
            f'<td class="n proj crec" data-v="{ptg or 0}">{c(ptg, 1)}</td>'
            f'<td class="n proj strong crec" data-v="{pry2 or 0}">{c(pry2)}</td>'
            f'<td class="n proj rate crec" data-v="{per(pry2, pgm)}">'
            f'{c(per(pry2, pgm), 1)}</td>'
            f'<td class="n band crec" data-v="{p10y or 0}">'
            f'{band(p10y, p90y)}</td>'
            f'<td class="n proj strong crec" data-v="{prtd2 or 0}">{c(prtd2, 1)}</td>'
            f'<td class="n band crec" data-v="{p10td or 0}">'
            f'{band(p10td, p90td, 1)}</td>'
            # --- rushing
            f'<td class="n was crush" data-v="{prc or 0}">{c(prc)}</td>'
            f'<td class="n was crush" data-v="{prry or 0}">{c(prry)}</td>'
            f'<td class="n proj crush" data-v="{pc or 0}">{c(pc, 1)}</td>'
            f'<td class="n proj strong crush" data-v="{prush or 0}">{c(prush)}</td>'
            f'<td class="n proj rate crush" data-v="{per(prush, rgm)}">'
            f'{c(per(prush, rgm), 1)}</td>'
            f'<td class="n band crush" data-v="{p10r or 0}">'
            f'{band(p10r, p90r)}</td>'
            f'</tr>')

    counts = {}
    for r in rows:
        counts[r[2]] = counts.get(r[2], 0) + 1
    btns = ('<button data-pos="ALL" aria-pressed="true">All'
            f'<span class="ct">{len(rows)}</span></button>')
    for pos in ("QB", "WR", "RB", "TE"):
        if counts.get(pos):
            btns += (f'<button data-pos="{pos}" aria-pressed="false">{pos}'
                     f'<span class="ct">{counts[pos]}</span></button>')
    n_flag = sum(1 for r in rows if r[30])
    n_gm = next((r[31] for r in rows if r[2] == "QB" and r[31]), 17.0)

    return f'''<div class="posfilter" role="group" aria-label="Filter by position">{btns}</div>
<div class="tw"><table class="pseason"><thead>
 <tr class="grp">
  <th colspan="5" class="gwas">2025 actual</th>
  <th colspan="4" class="gwas cpass">passing</th>
  <th colspan="5" class="gproj cpass">2026 passing</th>
  <th colspan="3" class="gwas crec">receiving</th>
  <th colspan="6" class="gproj crec">2026 receiving</th>
  <th colspan="2" class="gwas crush">rushing</th>
  <th colspan="4" class="gproj crush">2026 rushing</th>
 </tr>
 <tr>
  <th class="s n">#</th><th class="s">player</th><th class="s">tm</th>
  <th class="s">pos</th><th class="s n was">gm</th>
  <th class="s n was cpass">att</th><th class="s n was cpass">yds</th>
  <th class="s n was cpass">TD</th><th class="s n was cpass">INT</th>
  <th class="s n cpass">yards</th>
  <th class="s n cpass">yd/gm</th><th class="s n cpass">band</th>
  <th class="s n cpass">TDs</th><th class="s n cpass">band</th>
  <th class="s n was crec">tgt</th><th class="s n was crec">yds</th>
  <th class="s n was crec">TD</th>
  <th class="s n crec">tgt</th><th class="s n crec">yards</th><th class="s n crec">yd/gm</th>
  <th class="s n crec">band</th><th class="s n crec">TDs</th>
  <th class="s n crec">band</th>
  <th class="s n was crush">car</th><th class="s n was crush">yds</th>
  <th class="s n crush">car</th><th class="s n crush">yards</th><th class="s n crush">yd/gm</th>
  <th class="s n crush">band</th>
 </tr>
</thead><tbody>{body}</tbody></table></div>
<div class="method">
<h4 class="nh">How these are built</h4>
<p class="lede"><b>A full 17-game season, on purpose.</b> These project
production, not availability. An earlier version discounted every line to the
{n_gm:.0f} games this population actually averages, which made every healthy
season look like a forecast of decline. It was not one: on a per-game basis
<b>16 of 36</b> quarterbacks project up, and the ones that do are exactly the
weak 2025 seasons. Pool-wide the change is <b>&minus;1.6%</b> per game, which is
shrinkage toward the league, not pessimism.</p>
<p class="lede" style="margin-top:11px"><b>Projections, not edges.</b> Each stat
was backtested the same way, fit on 2021&ndash;22 and scored on 2023&ndash;25,
against simply reusing last season&rsquo;s total. QB passing yards beat that by
<b>13.8%</b>, QB passing TDs by <b>17.2%</b>, receiving TDs by <b>13.2%</b>,
receiving yards by <b>9.6%</b>. None of it is tested against a market: no
season-long player line exists in either feed, and there is no ADP source
here.</p>
<p class="lede" style="margin-top:11px"><b>Every band is its own, and all of
them were re-measured</b> when the projection moved to a full season. They used
to be ratios of actual season total to projected total, so they carried the
spread of missed games as much as of production. Now they compare per-game to
per-game, which removes availability from both sides: QB passing yards
0.82&ndash;1.12&times;, QB passing TDs 0.72&ndash;1.36&times;, receiving yards
0.66&ndash;1.40&times;, rushing yards 0.49&ndash;1.55&times;, and receiving TDs
<b>0.32&ndash;2.12&times;</b>, still by far the widest because TD-per-target
carries year over year at only +0.221.</p>
<p class="lede" style="margin-top:11px">A quarterback&rsquo;s TD rate is shrunk
far harder than his yards: <b>k=300</b> against roughly 600 attempts rather than
the 40 used for yardage, chosen on the fit period alone and worth +3.5% out of
sample. Without it a 46-TD season carried through nearly untouched.
Interceptions show last season&rsquo;s count and are never projected, since INT
rate carries at +0.055. QB passing yards is a <b>level, not a ranking</b>: it
wins on error by shrinking outliers, and correlates with the actual at only
+0.221. {n_flag} players who played 11 games or fewer are flagged, not
modeled.</p>
</div>'''


PRIOR_SEASON_LABEL = "2025"


def panel_news(con):
    """Injury status and headlines. Context only, and it says so."""
    if con is None:
        return '<p class="lede">News needs a database connection.</p>'
    try:
        inj = con.execute("""
            SELECT team, player, position, status, injury, reported_at
            FROM ref.injury_current
            WHERE status IS NOT NULL AND status <> 'Active'
            ORDER BY
              CASE status WHEN 'Out' THEN 0 WHEN 'Doubtful' THEN 1
                          WHEN 'Injured Reserve' THEN 2
                          WHEN 'Suspension' THEN 3
                          WHEN 'Questionable' THEN 4 ELSE 5 END,
              team, player
        """).fetchall()
        news = con.execute("""
            SELECT published, headline, description, link
            FROM ref.news_item
            WHERE headline IS NOT NULL
            ORDER BY published DESC LIMIT 30
        """).fetchall()
        cap = con.execute(
            "SELECT cast(max(captured_at) AS VARCHAR) "
            "FROM ref.injury_status").fetchone()[0]
    except Exception:
        return ('<p class="lede">No news captured yet &mdash; run '
                '<span class="mono">capture_news.py</span>.</p>')

    by_status = {}
    for r in inj:
        by_status[r[3]] = by_status.get(r[3], 0) + 1
    chips = " ".join(
        f'<span class="pill {"play" if s == "Questionable" else "pass"}">'
        f'{e(s)} {n}</span>'
        for s, n in sorted(by_status.items(), key=lambda kv: -kv[1]))

    irows = "".join(
        f'<tr data-team="{e(r[0])}">'
        f'<td class="nm">{e(r[1])}</td>'
        f'<td class="ps">{e(r[2] or "")}</td>'
        f'<td>{e(r[0])}</td>'
        f'<td><span class="ist is-{str(r[3])[:1].lower()}">{e(r[3])}</span></td>'
        f'<td>{e(r[4] or "")}</td>'
        f'<td class="ps">{e(str(r[5] or "")[:10])}</td></tr>' for r in inj)

    nrows = "".join(
        f'<li><div class="when">{e(str(r[0])[:16].replace("T", " "))}</div>'
        f'<div class="hd">'
        + (f'<a href="{e(r[3])}" target="_blank" rel="noopener">{e(r[1])}</a>'
           if r[3] else e(r[1]))
        + f'</div><p>{e(str(r[2] or "")[:260])}</p></li>' for r in news)

    return f'''<p class="lede"><b>Context, not a signal.</b> The official injury
report has a partial correlation of <b>+0.0425</b> with margin once the closing
spread is held fixed, over 1,609 games &mdash; under the 0.05 bar this project
uses, and the market visibly prices it already at +0.157 with the spread. So
nothing here feeds a price. It is here to explain a number, not to move one.</p>
<p class="lede" style="margin-top:11px">Status as of
<b>{e(str(cap)[:16])}</b>. Players listed Active are omitted: they are the
absence of news, and they were 508 of the 800 rows. Headlines link out to the
source; only the headline and one line of description are stored.</p>
<div class="chips">{chips}</div>
<h4 class="nh">Injury report &middot; {len(inj)} players</h4>
<div class="tw"><table><thead><tr>
 <th class="s">player</th><th class="s">pos</th><th class="s">team</th>
 <th class="s">status</th><th class="s">injury</th><th class="s">reported</th>
</tr></thead><tbody>{irows}</tbody></table></div>
<h4 class="nh">Headlines</h4>
<ol class="log news">{nrows}</ol>'''


H_METRICS_1 = '<h4 class="nh">1. Does it describe the team?</h4>'
H_METRICS_2 = '<h4 class="nh">2. Does it beat the price?</h4>'

LEDE_METRICS_1 = """<div class="method"><p class="lede"><b>Stability is not edge, and the
difference is the whole point.</b> A stable metric is one the market has had
every chance to price. Points per game is among the most stable numbers on this
page and knows <b>nothing</b> the closing line does not. So the two tables below
are separate on purpose: the first asks whether a number is measuring the team
or the schedule, the second asks whether it beats a price.</p>

<p class="lede" style="margin-top:11px">Each cell is the correlation between a
team&rsquo;s first N games and the <b>rest of that same season</b>, over
2020&ndash;2025. Not split-half reliability, which asks an easier question, but
the one you actually face in week 5, including the part where early opponents
differ from late ones. A metric is usable when the number is high <b>and
flat</b> across N. Still climbing at N=10 means it needs a full season. Pressure
metrics are measured on 2023&ndash;2025 only, where the charting is native.</p>
"""

LEDE_METRICS_2 = """<p class="lede" style="margin-top:12px"><b>Three claims did
not survive.</b> &ldquo;Prefer pressure rate to sack rate&rdquo; is a
<b>defensive</b> rule, not a general one: on defense sack rate generated is
noise (0.01 by N=10) while pressure generated reaches 0.43, but on offense it
inverts and sack rate <i>allowed</i> (0.49) is the more stable of the two,
because it carries quarterback identity and a quarterback is more stable than a
line. Pace was expected to settle in 3&ndash;5 games and never gets past 0.39.
Points per game was filed as noise and is more stable than pass EPA. Confirmed
as claimed: PROE settles fast, every defensive metric is weak at every N,
turnover margin decays toward zero, and red zone TD rate is close to noise.
Offensive EPA is <b>better</b> than claimed, already usable at N=4.</p>

<p class="lede" style="margin-top:11px">Every metric built point-in-time from
prior games only, differenced home minus away, then correlated with the margin
<b>holding the closing spread fixed</b>. The raw column is there to show why raw
correlation is worthless: everything correlates with margin, and so does the
spread. Only the partial matters.</p>
"""

LEDE_METRICS_3 = """<p class="lede" style="margin-top:12px"><b>Nothing
survives.</b> The largest absolute t across 17 metrics is <b>1.54</b>, and
screening 17 at once needs about 3.0. At these sample sizes a correlation
carries a standard error of 0.029 to 0.040 on its own, so a 0.06 partial is one
and a half standard errors: two metrics nominally clearing a flat 0.05 bar is
precisely what seventeen draws from noise look like, and both of those came out
with the <b>wrong sign</b>. This is the third independent route to the same
conclusion, after opponent-adjusted ratings at &minus;0.04 and the drive
simulator at &minus;0.030.</p>
<p class="lede" style="margin-top:11px"><b>So what is any of it for.</b> These
numbers explain a line, size a result against expectation, and flag when a
win came from something that will not repeat. Pressure rate is now stored per
team-game and is new here. None of it enters a price on a side, because none of
it earned that.</p></div>
"""


def panel_metrics(con):
    """Which advanced metrics describe a team, and which of them beat a price.

    Two tables because they answer two different questions, and conflating them
    is the standard mistake. The first asks whether a number is measuring the
    team or the schedule. The second asks whether it knows anything the closing
    line does not. A metric can pass the first and fail the second, and nearly
    all of them do.
    """
    if con is None:
        return '<p class="lede">Metrics need a database connection.</p>'
    try:
        stab = con.execute("""
            SELECT metric, claimed, n4, n6, n8, n10, verdict, grp
            FROM mart.metric_stability
        """).fetchall()
        edge = con.execute("""
            SELECT metric, games, raw, partial, se, tstat, verdict
            FROM mart.metric_edge ORDER BY abs(tstat) DESC
        """).fetchall()
    except Exception:
        return ('<p class="lede">Not measured yet. Run '
                '<span class="mono">metric_stability.py</span> and '
                '<span class="mono">metric_edge_test.py</span>.</p>')

    def hue(v):
        if v is None:
            return ""
        if v >= 0.45:
            return "s-hi"
        if v >= 0.30:
            return "s-mid"
        return "s-lo"

    srows, seen = "", None
    for m, claim, n4, n6, n8, n10, verd, grp in stab:
        if grp != seen:
            seen = grp
            srows += '<tr class="grp"><td colspan="7">' + e(grp) + '</td></tr>'
        cells = "".join(
            f'<td class="n {hue(v)}">{v:.2f}</td>' if v is not None
            else '<td class="n">&middot;</td>' for v in (n4, n6, n8, n10))
        srows += (f'<tr><td class="nm">{e(m)}</td>'
                  f'<td class="ps">{e(claim)}</td>{cells}'
                  f'<td><span class="vd v-{verd.lower().replace(" ", "")}">'
                  f'{e(verd)}</span></td></tr>')

    erows = "".join(
        f'<tr><td class="nm">{e(m)}</td><td class="n">{g:,}</td>'
        f'<td class="n dim">{raw:+.3f}</td>'
        f'<td class="n strong">{pc:+.3f}</td>'
        f'<td class="n dim">{se:.3f}</td>'
        f'<td class="n">{ts:+.2f}</td>'
        f'<td><span class="vd v-{v.lower().replace(" ", "")}">{e(v)}</span></td>'
        f'</tr>' for m, g, raw, pc, se, ts, v in edge)

    return H_METRICS_1 + (
        '<div class="tw"><table><thead><tr>'
        '<th class="s">metric</th><th class="s">claimed</th>'
        '<th class="s n">N=4</th><th class="s n">N=6</th>'
        '<th class="s n">N=8</th><th class="s n">N=10</th>'
        '<th class="s">verdict</th>'
        '</tr></thead><tbody>' + srows + '</tbody></table></div>'
    ) + LEDE_METRICS_1 + LEDE_METRICS_2 + H_METRICS_2 + (
        '<div class="tw"><table><thead><tr>'
        '<th class="s">metric</th><th class="s n">games</th>'
        '<th class="s n">raw</th><th class="s n">partial</th>'
        '<th class="s n">SE</th><th class="s n">t</th>'
        '<th class="s">verdict</th>'
        '</tr></thead><tbody>' + erows + '</tbody></table></div>'
    ) + LEDE_METRICS_3


def build(data, con=None, dash=None) -> str:
    meta, games = data["meta"], data["games"]
    n_play = sum(1 for g in games for m in g["markets"]
                 for o in m.get("offers", []) if o.get("verdict") == "play")
    n_thin = sum(1 for g in games for m in g["markets"]
                 for o in m.get("offers", []) if o.get("verdict") == "thin")
    n_mid = sum(len(g.get("middles", [])) for g in games)
    n_pos = sum(1 for g in games for x in g.get("middles", [])
                if x.get("verdict") == "positive")
    n_proj = sum(len(g["projections"][s]) for g in games for s in ("away", "home"))
    n_prop_posted = sum(len(g.get("props", [])) for g in games)
    n_prop_priced = sum(1 for g in games for x in g.get("props", [])
                        if x.get("status") == "priced")
    gated = [g for g in games if g.get("shape_gated")]
    n_read = sum(len(g["takes"][s]) for g in games for s in ("away", "home"))

    # THE CLUB LEDGER IS MOUNTED HERE, not shipped as a separate file. A club
    # page that lives away from the betting data makes the reader hold two
    # tabs open to answer one question. It replaces the thinner Teams panel it
    # duplicated rather than sitting beside it.
    if con is not None:
        c_nav, c_panes, c_n, _ = TP.build_pages(con)
        teams_n, teams_html = (c_n, TP.ledger_html(c_nav, c_panes))
    else:
        teams_n, teams_html = (0, '<p class="empty">Club ledger needs a database connection; '
                    'pass --db.</p>')

    # ONE ARTIFACT, NOT THREE. The dashboard mounts here rather than shipping
    # as a separate link. Bouncing between three files is how the Projections
    # tab appeared to have vanished when it had never moved.
    if dash is not None:
        board_n = len(dash.get("games") or [])
        board_html = DR.dash_body(dash)
    else:
        board_n, board_html = (0, '<p class="empty">Board state needs the '
                                  'dashboard payload; pass --dash.</p>')

    # ORDER IS THE WEEKLY WORKFLOW, front to back. This week's board and what
    # cleared come first because that is what the reader opens it for during a
    # game week; the season record sits near the end because it is a review
    # surface, and an almost-empty one until results start landing. It will be
    # the most valuable tab here by December and it is still not tab one.
    #
    # "Board" is this week's slate including the passes; "Live board" is the
    # price dashboard. They were briefly called Board and Board state, which
    # nobody could tell apart.
    panes = [
        ("board", "Board", len(games), panel_board(games)),
        ("plays", "Plays", n_play + n_thin,
         panel_plays(games, meta["min_edge_pct"], data.get("teams"))),
        ("live", "Live board", board_n, board_html),
        ("mid", "Middles", n_mid, panel_middles(games)),
        ("proj", "Projections", n_proj, panel_proj(games)),
        ("sim", "Simulation", len(games), panel_sim(games, meta)),
        ("teams", "Teams", teams_n, teams_html),
        ("players", "Player season", None,
         panel_player_season(con, meta.get("season", 2026))),
        ("reads", "Outside reads", n_read, panel_reads(games)),
        ("metrics", "Metrics", None, panel_metrics(con)),
        ("news", "News", None, panel_news(con)),
        ("season", "Season", None, panel_season(con, meta.get("season", 2026),
                                                meta["week"])),
        ("method", "Method", None, panel_method(meta, games)),
    ]
    nav = "".join(
        f'<button role="tab" data-tab="{i}" '
        f'aria-selected="{"true" if k == 0 else "false"}">{lab}'
        f'{f"<span class=ct>{n}</span>" if n is not None else ""}</button>'
        for k, (i, lab, n, _) in enumerate(panes))
    body = "".join(
        f'<section role="tabpanel" class="pane {"on" if k == 0 else ""}" id="{i}">{h}</section>'
        for k, (i, _, _, h) in enumerate(panes))

    return f"""<title>{DS.BRAND}</title>
<style>{FONT_CSS}{CSS}{CHART_CSS}{TEAM_CHART_CSS}{TP.LEDGER_CSS}{TV.CHART_CSS}{DR.EXTRA_CSS}{DR.COMPONENT_CSS}{logo_css()}</style>
<div class="wrap">
<div class="mast">
  <div>
    <h1>{DS.BRAND}</h1>
    <div class="sub">NFL {meta.get('season', 2026)} &middot; <b>Week {meta['week']}</b> &middot; {len(games)} games &middot; {meta['books']} books &middot;
      <b>{n_play} plays</b> &middot; {n_thin} thin &middot;
      {n_mid} middles, {n_pos or 'none'} positive &middot;
      {n_prop_priced} props priced</div>
  </div>
  <div class="rt">
    <b>Captured</b> {e(meta['captured'][:19])}<br>
    <b>Threshold</b> {meta['min_edge_pct']:g}% of stake &middot;
    <b>Sims</b> {meta['n_sims']:,}/game
  </div>
  {DS.TOGGLE_HTML}
</div>
<nav class="tabs" role="tablist">{nav}</nav>
{body}
</div>
{JS}{JS_LEDGER}{DR.JS}{JS_THEME}{JS_POS}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--inp", default="week01_slate.json")
    ap.add_argument("-o", "--out", default="week01_brief.html")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent
                                        / "data" / "nfl.duckdb"))
    ap.add_argument("--dash", default="week01_dash.json")
    a = ap.parse_args()
    data = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    dash = None
    if Path(a.dash).exists():
        dash = json.loads(Path(a.dash).read_text(encoding="utf-8"))
    con = duckdb.connect(a.db, read_only=True)
    try:
        html_doc = build(data, con, dash)
    finally:
        con.close()
    Path(a.out).write_text(html_doc, encoding="utf-8")
    print(f"  wrote {a.out} ({Path(a.out).stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
