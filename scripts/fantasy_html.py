"""fantasy_html.py - render the draft board someone actually drafts from.

WHO IT IS FOR. Someone who has never done this and does not follow football.
That drives every choice: positions are spelled out, nothing is abbreviated,
the jargon is gone, and the page opens by explaining what a draft even is.

DESIGN NOTE, because the colour is load-bearing. Position does NOT get a
colour: the position names already say what they are, and four hues in one
lightness band failed colour-blind separation every way they were arranged
(blue against violet came out at 1.7 delta-E under protanopia). Colour instead
carries the only thing needing a decision - take this person earlier than
everyone else would, or let them go - as a blue/orange pair, the one that
survives red-green blindness. Validated on both grounds, and every colour is
paired with a word so nothing depends on seeing it.

Usage:
    py -3 scripts/fantasy_html.py
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

POS_LONG = {"QB": "Quarterback", "RB": "Running back",
            "WR": "Receiver", "TE": "Tight end"}


def e(s):
    return html.escape("" if s is None else str(s), quote=True)


def log(m=""):
    print(m, flush=True)


CSS = """
:root{
  --ground:#F7F2F3; --card:#FFFFFF; --ink:#2B2027; --ink2:#5C4E56;
  --ink3:#8A7A83; --line:#E6DCDF; --plum:#6E2A4E; --plum-soft:#F0E4EA;
  --early:#1F6FA8; --fade:#C2622B; --gone:#B4A7AD;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ground:#1A1418; --card:#241C21; --ink:#F4EDF0; --ink2:#C3B4BC;
    --ink3:#93838C; --line:#372C33; --plum:#D9A0BE; --plum-soft:#33232C;
    --early:#4290C4; --fade:#C87C30; --gone:#6A5B63;
  }
}
:root[data-theme="dark"]{
  --ground:#1A1418; --card:#241C21; --ink:#F4EDF0; --ink2:#C3B4BC;
  --ink3:#93838C; --line:#372C33; --plum:#D9A0BE; --plum-soft:#33232C;
  --early:#4290C4; --fade:#C87C30; --gone:#6A5B63;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:Karla,system-ui,-apple-system,sans-serif;font-size:16px;
  line-height:1.6;-webkit-text-size-adjust:100%}
.wrap{max-width:820px;margin:0 auto;padding:34px 20px 90px}

h1{font-family:Fraunces,Georgia,serif;font-weight:600;font-size:clamp(38px,8vw,62px);
  line-height:1.02;margin:0;letter-spacing:-.015em;text-wrap:balance;
  color:var(--plum)}
.kicker{font-size:12px;letter-spacing:.2em;text-transform:uppercase;
  color:var(--ink3);margin:0 0 12px}
.sub{font-size:18px;color:var(--ink2);margin:14px 0 0;max-width:56ch;
  line-height:1.55}
h2{font-family:Fraunces,Georgia,serif;font-weight:600;font-size:25px;
  margin:44px 0 6px;color:var(--ink);letter-spacing:-.01em}
h2 + .lead{color:var(--ink2);margin:0 0 14px;max-width:58ch}

.explain{background:var(--card);border:1px solid var(--line);
  border-radius:14px;padding:22px 24px;margin-top:26px}
.explain h3{font-family:Fraunces,Georgia,serif;font-size:19px;margin:0;
  font-weight:600}
.explain ol{margin:14px 0 0;padding-left:22px;color:var(--ink2)}
.explain li{margin:8px 0}
.explain b{color:var(--ink)}
.jobs{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
  gap:12px;margin-top:18px}
.job{background:var(--plum-soft);border-radius:10px;padding:12px 14px}
.job b{display:block;font-family:Fraunces,Georgia,serif;font-size:16px}
.job span{color:var(--ink2);font-size:13.5px;line-height:1.45;display:block;
  margin-top:2px}

.now{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:24px 26px;margin-top:22px;box-shadow:0 1px 2px rgba(43,32,39,.05)}
.now .lbl{font-size:11px;letter-spacing:.2em;text-transform:uppercase;
  color:var(--plum);font-weight:700}
