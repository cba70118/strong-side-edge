"""
market.py — odds conversion, devigging, sizing, and CLV for NFL betting analysis.

Run the arithmetic. Do not do it mentally: devig and Kelly errors are silent,
directional, and compounding.

    python3 market.py            # runs the self-test / demo
"""

from __future__ import annotations
import math
from typing import Sequence

# ---------------------------------------------------------------- conversions


def american_to_decimal(a: float) -> float:
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / -a)


def decimal_to_american(d: float) -> float:
    return (d - 1.0) * 100.0 if d >= 2.0 else -100.0 / (d - 1.0)


def american_to_prob(a: float) -> float:
    """Raw (vig-inclusive) implied probability."""
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def prob_to_american(p: float) -> float:
    if not 0.0 < p < 1.0:
        raise ValueError("probability must be strictly between 0 and 1")
    return -100.0 * p / (1.0 - p) if p >= 0.5 else 100.0 * (1.0 - p) / p


def hold(odds: Sequence[float]) -> float:
    """Bookmaker hold (vig) as a fraction, from American odds for all outcomes."""
    return sum(american_to_prob(a) for a in odds) - 1.0


# -------------------------------------------------------------------- devig


def devig_multiplicative(raw: Sequence[float]) -> list[float]:
    """Proportional. Default for balanced two-way markets (spreads, totals)."""
    s = sum(raw)
    return [p / s for p in raw]


def devig_additive(raw: Sequence[float]) -> list[float]:
    """Subtract the overround evenly. Can go negative on extreme longshots."""
    excess = (sum(raw) - 1.0) / len(raw)
    return [p - excess for p in raw]


def devig_power(raw: Sequence[float], tol: float = 1e-12) -> list[float]:
    """Solve k such that sum(p_i ** k) == 1. Handles favorite-longshot bias.

    Recommended for moneylines with a sizeable favorite and for longshot-heavy
    markets such as anytime touchdown.
    """
    lo, hi = 0.5, 10.0
    for _ in range(300):
        k = (lo + hi) / 2.0
        s = sum(p ** k for p in raw)
        if abs(s - 1.0) < tol:
            break
        # sum(p**k) is decreasing in k for p < 1
        if s > 1.0:
            lo = k
        else:
            hi = k
    return [p ** k for p in raw]


def devig_shin(raw: Sequence[float], tol: float = 1e-12) -> list[float]:
    """Shin (1993): models a fraction z of insider money. Best for large fields.

    p_i = (sqrt(z^2 + 4(1-z) * pi_i^2 / PI) - z) / (2(1-z))
    """
    total = sum(raw)

    def probs(z: float) -> list[float]:
        if z <= 1e-12:
            return devig_multiplicative(raw)
        return [
            (math.sqrt(z * z + 4.0 * (1.0 - z) * (p * p) / total) - z)
            / (2.0 * (1.0 - z))
            for p in raw
        ]

    lo, hi = 0.0, 0.99
    for _ in range(300):
        z = (lo + hi) / 2.0
        s = sum(probs(z))
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = z
        else:
            hi = z
    return probs(z)


DEVIG_METHODS = {
    "multiplicative": devig_multiplicative,
    "additive": devig_additive,
    "power": devig_power,
    "shin": devig_shin,
}


def fair_probs(american_odds: Sequence[float], method: str = "multiplicative") -> list[float]:
    """Devig a full market given American odds for every outcome."""
    if method not in DEVIG_METHODS:
        raise ValueError(f"method must be one of {sorted(DEVIG_METHODS)}")
    return DEVIG_METHODS[method]([american_to_prob(a) for a in american_odds])


def recommend_devig(american_odds: Sequence[float]) -> str:
    """Pick a devig method per the rules in references/market-math.md."""
    n = len(american_odds)
    if n > 3:
        return "shin"
    raw = [american_to_prob(a) for a in american_odds]
    if max(raw) / sum(raw) > 0.70:      # heavy favorite / longshot present
        return "power"
    return "multiplicative"


# ------------------------------------------------------------ edge & sizing


def edge(model_prob: float, american_price: float, market_odds: Sequence[float],
         method: str | None = None) -> dict:
    """Edge of `model_prob` against the NO-VIG price implied by the full market."""
    method = method or recommend_devig(market_odds)
    fair = fair_probs(market_odds, method)
    raw = [american_to_prob(a) for a in market_odds]
    target = min(range(len(market_odds)),
                 key=lambda i: abs(raw[i] - american_to_prob(american_price)))
    return {
        "method": method,
        "novig_prob": fair[target],
        "raw_prob": raw[target],
        "edge": model_prob - fair[target],
        "hold": sum(raw) - 1.0,
    }


def kelly(model_prob: float, american_price: float, fraction: float = 0.25) -> float:
    """Fractional Kelly stake as a share of bankroll. Quarter Kelly by default.

    Full Kelly assumes your probability is exact. It never is.
    """
    b = american_to_decimal(american_price) - 1.0
    f = (b * model_prob - (1.0 - model_prob)) / b
    return max(0.0, f * fraction)


