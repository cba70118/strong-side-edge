"""team_viz.py - the per-team charts for the Team Ledger.

FORM BEFORE COLOR, which is the order that stops a chart being decoration:

  net_trend    change over time, one series -> line with a drawn zero, a
               labeled y-range and the endpoint called out. The first version
               of this page shipped a bare sparkline with no scale and no
               number, so the net rating was literally unreadable. A trend line
               without a value is a texture.
  off_def      polarity around a league average -> diverging bars from a shared
               zero. Offense and defense are on the SAME scale here, which is
               the only reason they can share an axis.
  metric_strip magnitude against the field -> percentile bars, one hue, darker
               = better. Only metrics that survived stability.py appear; that
               omission is the point, not an oversight.
  drive_mix    composition -> one stacked bar. Punts are the UNFILLED track
               rather than a segment: a punt is the absence of an outcome, and
               giving it a color forced a gray slot between two hues that no
               reader could separate.

PALETTE, validated not eyeballed. Four slots, two hues either side of the
punt track, run through the dataviz validator against this project's actual
surfaces (#FFFFFF light, #111621 dark):

  light  #0F7351 #63BE97 #DE7F55 #A82F2F   all checks pass
  dark   #007852 #36a980 #c0851f #c34e54   all checks pass

The light set carries a sub-3:1 contrast WARN on the two middle steps, which
obligates relief - so every segment is directly labeled and the same numbers
appear as text beneath. That is a requirement, not a nicety.
"""

from __future__ import annotations

import html

# Ordered good -> bad. Punt is deliberately absent; it is the track.
DRIVE_KEYS = [("td", "TD"), ("fg", "FG"), ("dn", "Downs"), ("to", "Turnover")]

CHART_CSS = """
:root{
  --v-td:#0F7351; --v-fg:#63BE97; --v-dn:#DE7F55; --v-to:#A82F2F;
  --v-track:#E3E7ED; --v-grid:#D5DAE3; --v-line:#1F4E8C;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --v-td:#007852; --v-fg:#36a980; --v-dn:#c0851f; --v-to:#c34e54;
  --v-track:#1B2231; --v-grid:#242C3B; --v-line:#6FA3E0;
}}
:root[data-theme="dark"]{
  --v-td:#007852; --v-fg:#36a980; --v-dn:#c0851f; --v-to:#c34e54;
  --v-track:#1B2231; --v-grid:#242C3B; --v-line:#6FA3E0;
}
.cv{display:block;max-width:100%;height:auto;overflow:visible}
.cv .grid{stroke:var(--v-grid);stroke-width:1}
.cv .zero{stroke:var(--ink3);stroke-width:1;stroke-dasharray:3 3;opacity:.7}
.cv .ln{fill:none;stroke:var(--v-line);stroke-width:2;
  stroke-linejoin:round;stroke-linecap:round}
.cv .area{fill:var(--v-line);opacity:.10}
.cv .dot{fill:var(--v-line);stroke:var(--panel);stroke-width:2}
.cv .ax{fill:var(--ink3);font-size:9.5px;
  font-family:'IBM Plex Mono',ui-monospace,monospace}
.cv .val{fill:var(--ink);font-size:11px;font-weight:600;
  font-family:'IBM Plex Mono',ui-monospace,monospace}
.cv .hit{fill:transparent;cursor:crosshair}
.cv .hit:hover ~ .cross,.cv .hit:focus ~ .cross{opacity:1}
.cv .bar-td{fill:var(--v-td)} .cv .bar-fg{fill:var(--v-fg)}
.cv .bar-dn{fill:var(--v-dn)} .cv .bar-to{fill:var(--v-to)}
.cv .track{fill:var(--v-track)}
.cv .pos{fill:var(--v-td)} .cv .negb{fill:var(--v-to)}
.cv .seglab{fill:#fff;font-size:9.5px;font-weight:600;
  font-family:'IBM Plex Mono',ui-monospace,monospace}
.cv .barlab{fill:var(--ink2);font-size:10px;
  font-family:'IBM Plex Sans',sans-serif}
.legend{display:flex;flex-wrap:wrap;gap:10px;margin-top:7px;font-size:10.5px;
  color:var(--ink2)}
.legend span{display:inline-flex;align-items:center;gap:4px}
.legend i{width:9px;height:9px;border-radius:2px;display:inline-block}
.legend .k-td{background:var(--v-td)} .legend .k-fg{background:var(--v-fg)}
.legend .k-dn{background:var(--v-dn)} .legend .k-to{background:var(--v-to)}
.legend .k-punt{background:var(--v-track)}
.mstrip{display:flex;flex-direction:column;gap:5px;margin-top:2px}
.ms{display:grid;grid-template-columns:150px 52px 1fr 30px;align-items:center;
  gap:8px;font-size:11.5px}
.ms .ml{color:var(--ink2)}
.ms .ml em{font-style:normal;color:var(--ink3);font-size:9.5px;margin-left:5px;
  font-family:'IBM Plex Mono',ui-monospace,monospace}
.ms .mv{font-family:'IBM Plex Mono',ui-monospace,monospace;text-align:right;
  font-variant-numeric:tabular-nums;color:var(--ink)}
.ms .mb{background:var(--v-track);height:7px;border-radius:2px;overflow:hidden}
.ms .mb i{display:block;height:100%;background:var(--v-line);border-radius:2px}
.ms .mr{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:10px;
  color:var(--ink3);text-align:right;font-variant-numeric:tabular-nums}
"""


