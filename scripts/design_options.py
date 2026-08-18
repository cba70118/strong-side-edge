"""design_options.py - three visual directions for the team ledger, side by side.

The point is a DECISION, not a gallery. Each direction renders the same two
teams from the same warehouse data, so the only variable is the design. Flip
between them and pick one; the winner gets applied to the brief, the dashboard
and the ledger together.

THE THREE, and what each is actually betting on:

  A  TRADING DESK   Density is the feature. Dark ground, monospace figures,
                    hairline rules, square corners, nothing decorative. The
                    claim is that this is a working instrument read many times
                    a day, and every pixel of ornament costs a row of data.

  B  ANNUAL         Hierarchy is the feature. Paper ground, a serif display
                    face used large, wide margins, generous leading. The claim
                    is that a team page is READ, not scanned, and that the
                    reader should be able to tell what matters from ten feet
                    away.

  C  SIGNAGE        Identity is the feature. Team color as a structural block
                    rather than a 4px stripe, condensed numerals at jersey
                    scale, uppercase labels with wide tracking. The claim is
                    that 32 near-identical pages need to feel different from
                    each other, and the sport already supplies the vocabulary.

All three use the SAME validated chart palette, re-checked against each
direction's own surface (#12161D, #FBFBF9, #FFFFFF) - the marks stay honest
whichever shell wins.

Usage:
    py -3 scripts/design_options.py -o design_options.html
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import team_pages as TP                                   # noqa: E402
import team_viz as TV                                     # noqa: E402

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
SHOW = ["LA", "ARI"]          # one at the top of the table, one at the bottom


def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def num(v, dp=2, sign=False):
    if v is None:
        return "--"
    return (f"{{:+.{dp}f}}" if sign else f"{{:.{dp}f}}").format(v)


# ---------------------------------------------------------------- direction A
A_CSS = """
.dir-a{--bg:#0B0E13;--panel:#12161D;--row:#171C25;--line:#222936;
  --ink:#DDE3EC;--ink2:#8D97A8;--ink3:#5E6878;--accent:#5B9BD8;--neg:#D2555B;
  --v-td:#007852;--v-fg:#36a980;--v-dn:#c0851f;--v-to:#c34e54;
  --v-track:#1B2231;--v-line:#5B9BD8;--v-grid:#222936;
  background:var(--bg);color:var(--ink);
  font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:12.5px}
.dir-a .hd{display:flex;align-items:center;gap:12px;padding:12px 14px;
  border-bottom:1px solid var(--line)}
.dir-a .lg{width:26px;height:26px}
.dir-a h3{margin:0;font-family:'IBM Plex Sans Condensed',sans-serif;
  font-size:15px;font-weight:600;letter-spacing:.02em;text-transform:uppercase}
.dir-a .meta{color:var(--ink3);font-size:10.5px;letter-spacing:.05em;
  text-transform:uppercase}
.dir-a .rk{margin-left:auto;font-family:'IBM Plex Mono',monospace;
  font-size:22px;font-weight:600}
.dir-a .strip{display:grid;grid-template-columns:repeat(4,1fr);
  border-bottom:1px solid var(--line)}
.dir-a .strip div{padding:8px 14px;border-right:1px solid var(--line)}
.dir-a .strip div:last-child{border-right:0}
.dir-a .k{color:var(--ink3);font-size:9.5px;letter-spacing:.09em;
  text-transform:uppercase}
.dir-a .v{font-family:'IBM Plex Mono',monospace;font-size:15px;margin-top:2px;
  font-variant-numeric:tabular-nums}
.dir-a .split{display:grid;grid-template-columns:1fr 1fr}
.dir-a .split>div{padding:11px 14px;border-right:1px solid var(--line);
  border-bottom:1px solid var(--line)}
.dir-a .split>div:nth-child(2n){border-right:0}
.dir-a table{width:100%;border-collapse:collapse;font-size:11.5px}
.dir-a th{text-align:left;color:var(--ink3);font-weight:400;font-size:9.5px;
  letter-spacing:.09em;text-transform:uppercase;padding:8px 14px 5px;
  border-bottom:1px solid var(--line)}
