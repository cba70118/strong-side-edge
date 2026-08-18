"""dash_components.py - the named components from references/dashboard-spec.md.

Each function is one component from section 2 and honours its "Never" clause.
Screens are composed from these; nothing on a dashboard screen is authored as
free HTML.

The one to read carefully is `disagreement_line`. It is the system's thesis
drawn as a repeated primitive: market number as a bone tick, model number as a
market-colored tick, the gap shaded, the measured error band as a translucent
span. When the band covers the market tick the row is NO EDGE and desaturates -
which, on sides, is every row, because the band is the backtest error and the
backtest says we do not beat the close.
"""

from __future__ import annotations

import html

VERDICTS = ("PLAY", "LEAN", "PASS", "NO EDGE", "STALE")


def e(s) -> str:
    return html.escape(str(s), quote=True)


def num(v, dp=1, signed=False) -> str:
    if v is None:
        return "&ndash;"
    return f"{v:+.{dp}f}" if signed else f"{v:.{dp}f}"


def disagreement_line(d, family="sides", width=260, height=34,
                      floor=None) -> str:
    """Market vs model on one axis, with the error band. Never without the band.

    `floor` clamps the axis where the quantity cannot go below a value -
    receiving yards cannot be negative, and padding a band whose low edge is
    zero drew an axis starting at -16.6 yards.
    """
    if not d:
        return '<span class="mut">&ndash;</span>'
    lo = min(d["band_low"], d["market"], d["model"])
    hi = max(d["band_high"], d["market"], d["model"])
    pad = (hi - lo) * 0.12 or 1.0
    lo, hi = lo - pad, hi + pad
    if floor is not None:
        lo = max(lo, floor)
    span = hi - lo

    def X(v):
        return (v - lo) / span * (width - 2) + 1

    overlap = d["band_low"] <= d["market"] <= d["band_high"]
    cls = " dl-flat" if overlap else ""
    bx0, bx1 = X(d["band_low"]), X(d["band_high"])
    gx0, gx1 = sorted((X(d["market"]), X(d["model"])))
    return (
        f'<svg class="dl{cls}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Market {d["market"]}, model {d["model"]}, band '
        f'{d["band_low"]} to {d["band_high"]}">'
        f'<title>Market {d["market"]}{d["unit"]}, model {d["model"]}{d["unit"]}, '
        f'error band {d["band_low"]} to {d["band_high"]}</title>'
        f'<rect x="{bx0:.1f}" y="11" width="{max(bx1-bx0,1):.1f}" height="12" '
        f'class="dl-band f-{family}"/>'
        f'<rect x="{gx0:.1f}" y="15" width="{max(gx1-gx0,1):.1f}" height="4" '
        f'class="dl-gap f-{family}"/>'
        f'<line x1="{X(d["model"]):.1f}" y1="8" x2="{X(d["model"]):.1f}" '
        f'y2="26" class="dl-model f-{family}"/>'
        f'<line x1="{X(d["market"]):.1f}" y1="6" x2="{X(d["market"]):.1f}" '
        f'y2="28" class="dl-market"/>'
        f'<text x="1" y="33" class="dl-ax">{num(lo)}</text>'
        f'<text x="{width-1}" y="33" class="dl-ax" text-anchor="end">'
        f'{num(hi)}</text></svg>')


def verdict_chip(v) -> str:
    """One of five. Never a sixth."""
    v = v if v in VERDICTS else "PASS"
    key = v.lower().replace(" ", "")
    return f'<span class="chip v-{key}">{e(v)}</span>'


def edge_bar(edge_pct, threshold, width=64) -> str:
    """Never renders an under-threshold edge as positive.

    Bar length encodes POSITIVE edge only. Using abs() drew a longer bar the
    more negative the number, so -13.1% rendered as a nearly full track and
    read as the strongest row on the screen.
    """
    if edge_pct is None:
        return '<span class="mut">&ndash;</span>'
    live = edge_pct >= threshold
    w = (min(edge_pct / max(threshold * 2, 1), 1.0) * width
         if edge_pct > 0 else 0)
    cls = "eb-live" if live else "eb-flat"
    return (f'<span class="eb"><span class="eb-t" style="width:{width}px">'
            f'<span class="eb-f {cls}" style="width:{w:.0f}px"></span></span>'
            f'<span class="eb-n {"pos" if live else "mut"}">'
            f'{edge_pct:+.1f}%</span></span>')