def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _fmt(v, dp=3, sign=True):
    if v is None:
        return "--"
    return (f"{{:+.{dp}f}}" if sign else f"{{:.{dp}f}}").format(v)


def net_trend(series, width=330, height=118, label="net rating"):
    """Weekly net rating with a readable scale, a drawn zero and a called-out
    endpoint. `series` is [(week, value), ...]."""
    pts = [(w, v) for w, v in (series or []) if v is not None]
    if len(pts) < 2:
        return '<p class="empty">Not enough weekly history to draw a trend.</p>'
    L, R, T, B = 40, 46, 12, 20
    iw, ih = width - L - R, height - T - B
    ys = [v for _, v in pts]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.18 or 0.02
    lo, hi = lo - pad, hi + pad
    if lo > 0:
        lo = -pad          # zero must be inside the frame or it cannot anchor
    if hi < 0:
        hi = pad
    n = len(pts)

    def X(i):
        return L + (iw * i / (n - 1))

    def Y(v):
        return T + ih - (v - lo) / (hi - lo) * ih

    line = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(pts))
    area = (f"{L},{Y(0):.1f} " + line + f" {X(n - 1):.1f},{Y(0):.1f}")
    zero = Y(0)
    last_w, last_v = pts[-1]
    hits = "".join(
        f'<g><circle class="hit" cx="{X(i):.1f}" cy="{Y(v):.1f}" r="9">'
        f'<title>week {w}: {_fmt(v)}</title></circle></g>'
        for i, (w, v) in enumerate(pts))
    return f'''<svg class="cv" viewBox="0 0 {width} {height}" role="img"
 aria-label="{e(label)} by week, ending at {_fmt(last_v)}">
 <line class="zero" x1="{L}" y1="{zero:.1f}" x2="{L + iw}" y2="{zero:.1f}"/>
 <polygon class="area" points="{area}"/>
 <polyline class="ln" points="{line}"/>
 <circle class="dot" cx="{X(n - 1):.1f}" cy="{Y(last_v):.1f}" r="3.6"/>
 <text class="val" x="{X(n - 1) + 7:.1f}" y="{Y(last_v) + 4:.1f}">{_fmt(last_v)}</text>
 <text class="ax" x="{L - 5}" y="{T + 4}" text-anchor="end">{_fmt(hi, 2)}</text>
 <text class="ax" x="{L - 5}" y="{zero + 3:.1f}" text-anchor="end">0</text>
 <text class="ax" x="{L - 5}" y="{T + ih + 3}" text-anchor="end">{_fmt(lo, 2)}</text>
 <text class="ax" x="{L}" y="{height - 6}">wk {pts[0][0]}</text>
 <text class="ax" x="{L + iw}" y="{height - 6}" text-anchor="end">wk {last_w}</text>
 {hits}
</svg>'''


