"""design_system.py - the Signage system, shared by every surface.

ONE FILE, THREE PRODUCTS. The brief, the dashboard and the team ledger had
three separate token blocks that had already drifted apart - the dashboard used
--surface/--rule, the brief used --panel/--line, and team_charts.py rendered
invisible in the dashboard because it was authored against the other set. A
direction chosen for the system has to live somewhere the system can import,
or it becomes three re-implementations that diverge again.

THE DIRECTION: SIGNAGE. Picked over a dense trading-desk shell and an
editorial annual. Its bet is that identity is the feature - 32 near-identical
team pages, seventeen near-identical games, need to feel distinct, and the
sport already supplies the vocabulary: block color, condensed numerals at
jersey scale, wide-tracked uppercase, heavy horizontal rules.

WHAT SIGNAGE MUST NOT LOSE. The first sketch of it dropped the head coach, the
tier and the context note, and the reaction was immediate: the other two
directions "have better data layers". Bold type is not a substitute for a
labelled number. Every figure in this system carries its name and its rank.

TYPE ROLES
  Condensed 700   numerals and headings - the jersey voice, used at size
  Sans 400/600    running text and labels
  Mono 400        only where columns of digits must align across rows

CONTRAST IS MEASURED, NOT ASSUMED. --ink3 carries every wide-tracked uppercase
label at 9.5px. That is small text, so it needs 4.5:1, and it shipped at 3.39:1
in light and 3.79:1 in dark - which is precisely what "hard to read" meant. The
label voice being the palette's faintest colour at its smallest size is the
failure mode this direction invites, so those two values are pinned by
measurement: 5.01:1 and 5.49:1 against their own panels.

COLOR
  Structure is near-black and rules; team color is the only large field of
  color and it is real data, not decoration. Semantic green/red are reserved
  for direction and never used as an accent. Chart marks come from the
  validated four-step palette and are identical in every surface.
"""

from __future__ import annotations

# THE PRODUCT NAME LIVES HERE, once. Every surface reads it, so a rename is one
# edit rather than a hunt through three files and a <title>.
BRAND = "Strong Side Edge"
BRAND_SHORT = "SSE"

# Chart marks, validated with the dataviz validator against every surface this
# system uses (#FFFFFF, #FBFBF9, #12161D, #14171C). Do not re-pick by eye.
CHART_LIGHT = ("#0F7351", "#63BE97", "#DE7F55", "#A82F2F")
CHART_DARK = ("#007852", "#36a980", "#c0851f", "#c34e54")

TOKENS = """
:root{
  --bg:#EFF1F3; --panel:#FFFFFF; --row:#F5F6F8; --hd:#E9EBEF;
  --ink:#0B0D10; --ink2:#484F58; --ink3:#697079;
  --line:#DCE0E6; --rule:#0B0D10;
  --accent:#12507E; --accent-ink:#FFFFFF;
  --play:#0F7351; --play-bg:#E6F2EC;
  --neg:#A82F2F; --neg-bg:#F7E9E7;
  --warn:#8A5A12;
  --v-td:#0F7351; --v-fg:#63BE97; --v-dn:#DE7F55; --v-to:#A82F2F;
  --v-track:#E4E7EC; --v-grid:#DCE0E6; --v-line:#12507E;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0B0D10; --panel:#14171C; --row:#1A1E25; --hd:#1F242C;
  --ink:#E9ECF1; --ink2:#9AA3AF; --ink3:#868F9C;
  --line:#262C35; --rule:#E9ECF1;
  --accent:#5B9BD8; --accent-ink:#0B0D10;
  --play:#36a980; --play-bg:#10251E;
  --neg:#d2666b; --neg-bg:#2A1618;
  --warn:#c0851f;
  --v-td:#007852; --v-fg:#36a980; --v-dn:#c0851f; --v-to:#c34e54;
  --v-track:#1F242C; --v-grid:#262C35; --v-line:#5B9BD8;
}}
:root[data-theme="dark"]{
  --bg:#0B0D10; --panel:#14171C; --row:#1A1E25; --hd:#1F242C;
  --ink:#E9ECF1; --ink2:#9AA3AF; --ink3:#868F9C;
  --line:#262C35; --rule:#E9ECF1;
  --accent:#5B9BD8; --accent-ink:#0B0D10;
  --play:#36a980; --play-bg:#10251E;
  --neg:#d2666b; --neg-bg:#2A1618;
  --warn:#c0851f;
  --v-td:#007852; --v-fg:#36a980; --v-dn:#c0851f; --v-to:#c34e54;
  --v-track:#1F242C; --v-grid:#262C35; --v-line:#5B9BD8;
}
"""