.dir-a td{padding:3px 14px;border-bottom:1px solid var(--row)}
.dir-a td.n,.dir-a th.n{text-align:right;font-family:'IBM Plex Mono',monospace;
  font-variant-numeric:tabular-nums}
.dir-a tr:hover td{background:var(--row)}
"""

# ---------------------------------------------------------------- direction B
B_CSS = """
.dir-b{--bg:#EDEFEC;--panel:#FBFBF9;--row:#F3F4F1;--line:#D6D9D3;
  --ink:#14181A;--ink2:#4C5358;--ink3:#828A8F;--accent:#1D4E5F;--neg:#9C3A2E;
  --v-td:#0F7351;--v-fg:#63BE97;--v-dn:#DE7F55;--v-to:#A82F2F;
  --v-track:#E4E7E1;--v-line:#1D4E5F;--v-grid:#D6D9D3;
  background:var(--panel);color:var(--ink);
  font-family:'IBM Plex Sans',Georgia,serif;font-size:14.5px;line-height:1.62}
.dir-b .hd{padding:26px 30px 18px;border-bottom:2px solid var(--ink)}
.dir-b .eyebrow{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink3)}
.dir-b h3{margin:5px 0 0;font-family:'IBM Plex Serif',Georgia,serif;
  font-size:38px;font-weight:600;line-height:1.05;letter-spacing:-.015em;
  text-wrap:balance}
.dir-b .sub{color:var(--ink2);font-size:14px;margin-top:7px;max-width:52ch}
.dir-b .rk{float:right;font-family:'IBM Plex Serif',Georgia,serif;
  font-size:62px;line-height:.85;color:var(--accent);font-weight:600}
.dir-b .rk small{display:block;font-family:'IBM Plex Sans',sans-serif;
  font-size:10px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink3);font-weight:400;text-align:right;margin-top:5px}
.dir-b .body{padding:22px 30px 26px;display:flex;flex-direction:column;
  gap:26px}
.dir-b .k{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink3);border-bottom:1px solid var(--line);padding-bottom:5px;
  margin-bottom:11px}
.dir-b .figs{display:flex;gap:44px;flex-wrap:wrap}
.dir-b .fig .v{font-family:'IBM Plex Serif',Georgia,serif;font-size:27px;
  font-variant-numeric:tabular-nums}
.dir-b .fig .l{font-size:11.5px;color:var(--ink2)}
.dir-b .cols{display:grid;grid-template-columns:1fr 1fr;gap:34px}
.dir-b table{width:100%;border-collapse:collapse;font-size:13px}
.dir-b th{text-align:left;font-size:10.5px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink3);font-weight:400;
  padding-bottom:6px;border-bottom:1px solid var(--line)}
.dir-b td{padding:5px 0;border-bottom:1px solid var(--row)}
.dir-b td.n,.dir-b th.n{text-align:right;
  font-family:'IBM Plex Mono',monospace;font-variant-numeric:tabular-nums}
.dir-b p.note{margin:0;max-width:62ch;color:var(--ink2)}
"""

# ---------------------------------------------------------------- direction C
C_CSS = """
.dir-c{--bg:#FFFFFF;--panel:#FFFFFF;--row:#F4F5F7;--line:#DDE0E5;
  --ink:#0B0D10;--ink2:#4A5058;--ink3:#878E97;--accent:#0B0D10;--neg:#A82F2F;
  --v-td:#0F7351;--v-fg:#63BE97;--v-dn:#DE7F55;--v-to:#A82F2F;
  --v-track:#E7E9ED;--v-line:#0B0D10;--v-grid:#DDE0E5;
  background:var(--panel);color:var(--ink);
  font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:13.5px}
.dir-c .hero{padding:22px 24px 20px;color:#fff;display:flex;
  align-items:flex-end;gap:18px}