def off_def(off, dfn, off_rank, dfn_rank, width=330, height=92):
    """Diverging bars from a shared zero.

    Defense is stored so that NEGATIVE is good. It is flipped here so both bars
    point right when the unit is good - otherwise the reader has to hold a sign
    convention in their head to read a picture, which defeats the picture.
    """
    if off is None or dfn is None:
        return '<p class="empty">No split available.</p>'
    d_good = -dfn
    span = max(0.12, abs(off), abs(d_good)) * 1.15
    L, R = 84, 52
    iw = width - L - R
    mid = L + iw / 2

    def bar(v, y, cls, name, rank):
        w = abs(v) / span * (iw / 2)
        x = mid if v >= 0 else mid - w
        return (f'<rect class="{cls}" x="{x:.1f}" y="{y}" width="{max(w, 1.5):.1f}"'
                f' height="16" rx="2"><title>{e(name)} {_fmt(v)}, rank {rank}</title></rect>'
                f'<text class="barlab" x="{L - 8}" y="{y + 12}" text-anchor="end">'
                f'{e(name)}</text>'
                f'<text class="val" x="{width - R + 6}" y="{y + 12}">{_fmt(v)}</text>'
                f'<text class="ax" x="{width - 4}" y="{y + 12}" text-anchor="end">'
                f'#{rank}</text>')

    return f'''<svg class="cv" viewBox="0 0 {width} {height}" role="img"
 aria-label="offense {_fmt(off)}, defense {_fmt(d_good)}, higher is better">
 <line class="zero" x1="{mid:.1f}" y1="14" x2="{mid:.1f}" y2="{height - 20}"/>
 {bar(off, 20, "pos" if off >= 0 else "negb", "Offense", off_rank or "--")}
 {bar(d_good, 46, "pos" if d_good >= 0 else "negb", "Defense", dfn_rank or "--")}
 <text class="ax" x="{mid:.1f}" y="{height - 6}" text-anchor="middle">league average</text>
 <text class="ax" x="{L}" y="10">worse</text>
 <text class="ax" x="{L + iw}" y="10" text-anchor="end">better</text>
</svg>'''


def metric_strip(rows, width=330):
    """Percentile bars, one hue, for metrics that actually carry.

    `rows` is [(label, value, pct, rank, dp, r)]. Sequential by definition - a
    percentile is a magnitude - so one hue and no legend.

    `r` is the metric's OWN measured year-over-year stability from
    stability.py, printed beside it. A team page that shows a number without
    saying how much that number carries invites the reader to over-read it;
    pass rate over expected at 0.46 and defensive EPA at 0.21 do not deserve
    equal weight and the page should say so rather than imply parity by
    drawing them the same size.
    """
    if not rows:
        return '<p class="empty">No metrics available.</p>'
    out = []
    for label, val, pct, rank, dp, r in rows:
        p = max(0.0, min(1.0, pct if pct is not None else 0.0))
        out.append(
            f'<div class="ms"><span class="ml">{e(label)}'
            f'<em title="year-over-year stability measured by stability.py">'
            f'r{_fmt(r, 2)[1:] if r is not None else "--"}</em></span>'
            f'<span class="mv">{_fmt(val, dp, False)}</span>'
            f'<span class="mb"><i style="width:{p * 100:.1f}%"></i></span>'
            f'<span class="mr">#{rank if rank else "--"}</span></div>')
    return f'<div class="mstrip">{"".join(out)}</div>'


