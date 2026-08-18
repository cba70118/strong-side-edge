"""team_charts.py - season-level figures for the Teams panel.

Kept separate from slate_charts.py, which serves the weekly board. These read
the last COMPLETED season plus the current preseason prior, because the
interesting object is our measurement of what a club was sitting beside the
market's view of what it will be, with the gap between them visible.
"""

from __future__ import annotations

import html


def e(s) -> str:
    return html.escape(str(s), quote=True)


def quadrant(teams: dict, mark_html, width: int = 720,
             height: int = 480) -> str:
    """Offense against defense, every club placed by measured EPA per play.

    The canonical football-analytics view, and the one chart where a reader
    finds their own club first and reasons outward. Defensive EPA is inverted
    so up-and-right is unambiguously good on both axes; plotting it raw puts
    the best defenses at the bottom and inverts the intuition the quadrant
    exists to give.

    Marks are HTML positioned over the SVG frame rather than <image> elements
    inside it. An <image> needs its own href, which meant inlining a full
    base64 crest per club - 216 KB, a second complete copy of what the CSS
    already carries for the tables, and enough to push the page past what the
    artifact host would accept. Positioned spans reuse those same classes.
    """
    pts = [(k, v) for k, v in teams.items()
           if v.get("oepa") is not None and v.get("depa") is not None]
    if not pts:
        return ""
    xs = [v["oepa"] for _, v in pts]
    ys = [-v["depa"] for _, v in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    px = (x1 - x0) * 0.10 or 0.01
    py = (y1 - y0) * 0.10 or 0.01
    x0, x1, y0, y1 = x0 - px, x1 + px, y0 - py, y1 + py
    pad = 40
    pw, ph = width - pad * 2, height - pad * 2

    def fx(v):
        return (pad + (v - x0) / (x1 - x0) * pw) / width * 100

    def fy(v):
        return (pad + ph - (v - y0) / (y1 - y0) * ph) / height * 100

    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    g = [
        '<rect x="%d" y="%d" width="%d" height="%d" class="qbg"/>'
        % (pad, pad, pw, ph),
        '<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" class="qax"/>'
        % (fx(mx) / 100 * width, pad, fx(mx) / 100 * width, pad + ph),
        '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="qax"/>'
        % (pad, fy(my) / 100 * height, pad + pw, fy(my) / 100 * height),
        '<text x="%d" y="%d" class="qlab" text-anchor="end">'
        'better offense &#8594;</text>' % (pad + pw, height - 12),
        '<text transform="rotate(-90)" x="%d" y="%d" class="qlab" '
        'text-anchor="end">better defense &#8594;</text>' % (-pad, 14),
    ]
    for name, qx, qy, anc in (("elite", pad + pw - 6, pad + 14, "end"),
                              ("offense carries", pad + pw - 6, pad + ph - 7, "end"),
                              ("defense carries", pad + 6, pad + 14, "start"),
                              ("rebuilding", pad + 6, pad + ph - 7, "start")):
        g.append('<text x="%d" y="%d" class="qquad" text-anchor="%s">%s</text>'
                 % (qx, qy, anc, name))

    # The class goes ON the span, not inside it - passing it as content
    # rendered the literal text "lg-SEA" where the crest should be.
    marks = "".join(
        '<span class="qmark %s" style="left:%.2f%%;top:%.2f%%" title="%s: '
        'offense %+.3f, defense %+.3f EPA per play">%s</span>'
        % (mark_html(k), fx(v["oepa"]), fy(-v["depa"]), e(k),
           v["oepa"], v["depa"], "" if mark_html(k) else e(k))
        for k, v in pts)

    return ('<div class="qwrap" style="aspect-ratio:%d/%d">'
            '<svg class="fig qsvg" viewBox="0 0 %d %d" role="img" '
            'aria-label="Offense versus defense EPA per play">'
            '<title>Each club placed by offensive and defensive EPA per play. '
            'Up and right is better on both axes.</title>%s</svg>%s</div>'
            % (width, height, width, height, "".join(g), marks))


def sparkline(series, width: int = 104, height: int = 22) -> str:
    """Weekly net rating across a season, as one unlabeled line.

    A sparkline answers a single question - which way was this club going - and
    it lives inside a table row, so it carries no axes. Zero is the only
    reference drawn, because the sign is what a reader is scanning for.
    """
    if not series or len(series) < 2:
        return ""
    ys = [v for _, v in series]
    lo, hi = min(ys), max(ys)
    if hi == lo:
        lo, hi = lo - 0.01, hi + 0.01
    n = len(series)

    def X(i):
        return i / (n - 1) * (width - 3) + 1.5

    def Y(v):
        return height - 2 - (v - lo) / (hi - lo) * (height - 4)

    d = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", X(i), Y(v))
                 for i, (_, v) in enumerate(series))
    zero = ""
    if lo < 0 < hi:
        zero = ('<line x1="0" y1="%.1f" x2="%d" y2="%.1f" class="spz"/>'
                % (Y(0), width, Y(0)))
    last = series[-1][1]
    cls = "spup" if last >= 0 else "spdn"
    return ('<svg class="fig sp" viewBox="0 0 %d %d" role="img" '
            'aria-label="Weekly rating trend"><title>Weekly net rating through '
            'the season, ending %+.3f</title>%s<path d="%s" class="spl"/>'
            '<circle cx="%.1f" cy="%.1f" r="2.6" class="%s"/></svg>'
            % (width, height, last, zero, d, X(n - 1), Y(last), cls))


