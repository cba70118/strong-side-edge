"""
devig.py - remove bookmaker margin from a set of prices.

Shared primitive. Every fair price, EV number and CLV grade in the system
depends on this, so it lives in one place rather than being re-derived per
skill.

METHOD MATTERS. For a -110/-110 spread all four methods agree to ~0.1%. For a
heavy moneyline favorite or a 20-runner anytime-TD market they diverge by
several percent, and that difference is larger than the edge being hunted.

  multiplicative  q_i = p_i / sum(p)
                  Simplest, and the default almost everywhere. Strips margin
                  proportionally, which means it PRESERVES the favorite-
                  longshot bias already baked into the prices: longshots stay
                  OVERSTATED and favorites stay UNDERSTATED relative to Shin
                  or power. Acceptable on symmetric two-way markets, actively
                  misleading on skewed ones - and a common source of phantom
                  longshot "edges".

  additive        q_i = p_i - (sum(p) - 1)/n
                  Splits margin equally in probability terms. Better on
                  longshots, but can emit negative probabilities on very
                  lopsided books (falls back to multiplicative when it would).

  power           find k such that sum(p_i^k) = 1
                  Well behaved across the whole range. Good general default
                  for two-way markets.

  shin            Shin (1993): assumes margin arises from insider trading and
                  solves for the insider proportion z. Best supported method
                  for genuinely multiway, longshot-heavy markets - division
                  winner, MVP, Super Bowl.

WHAT IS AND IS NOT A MULTIWAY MARKET. Anytime-TD is NOT one n-way market; it
is n independent Yes/No two-way markets, one per player, and devigging them
jointly is meaningless (their raw probabilities sum to far more than 1 because
many players score in the same game). The same is true of every Over/Under
player prop. True NFL multiway markets are the futures: division winner,
conference winner, MVP.

Stdlib only - bisection solvers, no numpy or scipy.

Self-test:
    py -3 scripts/devig.py
"""

from __future__ import annotations

METHODS = ("multiplicative", "additive", "power", "shin")


# --- price conversions ----------------------------------------------------

def american_to_prob(american: float) -> float:
    """Raw implied probability, margin included."""
    a = float(american)
    return 100.0 / (a + 100.0) if a > 0 else abs(a) / (abs(a) + 100.0)