.now .nm{font-family:Fraunces,Georgia,serif;font-weight:600;
  font-size:clamp(30px,6vw,42px);line-height:1.05;margin-top:6px;
  letter-spacing:-.015em}
.now .meta{color:var(--ink3);font-size:14px;margin-top:6px}
.now .why{color:var(--ink2);font-size:16px;margin-top:12px;max-width:54ch;
  line-height:1.55}

.ctl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:30px 0 0}
.ctl button{font:inherit;font-size:14px;padding:9px 16px;border-radius:999px;
  background:var(--card);color:var(--ink2);border:1px solid var(--line);
  cursor:pointer}
.ctl button[aria-pressed="true"]{background:var(--plum);color:var(--card);
  border-color:var(--plum)}
.ctl .spacer{flex:1 1 auto}
.ctl .reset{border-style:dashed;font-size:13px}
button:focus-visible,.row:focus-visible{outline:2.5px solid var(--plum);
  outline-offset:2px}

.tier{margin:38px 0 10px}
.tier .t{font-family:Fraunces,Georgia,serif;font-size:20px;font-weight:600}
.tier .d{color:var(--ink3);font-size:14px;display:block;margin-top:1px}
.board{display:flex;flex-direction:column;gap:8px}
.row{display:grid;grid-template-columns:38px minmax(0,1fr) auto;gap:14px;
  align-items:start;background:var(--card);border:1px solid var(--line);
  border-radius:12px;padding:14px 16px;cursor:pointer;text-align:left;
  font:inherit;color:inherit;width:100%}
.row:hover{border-color:var(--plum)}
.row .rk{font-family:Fraunces,Georgia,serif;font-size:19px;font-weight:600;
  color:var(--ink3);font-variant-numeric:tabular-nums;padding-top:1px}
.row .nm{font-family:Fraunces,Georgia,serif;font-size:20px;font-weight:600;
  line-height:1.2;letter-spacing:-.01em}
.row .meta{color:var(--ink3);font-size:13.5px;margin-top:1px}
.row .why{color:var(--ink2);font-size:14.5px;line-height:1.5;margin-top:7px}
.tag{font-size:11.5px;letter-spacing:.04em;padding:5px 11px;border-radius:999px;
  white-space:nowrap;font-weight:700;border:1px solid currentColor}
.t-early{color:var(--early)} .t-fade{color:var(--fade)}
.t-none{color:var(--ink3);border-color:var(--line);font-weight:400}
.row[data-taken="1"]{background:transparent;border-style:dashed}
.row[data-taken="1"] .nm{color:var(--gone);text-decoration:line-through}
.row[data-taken="1"] .rk,.row[data-taken="1"] .why,
.row[data-taken="1"] .meta,.row[data-taken="1"] .tag{opacity:.45}

