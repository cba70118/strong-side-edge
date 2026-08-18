# Player Props Modeling

Props are the softest NFL market and the easiest place to be confidently wrong. The
softness comes from volume: books price hundreds of props per game algorithmically, at
low limits, with less sharp action correcting them. The trap is that a plausible story
about a player produces a plausible-sounding number, and plausible numbers lose.

The whole discipline is: **project opportunity, then efficiency, then output a
distribution — and price against the distribution, never against the mean.**

---

## 1. The Usage Tree

Work top-down. Each step is an estimate with a range; carry the ranges forward.

```
1. Game total & spread          → implied team total  (total/2 ∓ spread/2)
2. Pace (sec/play) & script     → team plays
3. PROE + game script           → pass attempts vs. rush attempts
4. Snap share                   → player's share of the field
5. Target share / carry share   → player volume
6. aDOT, YPRR, YPC              → efficiency per opportunity
7. Distribution                 → P(over line)
```

**Steps 1–5 carry almost all of the predictive signal. Step 6 is where everyone spends
their time and where the least edge lives.** Efficiency regresses hard; opportunity does
not. If you have limited effort, spend it on volume.

**Game script feedback loop — do not skip this.** A team projected to trail passes more
than its PROE suggests; a team projected to lead runs more. Adjust the pass/run split by
the spread, not just by tendency. This single adjustment is responsible for a large share
of the mispricing available in RB rushing and WR receiving props on lopsided games.

---

## 2. Distribution Choices by Market

Never answer "is the projection above the line." Answer "what is P(result > line)."

| Market | Distribution | Notes |
|---|---|---|
| Receptions | Negative binomial (or binomial on targets × catch rate) | Overdispersed relative to Poisson — Poisson understates the tails |
| Receiving yards | Gamma or lognormal | Strongly right-skewed; **median < mean** |
| Rushing yards | Gamma | Right-skewed; a single long run dominates the mean |
| Pass attempts | Normal (roughly symmetric, moderate variance) | Tightly tied to script |
| Passing yards | Normal-ish, slight right skew | Attempts × YPA, with YPA variance dominating |
| Passing TDs | Poisson | λ from implied team total × pass-TD share |
| Anytime TD | Poisson → `P(≥1) = 1 - e^(-λ)` | λ = team implied TDs × player TD share |
| Longest reception/rush | Extreme value (or simulate) | Do not model with a mean |

**The skew rule, stated plainly:** for right-skewed yardage markets, a projection whose
*mean* clears the line can still be an **under**, because the mean is pulled up by
low-probability big plays. This is the single most common prop error, and it points the
same direction as public bias (overs), which is exactly why unders on skewed yardage
markets are where the recurring edge tends to sit. Always compute P(over) from the
distribution.

---

## 3. Where the Edges Actually Are

- **Vacated volume the market prices slowly.** A WR1 ruled out Friday redistributes
  targets; the WR2 and TE lines often move less than the target redistribution warrants.
- **Alternate lines and cross-market inconsistency.** A player's 50+ yard alt line, his
  main line, and his reception line all imply a distribution. When they imply
  *different* distributions, one of them is wrong.
- **Prop vs. game-line consistency.** Sum the player props for a team and compare to the
  implied team total. If the props imply more scoring than the game line does, the props
  are collectively too high — bet the direction of the disagreement.
- **Longshot-heavy anytime-TD markets.** Devig with power or Shin (see
  `references/market-math.md`); multiplicative devig badly misprices these.
- **Low-limit staleness.** Prop lines update more slowly than game lines after news.
  Speed on Friday–Sunday morning news is a genuine edge here in a way it is not on sides.

---

## 4. Traps

- **Season-long averages on a changed role.** A player whose snap share jumped three
  weeks ago has a season average that describes someone else.
- **Touchdown regression, both directions.** TD rate is among the least stable stats in
  football. A player "on pace" for a huge TD total is a fade candidate on price, not a
  buy.
- **Correlated props stacked as independent positions.** QB passing yards over and WR1
  receiving yards over are largely the same bet. Size them as one.
- **Ignoring the alternate-line vig.** Alt lines carry much higher hold than main lines.
  A 6% edge on an alt line at -250 is often a 0% edge after honest devigging.
- **Assuming the projection is precise.** If the line sits inside your projection's
  credible interval, there is no bet — no matter how confident the story sounds.

---

## 5. Worked Pattern

This is the actual output of `scripts/props.py` — run it, don't reproduce it by hand.

```
Game: total 46.0, spread -3.0  →  implied team total 24.5
Pace 27.0 sec/play             →  66.7 plays
Base pass rate 0.600, script-adjusted for a 3-point favorite  →  0.573
                               →  38.2 dropbacks, 28.5 rush attempts

WR1: 88% snap share, 24% target share  →  8.96 targets
     66% catch rate                    →  5.92 receptions
     11.5 yards per reception          →  68.0 MEAN receiving yards
     Gamma, CV 0.58                    →  60.6 MEDIAN receiving yards

Line 64.5:  mean beats it by 3.5 yards
            P(over) = 0.459  →  fair price on the over is +118
            CORRECT SIDE: UNDER
```

The mean clears the line and the bet is still the under. That gap is the entire reason
this file exists — and because the public bets overs, the market rarely corrects it.

Note the receptions line in the same output: P(over 4.5 receptions) = 0.655. **The same
player is an under on yards and an over on receptions**, which is coherent, not
contradictory — receptions are far less skewed than yards. Anyone who prices both off
one "he'll have a good game" narrative gets one of them wrong.
