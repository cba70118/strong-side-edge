# NFL Tool — Logic Specification

*How the tool should think, translated from the working principles of the best public
NFL modelers (Koerner, Massey-Peabody, Baldwin, Schatz, Cole, FPI, Elo). This is an
architecture-of-reasoning spec, not a tech stack spec — it defines what the tool
computes, in what order, and what it refuses to do.*

---

## Design thesis

The tool holds **two independent estimates** of every game and player outcome and
only ever acts on the *disagreement* between them:

1. **The football estimate** — what performance data says, built from predictive,
   opponent-adjusted, stability-weighted inputs.
2. **The market estimate** — what the no-vig price says the world believes.

Every downstream feature (bet flags, projections, confidence labels, tracking) is a
function of the gap between those two numbers and the error bar around each. This is
the architecture every sharp public modeler converges on: the market is the prior,
the model is the challenger, and the burden of proof is always on the model.

---

## Layer 1 — Data foundation

**Source of truth:** nflverse play-by-play (free, open, EPA/WP/xPass precomputed).
Odds from a multi-book API, storing *open, current, and close* for every market —
the close is required because it is the grading benchmark (Layer 6).

**Cleaning rules (the Massey-Peabody "clean variables" step) applied before any
metric is computed:**

- Filter garbage time (win probability outside ~[0.05, 0.95], configurable) before
  computing team efficiency.
- Tag and segment samples on structural breaks: QB change, play-caller change,
  multi-starter OL change. A sample that spans a structural break is two samples.
- Opponent-adjust everything (ridge/regularized adjustment is fine; unadjusted
  numbers are display-only, never model inputs).
- Situational baselines, not global ones (the DVOA principle): success is defined
  per down-and-distance, not per yard.

**Provenance requirement (the Baldwin principle):** every number the tool surfaces
carries its window, filter, and source. No orphan stats in the UI.

## Layer 2 — Ratings engine (the football estimate)

- Core team ratings from opponent-adjusted EPA/play and success rate, split
  offense/defense and pass/run, using **stability-weighted windows** — weights per
  metric come from how quickly that metric stabilizes (offense fast, defense slow,
  turnovers barely ever). Not season-to-date by default.
- **Out-of-sample validation gate:** a feature enters the rating only if it improves
  prediction on held-out seasons. Keep a registry: feature → validated lift → date
  tested. Unvalidated features can be displayed but are excluded from the rating.
  This gate is the single most important line of code in the tool.
- **Preseason priors that decay** (the FPI posture): initialize each season from
  last season's stable metrics + market win totals + personnel deltas; blend toward
  in-season data on a schedule tied to metric stability. Expose the blend ("this
  rating is 64% prior") in the UI.
- **Dumb baseline in parallel:** maintain a simple Elo alongside the main rating.
  When the main model and Elo disagree sharply, flag it — the burden of proof rises.
- Output per game: projected margin and total **as distributions** (mean + spread),
  never bare points.

## Layer 3 — Market engine (the market estimate)

- Devig every price (method configurable: multiplicative default, power/Shin for
  longshot-heavy markets) → fair probability per outcome.
- Derive implied team totals from spread + total; these drive all props logic.
- Track line movement open → now, and book disagreement; both are signal, not noise.
- **Market-structure awareness (Miller/Davidow):** weight sharp-origin books over
  copy books when constructing the "market consensus" number.

## Layer 4 — Disagreement and decision logic

The tool never says "bet X because the model likes it." The decision function is:

```
edge_raw     = model_fair_prob − market_fair_prob
edge_usable  = edge_raw only if:
                 (a) |edge_raw| > model error bar for this market type
                 (b) |edge_raw| > minimum threshold (~2% vs full-market no-vig)
                 (c) a nameable cause exists (injury not fully priced, structural
                     break the sample hides, situational total misread, book lag)
                 (d) the "killer questions" pass: market may know something you
                     don't? sample broken? would you flip sides 1pt away?
```

If (c) fails — no nameable mispricing — the tool outputs **PASS** with the reason,
and passes are logged as first-class records (selectivity is the product).

Sizing: fractional Kelly (quarter default) off the usable edge, with a per-week
exposure cap and correlation-aware caps for same-game combinations.

## Layer 5 — Presentation logic (how outputs speak)

- **Tiers, not ranks (Koerner):** any ranked list collapses entries whose gap is
  inside the error bar into tiers. Within-tier order is presented as arbitrary.
- **Probabilities, not verdicts:** "58% to clear," "range 17–31 points," "odds of
  top-12 finish" — every claim is a distribution statement.
- **Confidence labels tied to sample state:** early-season outputs are visibly
  tagged as prior-dominated; post-structural-break outputs tagged as small-sample.
- **No narrative fields anywhere.** The schema simply has no place for revenge
  games, primetime records, or "due" — narratives are excluded at the data-model
  level, not by policy.

## Layer 6 — Grading and self-correction

- **CLV is the scorecard:** every recommendation is graded against the closing
  number, win or lose. Weekly report: CLV per bet, aggregate CLV, hit rate as a
  secondary stat with explicit "small sample" framing.
- **Kill conditions logged at bet time** and checked pre-kickoff; a triggered kill
  condition auto-flags the bet for review regardless of outcome.
- **Process post-mortems (Cole):** losses with positive CLV are graded "good
  process"; wins with negative CLV are graded "bad process — do not repeat."
- **Calibration audit:** quarterly, compare stated probabilities to observed
  frequencies (60% claims should hit ~60%). Miscalibration adjusts the error bars
  in Layer 4, widening thresholds automatically.
- **Versioned methodology (Schatz):** any change to features, weights, or windows
  bumps a model version stored with every historical output, so backtests never
  silently mix methods.

## Guardrails (things the tool must refuse)

- Never invent or estimate a line to price against when real odds are unavailable —
  fake reference prices manufacture fake edges. Football read only in that state.
- Never report an edge under the noise threshold as a "small edge" — it is "no edge."
- Never present season-to-date stats across a structural break without the break flag.
- Never treat same-game parlay legs as independent.
- Expectation management is a feature: the tool's own docs state that elite
  long-run ATS performance is ~55–56% (Massey-Peabody's public record) and that any
  internal result implying materially more is a bug to investigate, not an edge.

---

## Build order (if implementing incrementally)

1. Layer 1 + Layer 3 first (clean data + devig): even with no model, "what does the
   market fairly believe" is immediately useful and everything else depends on it.
2. Layer 2 with priors + Elo baseline; validate on held-out seasons before wiring
   to Layer 4.
3. Layer 4 decision function with PASS logging.
4. Layer 6 CLV tracking (start recording closes from day one, even before betting).
5. Layer 5 presentation polish; props pipeline (usage-tree, distribution outputs)
   as a second product cycle.

*Compiled August 16, 2026, from public methodology of Sean Koerner (Action Network),
Massey-Peabody Analytics, Ben Baldwin (nflfastR/Open Source Football), Aaron Schatz
(DVOA/FTN), Kevin Cole (Unexpected Points), ESPN FPI, and FiveThirtyEight Elo. Sources
and links in `docs/modelers-guide.md`. The build order above maps onto `ROADMAP.md`:
Layers 1+3 ≈ `nfl-data-pipeline`, Layer 2 ≈ `nfl-power-ratings`, Layer 6 ≈
`nfl-bet-log`, Layer 5's props cycle ≈ `nfl-prop-scanner`.*
