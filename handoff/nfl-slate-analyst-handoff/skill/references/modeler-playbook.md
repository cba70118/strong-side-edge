# The Modeler Playbook — How the Best Public NFL Modelers Think

This file encodes the working principles of the most respected public NFL modelers,
so the analyst can reason *the way they do*, not just cite them. Each entry is:
who they are → their core principle → how this skill applies it.

---

## Sean Koerner (Action Network, ex-STATS Inc.)

4x FantasyPros most-accurate ranker. Former weather forecaster — and it shows:
everything is a probability distribution, nothing is a point estimate.

**Principles to apply:**
- **Tiers, not ranks.** When the gap between two options is smaller than the error
  bar, they are the same. Never present a rank-ordered list that implies false
  precision — group into tiers and say the tiers are the signal.
- **Probability-of-outcome outputs.** Prefer "62% to clear the line" or "top-12
  finish odds" over single-number projections. A projection that can't be stated
  as a probability isn't finished.
- **Continuous updating.** Projections are living objects. Every piece of news
  (role change, injury, depth chart move) re-runs the number. Never defend a stale
  number because it was published.
- **Opportunity-cost framing.** The value of any choice is measured against the
  next-best alternative that will still be available — the drop-off, not the
  absolute number.

**Learn more:** actionnetwork.com/article/author/sean-koerner · Fantasy Flex podcast ·
FantasyLabs "Koerner's Fantasy Tiers" articles.

---

## Rufus Peabody & Cade Massey (Massey-Peabody, Unabated, Bet the Process)

Professional bettor + Wharton professor. The most openly documented sharp NFL
power-rating operation.

**Principles to apply:**
- **Weight by out-of-sample predictive power.** Massey: "a lot of people are
  crunching numbers these days but there aren't many who are assigning those
  numbers weight by how well they predict performance out of sample." In-sample
  correlation is how you fool yourself. Any factor you lean on must have shown it
  predicts *future* games, not that it explains past ones.
- **Clean the variables first.** Their stated edge is having "a cleaner version"
  of basic performance factors (rushing, passing, scoring, play success) than
  everyone else — contextualized for opponent, situation, and home field —
  *before* any weighting happens. Garbage in, regressed garbage out.
- **Exclude what you can't measure.** Personnel drama, coaching narratives, and
  motivation are deliberately not in the model. If it can't be measured and
  validated, it's not an input.
- **Respect the ceiling.** Their long-run ATS rate is ~56% — and that is
  elite. Anyone's process implying 60%+ hit rates is broken. Expect edges to be
  small, rare, and mostly luck-dominated on any single week.

**Learn more:** massey-peabody.com · Bet the Process podcast (with Jeff Ma) ·
unabated.com education library · his MIT Sloan Sports Analytics talks.

---

## Ben Baldwin (nflfastR, rbsdm.com, Open Source Football)

Economist; co-author of the open-source models this skill's data pipeline sits on.

**Principles to apply:**
- **"EPA is not a magic bullet."** His own words about his own metric. EPA/play is
  the best single team-strength input, but it inherits QB health, garbage time,
  opponent strength, and turnover luck. Always decompose before trusting.
- **Stability windows over season-to-date.** His Open Source Football work on
  rolling EPA averages is the empirical basis for using stable-window samples —
  roughly, offensive EPA stabilizes far faster than defensive EPA, and both need
  garbage-time and opponent adjustment before use.
- **Verifiability.** His models are open because claims you can't check are
  worthless. Mirror this: show the number's provenance (source, window, filter)
  with every stat cited.

**Learn more:** nflfastr.com model write-ups · opensourcefootball.com ·
rbsdm.com/stats.

---

## Aaron Schatz (DVOA — FTN, formerly Football Outsiders)

Inventor of DVOA, the original public situation-adjusted efficiency metric.

**Principles to apply:**
- **Situation-specific baselines.** A 5-yard gain on 3rd-and-4 is a success; on
  1st-and-10 it's roughly neutral; on 3rd-and-12 it's a failure. Never evaluate
  production against a global average when a situational baseline exists.
- **Opponent adjustment is not optional.** Every DVOA number is
  defense-adjusted. Raw efficiency vs. a soft schedule is a trap this skill must
  name explicitly whenever it appears.
- **Version your methodology.** DVOA is on v8+. When the method changes, say so
  and say why. Silent methodology drift destroys backtests.

**Learn more:** ftnfantasy.com/learn-more-about-dvoa · the annual FTN Football
Almanac methodology essays.

---

## Kevin Cole (Unexpected Points, ex-PFF head of quant research)

Publishes what most modelers keep private: full model builds, backtests, and
honest post-mortems.

**Principles to apply:**
- **Backtest in public (to yourself).** Before trusting a signal for this week,
  state how it performed historically — and if you don't know, say the signal is
  unvalidated and downweight it.
- **Post-mortem losses by process, not outcome.** The review question is never
  "did it win," it's "did the number beat the close, and was the thesis's causal
  story correct."

**Learn more:** unexpectedpoints.com · his RotoGrinders 2018 model-build series ·
Unexpected Points podcast (interviews with Peabody, Baldwin, et al.).

---

## Institutional patterns worth copying

- **ESPN FPI — priors that decay.** FPI starts each season from priors built
  partly on market-derived expectations (Vegas win totals) and shrinks them as
  real games arrive. This is the correct early-season posture: Weeks 1–4 opinions
  should be mostly prior, mostly market, and explicitly labeled as such.
- **FiveThirtyEight Elo — the transparent baseline.** A simple, fully open Elo
  with a QB adjustment layer. Useful as a sanity-check number: if a conclusion
  disagrees wildly with a dumb-but-honest baseline, the burden of proof rises.
- **Josh Hermsmeyer — usage before outcome.** Air yards and target share are the
  receiver world's volume signal; efficiency follows volume, never the reverse.
  This is the ancestor of this skill's props usage chain.
- **Miller & Davidow, *The Logic of Sports Betting* — market structure literacy.**
  Know how the line you're betting was made: which books originate vs. copy,
  where the synthetic hold sits, and why beating the close is the only durable
  scorecard. When evaluating any price, ask *whose* number it is.

---

## The consensus principles (what everyone converges on independently)

1. The closing market is the benchmark; CLV is the scorecard.
2. Predictive beats descriptive; validate out-of-sample or downweight.
3. Context (opponent, situation, garbage time, home field) before weighting.
4. Priors + regression early; data takes over as samples stabilize.
5. Distributions, tiers, and probabilities — never bare point estimates.
6. Narratives are not inputs.
7. Show provenance; version the method; post-mortem by process.
