"""
totals_model.py - P(combined points | closing total). The totals twin of
margin_model.py, and the other half of the game-level distribution layer.

Same architecture, because the same logic applies: the market owns the mean,
this model owns the shape.

    mu(t)       = t                    LOCATION FROM THE MARKET
    sigma(t)    = sqrt(c0 + c1*t)      dispersion scales with the total
    P(x|t)      = [Phi(x+.5) - Phi(x-.5)] * mult(x), renormalized

TWO WAYS TOTALS DIFFER FROM MARGINS, both handled here:

  1. KEY NUMBERS ARE MUCH WEAKER. Margin lands on 3 in ~14.6% of games. No
     single total value comes close - scoring is a sum of two teams, which
     smears the distribution. Multipliers are therefore estimated on the
     ABSOLUTE total (41, 44, 51 are mildly special), not on a residual, and
     they stay near 1.0. Do not expect a teaser-sized edge here.

  2. SIGMA SCALES WITH THE TOTAL. A 52-point game has more scoring variance in
     absolute terms than a 38-point game, so a single sigma is wrong. Margin
     dispersion barely moved with |spread|; totals dispersion genuinely moves.

Stdlib only: math.erf. No numpy, no scipy.

Usage:
    py -3 scripts/totals_model.py build
    py -3 scripts/totals_model.py validate
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

TOTAL_MIN, TOTAL_MAX = 0, 110
LINE_MIN, LINE_MAX, LINE_STEP = 30.0, 62.0, 0.5
MIN_SEASON = 1999          # shape
SIGMA_SEASON = 2018        # dispersion drifts with the scoring environment


def log(m: str = "") -> None:
    print(m, flush=True)


def _ncdf(x, mu, s):
    return 0.5 * (1.0 + math.erf((x - mu) / (s * math.sqrt(2.0))))


def _npmf(x, mu, s):
    return _ncdf(x + 0.5, mu, s) - _ncdf(x - 0.5, mu, s)


def sigma_at(total: float, sig) -> float:
    c0, c1 = sig
    return math.sqrt(max(c0 + c1 * total, 1.0))


def fit(con):
    rows = con.execute(f"""
        SELECT total_line, total FROM raw.games
        WHERE total IS NOT NULL AND total_line IS NOT NULL
          AND season >= {MIN_SEASON}
    """).fetchall()
    n = len(rows)
    resid = [t - l for l, t in rows]
    log(f"  fitted on {n:,} games ({MIN_SEASON}+)")
    log(f"    mean residual (actual - line): {sum(resid)/n:+.4f} pts")

    recent = con.execute(f"""
        SELECT total_line, total FROM raw.games
        WHERE total IS NOT NULL AND total_line IS NOT NULL
          AND season >= {SIGMA_SEASON}
    """).fetchall()
    log(f"    sigma window: {SIGMA_SEASON}+ ({len(recent):,} games)")
    xs = [l for l, _ in recent]
    ys = [(t - l) ** 2 for l, t in recent]
    m = len(recent)
    mx, my = sum(xs) / m, sum(ys) / m
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    c1 = sxy / sxx if sxx else 0.0
    c0 = my - c1 * mx
    pooled = (sum((t - l) ** 2 for l, t in recent) / (m - 1)) ** 0.5
    log(f"    sigma(38)={sigma_at(38,(c0,c1)):.3f}  "
        f"sigma(45)={sigma_at(45,(c0,c1)):.3f}  "
        f"sigma(52)={sigma_at(52,(c0,c1)):.3f}   (pooled {pooled:.3f})")

    observed, expected = {}, {}
    for l, t in rows:
        observed[int(round(t))] = observed.get(int(round(t)), 0.0) + 1.0
    for l, _ in rows:
        s = sigma_at(l, (c0, c1))
        for x in range(max(TOTAL_MIN, int(l - 5 * s)),
                       min(TOTAL_MAX, int(l + 5 * s)) + 1):
            expected[x] = expected.get(x, 0.0) + _npmf(x, l, s)

    mult = {}
    for x in range(TOTAL_MIN, TOTAL_MAX + 1):
        e, o = expected.get(x, 0.0), observed.get(x, 0.0)
        if e < 5.0:
            mult[x] = 1.0
        else:
            k = e / (e + 25.0)
            mult[x] = 1.0 + k * (o / e - 1.0)
    return (c0, c1), mult, n


def pmf_for_total(line: float, sig, mult) -> dict[int, float]:
    s = sigma_at(line, sig)
    out = {}
    for x in range(TOTAL_MIN, TOTAL_MAX + 1):
        p = _npmf(x, line, s) * mult.get(x, 1.0)
        if p > 0:
            out[x] = p
    tot = sum(out.values())
    return {k: v / tot for k, v in out.items()}


def over_prob(pmf: dict[int, float], line: float) -> tuple[float, float]:
    """(P(over), P(push)). Half-point lines cannot push."""
    over = sum(p for x, p in pmf.items() if x > line)
    push = sum(p for x, p in pmf.items() if x == line)
    return over, push


def fair_prob_over(pmf: dict[int, float], line: float) -> float:
    o, push = over_prob(pmf, line)
    return o / (1.0 - push) if push < 1.0 else float("nan")


def load_params(con):
    p = dict(con.execute(
        "SELECT param, value FROM mart.total_model_params").fetchall())
    mult = dict(con.execute(
        "SELECT total, multiplier FROM mart.total_multiplier").fetchall())
    return (p["sigma_c0"], p["sigma_c1"]), mult


def build(con) -> int:
    log("building totals model")
    sig, mult, n = fit(con)
    top = sorted(((k, v) for k, v in mult.items() if 20 <= k <= 70),
                 key=lambda kv: -kv[1])[:8]
    log("    largest total multipliers: "
        + ", ".join(f"{k}:{v:.2f}" for k, v in top))

    now = datetime.now(timezone.utc)
    con.execute("""CREATE OR REPLACE TABLE mart.total_model_params
                   (param VARCHAR PRIMARY KEY, value DOUBLE, built_at TIMESTAMPTZ)""")
    con.executemany("INSERT INTO mart.total_model_params VALUES (?,?,?)",
                    [("sigma_c0", sig[0], now), ("sigma_c1", sig[1], now),
                     ("n_games", float(n), now)])
    con.execute("""CREATE OR REPLACE TABLE mart.total_multiplier
                   (total INTEGER PRIMARY KEY, multiplier DOUBLE)""")
    con.executemany("INSERT INTO mart.total_multiplier VALUES (?,?)",
                    sorted(mult.items()))

    rows = []
    t = LINE_MIN
    while t <= LINE_MAX + 1e-9:
        for x, p in pmf_for_total(t, sig, mult).items():
            if p >= 1e-7:
                rows.append((round(t, 2), x, p))
        t += LINE_STEP
    con.execute("""CREATE OR REPLACE TABLE mart.total_pmf
                   (line DOUBLE, total INTEGER, prob DOUBLE,
                    PRIMARY KEY (line, total))""")
    con.executemany("INSERT INTO mart.total_pmf VALUES (?,?,?)", rows)
    log(f"  mart.total_pmf rows: {len(rows):,}")
    return 0


def validate(con) -> int:
    sig, mult = load_params(con)
    log("=" * 74)
    log("1. OVER/UNDER CALIBRATION  (~50% predicted by construction)")
    log("=" * 74)
    rows = con.execute("""
        SELECT total_line, total FROM raw.games
        WHERE total IS NOT NULL AND total_line IS NOT NULL AND season >= 2006
    """).fetchall()
    cache = {}
    buckets = [(0, 41), (41, 45), (45, 49), (49, 99)]
    log(f"  {'line':<12}{'games':>7}{'pred over':>11}{'actual':>9}{'diff':>8}{'2 SE':>8}")
    for lo, hi in buckets:
        pred = act = cnt = 0.0
        for l, t in rows:
            if not (lo < l <= hi) or t == l:
                continue
            k = round(l * 2) / 2
            if k not in cache:
                cache[k] = pmf_for_total(k, sig, mult)
            pred += fair_prob_over(cache[k], l)
            act += 1.0 if t > l else 0.0
            cnt += 1.0
        if cnt:
            d = (pred - act) / cnt * 100
            se2 = 2 * math.sqrt(0.25 / cnt) * 100
            flag = "" if abs(d) <= se2 else "  <-- beyond noise"
            log(f"  {f'{lo}-{hi}':<12}{int(cnt):>7}{pred/cnt*100:>10.1f}%"
                f"{act/cnt*100:>8.1f}%{d:>+8.1f}{se2:>8.1f}{flag}")

    log("\n" + "=" * 74)
    log("2. NULL TEST vs THE MARKET'S OWN OVER/UNDER PRICES")
    log("=" * 74)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from devig import devig_american
    rows2 = con.execute("""
        SELECT total_line, over_odds, under_odds FROM raw.games
        WHERE total_line IS NOT NULL AND over_odds IS NOT NULL
          AND under_odds IS NOT NULL AND season >= 2018
    """).fetchall()
    errs = []
    for l, oo, uo in rows2:
        k = round(l * 2) / 2
        if k not in cache:
            cache[k] = pmf_for_total(k, sig, mult)
        try:
            mkt = devig_american([oo, uo], "power")[0]
        except Exception:
            continue
        errs.append(fair_prob_over(cache[k], l) - mkt)
    if errs:
        n = len(errs)
        log(f"  games: {n:,}")
        log(f"  mean abs error : {sum(abs(e) for e in errs)/n*100:.2f} pp")
        log(f"  bias           : {sum(errs)/n*100:+.2f} pp")
        log("  A market-anchored totals model should sit within ~1pp; the")
        log("  over/under price is nearly always -110/-110, so this mostly")
        log("  confirms the push handling and the half-point logic.")

    log("\n" + "=" * 74)
    log("3. KEY NUMBERS - much weaker than margins, as expected")
    log("=" * 74)
    emp = dict(con.execute("""
        SELECT total, count(*) * 1.0 / sum(count(*)) OVER ()
        FROM raw.games WHERE total IS NOT NULL AND season >= 2006 GROUP BY 1
    """).fetchall())
    log(f"  {'total':>7}{'empirical':>12}{'multiplier':>13}")
    for t in sorted(emp, key=lambda k: -emp[k])[:8]:
        log(f"  {int(t):>7}{emp[t]*100:>11.2f}%{mult.get(int(t), 1.0):>13.2f}")
    log("\n  Compare: margin lands on 3 in 14.6% of games. The most common")
    log("  single total is under 5%. There is no teaser-grade key number in")
    log("  totals, and any model claiming one is fitting noise.")

    log("\n" + "=" * 74)
    log("4. WORKED EXAMPLE - half-point value around 44")
    log("=" * 74)
    pm = pmf_for_total(44.0, sig, mult)
    prev = None
    log(f"    {'line':>6}{'P(over)':>10}{'push':>8}{'fair':>9}{'d vs prev':>11}")
    for l in (42.5, 43.0, 43.5, 44.0, 44.5, 45.0, 45.5):
        o, push = over_prob(pm, l)
        fair = o / (1 - push) if push < 1 else float("nan")
        d = "" if prev is None else f"{(fair-prev)*100:+.2f}pp"
        log(f"    {l:>6.1f}{o*100:>9.2f}%{push*100:>7.2f}%{fair*100:>8.2f}%{d:>11}")
        prev = fair
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "validate"])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db))
    try:
        return build(con) if a.cmd == "build" else validate(con)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
