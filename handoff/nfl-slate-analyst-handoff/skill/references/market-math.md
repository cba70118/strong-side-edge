# Market Math

Everything here is arithmetic you must run, not estimate. `scripts/market.py` implements
all of it.

---

## 1. Odds Conversion

**American → implied probability (with vig)**
- Negative odds: `p = -A / (-A + 100)` → `-150` = 0.600
- Positive odds: `p = 100 / (A + 100)` → `+130` = 0.4348

**American → decimal**
- Negative: `d = 1 + 100/(-A)` · Positive: `d = 1 + A/100`

**Decimal → probability:** `p = 1/d`

**Hold (vig):** sum the raw implied probabilities across all outcomes and subtract 1.
A two-way market at -110/-110 sums to 1.0476 → 4.76% hold.

---

## 2. Removing the Vig (Devigging)

This is the step most people skip and it changes conclusions. Four methods:

**Multiplicative (proportional)** — divide each raw probability by the sum.
Simple, fast, the right default for balanced two-way markets like spreads and totals
priced near -110/-110. Its flaw: it assumes vig is distributed proportionally, which
systematically **overstates the fair probability of heavy favorites**.

**Additive** — subtract `(sum - 1)/n` from each. Assumes vig is spread evenly in
probability terms. Better than multiplicative on lopsided two-way markets, but can
produce negative probabilities on extreme longshots.

**Power** — solve for exponent `k` such that `Σ pᵢ^k = 1`. Handles favorite–longshot
bias much better than multiplicative and is the recommended default for **moneylines
with a big favorite** and for **anytime-TD and other longshot-heavy markets**.

**Shin** — models the market as containing a fraction `z` of insider money and solves
for it. The most theoretically grounded for longshot-heavy markets. Use for outrights
and season futures where the field is large.

**Practical rule:**
| Market | Devig method |
|---|---|
| Spread, total (near -110/-110) | Multiplicative |
| Moneyline, moderate favorite | Power |
| Moneyline, favorite shorter than about -300 | Power or Shin |
| Anytime TD, longshot props | Power or Shin |
| Futures / outrights (many outcomes) | Shin |

**Multi-way props (e.g. 3+ outcomes) must be devigged across the full set**, not pairwise.

---

## 3. Key Numbers

NFL margins cluster hard on **3** and **7**, secondarily on **6, 10, 14, 4**. This is
the reason half-points matter more in football than any other sport, and why moving a
spread from -2.5 to -3.5 costs far more than moving -5.5 to -6.5.

**Do not quote memorized frequencies.** Run `scripts/key_numbers.py`, which pulls actual
game results from nflverse and computes the current margin distribution, then cite the
number you computed and the seasons it covers. Margin distributions drift with rule
changes and scoring environment — a frequency table from an old article can be stale.

Reference output (2018–2025, n = 2,227 games — regenerate before relying on it):

| Margin | % of games |
|---|---|
| **3** | 14.8 |
| **7** | 8.5 |
| 6 | 6.6 |
| 14 | 5.2 |
| 2 | 4.9 |
| 10 | 4.8 |
| 1 | 4.8 |
| 4 | 4.8 |

Note the shape: 3 is nearly twice as common as 7, and 7 is not much more common than 6.
The "key numbers" hierarchy is much flatter below 3 than folklore suggests — which means
half-point buying is worth far less at 6, 10, and 14 than at 3.

That same run gives two other numbers worth having: the **residual standard deviation of
margin around the spread is ≈ 12.8** (not the raw margin SD of ≈ 14.3 — using the raw
number in a win-probability model overstates upsets), and **home field advantage averaged
≈ +1.7 points** over that span with real season-to-season swing, including years near
zero. Do not carry a legacy 2.5–3 point HFA constant; it biases every game on your board
in the same direction.

**How to use key numbers:**
- Buying or selling a half point *across* 3 or 7 is expensive and usually not worth it
  at standard prices. Buying across a non-key number is usually a losing proposition too,
  but for a different reason: the price is set for the average buyer, not for you.
- **Line shopping across a key number is the single highest-ROI habit in NFL betting.**
  Getting +3.5 instead of +3 is worth more than most model edges you will ever find.
  If your process finds a 2% edge but you're not shopping books, the shopping was worth
  more than the model.
- Totals have weaker key numbers than sides, but 41, 43, 44, and 47 carry more mass than
  their neighbors. Verify with the script.

---

## 4. Spread ↔ Moneyline

