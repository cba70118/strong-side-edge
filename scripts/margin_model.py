"""
margin_model.py - P(final margin | closing spread), key numbers preserved.

This is the primitive that turns a LINE into a PROBABILITY. It is required by:
  * bet_log CLV grading, whenever our line differs from the reference line
  * alternate-spread and moneyline fair pricing
  * teaser evaluation (which lives or dies on crossing 3 and 7)
  * the simulation layer's null test

METHOD. Two obvious approaches both fail:
  * Raw empirical tabulation per spread bucket is far too sparse in the tails -
    7,344 games spread over ~40 spread values and ~80 margin values.
  * A plain normal is smooth but erases the key numbers, and a margin of 3 is
    14.6% of all games. Any model that misses that misprices every alt spread,
    moneyline and teaser that crosses it.

So: fit a smooth normal for location and scale, then multiply by empirically
estimated KEY-NUMBER MULTIPLIERS.

    mu(s)       = s               LOCATION IS TAKEN FROM THE MARKET
    sigma       = rms(margin - s)
    P_norm(m|s) = Phi(m+0.5) - Phi(m-0.5)      discretised normal
    mult(m)     = observed_count(m) / expected_count(m)   pooled over ALL games
    P(m|s)      = P_norm(m|s) * mult(m),  renormalized

LOCATION COMES FROM THE MARKET, DELIBERATELY. An unconstrained regression of
margin on spread returns a slope of 1.0430, which amplifies every handicap and
pushed cover calibration ~5pp off in the 0-3 bucket. For a market as efficient
as NFL sides, E[margin | spread] = spread by definition, and we measured that
bias at -0.040 points over 5,431 games. The market owns the mean; this model
owns the SHAPE around it. Re-estimating the mean adds noise and nothing else.
The unconstrained fit is still reported at build time as a diagnostic - if that
slope ever drifts far from 1.0, the market or the data changed.

CONVENTION. `spread` here is ALWAYS nflverse `spread_line`: home perspective,
POSITIVE MEANS HOME IS FAVORED. The odds feed uses the opposite sign for its
`point` field (home favored -> -3.5). Converting between them is the caller's
job; `handicap_from_spread_line()` does it explicitly.

Multipliers are pooled across every game rather than estimated per spread
bucket, because key numbers are a property of football scoring (3s and 7s), not
of the handicap - which makes them stable and cheap to estimate well.

Stdlib only: math.erf for the normal CDF. No numpy, no scipy.

Usage:
    py -3 scripts/margin_model.py build
    py -3 scripts/margin_model.py validate
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"

MARGIN_MIN, MARGIN_MAX = -60, 60
SPREAD_MIN, SPREAD_MAX, SPREAD_STEP = -21.0, 21.0, 0.5
MIN_SEASON = 1999          # spread_line coverage begins here (key-number shape)
# Sigma is fitted on a RECENT window because margin dispersion DRIFTS while the
# key-number shape does not. Measured residual SD of (margin - spread_line):
#   1999-2005 13.25 | 2006-2011 13.77 | 2012-2017 13.18 | 2018-2025 12.75
# A full-history 13.20 is stale by ~0.45 points and it was the main driver of
# the model understating big favorites against the moneyline market. Refitting
# on 2018+ cuts moneyline MAE from 3.03pp to 2.70pp and |err|>5pp from 15.8% to
# 10.2%. Key-number MULTIPLIERS keep the long window - they need the sample and
# do not drift materially.
SIGMA_SEASON = 2018


def log(m: str = "") -> None:
    print(m, flush=True)


def _ncdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _norm_pmf(m: int, mu: float, sigma: float) -> float:
    """Probability that a continuous normal rounds to integer m."""
    return _ncdf(m + 0.5, mu, sigma) - _ncdf(m - 0.5, mu, sigma)


# --------------------------------------------------------------------------
# fitting
# --------------------------------------------------------------------------

def fit(con) -> tuple[float, float, float, dict[int, float], int]:
    rows = con.execute(f"""
        SELECT spread_line, result FROM raw.games
        WHERE result IS NOT NULL AND spread_line IS NOT NULL
          AND season >= {MIN_SEASON}
    """).fetchall()
    n = len(rows)
    if n < 500:
        raise SystemExit(f"only {n} games available; refusing to fit")

    # Unconstrained OLS, reported as a DIAGNOSTIC only - see module docstring.
    sx = sum(r[0] for r in rows)
    sy = sum(r[1] for r in rows)
    sxx = sum(r[0] * r[0] for r in rows)
    sxy = sum(r[0] * r[1] for r in rows)
    b_hat = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    a_hat = (sy - b_hat * sx) / n

    # The model actually used: location from the market, shape from the data.
    a, b = 0.0, 1.0
    resid = [r[1] - r[0] for r in rows]
    sigma0 = (sum(e * e for e in resid) / (n - 1)) ** 0.5
    log(f"    diagnostic OLS fit : margin = {a_hat:+.4f} + {b_hat:.4f} * spread")
    log(f"    model in use       : mu = spread (market-anchored)")
    log(f"    mean residual      : {sum(resid)/n:+.4f} pts "
        f"(confirms the market owns the mean)")

    # Heteroskedasticity: does spread magnitude change variance?
    # Regress e^2 on |spread| -> sigma(s) = sqrt(c0 + c1*|s|).
    # sigma from the recent era only; multipliers below still use every game
    recent = con.execute(f"""
        SELECT spread_line, result FROM raw.games
        WHERE result IS NOT NULL AND spread_line IS NOT NULL
          AND season >= {SIGMA_SEASON}
    """).fetchall()
    r_resid = [y - x for x, y in recent]
    log(f"    sigma window       : {SIGMA_SEASON}+ ({len(recent):,} games)")
    xs = [abs(x) for x, _ in recent]
    ys = [e * e for e in r_resid]
    n_sig = len(recent)
    mx, my = sum(xs) / n_sig, sum(ys) / n_sig
    sxx2 = sum((x - mx) ** 2 for x in xs)
    sxy2 = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    c1 = sxy2 / sxx2 if sxx2 else 0.0
    c0 = my - c1 * mx
    log(f"    sigma(0)={math.sqrt(max(c0,1.0)):.3f}  "
        f"sigma(7)={math.sqrt(max(c0+7*c1,1.0)):.3f}  "
        f"sigma(14)={math.sqrt(max(c0+14*c1,1.0)):.3f}   "
        f"(pooled {sigma0:.3f})")

    # Key-number multipliers, pooled over every game and SIGN-SYMMETRISED.
    # An asymmetric multiplier (3 -> 2.74 vs -3 -> 2.51) re-injects home-field
    # advantage that the spread already contains, which biased cover
    # calibration by ~5pp. Landing on 3 is a property of football scoring, not
    # of which team is at home, so mult(m) must equal mult(-m).
    observed: dict[int, float] = {}
    expected: dict[int, float] = {}
    for s, margin in rows:
        mi = int(round(margin))
        observed[mi] = observed.get(mi, 0.0) + 1.0
    for s, _ in rows:
        mu = a + b * s
        sg = math.sqrt(max(c0 + c1 * abs(s), 1.0))
        lo = max(MARGIN_MIN, int(mu - 5 * sg))
        hi = min(MARGIN_MAX, int(mu + 5 * sg))
        for m in range(lo, hi + 1):
            expected[m] = expected.get(m, 0.0) + _norm_pmf(m, mu, sg)

    mult: dict[int, float] = {}
    for m in range(0, MARGIN_MAX + 1):
        o = observed.get(m, 0.0) + (observed.get(-m, 0.0) if m else 0.0)
        e = expected.get(m, 0.0) + (expected.get(-m, 0.0) if m else 0.0)
        if e < 5.0:
            v = 1.0                  # too little expected mass to estimate
        else:
            # shrink toward 1 by expected count, so thin cells cannot swing
            k = e / (e + 25.0)
            v = 1.0 + k * (o / e - 1.0)
        mult[m] = v
        if m:
            mult[-m] = v
    return a, b, (c0, c1), mult, n


def sigma_at(spread: float, sig) -> float:
    """sig is either a scalar or the (c0, c1) pair of sigma^2 = c0 + c1*|s|."""
    if isinstance(sig, tuple):
        c0, c1 = sig
        return math.sqrt(max(c0 + c1 * abs(spread), 1.0))
    return float(sig)


def pmf_for_spread(spread: float, a: float, b: float, sig,
                   mult: dict[int, float]) -> dict[int, float]:
    mu = a + b * spread
    sigma = sigma_at(spread, sig)
    out: dict[int, float] = {}
    for m in range(MARGIN_MIN, MARGIN_MAX + 1):
        p = _norm_pmf(m, mu, sigma) * mult.get(m, 1.0)
        if p > 0:
            out[m] = p
    tot = sum(out.values())
    return {m: p / tot for m, p in out.items()}


# --------------------------------------------------------------------------
# query API
# --------------------------------------------------------------------------

def cover_prob(pmf: dict[int, float], handicap: float,
               side: str = "home") -> tuple[float, float]:
    """Probability a side covers `handicap`, and the push probability.

    handicap is from that side's perspective, as the odds feed reports `point`:
    home favored by 3.5 -> handicap = -3.5. The side covers when
    margin + handicap > 0 for home, or -margin + handicap > 0 for away.
    """
    win = push = 0.0
    for m, p in pmf.items():
        v = (m + handicap) if side == "home" else (-m + handicap)
        if v > 0:
            win += p
        elif v == 0:
            push += p
    return win, push


def win_prob(pmf: dict[int, float], side: str = "home") -> float:
    """Moneyline probability, ties excluded and redistributed."""
    w = sum(p for m, p in pmf.items() if (m > 0 if side == "home" else m < 0))
    tie = sum(p for m, p in pmf.items() if m == 0)
    return w / (1.0 - tie) if tie < 1.0 else w


def fair_prob_of_bet(pmf: dict[int, float], market_id: str,
                     handicap: float | None, side: str) -> float | None:
    """Fair probability of one bet under a given margin distribution.

    Push probability is removed and the bet priced on the decided outcomes,
    which is how a push actually settles (stake returned).
    """
    if market_id in ("ml_game",):
        return win_prob(pmf, side)
    if market_id in ("spread_game", "spread_alt") and handicap is not None:
        w, push = cover_prob(pmf, handicap, side)
        return w / (1.0 - push) if push < 1.0 else None
    return None


# --------------------------------------------------------------------------
# build / persist
# --------------------------------------------------------------------------

def handicap_from_spread_line(spread_line: float) -> float:
    """nflverse spread_line (positive = home favored) -> odds-feed home
    handicap (negative = home favored). They are simple negations, but the
    sign error is the single most common footgun in NFL modeling, so it gets
    a named function rather than an inline minus sign."""
    return -spread_line


def build(con) -> int:
    log(f"  fitting...")
    a, b, sig, mult, n = fit(con)
    log(f"  fitted on {n:,} games ({MIN_SEASON}+)")
    keys = sorted(((k, v) for k, v in mult.items() if k >= 0),
                  key=lambda kv: -kv[1])[:8]
    log("    largest key-number multipliers (symmetric): "
        + ", ".join(f"{k}:{v:.2f}" for k, v in keys))

    con.execute("""
        CREATE OR REPLACE TABLE mart.margin_model_params (
            param VARCHAR PRIMARY KEY, value DOUBLE, built_at TIMESTAMPTZ)
    """)
    now = datetime.now(timezone.utc)
    c0, c1 = sig
    con.executemany(
        "INSERT INTO mart.margin_model_params VALUES (?,?,?)",
        [("intercept", a, now), ("slope", b, now),
         ("sigma_c0", c0, now), ("sigma_c1", c1, now),
         ("n_games", float(n), now), ("min_season", float(MIN_SEASON), now)])

    con.execute("""
        CREATE OR REPLACE TABLE mart.margin_multiplier (
            margin INTEGER PRIMARY KEY, multiplier DOUBLE)
    """)
    con.executemany("INSERT INTO mart.margin_multiplier VALUES (?,?)",
                    [(m, v) for m, v in sorted(mult.items())])

    rows = []
    s = SPREAD_MIN
    while s <= SPREAD_MAX + 1e-9:
        pmf = pmf_for_spread(s, a, b, sig, mult)
        for m, p in pmf.items():
            if p >= 1e-7:
                rows.append((round(s, 2), m, p))
        s += SPREAD_STEP
    con.execute("""
        CREATE OR REPLACE TABLE mart.margin_pmf (
            spread DOUBLE, margin INTEGER, prob DOUBLE,
            PRIMARY KEY (spread, margin))
    """)
    con.executemany("INSERT INTO mart.margin_pmf VALUES (?,?,?)", rows)
    log(f"  mart.margin_pmf rows: {len(rows):,} "
        f"({int((SPREAD_MAX-SPREAD_MIN)/SPREAD_STEP)+1} spread grid points)")
    return 0


def load_params(con):
    p = dict(con.execute(
        "SELECT param, value FROM mart.margin_model_params").fetchall())
    mult = dict(con.execute(
        "SELECT margin, multiplier FROM mart.margin_multiplier").fetchall())
    return p["intercept"], p["slope"], (p["sigma_c0"], p["sigma_c1"]), mult


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate(con) -> int:
    a, b, sigma, mult = load_params(con)
    base = pmf_for_spread(0.0, a, b, sigma, mult)

    log("=" * 74)
    log("1. KEY NUMBERS  (population-averaged, matched to real spreads)")
    log("=" * 74)
    log("Averaged over the ACTUAL distribution of closing spreads, not")
    log("evaluated at pick'em - key-number mass concentrates near zero, so")
    log("comparing a pick'em model against an all-spreads empirical would")
    log("flatter the model by construction.\n")
    spreads = [r[0] for r in con.execute("""
        SELECT spread_line FROM raw.games
        WHERE result IS NOT NULL AND spread_line IS NOT NULL AND season >= 2006
    """).fetchall()]
    cache0: dict[float, dict[int, float]] = {}
    agg: dict[int, float] = {}
    for s in spreads:
        key = round(s * 2) / 2
        if key not in cache0:
            cache0[key] = pmf_for_spread(key, a, b, sigma, mult)
        for m, p in cache0[key].items():
            agg[abs(m)] = agg.get(abs(m), 0.0) + p
    tot = sum(agg.values())
    agg = {k: v / tot for k, v in agg.items()}

    emp = dict(con.execute("""
        SELECT abs(result), count(*) * 1.0 / sum(count(*)) OVER ()
        FROM raw.games WHERE result IS NOT NULL AND spread_line IS NOT NULL
          AND season >= 2006
        GROUP BY 1
    """).fetchall())
    log(f"  {'|margin|':>9}{'model':>10}{'empirical':>12}{'diff':>9}")
    worst = 0.0
    for k in (3, 7, 6, 10, 4, 14, 1, 17, 2, 8):
        mod, e = agg.get(k, 0.0), emp.get(k, 0.0)
        worst = max(worst, abs(mod - e))
        log(f"  {k:>9}{mod*100:>9.2f}%{e*100:>11.2f}%{(mod-e)*100:>+8.2f}pp")
    log(f"\n  largest key-number error: {worst*100:.2f}pp")

    log("\n" + "=" * 74)
    log("2. SPREAD -> MONEYLINE NULL TEST")
    log("=" * 74)
    log("Predict each game's moneyline from its closing spread alone, and")
    log("compare with the market's own de-vigged moneyline. If the model")
    log("cannot reproduce a price the market derived from the same input,")
    log("it has no business pricing derivatives.\n")

    rows = con.execute("""
        SELECT spread_line, home_moneyline, away_moneyline
        FROM raw.games
        WHERE result IS NOT NULL AND spread_line IS NOT NULL
          AND home_moneyline IS NOT NULL AND away_moneyline IS NOT NULL
          AND season >= 2006
    """).fetchall()

    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from devig import devig_american  # noqa: E402

    errs, big = [], 0
    cache: dict[float, dict[int, float]] = {}
    for s, hml, aml in rows:
        key = round(s * 2) / 2
        if key not in cache:
            cache[key] = pmf_for_spread(key, a, b, sigma, mult)
        model_p = win_prob(cache[key], "home")
        try:
            mkt_p = devig_american([hml, aml], "power")[0]
        except Exception:
            continue
        e = model_p - mkt_p
        errs.append(e)
        if abs(e) > 0.05:
            big += 1

    n = len(errs)
    mae = sum(abs(e) for e in errs) / n
    bias = sum(errs) / n
    rmse = (sum(e * e for e in errs) / n) ** 0.5
    log(f"  games compared        : {n:,}")
    log(f"  mean abs error        : {mae*100:.2f} pp")
    log(f"  bias (model - market) : {bias*100:+.2f} pp")
    log(f"  RMSE                  : {rmse*100:.2f} pp")
    log(f"  |error| > 5pp         : {big:,}  ({100*big/n:.1f}%)")
    verdict = "PASS" if mae < 0.02 else ("MARGINAL" if mae < 0.035 else "FAIL")
    log(f"\n  VERDICT: {verdict}  (target: mean abs error under 2pp)")

    log("\n  error by spread bucket (locates the disagreement):")
    log(f"    {'|spread|':<11}{'games':>7}{'MAE pp':>9}{'bias pp':>10}")
    bk = [(0, 1.5), (1.5, 3), (3, 6), (6, 10), (10, 99)]
    for lo, hi in bk:
        sel = [(s, h, aw) for s, h, aw in rows if lo < abs(s) <= hi]
        if not sel:
            continue
        es = []
        for s, hml, aml in sel:
            key = round(s * 2) / 2
            if key not in cache:
                cache[key] = pmf_for_spread(key, a, b, sigma, mult)
            try:
                es.append(win_prob(cache[key], "home")
                          - devig_american([hml, aml], "power")[0])
            except Exception:
                pass
        if es:
            log(f"    {f'{lo}-{hi}':<11}{len(es):>7}"
                f"{sum(abs(x) for x in es)/len(es)*100:>9.2f}"
                f"{sum(es)/len(es)*100:>+10.2f}")
    log("\n  Note the market prices spreads and moneylines semi-independently,")
    log("  and NFL moneylines carry well-documented favorite bias, so exact")
    log("  agreement is not expected. What matters is that no bucket is wildly")
    log("  off, which would indicate a broken distribution rather than a")
    log("  pricing convention difference.")

    log("\n" + "=" * 74)
    log("3. COVER CALIBRATION  (predicted vs realised, by spread bucket)")
    log("=" * 74)
    log("A market-anchored model predicts ~50% cover BY CONSTRUCTION. The")
    log("column that moves is `actual`, and it moves within sampling error -")
    log("so `diff` is compared against 2 standard errors, not against zero.\n")
    buckets = [(0, 3), (3, 7), (7, 10), (10, 14), (14, 99)]
    log(f"  {'|spread|':<12}{'games':>7}{'pred':>8}{'actual':>9}{'diff':>8}"
        f"{'2 SE':>8}{'':>4}")
    for lo, hi in buckets:
        gs = con.execute("""
            SELECT spread_line, result FROM raw.games
            WHERE result IS NOT NULL AND spread_line IS NOT NULL
              AND season >= 2006 AND abs(spread_line) > ? AND abs(spread_line) <= ?
        """, [lo, hi]).fetchall()
        if not gs:
            continue
        pred = act = cnt = 0.0
        pushes = 0
        for s, res in gs:
            # Skip pushes FIRST. Incrementing pred before this check but cnt
            # after it silently inflated predicted cover by ~5pp in the 0-3
            # bucket, because pushes cluster on spread 3. The model was fine;
            # the harness was double-counting.
            if res == s:
                pushes += 1
                continue
            key = round(s * 2) / 2
            if key not in cache:
                cache[key] = pmf_for_spread(key, a, b, sigma, mult)
            w, push = cover_prob(cache[key], -s, "home")
            if push >= 1.0:
                continue
            pred += w / (1.0 - push)
            if res > s:
                act += 1.0
            cnt += 1.0
        if cnt:
            diff = (pred - act) / cnt * 100
            se2 = 2 * math.sqrt(0.25 / cnt) * 100
            flag = "" if abs(diff) <= se2 else "  <-- beyond noise"
            log(f"  {f'{lo}-{hi}':<12}{int(cnt):>7}{pred/cnt*100:>7.1f}%"
                f"{act/cnt*100:>8.1f}%{diff:>+7.1f}{se2:>8.1f}{flag}")

    log("\n" + "=" * 74)
    log("4. WORKED EXAMPLES")
    log("=" * 74)
    log("spread_line is nflverse convention: POSITIVE = HOME FAVORED.")
    log("The odds-feed handicap is its negation.\n")
    from devig import prob_to_american  # noqa: E402
    log(f"  {'spread_line':>12}{'meaning':<22}{'P(home win)':>12}{'fair ML':>9}")
    for s in (3.0, 3.5, 7.0, -1.5):
        pm = pmf_for_spread(s, a, b, sigma, mult)
        wp = win_prob(pm, "home")
        meaning = (f"home favored by {abs(s)}" if s > 0
                   else f"home dog by {abs(s)}")
        log(f"  {s:>+12.1f}  {meaning:<20}{wp*100:>11.2f}%"
            f"{prob_to_american(wp):>+9.0f}")

    log("\n  Line value across key number 3 (home favored by 3.5):")
    log("  handicap is odds-feed sign, so -3.5 means home laying 3.5.\n")
    pm = pmf_for_spread(3.5, a, b, sigma, mult)
    prev = None
    log(f"    {'handicap':>9}{'cover':>9}{'push':>8}{'fair':>9}{'fair ML':>9}"
        f"{'d vs prev':>11}")
    for h in (-2.5, -3.0, -3.5, -4.0, -4.5):
        w, push = cover_prob(pm, h, "home")
        fair = w / (1 - push) if push < 1 else float("nan")
        delta = "" if prev is None else f"{(fair-prev)*100:+.2f}pp"
        log(f"    {h:>+9.1f}{w*100:>8.2f}%{push*100:>7.2f}%{fair*100:>8.2f}%"
            f"{prob_to_american(fair):>+9.0f}{delta:>11}")
        prev = fair
    log("\n  The -2.5 -> -3.5 step is worth far more than -3.5 -> -4.5:")
    log("  that is the key number 3, and it is exactly what a Gaussian sim")
    log("  would flatten away.")
    return 0 if verdict != "FAIL" else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "validate"])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    if not a.db.exists():
        log(f"database not found: {a.db}")
        return 1
    con = duckdb.connect(str(a.db))
    try:
        if a.cmd == "build":
            log("building margin model")
            return build(con)
        return validate(con)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