BASE = """
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:'IBM Plex Sans',system-ui,-apple-system,Segoe UI,sans-serif;
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}

/* THE JERSEY VOICE. Condensed 700 is the system's numeral face - used at size
   for anything a reader should be able to identify across a room. */
.sig,.num,.hero h2,.hrk .n,h1,h2,h3{
  font-family:'IBM Plex Sans Condensed','IBM Plex Sans',sans-serif}
.num{font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.mono{font-family:'IBM Plex Mono',ui-monospace,monospace;
  font-variant-numeric:tabular-nums}

/* Wide-tracked uppercase is the label voice; it never carries a value. */
.lb,dt,th,.k{font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--ink3);font-weight:400}

.wrap{max-width:1220px;margin:0 auto;padding:0 18px 60px}
.mast{display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap;
  padding:24px 0 14px;border-bottom:3px solid var(--rule)}
.mast h1{margin:0;font-size:30px;font-weight:700;letter-spacing:-.02em;
  text-transform:uppercase;line-height:.98}
.mast .sub{color:var(--ink2);font-size:12.5px}
.mast .rt{margin-left:auto;text-align:right;font-size:11px;color:var(--ink3);
  line-height:1.7}
.mast .rt b{color:var(--ink2)}

/* Tabs read as signage tabs: square, heavy underline on the active one. */
nav.tabs{display:flex;flex-wrap:wrap;gap:0;border-bottom:1px solid var(--line);
  background:var(--bg);position:sticky;top:0;z-index:30}
nav.tabs button{font:inherit;font-size:11px;font-weight:600;cursor:pointer;
  letter-spacing:.14em;text-transform:uppercase;background:none;border:0;
  border-bottom:3px solid transparent;padding:11px 15px;color:var(--ink3)}
nav.tabs button:hover{color:var(--ink)}
nav.tabs button[aria-selected="true"]{color:var(--ink);
  border-bottom-color:var(--rule)}
nav.tabs button:focus-visible{outline:2px solid var(--accent);
  outline-offset:-3px}
nav.tabs button .ct{font-weight:400;color:var(--ink3);margin-left:6px;
  letter-spacing:0}

table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;padding:11px 10px 6px;border-bottom:2px solid var(--rule)}
td{padding:5px 10px;border-bottom:1px solid var(--line)}
td.n,th.n{text-align:right;font-family:'IBM Plex Sans Condensed',sans-serif;
  font-weight:600;font-variant-numeric:tabular-nums}
tbody tr:hover td{background:var(--row)}

.tag{font-style:normal;font-size:9.5px;letter-spacing:.1em;
  text-transform:uppercase;padding:2px 7px;border:1px solid var(--line);
  color:var(--ink3);white-space:nowrap}
.tag.new{color:var(--accent);border-color:currentColor}
.tag.warn{color:var(--warn);border-color:currentColor}
.pill{display:inline-block;font-size:9.5px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;padding:2px 8px}
.pill.play{background:var(--play);color:#fff}
.pill.pass{background:var(--hd);color:var(--ink2)}
.up{color:var(--play)} .down{color:var(--neg)}
.empty{color:var(--ink3);font-size:12.5px}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""


TOGGLE_CSS = """
.themebtn{margin-left:12px;align-self:center;font:inherit;font-size:9.5px;
  letter-spacing:.14em;text-transform:uppercase;cursor:pointer;
  background:none;color:var(--ink3);border:1px solid var(--line);
  padding:5px 10px}
.themebtn:hover{color:var(--ink);border-color:var(--ink3)}
.themebtn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
"""

# The artifact host stamps its own theme, and that is not always what the
# reader wants to look at. An explicit switch writes data-theme on <html>,
# which both token blocks are written to respect in either direction.
TOGGLE_JS = """
(function(){
  var b=document.querySelector('.themebtn'); if(!b) return;
  function cur(){
    var s=document.documentElement.getAttribute('data-theme');
    if(s) return s;
    return window.matchMedia&&window.matchMedia('(prefers-color-scheme:dark)')
      .matches ? 'dark' : 'light';
  }
  function paint(){ b.textContent = cur()==='dark' ? 'Light' : 'Dark'; }
  b.addEventListener('click',function(){
    document.documentElement.setAttribute('data-theme',
      cur()==='dark'?'light':'dark');
    paint();
  });
  paint();
})();
"""

TOGGLE_HTML = ('<button class="themebtn" type="button" '
               'aria-label="Switch between light and dark">Dark</button>')


def css() -> str:
    return TOKENS + BASE + TOGGLE_CSS
