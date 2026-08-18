"""slate_charts.py - inline SVG figures for the week brief.

Three charts, each earning its place by showing something a table cannot.

  margin_chart   the simulated margin distribution, with the market handicap
                 drawn on it. The whole value of the simulator is the SHAPE -
                 the spikes at 3 and 7 and where the line falls against them -
                 and reducing that to four percentages threw it away.

  book_strip     every book's number on one game as a dot strip. With 29 books
                 in the capture, where they disagree IS the product; a single
                 "best price" column hides it.

  proj_bullet    a player's quoting band with our line on it, and the market
                 line when one exists. Replaces four numeric columns, which is
                 also what stops the projections tables scrolling sideways.

No chart library - a strict CSP and a self-contained file rule that out, and
these shapes are simple enough that hand-built SVG is clearer anyway.

Conventions kept deliberately consistent across all three: one accent hue for
our own numbers, a neutral for observed market numbers, thin marks, recessive
axes, and direct labels only where a reader would otherwise have to count
gridlines. Every figure carries a <title> for screen readers and a plain-text
fallback in the surrounding markup.
"""

from __future__ import annotations

import html


def e(s) -> str:
    return html.escape(str(s), quote=True)


def margin_chart(pmf: dict, handicap: float, home: str, away: str,
                 width: int = 430, height: int = 120) -> str:
    """Column chart of P(home margin = k), with the market handicap marked.

    `handicap` is the home team's spread in market convention (negative when
    the home side lays points), so the cover threshold sits at -handicap on the
    margin axis. Drawing the threshold rather than stating it is the point: a
    reader can see instantly whether the line sits on a spike or beside one.
    """
    if not pmf:
        return ""
    ks = sorted(int(k) for k in pmf)
    lo, hi = max(min(ks), -24), min(max(ks), 24)
    span = hi - lo + 1
    top = max(pmf.values()) or 1.0

    pad_l, pad_b, pad_t = 4, 15, 6
    plot_w = width - pad_l * 2
    plot_h = height - pad_b - pad_t
    bw = plot_w / span

    def x_of(k):
        return pad_l + (k - lo) * bw

    bars = []
    for k in range(lo, hi + 1):
        v = pmf.get(str(k), 0.0)
        if v <= 0:
            continue
        h = v / top * plot_h
        key = abs(k) in (3, 7)
        # A 2px gap between fills keeps adjacent columns readable as separate
        # marks rather than one block.
        bars.append(
            f'<rect x="{x_of(k) + 1:.1f}" y="{pad_t + plot_h - h:.1f}" '
            f'width="{max(bw - 2, 1):.1f}" height="{h:.1f}" rx="1.5" '
            f'class="{"mk" if key else "mb"}"><title>Margin '
            f'{k:+d}: {v * 100:.1f}%</title></rect>')

    # The rule sits at the MARGIN the home team must beat, but the label must
    # read as the HANDICAP a bettor sees on the board - those are opposite
    # signs, and labeling the threshold made the chart contradict the board.
    thr = -handicap
    tx = x_of(thr) + bw / 2
    lab = "PK" if handicap == 0 else f"{handicap:+g}"
    marks = (f'<line x1="{tx:.1f}" y1="{pad_t - 4}" x2="{tx:.1f}" '
             f'y2="{pad_t + plot_h}" class="mline"/>'
             f'<text x="{tx:.1f}" y="{pad_t - 6}" class="mlab" '
             f'text-anchor="middle">{e(home)} {lab}</text>')

    ticks = []
    for k in (lo, 0, hi):
        lbl = "0" if k == 0 else f"{k:+d}"
        ticks.append(f'<text x="{x_of(k) + bw / 2:.1f}" y="{height - 4}" '
                     f'class="ax" text-anchor="middle">{lbl}</text>')
    zx = x_of(0) + bw / 2
    ticks.append(f'<line x1="{zx:.1f}" y1="{pad_t}" x2="{zx:.1f}" '
                 f'y2="{pad_t + plot_h}" class="zero"/>')

    return (f'<svg class="fig" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="Simulated margin distribution">'
            f'<title>Margin distribution: {e(away)} at {e(home)}. Bars are the '
            f'probability the game ends on each margin; the rule marks the '
            f'market handicap.</title>'
            f'{"".join(bars)}{"".join(ticks)}{marks}</svg>')