.dir-c .hero .lg{width:56px;height:56px;filter:drop-shadow(0 1px 2px rgba(0,0,0,.35))}
.dir-c .hero h3{margin:0;font-family:'IBM Plex Sans Condensed',sans-serif;
  font-size:44px;font-weight:700;line-height:.92;letter-spacing:-.02em;
  text-transform:uppercase}
.dir-c .hero .sub{font-size:11px;letter-spacing:.16em;text-transform:uppercase;
  opacity:.85;margin-top:6px}
.dir-c .hero .rk{margin-left:auto;text-align:right;
  font-family:'IBM Plex Sans Condensed',sans-serif;font-size:62px;
  font-weight:700;line-height:.82}
.dir-c .hero .rk small{display:block;font-size:10px;letter-spacing:.16em;
  opacity:.8;font-weight:400}
.dir-c .band{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
  border-bottom:3px solid var(--ink)}
.dir-c .band div{padding:12px 16px;border-right:1px solid var(--line)}
.dir-c .band div:last-child{border-right:0}
.dir-c .k{font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink3)}
.dir-c .v{font-family:'IBM Plex Sans Condensed',sans-serif;font-size:26px;
  font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1}
.dir-c .split{display:grid;grid-template-columns:1fr 1fr}
.dir-c .split>div{padding:14px 16px;border-right:1px solid var(--line);
  border-bottom:1px solid var(--line)}
.dir-c .split>div:nth-child(2n){border-right:0}
.dir-c table{width:100%;border-collapse:collapse;font-size:12.5px}
.dir-c th{text-align:left;font-size:9.5px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--ink3);font-weight:400;
  padding:12px 16px 6px;border-bottom:2px solid var(--ink)}
.dir-c td{padding:5px 16px;border-bottom:1px solid var(--row)}
.dir-c td.n,.dir-c th.n{text-align:right;
  font-family:'IBM Plex Sans Condensed',sans-serif;font-weight:600;
  font-variant-numeric:tabular-nums}