def drive_mix(td, fg, dn, to, width=330, height=54):
    """One stacked bar. Punts are the unfilled track, not a segment.

    Segments carry direct labels wherever they fit, and the same numbers repeat
    as text below - the light palette's two middle steps sit under 3:1 against
    white, which obligates that relief rather than leaving color to carry it.
    """
    vals = {"td": td or 0, "fg": fg or 0, "dn": dn or 0, "to": to or 0}
    if sum(vals.values()) <= 0:
        return '<p class="empty">No drive history.</p>'
    GAP = 2
    y, h = 10, 22
    x = 0.0
    segs = []
    for key, name in DRIVE_KEYS:
        v = vals[key]
        w = v / 100.0 * width
        if w > 0.5:
            segs.append(
                f'<rect class="bar-{key}" x="{x:.1f}" y="{y}" '
                f'width="{max(w - GAP, 1):.1f}" height="{h}" rx="2">'
                f'<title>{e(name)} {v:.1f}% of drives</title></rect>')
            if w > 34:
                segs.append(
                    f'<text class="seglab" x="{x + (w - GAP) / 2:.1f}" '
                    f'y="{y + 15}" text-anchor="middle">{v:.0f}%</text>')
        x += w
    punt = max(0.0, width - x)
    if punt > 0.5:
        segs.append(f'<rect class="track" x="{x:.1f}" y="{y}" '
                    f'width="{punt:.1f}" height="{h}" rx="2">'
                    f'<title>Punt or end of half {punt / width * 100:.1f}%</title></rect>')
    return f'''<svg class="cv" viewBox="0 0 {width} {height}" role="img"
 aria-label="drive outcomes: TD {vals['td']:.1f}%, FG {vals['fg']:.1f}%,
 downs {vals['dn']:.1f}%, turnover {vals['to']:.1f}%">
 {''.join(segs)}
 <text class="ax" x="0" y="8">share of drives</text>
 <text class="ax" x="{width}" y="8" text-anchor="end">punt / end half is the track</text>
</svg>
<div class="legend">
 <span><i class="k-td"></i>TD {vals['td']:.1f}%</span>
 <span><i class="k-fg"></i>FG {vals['fg']:.1f}%</span>
 <span><i class="k-dn"></i>Downs {vals['dn']:.1f}%</span>
 <span><i class="k-to"></i>Turnover {vals['to']:.1f}%</span>
 <span><i class="k-punt"></i>Punt / end half {punt / width * 100:.1f}%</span>
</div>'''


def season_line(points, width=330, height=96):
    """Net rating by season - the long arc behind a single year's line.

    `points` is [(season, value), ...]. Few enough points that every one is
    labeled; that is a table's job at this size, so the marks carry values.
    """
    pts = [(s, v) for s, v in (points or []) if v is not None]
    if len(pts) < 2:
        return '<p class="empty">Not enough seasons.</p>'
    L, R, T, B = 34, 20, 14, 20
    iw, ih = width - L - R, height - T - B
    ys = [v for _, v in pts]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.25 or 0.02
    lo, hi = min(lo - pad, -0.01), max(hi + pad, 0.01)
    n = len(pts)

    def X(i):
        return L + (iw * i / (n - 1))

    def Y(v):
        return T + ih - (v - lo) / (hi - lo) * ih

    line = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(pts))
    dots = "".join(
        f'<circle class="dot" cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3">'
        f'<title>{s}: {_fmt(v)}</title></circle>'
        f'<text class="ax" x="{X(i):.1f}" y="{height - 6}" '
        f'text-anchor="middle">{str(s)[2:]}</text>'
        for i, (s, v) in enumerate(pts))
    return f'''<svg class="cv" viewBox="0 0 {width} {height}" role="img"
 aria-label="net rating by season, {pts[0][0]} to {pts[-1][0]}">
 <line class="zero" x1="{L}" y1="{Y(0):.1f}" x2="{L + iw}" y2="{Y(0):.1f}"/>
 <polyline class="ln" points="{line}"/>
 {dots}
 <text class="ax" x="{L - 5}" y="{T + 4}" text-anchor="end">{_fmt(hi, 2)}</text>
 <text class="ax" x="{L - 5}" y="{Y(0) + 3:.1f}" text-anchor="end">0</text>
 <text class="ax" x="{L - 5}" y="{T + ih + 3}" text-anchor="end">{_fmt(lo, 2)}</text>
</svg>'''