def prob_to_american(p: float) -> float:
    """Inverse of american_to_prob.

    Note p == 0.5 maps to +100. American odds are degenerate at even money:
    +100 and -100 denote the same probability, and +100 is the convention.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"probability out of range: {p}")
    return -100.0 * p / (1.0 - p) if p > 0.5 else 100.0 * (1.0 - p) / p


def american_to_decimal(american: float) -> float:
    a = float(american)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


# --- devig methods --------------------------------------------------------

def _multiplicative(p: list[float]) -> list[float]:
    s = sum(p)
    return [x / s for x in p]


def _additive(p: list[float]) -> list[float]:
    n = len(p)
    excess = (sum(p) - 1.0) / n
    q = [x - excess for x in p]
    if any(x <= 0 for x in q):        # lopsided book: fall back rather than lie
        return _multiplicative(p)
    return q


def _power(p: list[float], tol: float = 1e-12, iters: int = 200) -> list[float]:
    """Find k with sum(p_i^k) = 1. sum(p) > 1 implies k > 1."""
    lo, hi = 0.05, 10.0
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        s = sum(x ** mid for x in p)
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = mid          # need a larger exponent to shrink the sum
        else:
            hi = mid
    k = (lo + hi) / 2.0
    q = [x ** k for x in p]
    s = sum(q)
    return [x / s for x in q]         # normalize away residual solver error


def _shin(p: list[float], tol: float = 1e-12, iters: int = 200) -> list[float]:
    """Shin (1993). Solve for insider proportion z in [0, 1)."""
    s = sum(p)
    if s <= 1.0:
        return _multiplicative(p)

    def q_of(z: float) -> list[float]:
        out = []
        for x in p:
            disc = z * z + 4.0 * (1.0 - z) * (x * x) / s
            out.append((disc ** 0.5 - z) / (2.0 * (1.0 - z)))
        return out

    lo, hi = 0.0, 0.99
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        total = sum(q_of(mid))
        if abs(total - 1.0) < tol:
            break
        if total > 1.0:
            lo = mid
        else:
            hi = mid
    z = (lo + hi) / 2.0
    q = q_of(z)
    t = sum(q)
    return [x / t for x in q]


_DISPATCH = {
    "multiplicative": _multiplicative,
    "additive": _additive,
    "power": _power,
    "shin": _shin,
}


# --- public API -----------------------------------------------------------

def devig_probs(probs: list[float], method: str = "power") -> list[float]:
    if method not in _DISPATCH:
        raise ValueError(f"unknown method {method!r}; choose from {METHODS}")
    if not probs or any(x <= 0 for x in probs):
        raise ValueError("probabilities must be positive")
    return _DISPATCH[method](list(probs))


def devig_american(prices: list[float], method: str = "power") -> list[float]:
    """American prices for one complete market -> fair probabilities."""
    return devig_probs([american_to_prob(a) for a in prices], method)


def hold_pct(prices: list[float]) -> float:
    """Bookmaker margin on a complete market, in percent."""
    return (sum(american_to_prob(a) for a in prices) - 1.0) * 100.0


def default_method(market_family: str, n_outcomes: int) -> str:
    """Which method to use where.

    Routes on OUTCOME COUNT, not on market family. Player props are two-way
    (Over/Under, or Yes/No per player) and therefore get `power` like any other
    two-way market - routing them to `shin` because they "feel like longshots"
    is the mistake this function exists to prevent. Only genuine n-way futures
    get `shin`.
    """
    return "shin" if n_outcomes > 2 else "power"


# --- self-test ------------------------------------------------------------

def _selftest() -> int:
    print("devig self-test\n" + "=" * 70)
    fails = 0

    def check(name, cond, detail=""):
        nonlocal fails
        if not cond:
            fails += 1
        print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")

    # 1. symmetric two-way: every method must give exactly 0.5
    for m in METHODS:
        q = devig_american([-110, -110], m)
        check(f"symmetric -110/-110 [{m}]",
              abs(q[0] - 0.5) < 1e-9, f"-> {q[0]:.6f}")

    # 2. all methods must sum to 1
    for m in METHODS:
        q = devig_american([-250, 200], m)
        check(f"sums to 1 [{m}]", abs(sum(q) - 1.0) < 1e-9, f"-> {sum(q):.10f}")

    # 3. hold is positive and sane
    h = hold_pct([-110, -110])
    check("hold(-110/-110) ~ 4.76%", abs(h - 4.7619) < 0.01, f"-> {h:.4f}%")

    # 4. the divergence that matters: heavy favorite
    print("\n  heavy favorite -600 / +425:")
    base = None
    for m in METHODS:
        q = devig_american([-600, 425], m)
        if base is None:
            base = q[0]
        print(f"    {m:<16} fav={q[0]:.4f}  dog={q[1]:.4f}  "
              f"fair={prob_to_american(q[0]):+.0f}/{prob_to_american(q[1]):+.0f}")
    spread = max(devig_american([-600, 425], m)[0] for m in METHODS) - \
             min(devig_american([-600, 425], m)[0] for m in METHODS)
    check("methods diverge on skewed books", spread > 0.005,
          f"range={spread*100:.2f}pp")

    # 5. genuine multiway: an 8-team conference-winner book.
    # Prices MUST produce positive hold or this is an arbitrage, not a book -
    # and shin deliberately falls back to multiplicative when sum(p) <= 1,
    # which silently makes the comparison below vacuous. Assert it first.
    print("\n  true multiway (conference winner, 8 runners):")
    prices = [180, 350, 500, 700, 900, 1300, 1800, 2800]
    check("test market has positive hold", hold_pct(prices) > 1.0,
          f"hold={hold_pct(prices):.2f}%")
    print(f"    raw hold = {hold_pct(prices):.2f}%")
    for m in METHODS:
        q = devig_american(prices, m)
        print(f"    {m:<16} fav={q[0]:.4f}  tail={q[-1]:.4f}  sum={sum(q):.8f}")
    qm = devig_american(prices, "multiplicative")
    qs = devig_american(prices, "shin")
    # Shin corrects favorite-longshot bias: it moves probability mass FROM the
    # tail TO the favorite relative to proportional devig.
    check("shin lifts the favorite vs multiplicative",
          qs[0] > qm[0], f"shin {qs[0]:.4f} > mult {qm[0]:.4f}")
    check("shin cuts the longshot tail vs multiplicative",
          qs[-1] < qm[-1], f"shin {qs[-1]:.4f} < mult {qm[-1]:.4f}")

    # 6. anytime TD is NOT multiway - it is per-player Yes/No
    print("\n  anytime TD, correctly treated as per-player two-way:")
    for name, (yes, no) in {"Kelce": (-105, -125), "WR3": (275, -350)}.items():
        q = devig_american([yes, no], default_method("player_prop", 2))
        print(f"    {name:<8} yes={yes:+5d} no={no:+5d}  ->  fair yes "
              f"{q[0]:.4f}  hold {hold_pct([yes, no]):.2f}%")
    q_joint = [american_to_prob(a) for a in (-105, 275, 150, 190, 260)]
    check("joint anytime-TD probabilities exceed 1 (hence not one market)",
          sum(q_joint) > 1.0, f"sum={sum(q_joint):.3f}")

    # 7. round trip. +100 and -100 are the same probability; +100 is canonical.
    for a in (-250, -110, 100, 340):
        p = american_to_prob(a)
        check(f"round-trip {a:+d}", abs(prob_to_american(p) - a) < 1e-6,
              f"-> {prob_to_american(p):+.1f}")
    check("even money normalizes to +100", prob_to_american(0.5) == 100.0)

    # 8. routing
    check("two-way -> power", default_method("side", 2) == "power")
    check("two-way prop -> power (NOT shin)",
          default_method("player_prop", 2) == "power")
    check("true multiway -> shin", default_method("futures", 12) == "shin")

    print("\n" + ("all passed" if not fails else f"{fails} FAILURE(S)"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