.dir-c tr:hover td{background:var(--row)}
.dir-c .nm{font-weight:600}
"""

SHELL_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#101317;color:#E6EAF0;
  font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:14px}
.page{max-width:1120px;margin:0 auto;padding:0 18px 60px}
.top{padding:26px 0 16px;border-bottom:1px solid #262C36}
.top h1{margin:0;font-family:'IBM Plex Sans Condensed',sans-serif;
  font-size:24px;font-weight:600;letter-spacing:-.01em}
.top p{margin:7px 0 0;color:#8C96A6;font-size:13px;max-width:74ch}
.switch{display:flex;gap:0;margin:20px 0 8px;border:1px solid #2C3440;
  border-radius:3px;overflow:hidden;width:fit-content}
.switch button{font:inherit;font-size:12.5px;font-weight:500;cursor:pointer;
  background:none;border:0;border-right:1px solid #2C3440;color:#8C96A6;
  padding:9px 20px}
.switch button:last-child{border-right:0}
.switch button[aria-pressed="true"]{background:#1B2029;color:#EAEEF4}
.switch button:hover{color:#EAEEF4}
.switch button:focus-visible{outline:2px solid #5B9BD8;outline-offset:-2px}
.pitch{color:#8C96A6;font-size:12.5px;margin:0 0 16px;max-width:82ch;
  min-height:34px}
.pitch b{color:#DDE3EC;font-weight:600}
.opt{display:none;flex-direction:column;gap:22px}
.opt.on{display:flex}
.card{border-radius:3px;overflow:hidden;border:1px solid #262C36}
.cv{display:block;max-width:100%;height:auto;overflow:visible}
.cv .zero{stroke:currentColor;stroke-width:1;stroke-dasharray:3 3;opacity:.45}
.cv .ln{fill:none;stroke:var(--v-line);stroke-width:2;stroke-linejoin:round}
.cv .area{fill:var(--v-line);opacity:.10}
.cv .dot{fill:var(--v-line);stroke:var(--panel);stroke-width:2}
.cv .ax{fill:var(--ink3);font-size:9.5px;font-family:'IBM Plex Mono',monospace}
.cv .val{fill:var(--ink);font-size:11px;font-weight:600;
  font-family:'IBM Plex Mono',monospace}
.cv .hit{fill:transparent;cursor:crosshair}
.cv .bar-td{fill:var(--v-td)}.cv .bar-fg{fill:var(--v-fg)}
.cv .bar-dn{fill:var(--v-dn)}.cv .bar-to{fill:var(--v-to)}
.cv .track{fill:var(--v-track)}
.cv .pos{fill:var(--v-td)}.cv .negb{fill:var(--v-to)}
.cv .seglab{fill:#fff;font-size:9.5px;font-weight:600;
  font-family:'IBM Plex Mono',monospace}
.cv .barlab{fill:var(--ink2);font-size:10px}
.legend{display:flex;flex-wrap:wrap;gap:10px;margin-top:7px;font-size:10.5px;
  color:var(--ink2)}
.legend span{display:inline-flex;align-items:center;gap:4px}
.legend i{width:9px;height:9px;border-radius:2px;display:inline-block}
.legend .k-td{background:var(--v-td)}.legend .k-fg{background:var(--v-fg)}
.legend .k-dn{background:var(--v-dn)}.legend .k-to{background:var(--v-to)}
.legend .k-punt{background:var(--v-track)}
.lg{display:inline-block;background-size:contain;background-repeat:no-repeat;
  background-position:center}
@media (max-width:760px){
  .dir-b .cols,.dir-a .split,.dir-c .split{grid-template-columns:1fr}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

JS = """
(function(){
  var btns=[].slice.call(document.querySelectorAll('.switch button'));
  var opts=[].slice.call(document.querySelectorAll('.opt'));
  var pitch=document.querySelector('.pitch');
  var TEXT={
    a:'<b>Trading desk.</b> Density is the feature. Dark ground, monospace '+
      'figures, hairline rules, square corners. The bet: this is an '+
      'instrument you read many times a day, and ornament costs rows.',
    b:'<b>Annual.</b> Hierarchy is the feature. Paper, a serif display face '+
      'used large, wide margins. The bet: a team page is read rather than '+
      'scanned, and importance should be legible from across the room.',
    c:'<b>Signage.</b> Identity is the feature. Team color as a structural '+
      'block, condensed numerals at jersey scale. The bet: 32 near-identical '+
      'pages need to feel distinct, and the sport supplies the vocabulary.'};
  function show(k){
    btns.forEach(function(b){b.setAttribute('aria-pressed',
      String(b.dataset.opt===k));});
    opts.forEach(function(o){o.classList.toggle('on',o.dataset.opt===k);});
    if(pitch) pitch.innerHTML=TEXT[k]||'';
    try{history.replaceState(null,'','#'+k);}catch(err){}
  }
  btns.forEach(function(b){
    b.addEventListener('click',function(){show(b.dataset.opt);});});
  var h=(location.hash||'').replace('#','');
  show(TEXT[h]?h:'a');
})();
"""


def facts(t, d, ranks):
    tm = d["teams"][t]
    return {
        "tm": tm, "name": tm.get("name") or t,
        "rank": ranks["net"].get(t), "off_r": ranks["off"].get(t),
        "def_r": ranks["def"].get(t),
        "trend": d["trend"].get(t, []), "seasons": d["seasons"].get(t, []),
        "drive": d["drives"].get(t), "metrics": TP.metric_rows(t, d),
        "players": [p for p in (d["players"].get(t) or [])
                    if (p.get("tgt") or 0) + (p.get("car") or 0) >= 1][:6],
        "game": d["games"].get(t),
    }


def drive_block(f):
    dm = f["drive"]
    return (TV.drive_mix(dm["td"], dm["fg"], dm["dn"], dm["to"])
            if dm else "<p>No drive history.</p>")


def players_rows(f):
    return "".join(
        f'<tr><td class="nm">{e(p["name"])}</td><td>{e(p["pos"])}</td>'
        f'<td class="n">{num(p.get("tgt"), 1)}</td>'
        f'<td class="n">{num(p.get("rec_yds"), 0)}</td></tr>'
        for p in f["players"])


def render_a(t, f):
    tm = f["tm"]
    lg = f'<span class="lg lg-{e(t)}"></span>' if t in LOGOS else ""
    return f'''<div class="card dir-a">
 <div class="hd">{lg}<div><h3>{e(f["name"])}</h3>
  <div class="meta">{e(tm.get("conf") or "")} {e(tm.get("div") or "")}
   &middot; {e(tm.get("record_2025") or "")} &middot; QB {e(tm.get("qb") or "")}</div></div>
  <div class="rk">{e(f["rank"] or "--")}</div></div>
 <div class="strip">
  <div><div class="k">Blended</div><div class="v">{num(tm.get("blended"), 4, True)}</div></div>
  <div><div class="k">Offense</div><div class="v">{num(tm.get("off"), 4, True)}</div></div>
  <div><div class="k">Defense</div><div class="v">{num(tm.get("def"), 4, True)}</div></div>
  <div><div class="k">Win total</div><div class="v">{e(tm.get("win_total"))}</div></div>
 </div>
 <div class="split">
  <div><div class="k">Net rating &middot; 2025</div>{TV.net_trend(f["trend"], 320, 110)}</div>
  <div><div class="k">Off / Def</div>{TV.off_def(tm.get("off"), tm.get("def"), f["off_r"], f["def_r"], 320, 88)}</div>
  <div><div class="k">Drive outcomes</div>{drive_block(f)}</div>
  <div><div class="k">By season</div>{TV.season_line(f["seasons"], 320, 92)}</div>
 </div>
 <table><thead><tr><th>projected usage</th><th></th><th class="n">tgt</th>
  <th class="n">rec yd</th></tr></thead><tbody>{players_rows(f)}</tbody></table>
</div>'''


def render_b(t, f):
    tm = f["tm"]
    return f'''<div class="card dir-b">
 <div class="hd">
  <div class="rk">{e(f["rank"] or "--")}<small>of 32</small></div>
  <div class="eyebrow">{e(tm.get("conf") or "")} {e(tm.get("div") or "")}
   &middot; {e(tm.get("tier") or "")}</div>
  <h3>{e(f["name"])}</h3>
  <div class="sub">{e(tm.get("record_2025") or "")} in 2025 &middot;
   {e(tm.get("hc") or "")} &middot; {e(tm.get("qb") or "")} &middot;
   win total {e(tm.get("win_total"))}</div>
 </div>
 <div class="body">
  <div>
   <div class="k">Strength</div>
   <div class="figs">
    <div class="fig"><div class="v">{num(tm.get("blended"), 4, True)}</div>
     <div class="l">blended rating</div></div>
    <div class="fig"><div class="v">{num(tm.get("off"), 4, True)}</div>
     <div class="l">offense &middot; rank {f["off_r"] or "--"}</div></div>
    <div class="fig"><div class="v">{num(tm.get("def"), 4, True)}</div>
     <div class="l">defense &middot; rank {f["def_r"] or "--"}</div></div>
   </div>
  </div>
  <div class="cols">
   <div><div class="k">Net rating through 2025</div>
    {TV.net_trend(f["trend"], 340, 118)}</div>
   <div><div class="k">Where the strength sits</div>
    {TV.off_def(tm.get("off"), tm.get("def"), f["off_r"], f["def_r"], 340, 92)}</div>
  </div>
  <div class="cols">
   <div><div class="k">Drive outcomes</div>{drive_block(f)}</div>
   <div><div class="k">Net rating by season</div>
    {TV.season_line(f["seasons"], 340, 96)}</div>
  </div>
  <div><div class="k">Context</div>
   <p class="note">{e(tm.get("note") or "")}</p></div>
  <div><div class="k">Projected usage</div>
   <table><thead><tr><th>player</th><th></th><th class="n">tgt</th>
    <th class="n">rec yd</th></tr></thead>
    <tbody>{players_rows(f)}</tbody></table></div>
 </div>
</div>'''


def render_c(t, f):
    tm = f["tm"]
    color = COLORS.get(t, "#222")
    lg = f'<span class="lg lg-{e(t)}"></span>' if t in LOGOS else ""
    return f'''<div class="card dir-c">
 <div class="hero" style="background:{e(color)}">
  {lg}
  <div><h3>{e(tm.get("name") or t)}</h3>
   <div class="sub">{e(tm.get("conf") or "")} {e(tm.get("div") or "")}
    &middot; {e(tm.get("record_2025") or "")} &middot;
    {e(tm.get("qb") or "")}</div></div>
  <div class="rk">{e(f["rank"] or "--")}<small>of 32</small></div>
 </div>
 <div class="band">
  <div><div class="k">Blended</div><div class="v">{num(tm.get("blended"), 3, True)}</div></div>
  <div><div class="k">Offense</div><div class="v">{num(tm.get("off"), 3, True)}</div></div>
  <div><div class="k">Defense</div><div class="v">{num(tm.get("def"), 3, True)}</div></div>
  <div><div class="k">Win total</div><div class="v">{e(tm.get("win_total"))}</div></div>
 </div>
 <div class="split">
  <div><div class="k">Net rating 2025</div>{TV.net_trend(f["trend"], 330, 114)}</div>
  <div><div class="k">Off / Def</div>{TV.off_def(tm.get("off"), tm.get("def"), f["off_r"], f["def_r"], 330, 90)}</div>
  <div><div class="k">Drive outcomes</div>{drive_block(f)}</div>
  <div><div class="k">By season</div>{TV.season_line(f["seasons"], 330, 94)}</div>
 </div>
 <table><thead><tr><th>projected usage</th><th></th><th class="n">tgt</th>
  <th class="n">rec yd</th></tr></thead><tbody>{players_rows(f)}</tbody></table>
</div>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("-o", "--out", default="design_options.html")
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)
    d = TP.fetch(con)
    con.close()

    rows = [dict(v, team=k) for k, v in d["teams"].items()]
    ranks = {"net": TP.rank_map(rows, "blended", True),
             "off": TP.rank_map(rows, "off", True),
             "def": TP.rank_map(rows, "def", False)}

    blocks = {"a": "", "b": "", "c": ""}
    for t in SHOW:
        f = facts(t, d, ranks)
        blocks["a"] += render_a(t, f)
        blocks["b"] += render_b(t, f)
        blocks["c"] += render_c(t, f)

    logo = "".join(f'.lg-{k}{{background-image:url({v})}}'
                   for k, v in sorted(LOGOS.items()) if k in SHOW)
    doc = f"""<title>Three Directions</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{FONT_CSS}{SHELL_CSS}{A_CSS}{B_CSS}{C_CSS}{logo}