.note{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:20px 24px;margin-top:38px;color:var(--ink2);max-width:62ch}
.note b{color:var(--ink)}
.note .tag{display:inline-block;vertical-align:baseline;margin:0 2px}
.foot{color:var(--ink3);font-size:14px;margin-top:22px;max-width:62ch}
@media (max-width:560px){
  .row{grid-template-columns:30px minmax(0,1fr)}
  .row .tag{grid-column:2;justify-self:start;margin-top:8px}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

JS = """
(function(){
  var KEY='big-board-taken-v2';
  var rows=[].slice.call(document.querySelectorAll('.row'));
  var taken={};
  try{ taken=JSON.parse(localStorage.getItem(KEY)||'{}'); }catch(e){ taken={}; }

  function nextUp(){
    var el=document.getElementById('nowCard');
    if(!el) return;
    var pick=rows.filter(function(r){ return !taken[r.dataset.id]; })[0];
    if(!pick){
      el.innerHTML='<div class="lbl">All done</div><div class="nm">'+
        'Everyone has been drafted</div>'; return;
    }
    el.innerHTML =
      '<div class="lbl">Pick this person next</div>'+
      '<div class="nm">'+pick.dataset.name+'</div>'+
      '<div class="meta">'+pick.dataset.long+' for '+pick.dataset.team+
      (pick.dataset.bye?' &middot; has week '+pick.dataset.bye+' off':'')+
      '</div><div class="why">'+pick.dataset.why+'</div>';
  }
  function paint(){
    rows.forEach(function(r){
      var t=!!taken[r.dataset.id];
      r.dataset.taken=t?'1':'0';
      r.setAttribute('aria-pressed', t?'true':'false');
    });
    nextUp();
    var n=Object.keys(taken).length;
    var c=document.getElementById('takenCount');
    if(c) c.textContent = n ? n+' crossed off' : '';
  }
  rows.forEach(function(r){
    r.addEventListener('click',function(){
      if(taken[r.dataset.id]) delete taken[r.dataset.id];
      else taken[r.dataset.id]=1;
      try{ localStorage.setItem(KEY,JSON.stringify(taken)); }catch(e){}
      paint();
    });
  });
  var reset=document.getElementById('reset');
  if(reset) reset.addEventListener('click',function(){
    taken={}; try{ localStorage.removeItem(KEY); }catch(e){} paint();
  });
  var btns=[].slice.call(document.querySelectorAll('.ctl [data-pos]'));
  btns.forEach(function(b){
    b.addEventListener('click',function(){
      btns.forEach(function(x){
        x.setAttribute('aria-pressed', String(x===b)); });
      var p=b.dataset.pos;
      rows.forEach(function(r){
        r.hidden = !(p==='ALL' || r.dataset.pos===p); });
      [].slice.call(document.querySelectorAll('.board')).forEach(function(g){
        var any=[].slice.call(g.querySelectorAll('.row'))
                  .some(function(r){ return !r.hidden; });
        g.hidden=!any;
        var t=g.previousElementSibling;
        if(t && t.classList.contains('tier')) t.hidden=!any;
      });
    });
  });
  paint();
})();
"""

# Where the board actually breaks, in points above the last usable starter at
# the same position. Not round numbers: these are the gaps.
TIERS = [
    (999, 130, "Start here",
     "Anyone in this group is a great first pick. Take whoever is left."),
    (130, 95, "Still really good",
     "Usually gone by the end of the third round."),
    (95, 60, "Solid picks",
     "These fill out the good part of your team."),
    (60, 30, "Fine to have",
     "Take these once your best spots are covered."),
    (30, -999, "Late picks",
     "Worth a shot at the end. Not much between them."),
]


def render(payload):
    ps = [p for p in payload["players"] if p.get("adp") is not None]
    ps.sort(key=lambda p: p["our_pick"])
    meta = payload.get("meta", {})

    body = ""
    for hi, lo, title, blurb in TIERS:
        grp = [p for p in ps if lo < p["edge"] <= hi]
        if not grp:
            continue
        body += (f'<div class="tier"><span class="t">{e(title)}</span>'
                 f'<span class="d">{e(blurb)}</span></div><div class="board">')
        for p in grp:
            v = p.get("value")
            if p.get("thin") and v is not None and v <= -12:
                cls, lab = "t-fade", "they know something we do not"
            elif v is not None and v >= 12:
                cls, lab = "t-early", "grab early"
            elif v is not None and v <= -12:
                cls, lab = "t-fade", "let them go"
            else:
                cls, lab = "t-none", "priced right"
            why = p.get("why") or ""
            longpos = POS_LONG.get(p["pos"], p["pos"])
            bye = (f' &middot; has week {p["bye"]} off' if p.get("bye") else "")
            body += (
                f'<button class="row" type="button" aria-pressed="false"'
                f' data-id="{e(p["name"])}" data-pos="{e(p["pos"])}"'
                f' data-name="{e(p["name"])}" data-team="{e(p["team"])}"'
                f' data-long="{e(longpos)}" data-bye="{e(p.get("bye") or "")}"'
                f' data-why="{e(why)}">'
                f'<span class="rk">{p["our_pick"]}</span>'
                f'<span><span class="nm">{e(p["name"])}</span>'
                f'<span class="meta">{e(longpos)} for {e(p["team"])}{bye}'
                f'</span><span class="why">{why}</span></span>'
                f'<span class="tag {cls}">{e(lab)}</span>'
                f'</button>')
        body += "</div>"

    n_thin = sum(1 for p in ps if p.get("thin"))
    return f"""<title>The Big Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600&family=Karla:wght@400;700&display=swap">
<style>{CSS}</style>
<div class="wrap">
<p class="kicker">Fantasy football &middot; 2026</p>
<h1>The Big Board</h1>
<p class="sub">A draft list for people who do not follow football. It is already
in the order you should pick. Take the name at the top that nobody else has
taken yet.</p>

<div class="explain">
<h3>How a draft works</h3>
<ol>
<li>Everyone in your league takes turns picking real NFL players, one at a
time. Once someone is picked, nobody else can have them.</li>
<li>Those players earn you points all season for the yards they gain and the
touchdowns they score. <b>Most points at the end wins.</b></li>
<li>So when it is your turn, you want the best person still available &mdash;
which is exactly what this page tells you.</li>
</ol>
<div class="jobs">
<div class="job"><b>Running back</b><span>Carries the ball. Scores a lot. You
want two, and early.</span></div>
<div class="job"><b>Receiver</b><span>Catches the ball. You want three. Take
them early too.</span></div>
<div class="job"><b>Tight end</b><span>Also catches, but fewer. One is
enough.</span></div>
<div class="job"><b>Quarterback</b><span>Throws the ball. Only one plays for
you, so there is no rush.</span></div>
</div>
</div>

<h2>Who to pick right now</h2>
<p class="lead">Tap any name below to cross it off once somebody takes them.
This updates as you go.</p>
<div class="now" id="nowCard"></div>

<div class="ctl">
  <button data-pos="ALL" aria-pressed="true">Everyone</button>
  <button data-pos="RB" aria-pressed="false">Running backs</button>
  <button data-pos="WR" aria-pressed="false">Receivers</button>
  <button data-pos="TE" aria-pressed="false">Tight ends</button>
  <button data-pos="QB" aria-pressed="false">Quarterbacks</button>
  <span class="spacer"></span>
  <span style="color:var(--ink3);font-size:13px" id="takenCount"></span>
  <button class="reset" id="reset">Start over</button>
</div>

{body}

<div class="note">
<p style="margin:0 0 10px"><b>What the little labels mean</b></p>
<p style="margin:0 0 8px"><span class="tag t-early">grab early</span> Everyone
else tends to wait on this person. Our numbers say they are better than that,
so take them sooner than you think you need to.</p>
<p style="margin:0 0 8px"><span class="tag t-fade">let them go</span> The
opposite. People reach for this person earlier than they are worth. Let
somebody else use their pick.</p>
<p style="margin:0"><span class="tag t-fade">they know something we do not</span>
The honest one. Our numbers only look at last season, and {n_thin} of these
players just changed teams or barely played, so everyone else is probably
right and we are behind. Do not fade these on our say-so.</p>
</div>

<p class="foot">The order is not simply who scores the most points. A
quarterback throws for thousands of yards, but only one of them plays for you
each week, so what matters is how much better someone is than the next person
you could have had at the same spot. That is why quarterbacks sit low here.</p>
<p class="foot">Where other people are drafting each player comes from
{meta.get('total_drafts', 0):,} real practice drafts run
{e(meta.get('start_date'))} to {e(meta.get('end_date'))}. Our projections do not
know about injuries, contract holdouts, or anything a coach said in August, and
touchdowns are the least predictable thing in football &mdash; so treat anyone
whose case rests on touchdowns as the shakiest name here.</p>
</div>
<script>{JS}</script>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path,
                    default=ROOT / "handoff" / "cheat_sheet.json")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "handoff" / "cheat_sheet.html")
    a = ap.parse_args()
    payload = json.loads(a.src.read_text(encoding="utf-8"))
    a.out.write_text(render(payload), encoding="utf-8")
    log(f"  wrote {a.out} ({a.out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