def rerating(teams: dict, width: int = 700, height: int = 440) -> str:
    """How far the market has moved each club off what we measured.

    A dumbbell rather than paired bars: the quantity of interest is the
    DISTANCE and its direction, and a connected pair reads as one object where
    two bars read as two. Sorted by the size of the move, so the clubs the
    market most disagrees with us about sit at the ends.
    """
    rows = [(k, v["rating_prior"], v["market_rating"])
            for k, v in teams.items()
            if v.get("rating_prior") is not None
            and v.get("market_rating") is not None]
    if not rows:
        return ""
    rows.sort(key=lambda r: r[2] - r[1])
    vals = [x for _, a, b in rows for x in (a, b)]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 0.01
    pad_l, pad_r, pad_t, pad_b = 46, 18, 16, 18
    pw = width - pad_l - pad_r
    row_h = (height - pad_t - pad_b) / len(rows)

    def X(v):
        return pad_l + (v - lo) / (hi - lo) * pw

    g = []
    if lo < 0 < hi:
        g.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%.1f" class="qax"/>'
                 % (X(0), pad_t - 4, X(0), pad_t + row_h * len(rows)))
    for i, (k, a, b) in enumerate(rows):
        y = pad_t + row_h * (i + 0.5)
        up = b >= a
        g.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="%s"/>'
                 % (X(a), y, X(b), y, "dbup" if up else "dbdn"))
        g.append('<circle cx="%.1f" cy="%.1f" r="2.6" class="dba"><title>'
                 '%s measured %+.3f</title></circle>' % (X(a), y, e(k), a))
        g.append('<circle cx="%.1f" cy="%.1f" r="3.6" class="%s"><title>'
                 '%s market %+.3f (%+.3f)</title></circle>'
                 % (X(b), y, "dbup-d" if up else "dbdn-d", e(k), b, b - a))
        g.append('<text x="%d" y="%.1f" class="qtxt" text-anchor="end">%s</text>'
                 % (pad_l - 8, y + 3, e(k)))
    g.append('<text x="%d" y="%d" class="qlab">&#8592; market rates lower</text>'
             % (pad_l, height - 4))
    g.append('<text x="%d" y="%d" class="qlab" text-anchor="end">'
             'market rates higher &#8594;</text>' % (width - pad_r, height - 4))
    return ('<svg class="fig" viewBox="0 0 %d %d" role="img" '
            'aria-label="Market re-rating against measured rating">'
            '<title>Small dot: what we measured last season. Large dot: what '
            'the market prices now. The line is the distance.</title>%s</svg>'
            % (width, height, "".join(g)))


TEAM_CHART_CSS = """
.fig .qbg{fill:none;stroke:var(--line);stroke-width:1}
.fig .qax{stroke:var(--line);stroke-width:1;stroke-dasharray:2 3}
.fig .qlab{fill:var(--ink3);font-size:9.5px;letter-spacing:.05em;
  text-transform:uppercase}
.fig .qquad{fill:var(--ink3);font-size:9px;opacity:.6;letter-spacing:.07em;
  text-transform:uppercase}
.fig .qdot{fill:var(--accent);opacity:.85}
.fig .qtxt{fill:var(--ink2);font-size:9.5px;font-weight:600;
  font-family:"IBM Plex Mono",ui-monospace,monospace}
.fig.sp{width:104px;height:22px;display:inline-block;vertical-align:middle}
.fig .spl{fill:none;stroke:var(--accent);stroke-width:1.5;
  stroke-linejoin:round}
.fig .spz{stroke:var(--line);stroke-width:1}
.fig .spup{fill:var(--play)}
.fig .spdn{fill:var(--neg)}
.fig .dba{fill:var(--ink3)}
.fig .dbup{stroke:var(--play);stroke-width:1.5;opacity:.5}
.fig .dbdn{stroke:var(--neg);stroke-width:1.5;opacity:.5}
.fig .dbup-d{fill:var(--play)}
.fig .dbdn-d{fill:var(--neg)}
.figrow{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}
.figbox{background:var(--panel);border:1px solid var(--line);padding:12px 14px}
.figbox h4{margin:0 0 2px;font-size:12.5px;font-weight:600}
.figbox p.cap{margin:0 0 9px;font-size:11.5px;color:var(--ink3);
  max-width:62ch;line-height:1.4}
.qwrap{position:relative;width:100%}
.qsvg{position:absolute;inset:0;width:100%;height:100%}
.qmark{position:absolute;transform:translate(-50%,-50%);width:23px;height:23px;
  background-size:contain;background-position:center;
  background-repeat:no-repeat;cursor:default;font-size:9px;font-weight:700;
  color:var(--ink2);display:flex;align-items:center;justify-content:center;
  font-family:"IBM Plex Mono",monospace}
"""