.dir-a .lg,.dir-c .lg{{}}</style>
<div class="page">
 <div class="top">
  <h1>Three directions for the team ledger</h1>
  <p>The same two teams, the same warehouse data, three shells. Charts use one
   validated palette re-checked against each direction&rsquo;s own surface, so
   the marks stay constant and only the design changes. Pick one and it gets
   applied to the brief, the dashboard and the ledger together.</p>
 </div>
 <div class="switch" role="group" aria-label="Design direction">
  <button data-opt="a" aria-pressed="true">A &middot; Trading desk</button>
  <button data-opt="b" aria-pressed="false">B &middot; Annual</button>
  <button data-opt="c" aria-pressed="false">C &middot; Signage</button>
 </div>
 <p class="pitch"><b>Trading desk.</b> Density is the feature. Dark ground,
  monospace figures, hairline rules, square corners. The bet: this is an
  instrument you read many times a day, and ornament costs rows.</p>
 <div class="opt on" data-opt="a">{blocks["a"]}</div>
 <div class="opt" data-opt="b">{blocks["b"]}</div>
 <div class="opt" data-opt="c">{blocks["c"]}</div>
</div>
<script>{JS}</script>"""
    out = Path(a.out)
    out.write_text(doc, encoding="utf-8")
    print(f"  3 directions x {len(SHOW)} teams -> {out} "
          f"({len(doc) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