There is no exact formula — the relationship is empirical and depends on the total
(higher totals compress win probability for a given spread, because more scoring means
more variance). Two approaches:

**Empirical (preferred):** fit win probability against spread using historical results
from nflverse. `scripts/key_numbers.py` includes a helper that does this.

**Normal approximation (quick):** treat margin as normal with mean = -spread and standard
deviation **≈ 12.8** — the residual SD around the spread, computed above. Then
`P(win) = Φ(-spread / σ)`, with push probability at integer spreads handled separately.
This is decent from about -3 to -10 and degrades at the extremes, understating big
favorites. Using the *raw* margin SD (≈14.3) here is a common and consequential error:
it inflates upset probability on every game.

**Use the conversion to arbitrage your own board:** if the no-vig moneyline implies a
higher win probability than the no-vig spread does, one of the two is mispriced.
Cross-market disagreement inside the same game is a real and frequently available edge.

---

## 5. Edge and Bet Sizing

**Edge** = `your_probability - no_vig_market_probability`. Always against the *no-vig*
number, never against the raw price, or you will systematically overstate every edge by
roughly half the hold.

**Kelly fraction:** `f = (b·p - q) / b` where `b` = decimal odds − 1, `p` = your
probability, `q = 1 - p`.

**Always use fractional Kelly.** Full Kelly assumes your probability estimate is correct.
It is not. Quarter Kelly is the default in this skill; half Kelly is the aggressive
ceiling. Full Kelly on an overestimated edge is how bankrolls die.

**Bankroll rules:**
- Cap any single bet at 2–3% of bankroll regardless of what Kelly says.
- Cap total open exposure at 15–20% of bankroll.
- Correlated bets count as **one** position for exposure purposes. Three overs in the
  same game is one bet on that game going over, sized accordingly.
- Recompute Kelly against current bankroll, not starting bankroll.

**Edge thresholds:**
- Under 2% vs. a sharp no-vig price: no bet. That's inside model noise.
- 2–4%: small position, and only with a named mispricing.
- Over 4% against a full-market sharp price: **be suspicious first.** Check for stale
  odds, a late scratch, or a market you misread before you check for genius.

---

## 6. Closing Line Value

`CLV` in probability terms: `no_vig_prob(closing) - no_vig_prob(your_price)`.
Report it in percentage points and in cents of line movement.

CLV is the only fast-feedback measure of whether your process works. Win/loss needs
hundreds of bets to separate skill from variance; CLV separates it in dozens. Track:

- % of bets that beat the close
- Average CLV in probability points
- CLV split by market type (sides vs. totals vs. props) — this tells you *where* your
  process actually has edge, which is usually not where you think.

**Caveat:** CLV is a valid scorecard only against markets that are efficient at close.
On a low-limit prop that never gets sharp action, beating the close means little. On a
Sunday sides market, it means a great deal.

---

## 7. Correlation and Same-Game Parlays

Legs within one game are **not independent**, and books price SGPs with a correlation
adjustment that is usually in their favor. Rules:

- QB passing yards over ↔ WR1 receiving yards over: **strongly positive**. The parlay is
  worth more than independent math suggests, but the book already knows this.
- Team total over ↔ opposing RB rushing yards over: often **negative** (trailing teams
  abandon the run).
- Under + favorite covering: **positive** (a comfortable lead means clock-killing runs).
- Over + underdog moneyline: **positive** (dogs win by scoring).

**Correct use of correlation:** find the leg pairs that are positively correlated in
reality but priced closer to independent than they should be. That is rare and usually
capped at tiny limits. **Default posture: SGPs are entertainment, not edge.** If you
build one, size it as a single small position and say plainly that the correlation
adjustment likely favors the book.

---

## 8. Reading the Market Itself as Data

- **Limits are the sharpness signal.** A number a book will take $500 on and a number
  it will take $50,000 on are not the same information. Weight the high-limit book.
- **Reverse line movement** (line moves toward the side with a minority of tickets)
  indicates money weight differs from ticket count. It is a real signal but a weak and
  widely-known one — it is not a bet by itself.
- **Opener vs. current:** the opener is a book's number; the close is the market's
  number. Value that existed at the open is usually gone by Sunday.
- **Book disagreement** of a half point or more on a side, or a full point on a total,
  is the most actionable market signal available. Take the best number, and ask why the
  outlier book is where it is before assuming it's stale.
- **Steam vs. stale:** if one book moves and others follow within minutes, that's steam
  and the value is gone. If one book sits alone for an hour, it may be stale — or it may
  know something about a scratch that hasn't been reported.