def size_bet(model_prob: float, american_price: float, bankroll: float,
             fraction: float = 0.25, max_pct: float = 0.03) -> dict:
    f = kelly(model_prob, american_price, fraction)
    capped = min(f, max_pct)
    return {
        "kelly_fraction": f,
        "capped_fraction": capped,
        "stake": round(bankroll * capped, 2),
        "capped": capped < f,
    }


# ---------------------------------------------------------------------- CLV


def clv(price_taken: float, closing_price: float,
        market_at_bet: Sequence[float] | None = None,
        market_at_close: Sequence[float] | None = None) -> dict:
    """Closing line value. Pass full markets to get an honest no-vig comparison."""
    if market_at_bet and market_at_close:
        m1 = recommend_devig(market_at_bet)
        m2 = recommend_devig(market_at_close)
        p_bet = fair_probs(market_at_bet, m1)[0]
        p_close = fair_probs(market_at_close, m2)[0]
    else:  # raw fallback — biased by hold, but directionally fine
        p_bet = american_to_prob(price_taken)
        p_close = american_to_prob(closing_price)
    return {
        "prob_at_bet": p_bet,
        "prob_at_close": p_close,
        "clv_prob_points": (p_close - p_bet) * 100.0,
        "beat_close": p_close > p_bet,
    }


# ------------------------------------------------------- spread <-> moneyline

# SD of margin AROUND THE SPREAD (not raw margin SD, which is ~14.3 and wrong here).
# Computed from nflverse games.csv, 2018-2025, n=2227: residual sd = 12.76.
# Re-run key_numbers.py each offseason and update this.
MARGIN_SD = 12.76


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def spread_to_winprob(spread: float, sd: float = MARGIN_SD) -> float:
    """Normal approximation. Decent from -3 to -10; understates big favorites.

    `spread` is from the team's perspective (negative = favorite).
    Prefer the empirical curve from key_numbers.py when you have the data.
    """
    return _phi(-spread / sd)


def winprob_to_spread(p: float, sd: float = MARGIN_SD) -> float:
    lo, hi = -30.0, 30.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if spread_to_winprob(mid, sd) > p:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 2)


def team_totals(total: float, home_spread: float) -> dict:
    """The most useful derived number on the board. home_spread negative = home favored."""
    return {
        "home": total / 2.0 - home_spread / 2.0,
        "away": total / 2.0 + home_spread / 2.0,
    }


def cross_market_check(spread: float, ml_market: Sequence[float],
                       sd: float = MARGIN_SD) -> dict:
    """Does the no-vig moneyline agree with the spread? Disagreement is a real edge."""
    p_spread = spread_to_winprob(spread, sd)
    p_ml = fair_probs(ml_market, recommend_devig(ml_market))[0]
    return {
        "winprob_from_spread": p_spread,
        "winprob_from_moneyline": p_ml,
        "disagreement_pts": (p_ml - p_spread) * 100.0,
        "note": "positive = ML implies a stronger favorite than the spread does",
    }


# --------------------------------------------------------------------- demo

if __name__ == "__main__":
    print("=== two-way spread, -110 / -110 ===")
    mkt = [-110, -110]
    print(f"hold: {hold(mkt):.4f}")
    print("multiplicative:", [round(p, 4) for p in fair_probs(mkt, "multiplicative")])
    print("power:         ", [round(p, 4) for p in fair_probs(mkt, "power")])

    print("\n=== lopsided moneyline, -420 / +330 ===")
    ml = [-420, 330]
    for m in ("multiplicative", "additive", "power", "shin"):
        print(f"{m:16s}", [round(p, 4) for p in fair_probs(ml, m)])
    print("recommended:", recommend_devig(ml))

    print("\n=== edge & sizing (model says 0.83 on the favorite) ===")
    e = edge(0.83, -420, ml)
    print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in e.items()})
    print(size_bet(0.83, -420, bankroll=10_000))
    print("\n=== same market, model says 0.63 -> no bet ===")
    print(size_bet(0.63, -420, bankroll=10_000))

    print("\n=== derived team totals: total 46.5, home -3.5 ===")
    print(team_totals(46.5, -3.5))

    print("\n=== cross-market check: home -3.5 vs ML -190/+160 ===")
    print({k: (round(v, 4) if isinstance(v, float) else v)
           for k, v in cross_market_check(-3.5, [-190, 160]).items()})

    print("\n=== CLV: took +3 (-110), closed +2.5 (-110) ===")
    print({k: (round(v, 4) if isinstance(v, float) else v)
           for k, v in clv(-110, -110, [-110, -110], [-118, -102]).items()})

    print("\n=== sanity checks ===")
    assert abs(american_to_prob(-110) - 0.5238) < 1e-3
    assert abs(american_to_decimal(150) - 2.5) < 1e-9
    assert abs(decimal_to_american(2.5) - 150) < 1e-9
    for m in DEVIG_METHODS:
        assert abs(sum(fair_probs([-420, 330], m)) - 1.0) < 1e-6, m
        assert abs(sum(fair_probs([250, 400, 550, 900], m)) - 1.0) < 1e-6, m
    assert abs(kelly(0.55, 100, 1.0) - 0.10) < 1e-9
    assert kelly(0.40, 100, 1.0) == 0.0
    assert abs(spread_to_winprob(0.0) - 0.5) < 1e-9
    assert team_totals(46.5, -3.5)["home"] == 25.0
    print("all passed")