def fmt_clock(ts) -> str:
    """Absolute, per spec section 4 format. A static page cannot carry a
    relative time honestly - "6h ago" baked in is wrong by tomorrow."""
    s = str(ts)
    try:
        from datetime import datetime
        d = datetime.fromisoformat(s)
        return d.strftime("%a %I:%M").lstrip("0") + ("a" if d.hour < 12 else "p")
    except ValueError:
        return s[11:16]


def price_row(r, threshold) -> str:
    """Never a price without its book and time. Stake is excluded by decision."""
    return (
        f'<tr class="{"stale" if r.get("stale") else ""}">'
        f'<td class="cap">{e(r["side"])}</td>'
        f'<td class="n mono">{r["price"]:+d}</td>'
        f'<td>{e(r["book"])}</td>'
        f'<td class="mono mut">{e(r.get("taken_disp") or r["taken_at"][11:16])}'
        f'{stale_badge(r) if r.get("stale") else ""}</td>'
        f'<td class="n mono">{round(r["fair"]):+d}</td>'
        f'<td class="n mono mut">{r["fair_pct"]}%</td>'
        f'<td>{edge_bar(r["edge_pct"], threshold)}</td>'
        f'<td>{verdict_chip(r["verdict"])}</td>'
        f'<td class="act"><button class="btn" data-log="{e(r.get("log_cmd") or "")}"'
        f'>Log bet</button></td></tr>')


def kill_card(thesis, kill, status) -> str:
    """Never renders with either line missing."""
    if not thesis or not kill:
        return ""
    st = status or "not evaluable"
    return (f'<div class="kc"><div class="kc-l"><span class="kc-k">Thesis</span>'
            f'<p>{e(thesis)}</p></div>'
            f'<div class="kc-l"><span class="kc-k">Kill</span><p>{e(kill)}</p>'
            f'<span class="kc-st">Status: {e(st)}</span></div></div>')


def pass_line(p) -> str:
    return (f'<tr><td>{e(p["game"])}</td><td class="cap">'
            f'{e(p["market_type"])}</td><td class="mut">{e(p["reason"])}</td>'
            f'</tr>')


def kpi_tile(k) -> str:
    return (f'<div class="kpi {"kpi-p" if k["weight"] == "primary" else ""}">'
            f'<span class="kpi-l">{e(k["label"])}</span>'
            f'<span class="kpi-v mono">{e(k["value"])}</span>'
            f'<span class="kpi-d">{e(k["delta"])}</span></div>')


def clv_spark(series, width=132, height=30) -> str:
    """Zero line always drawn."""
    if not series:
        return '<span class="mut">no graded bets</span>'
    lo, hi = min(series + [0]), max(series + [0])
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    n = len(series)

    def X(i):
        return (i / max(n - 1, 1)) * (width - 4) + 2

    def Y(v):
        return height - 4 - (v - lo) / (hi - lo) * (height - 8)

    pts = " ".join(f'{"M" if i == 0 else "L"}{X(i):.1f},{Y(v):.1f}'
                   for i, v in enumerate(series))
    zy = Y(0)
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Rolling CLV, zero marked">'
            f'<line x1="0" y1="{zy:.1f}" x2="{width}" y2="{zy:.1f}" '
            f'class="sp-zero"/><path d="{pts}" class="sp-l"/>'
            f'<text x="{width-1}" y="{zy-3:.1f}" class="dl-ax" '
            f'text-anchor="end">0</text></svg>')


def line_move_track(open_v, now_v, your_v, unit="", width=210,
                    height=30) -> str:
    """Open, now and your number on one axis. Never current alone."""
    vals = [v for v in (open_v, now_v, your_v) if v is not None]
    if len(vals) < 2:
        return '<span class="mut">&ndash;</span>'
    lo, hi = min(vals), max(vals)
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.2
    lo, hi = lo - pad, hi + pad

    def X(v):
        return (v - lo) / (hi - lo) * (width - 2) + 1

    g = [f'<line x1="1" y1="16" x2="{width-1}" y2="16" class="lm-ax"/>']
    g.append(f'<line x1="{X(open_v):.1f}" y1="8" x2="{X(open_v):.1f}" y2="24" '
             f'class="lm-open"><title>Open {open_v}{unit}</title></line>')
    g.append(f'<line x1="{X(now_v):.1f}" y1="6" x2="{X(now_v):.1f}" y2="26" '
             f'class="lm-now"><title>Now {now_v}{unit}</title></line>')
    if your_v is not None:
        g.append(f'<circle cx="{X(your_v):.1f}" cy="16" r="3.4" '
                 f'class="lm-you"><title>Your number {your_v}{unit}</title>'
                 f'</circle>')
    g.append(f'<text x="1" y="29" class="dl-ax">open {num(open_v)}</text>')
    g.append(f'<text x="{width-1}" y="29" class="dl-ax" text-anchor="end">'
             f'now {num(now_v)}</text>')
    return (f'<svg class="lm" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Open to now">{"".join(g)}</svg>')