def book_strip(books: list, sharp_line, width: int = 200,
               height: int = 46) -> str:
    """How many books sit on each number, as a compact count histogram.

    The first attempt drew one dot per book and stacked duplicates upward. With
    26 books spread over only three distinct lines that produced a 100px column
    inside a 34px box - the marks overflowed the table row. Books cluster hard,
    so the interesting quantity was never the individual dot but the COUNT at
    each number, and a column encodes that in the space available.

    The placeable share is drawn solid and the rest hollow, because a number
    only matters if it can actually be bet; the sharp reference gets a rule.
    """
    if not books:
        return ""
    counts = {}
    for b in books:
        c = counts.setdefault(b["line"], {"n": 0, "place": 0, "sharp": False})
        c["n"] += 1
        c["place"] += 1 if b["placeable"] else 0
        c["sharp"] = c["sharp"] or bool(b["sharp"])
    lines = sorted(counts)
    top = max(c["n"] for c in counts.values()) or 1

    pad_b, pad_t = 13, 4
    plot_h = height - pad_b - pad_t
    n = len(lines)
    slot = width / max(n, 1)
    bw = min(slot * 0.62, 24)

    parts = []
    for i, ln_ in enumerate(lines):
        c = counts[ln_]
        cx = slot * (i + 0.5)
        h = c["n"] / top * plot_h
        hp = c["place"] / top * plot_h
        y0 = pad_t + plot_h - h
        parts.append(
            f'<rect x="{cx - bw / 2:.1f}" y="{y0:.1f}" width="{bw:.1f}" '
            f'height="{h:.1f}" rx="2" class="bk-all"><title>{c["n"]} books at '
            f'{ln_:+g}, {c["place"]} placeable</title></rect>')
        if hp > 0:
            parts.append(
                f'<rect x="{cx - bw / 2:.1f}" y="{pad_t + plot_h - hp:.1f}" '
                f'width="{bw:.1f}" height="{hp:.1f}" rx="2" class="bk-place">'
                f'<title>{c["place"]} placeable at {ln_:+g}</title></rect>')
        cls = "ax bk-sharp" if c["sharp"] else "ax"
        parts.append(f'<text x="{cx:.1f}" y="{height - 3}" class="{cls}" '
                     f'text-anchor="middle">{ln_:+g}</text>')
        if c["sharp"]:
            parts.append(f'<line x1="{cx:.1f}" y1="{pad_t - 2}" x2="{cx:.1f}" '
                         f'y2="{pad_t + plot_h}" class="mline"/>')
    parts.append(f'<line x1="0" y1="{pad_t + plot_h:.1f}" x2="{width}" '
                 f'y2="{pad_t + plot_h:.1f}" class="ax-l"/>')
    return (f'<svg class="fig" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Books per line">'
            f'<title>Number of books offering each spread. Solid is placeable; '
            f'the dashed rule is the sharp reference.</title>'
            f'{"".join(parts)}</svg>')


def proj_bullet(our_line, band, market_line=None, scale_max=None,
                width: int = 150, height: int = 20) -> str:
    """Quoting band with our line on it, and the market line when posted.

    The band is the 25th-75th percentile - the only range the distribution is
    calibrated over - so a market line drawn outside it is visibly outside the
    range we will quote, rather than merely footnoted as refused.
    """
    if our_line is None or not band:
        return '<span class="mut">&mdash;</span>'
    top = scale_max or max(band[1], market_line or 0, our_line) * 1.15
    if top <= 0:
        return '<span class="mut">&mdash;</span>'

    def x_of(v):
        return min(max(v / top, 0.0), 1.0) * (width - 2) + 1

    b0, b1 = x_of(band[0]), x_of(band[1])
    parts = [f'<rect x="{b0:.1f}" y="6" width="{max(b1 - b0, 1):.1f}" '
             f'height="8" rx="3" class="pb-band"><title>Quote band '
             f'{band[0]:g} to {band[1]:g}</title></rect>',
             f'<line x1="{x_of(our_line):.1f}" y1="3" '
             f'x2="{x_of(our_line):.1f}" y2="17" class="pb-our">'
             f'<title>Our line {our_line:g}</title></line>']
    if market_line is not None:
        inside = band[0] <= market_line <= band[1]
        parts.append(
            f'<circle cx="{x_of(market_line):.1f}" cy="10" r="3.4" '
            f'class="{"pb-mkt" if inside else "pb-mkt-out"}">'
            f'<title>Market {market_line:g} - '
            f'{"inside" if inside else "outside"} the quote band</title>'
            f'</circle>')
    return (f'<svg class="fig" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Projection band">{"".join(parts)}</svg>')


CHART_CSS = """
.fig{display:block;width:100%;height:auto;overflow:visible}
.fig .mb{fill:var(--accent);opacity:.42}
.fig .mk{fill:var(--accent);opacity:.95}
.fig .mline{stroke:var(--neg);stroke-width:1.5;stroke-dasharray:3 2}
.fig .mlab{fill:var(--neg);font-size:9px;font-weight:700;
  font-family:"Roboto Mono",monospace}
.fig .zero{stroke:var(--line);stroke-width:1}
.fig .ax{fill:var(--ink3);font-size:8.5px;font-family:"Roboto Mono",monospace}
.fig .ax-l{stroke:var(--line);stroke-width:1}
.fig .bk-all{fill:var(--ink3);opacity:.3}
.fig .bk-place{fill:var(--accent);opacity:.85}
.fig .bk-sharp{fill:var(--neg);font-weight:700}
.fig .pb-band{fill:var(--accent);opacity:.22}
.fig .pb-our{stroke:var(--accent);stroke-width:2}
.fig circle.pb-mkt{fill:var(--ink2);stroke:var(--panel);stroke-width:1.5}
.fig circle.pb-mkt-out{fill:var(--neg);stroke:var(--panel);stroke-width:1.5}
.figleg{font-size:10.5px;color:var(--ink3);margin:3px 0 0}
.figleg i{font-style:normal;display:inline-flex;align-items:center;gap:4px;
  margin-right:11px}
.figleg .sw{width:9px;height:9px;border-radius:50%;display:inline-block}
.figleg .sw.place{background:var(--accent);border-radius:2px}
.figleg .sw.other{background:var(--ink3);opacity:.3;border-radius:2px}
.figleg .sw.key{background:var(--accent);border-radius:2px;opacity:.95}
.figleg .sw.oth{background:var(--accent);border-radius:2px;opacity:.42}
"""
