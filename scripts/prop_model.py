"""prop_model.py - turn a usage projection into a DISTRIBUTION, so a posted
player prop can actually be priced.

WHY THIS DID NOT EXIST. The usage engine produces point estimates, and a mean
cannot price an over/under: "6.2 receptions" says nothing about P(over 5.5)
without a spread around it. This module supplies that spread and nothing else -
it does not re-project usage, it takes `mart.player_usage_projection` as given.

WHAT THE DATA SAYS, measured on 37,946 point-in-time projection/actual pairs:

  * Counts are OVERDISPERSED. Variance over mean runs 1.8 at a projection of
    one reception down to about 1.3 at seven. Poisson would put that ratio at
    1.0 and would badly understate the tails, so receptions are modeled
    Negative Binomial.

  * The projection is BIASED HIGH, but only for fringe players. At a projected
    1.0 receptions the actual mean is 0.62; at 4, 5 and 6 it is 3.88, 5.01 and
    6.00. The gap is not a usage error, it is P(did not play) - a deep bench
    player is projected for the snaps he would get if active. Books do not post
    props on those players, so the pricer REFUSES to quote below a usage floor
    rather than quietly inheriting the bias.

  * Yardage is roughly Gamma around the projection, with a coefficient of
    variation that shrinks as volume grows.

Coverage is validated by a RANDOMIZED probability integral transform, which is
the only correct test for a mixture with an atom at zero. Getting that test
wrong is easy and expensive: an earlier bug in nb_sf below zero collapsed the
PIT to a spike on every zero outcome and produced a fictitious 10pp tail miss,
which in turn was used to justify refusing every alt line on the board.

Everything here is fitted point-in-time and validated by coverage: if the 50%
and 80% intervals do not contain the actual result about 50% and 80% of the
time, the distribution is wrong and nothing built on it can be trusted.

Usage:
    py -3 scripts/prop_model.py fit
    py -3 scripts/prop_model.py validate
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "nfl.duckdb"
PARAMS_PATH = ROOT / "data" / "prop_model.json"

# A book will not post a receiving line on a player projected for two targets,
# and our projection is unreliable down there anyway. Both facts point the same
# way: refuse to quote.
# Where the coverage test passes. This was (0.25, 0.75) on the strength of an
# apparent 10pp over-coverage of the 80% and 90% bands - which turned out to be
# an artifact of nb_sf returning 1-P(0) instead of 1.0 for a negative line. That
# made the survival function non-monotone, collapsed the randomized PIT to a
# spike for every zero outcome, and manufactured the very tail miss used to
# justify the narrow band. With it fixed the 80% band lands at 79.0/79.1/79.3
# and the 90% at 88.6/89.5/89.2, so the distribution is usable well into the
# tails and the band is set by measurement rather than by a bug.
QUOTE_BAND = (0.05, 0.95)

# Candidate quoting bands, widest first. `validate` keeps the widest band each
# stat actually earns rather than imposing one width on all three - receptions
# calibrates through the central half but skews across the full range, and
# forcing a single band either refuses it entirely or over-claims the others.
BAND_LADDER = [(0.05, 0.95), (0.10, 0.90), (0.20, 0.80), (0.25, 0.75)]

FLOORS = {
    "rec_yds": ("proj_targets", 3.0),
    "receptions": ("proj_targets", 3.0),
    "rush_yds": ("proj_carries", 5.0),
}


def log(m: str = "") -> None:
    print(m, flush=True)


# ------------------------------------------------------------- distributions

def nb_pmf(k: int, mean: float, phi: float) -> float:
    """Negative binomial by mean and variance-to-mean ratio `phi`.

    phi -> 1 collapses to Poisson, which is what the data rejects.
    """
    if mean <= 0:
        return 1.0 if k == 0 else 0.0
    if phi <= 1.0001:
        return math.exp(-mean + k * math.log(mean) - math.lgamma(k + 1))
    r = mean / (phi - 1.0)
    p = 1.0 / phi
    return math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
                    + r * math.log(p) + k * math.log(1 - p))


def nb_sf(line: float, mean: float, phi: float) -> float:
    """P(count > line).

    A negative line must return exactly 1.0 - the count cannot be below zero.
    Clamping the loop bound with max(line, 0) instead returned 1 - P(0), which
    made the survival function INCREASE from line -1 to line 0 and quietly
    corrupted every downstream use.
    """
    if line < 0:
        return 1.0
    hi = int(line) + 1
    cdf = sum(nb_pmf(k, mean, phi) for k in range(0, hi))
    return max(0.0, min(1.0, 1.0 - cdf))


def _gamma_sf(x: float, shape: float, scale: float) -> float:
    """Upper tail of a Gamma, via a series/continued-fraction incomplete gamma."""
    if x <= 0:
        return 1.0
    a, xx = shape, x / scale
    if xx < a + 1.0:                      # series for the lower tail
        term = 1.0 / a
        s = term
        n = a
        for _ in range(300):
            n += 1.0
            term *= xx / n
            s += term
            if abs(term) < abs(s) * 1e-12:
                break
        return max(0.0, min(1.0, 1.0 - s * math.exp(-xx + a * math.log(xx)
                                                    - math.lgamma(a))))
    b = xx + 1.0 - a                      # continued fraction for the upper
    c, d = 1e300, 1.0 / b
    h = d
    for i in range(1, 300):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-300:
            d = 1e-300
        c = b + an / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < 1e-12:
            break
    return max(0.0, min(1.0, h * math.exp(-xx + a * math.log(xx)
                                          - math.lgamma(a))))


def gamma_sf(line: float, mean: float, cv: float) -> float:
    """P(yards > line) for the CONDITIONAL (non-zero) component only.

    The point mass at zero is handled by the caller as a mixture weight, not
    folded in here - doing both is how the first fit over-covered its 80% band
    by nine points.
    """
    if mean <= 0:
        return 0.0
    if line < 0:
        return 1.0
    shape = 1.0 / (cv * cv)
    return _gamma_sf(line, shape, mean / shape)


# --------------------------------------------------------------------- fit

def fit(con) -> dict:
    """Fit a MIXTURE, because that is what the data is.

    Above the usage floor a player returns exactly zero about 14-16% of the
    time - inactive, or active and never involved. Conditional on producing at
    all, the projection is 4-7% LOW for receivers and 4% high for rushers. The
    first version modeled the zero spike but left the mean unconditional,
    which pushed the receptions PIT median to 0.370 and failed coverage: the
    center was too high because the zero mass had already been taken out of it.
    """
    rows = con.execute("""
        SELECT proj_targets, proj_receptions, proj_rec_yards,
               proj_carries, proj_rush_yards,
               receptions, rec_yards, carries, rush_yards
        FROM mart.player_usage_projection
        WHERE receptions IS NOT NULL
    """).fetchall()

    def gather(gate_i, floor, pi, ai):
        sel = [(r[pi], r[ai]) for r in rows
               if (r[gate_i] or 0) >= floor and r[pi] and r[pi] > 0
               and r[ai] is not None]
        nz = [(m, a) for m, a in sel if a > 0]
        return sel, nz

    def loglog(pairs):
        """Least squares on log(y) ~ log(x): returns k, p for y = k * x**p.

        Dispersion is not constant. Conditional CV of receiving yards runs
        0.802 at a 15-30 yard projection down to 0.495 at 90-105, and the
        conditional ratio drifts from 1.145 to 0.858 over the same range - the
        projection regresses to the mean, and the spread tightens as volume
        grows. A single pooled value over-disperses exactly where books post
        lines, which is what pushed the 80% coverage band to 90%.
        """
        n = len(pairs)
        sx = sum(math.log(x) for x, _ in pairs)
        sy = sum(math.log(y) for _, y in pairs)
        sxx = sum(math.log(x) ** 2 for x, _ in pairs)
        sxy = sum(math.log(x) * math.log(y) for x, y in pairs)
        den = n * sxx - sx * sx
        pw = (n * sxy - sx * sy) / den if den else 0.0
        k = math.exp((sy - pw * sx) / n)
        return round(k, 5), round(pw, 5)

    def binned(nz, width, minn=80):
        """Bucket by projection, returning (mean_proj, ratio, cv) per bucket."""
        b = defaultdict(list)
        for m, a in nz:
            b[int(m // width)].append((m, a))
        out_ = []
        for _, sel in sorted(b.items()):
            if len(sel) < minn:
                continue
            mm = sum(m for m, _ in sel) / len(sel)
            ratio = sum(a for _, a in sel) / sum(m for m, _ in sel)
            cv = math.sqrt(sum(((a - m * ratio) / (m * ratio)) ** 2
                               for m, a in sel) / len(sel))
            out_.append((mm, ratio, cv))
        return out_

    out = {}
    # ---- receptions: zero spike + negative binomial on the rest ----------
    sel, nz = gather(0, FLOORS["receptions"][1], 1, 5)
    bins = binned(nz, 1.0)
    rk, rp = loglog([(m, r) for m, r, _ in bins])
    phis = []
    for m, r, _ in bins:
        pool = [(mm, aa) for mm, aa in nz if int(mm // 1.0) == int(m // 1.0)]
        if len(pool) < 80:
            continue
        mu = [mm * (rk * mm ** rp) for mm, _ in pool]
        num = sum((aa - u) ** 2 for (_, aa), u in zip(pool, mu))
        den = sum(mu)
        if den > 0:
            phis.append((m, max(1.02, num / den)))
    pk, pp = loglog(phis) if len(phis) >= 3 else (1.2, 0.0)
    out["receptions"] = {"p_zero": round(1 - len(nz) / len(sel), 4),
                         "ratio_k": rk, "ratio_p": rp,
                         "phi_k": pk, "phi_p": pp, "n": len(sel)}
    # ---- yardage: zero spike + gamma on the rest -------------------------
    for name, gate_i, floor, pi, ai, w in (
            ("rec_yds", 0, FLOORS["rec_yds"][1], 2, 6, 15.0),
            ("rush_yds", 3, FLOORS["rush_yds"][1], 4, 8, 15.0)):
        sel, nz = gather(gate_i, floor, pi, ai)
        bins = binned(nz, w)
        rk, rp = loglog([(m, r) for m, r, _ in bins])
        ck, cp = loglog([(m, c) for m, _, c in bins])
        out[name] = {"p_zero": round(1 - len(nz) / len(sel), 4),
                     "ratio_k": rk, "ratio_p": rp,
                     "cv_k": ck, "cv_p": cp, "n": len(sel)}
    out["floors"] = {k: list(v) for k, v in FLOORS.items()}
    out["n_rows"] = len(rows)
    return out


def load_params() -> dict:
    return json.loads(PARAMS_PATH.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- pricing

def _untruncated_mean(target: float, phi: float) -> float:
    """Find mu with mu / (1 - P_NB(0; mu, phi)) == target, by bisection.

    The conditional ratio is fitted on non-zero outcomes, so it describes
    E[a | a>0]. Feeding it straight in as the NB mean and then truncating
    overstates the result.
    """
    if target <= 0:
        return 0.0
    lo, hi = 1e-6, max(target, 1.0) * 2.0
    for _ in range(60):
        mid = (lo + hi) / 2
        p0 = nb_pmf(0, mid, phi)
        val = mid / (1 - p0) if p0 < 0.999999 else float("inf")
        if val < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def quantile(stat: str, proj: dict, q: float, params: dict):
    """Inverse of prob_over by bisection - used to bound where we will quote.

    Returns None when the requested quantile falls inside the zero atom: the
    survival function tops out at 1 - p_zero, so anything at or below that is
    unreachable and bisection would silently collapse to ~0 and publish it as
    a band edge.
    """
    if q <= params[stat].get("p_zero", 0.0):
        return None
    lo, hi = 0.0, 400.0 if stat != "receptions" else 25.0
    for _ in range(48):
        mid = (lo + hi) / 2
        pv = prob_over(stat, proj, mid, params, gate=False)
        if pv is None:
            return None
        if pv > 1 - q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def prob_over(stat: str, proj: dict, line: float, params: dict, gate: bool = True):
    """P(stat > line) for one player, or None when we refuse to quote.

    Mixture throughout: with probability p_zero the player returns nothing, and
    otherwise the result is distributed around the projection scaled by the
    conditional ratio. A line at or below zero is therefore not a free bet -
    the zero mass sits exactly there.
    """
    gate_col, floor = FLOORS[stat]
    if (proj.get(gate_col) or 0) < floor:
        return None
    c = params[stat]
    if gate and not c.get("cleared", False):
        # Clearance is per stat, not global. Receptions misses the in-band
        # calibration by 4.2pp - the lattice is coarse enough that a half-point
        # line moves real probability - so it is refused while the two yardage
        # markets, at 0.2pp and 1.3pp, are cleared.
        return None
    src = {"receptions": "proj_receptions", "rec_yds": "proj_rec_yards",
           "rush_yds": "proj_rush_yards"}[stat]
    m = proj.get(src) or 0
    if m <= 0:
        return None
    # A line below zero is certain to be exceeded; the zero atom sits AT zero,
    # not below it. Returning the mixture here dropped that mass and made the
    # survival function non-monotone.
    if line < 0:
        return 1.0
    mu = m * (c["ratio_k"] * m ** c["ratio_p"])
    if stat == "receptions":
        phi = max(1.02, c["phi_k"] * m ** c["phi_p"])
        # Conditional on producing, receptions are at least one, so the NB is
        # truncated at zero and renormalized.
        # The fitted ratio targets E[a | a>0], but truncating an untruncated
        # NB at zero and renormalising RAISES its mean to mu/(1-p0). Solving
        # for the mu whose truncated mean equals the target removes a bias
        # that reached +12.8% at low volume.
        mu = _untruncated_mean(m * (c["ratio_k"] * m ** c["ratio_p"]), phi)
        p0 = nb_pmf(0, mu, phi)
        if p0 >= 0.999:
            return None
        cond = nb_sf(line, mu, phi) / (1 - p0)
        cond = max(0.0, min(1.0, cond))
    else:
        cv = max(0.15, c["cv_k"] * m ** c["cv_p"])
        cond = gamma_sf(line, mu, cv)
    pv = (1.0 - c["p_zero"]) * cond
    band = c.get("band") or list(QUOTE_BAND)
    if gate and not (band[0] <= 1.0 - pv <= band[1]):
        # VALIDATE AT THE GRANULARITY YOU WILL USE IT. Coverage is accurate
        # through the central half of the distribution and over-covers the 80%
        # and 90% bands by about 10pp - the conditional tails are too fat. A
        # line sitting out there would be priced off the part of the model that
        # does not hold, so it is refused rather than quoted.
        return None
    return pv


def refusal_reason(stat: str, proj: dict, line: float, params: dict) -> str:
    """Why we would not quote this line. Every refusal names its cause, so a
    reader can tell a model limit from a missing input."""
    gate_col, floor = FLOORS[stat]
    have = proj.get(gate_col) or 0
    if have < floor:
        return (f"Usage below the quoting floor: {have:.1f} {gate_col.replace('proj_','')} "
                f"against a floor of {floor:g}. The projection is unreliable "
                f"down there because it cannot see who is inactive.")
    if not params[stat].get("cleared"):
        return ("This market is not cleared by the coverage test - "
                f"{stat} misses in-band calibration, so it is refused.")
    pv = prob_over(stat, proj, line, params, gate=False)
    if pv is None:
        return "No projection value for this stat."
    q = 1.0 - pv
    band = params[stat].get("band") or list(QUOTE_BAND)
    return (f"The line sits at the {q:.0%} point of our distribution, outside "
            f"the {band[0]:.0%}-{band[1]:.0%} band where coverage "
            f"is validated. Beyond it there are too few observations to "
            f"certify the fit, so we do not quote.")


# ---------------------------------------------------------------- validation

def validate(con, params, write=False) -> int:
    """Coverage by RANDOMIZED probability integral transform.

    A plain PIT is only uniform for a continuous distribution. These are
    mixtures with an atom at zero and, for receptions, a lattice - so every
    zero maps to the identical PIT value and piles up a spike inside whatever
    band contains it. That artifact alone inflated the 90% coverage to 95.1%
    and read as model failure. Spreading each atom uniformly across the
    probability it occupies is the standard correction, and it is what makes
    the test actually about the model. The jitter is seeded from the row index
    so a re-run reproduces exactly.
    """
    rows = con.execute("""
        SELECT proj_targets, proj_receptions, proj_rec_yards,
               proj_carries, proj_rush_yards,
               receptions, rec_yards, carries, rush_yards
        FROM mart.player_usage_projection
        WHERE receptions IS NOT NULL
    """).fetchall()

    log("=" * 74)
    log("COVERAGE TEST - are the intervals the width they claim to be?")
    log("=" * 74)
    log("A distribution that cannot bracket the actual result at the stated")
    log("rate cannot price a prop, however good the point estimate is.")
    log("")

    rng = random.Random(11)
    ok_all = True
    for stat, proj_i, act_i, gate_i in (
            ("receptions", 1, 5, 0), ("rec_yds", 2, 6, 0),
            ("rush_yds", 4, 8, 3)):
        gate_col, floor = FLOORS[stat]
        pits = []
        for r in rows:
            if (r[gate_i] or 0) < floor:
                continue
            m, a = r[proj_i], r[act_i]
            if m is None or a is None or m <= 0:
                continue
            d = {"proj_targets": r[0], "proj_receptions": r[1],
                 "proj_rec_yards": r[2], "proj_carries": r[3],
                 "proj_rush_yards": r[4]}
            if stat == "receptions":
                hi = prob_over(stat, d, a - 1, params, gate=False)
                lo = prob_over(stat, d, a, params, gate=False)
            else:
                hi = prob_over(stat, d, a - 1e-9, params, gate=False)
                lo = prob_over(stat, d, a, params, gate=False)
            if hi is None or lo is None:
                continue
            # F(a-) = 1-hi, F(a) = 1-lo; spread the atom between them
            pits.append((1 - hi) + rng.random() * max(0.0, hi - lo))
        if not pits:
            continue
        pits.sort()
        n = len(pits)

        def cover(lo_, hi_):
            return sum(1 for x in pits if lo_ <= x <= hi_) / n * 100

        c50, c80, c90 = cover(.25, .75), cover(.10, .90), cover(.05, .95)
        # Inside the quoting band, is the model calibrated? Conditioned on
        # landing in the band, each half should hold half of THAT subset.
        # Walk the ladder widest-first and keep the first width that holds.
        band, band_err, m_ = None, None, 0
        for cand in BAND_LADDER:
            inb = [x for x in pits if cand[0] <= x <= cand[1]]
            mm_ = len(inb) or 1
            lower = sum(1 for x in inb if x <= (cand[0] + cand[1]) / 2) / mm_ * 100
            err = abs(lower - 50)
            if band is None or err <= 2.5:
                band, band_err, m_, c1 = cand, err, mm_, lower
            if err <= 2.5:
                break
        c2 = 100 - c1
        ok = band_err <= 2.5
        params[stat]["band"] = list(band)
        params[stat]["cleared"] = bool(ok)
        log(f"  {stat:<12} n={n:<6}  50% band {c50:5.1f}   80% band {c80:5.1f}"
            f"   90% band {c90:5.1f}")
        log(f"  {'':<12} median PIT {pits[n // 2]:.3f} (0.500 unbiased)  |  "
            f"BAND {band[0]:.0%}-{band[1]:.0%} n={m_:<5} lower {c1:4.1f}%  "
            f"upper {c2:4.1f}%  err {band_err:.1f}pp  "
            f"{'PASS' if ok else 'FAIL'}")
    log("")
    log("=" * 74)
    if write:
        PARAMS_PATH.write_text(json.dumps(params, indent=1), encoding="utf-8")
    for k in ("receptions", "rec_yds", "rush_yds"):
        b = params[k].get("band") or list(QUOTE_BAND)
        log(f"  {k:<11} quoting band {b[0]:.0%}-{b[1]:.0%}")
    cleared = [k for k in ("receptions", "rec_yds", "rush_yds")
               if params[k].get("cleared")]
    refused = [k for k in ("receptions", "rec_yds", "rush_yds")
               if not params[k].get("cleared")]
    log(f"  CLEARED to price: {', '.join(cleared) or 'nothing'}")
    log(f"  REFUSED         : {', '.join(refused) or 'nothing'}")
    log("  Lines outside the band are still refused - the far tail carries too")
    log("  few observations to certify - but the band is now empirical.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fit", "validate"])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = ap.parse_args()
    con = duckdb.connect(str(a.db), read_only=True)
    try:
        if a.cmd == "fit":
            p = fit(con)
            PARAMS_PATH.write_text(json.dumps(p, indent=1), encoding="utf-8")
            log(f"  fitted on {p['n_rows']:,} projection/actual pairs")
            for k in ("receptions", "rec_yds", "rush_yds"):
                c = p[k]
                d = ("phi" if "phi_k" in c else "cv")
                log(f"  {k:<11} P(zero) {c['p_zero']:.3f}  "
                    f"ratio {c['ratio_k']:.3f}*x^{c['ratio_p']:+.3f}  "
                    f"{d} {c[d + '_k']:.3f}*x^{c[d + '_p']:+.3f}  n={c['n']:,}")
            log(f"  saved -> {PARAMS_PATH}")
            return 0
        rc = validate(con, load_params(), write=True)
        return rc
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