def stale_badge(r) -> str:
    return '<span class="badge-stale">STALE</span>'


def source_stamp(source, fetched_at) -> str:
    return (f'<span class="src">{e(source)} &middot; '
            f'{e(str(fetched_at)[:16])}</span>')


COMPONENT_CSS = """
.chip{display:inline-block;font-family:"IBM Plex Sans Condensed",sans-serif;
  font-size:10px;font-weight:700;letter-spacing:.06em;padding:2px 7px;
  border-radius:2px;white-space:nowrap}
.v-play{background:var(--pos);color:#fff}
.v-lean{background:transparent;color:var(--ink);border:1px solid var(--ink3)}
.v-pass{background:transparent;color:var(--ink3);border:1px solid var(--rule)}
.v-noedge{background:transparent;color:var(--ink3);border:1px dashed var(--rule)}
.v-stale{background:var(--neg);color:#fff}
.badge-stale{margin-left:6px;font-size:9px;font-weight:700;letter-spacing:.06em;
  color:var(--neg);border:1px solid var(--neg);border-radius:2px;padding:0 4px}

.dl{width:100%;height:34px;display:block}
.dl-band{opacity:.20}
.dl-gap{opacity:.55}
.dl-model{stroke-width:2}
.dl-market{stroke:var(--bone);stroke-width:2.5}
.dl.dl-flat .dl-band,.dl.dl-flat .dl-gap{opacity:.10}
.dl.dl-flat .dl-model{opacity:.35}
.dl-ax{fill:var(--ink3);font-size:8.5px;font-family:"IBM Plex Mono",monospace}
.f-sides{fill:var(--sides);stroke:var(--sides)}
.f-totals{fill:var(--totals);stroke:var(--totals)}
.f-props{fill:var(--props);stroke:var(--props)}

.eb{display:inline-flex;align-items:center;gap:7px}
.eb-t{display:inline-block;height:6px;background:var(--rule2);border-radius:1px;
  overflow:hidden}
.eb-f{display:block;height:6px;border-radius:1px}
.eb-live{background:var(--pos)}
.eb-flat{background:var(--ink3);opacity:.5}
.eb-n{font-family:"IBM Plex Mono",monospace;font-size:11.5px}
.pos{color:var(--pos);font-weight:600}

.kc{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:10px 12px;
  border-top:1px solid var(--rule2);background:var(--plane)}
.kc-k{font-family:"IBM Plex Sans Condensed",sans-serif;font-size:9.5px;
  font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--ink3)}
.kc-l p{margin:2px 0 0;font-size:12.5px;line-height:1.4}
.kc-st{font-size:10px;color:var(--ink3);font-family:"IBM Plex Mono",monospace}

.kpi{display:flex;flex-direction:column;gap:1px;padding:8px 14px;
  border-right:1px solid var(--rule2);min-width:104px}
.kpi:last-child{border-right:0}
.kpi-l{font-family:"IBM Plex Sans Condensed",sans-serif;font-size:9.5px;
  font-weight:700;letter-spacing:.09em;text-transform:uppercase;
  color:var(--ink3)}
.kpi-v{font-size:20px;font-weight:600;line-height:1.1}
.kpi-p .kpi-v{font-size:24px}
.kpi-d{font-size:10.5px;color:var(--ink3)}

.spark{width:132px;height:30px}
.sp-l{fill:none;stroke:var(--pos);stroke-width:1.6}
.sp-zero{stroke:var(--rule);stroke-width:1}
.lm{width:100%;height:30px;display:block}
.lm-ax{stroke:var(--rule2);stroke-width:1}
.lm-open{stroke:var(--ink3);stroke-width:1.5;stroke-dasharray:2 2}
.lm-now{stroke:var(--bone);stroke-width:2.5}
.lm-you{fill:var(--pos)}
.src{font-size:10px;color:var(--ink3);font-family:"IBM Plex Mono",monospace}
"""
