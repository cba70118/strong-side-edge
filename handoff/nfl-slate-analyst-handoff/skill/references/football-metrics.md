# Football Metrics

The purpose of this file is to keep you from arguing a bet with a statistic that cannot
support it.

---

## 1. The Core Metrics

**EPA (Expected Points Added)** — the change in a drive's expected point value from one
play to the next, given down, distance, field position, time, and score. It is the best
single all-purpose efficiency measure in football because it correctly values a 4-yard
gain on 3rd-and-3 (good) differently from a 4-yard gain on 3rd-and-8 (bad). Available
per play in nflverse's `play_by_play` as `epa`.

**EPA/play** — total EPA divided by plays. Report offense and defense separately, and
**always split pass vs. run**, because the two have different stability and different
league-average baselines (passing EPA/play is meaningfully higher than rushing).

**Success rate** — the share of plays with positive EPA. Less sensitive to outliers than
EPA/play. A team can have high EPA/play from three explosive plays and be bad otherwise;
success rate catches that. **Use both. Where they disagree, the team is boom-bust —
which raises variance and matters for totals and spreads differently.**

**PROE (Pass Rate Over Expected)** — actual pass rate minus the rate expected given
down, distance, score, and time. This is the cleanest available measure of *coaching
intent*, and it stabilizes fast. It is the most useful single input for projecting play
volume splits, which is the foundation of every prop.

**Pressure rate** (generated and allowed) — stabilizes faster than sack rate, which is
noisier and partly a QB trait. Prefer pressure rate; treat sack totals as a lagging,
regression-prone stat.

**Explosive play rate** — 20+ yard passes, 10+ yard runs. Predicts scoring variance
better than it predicts scoring level.

**Yards per route run (YPRR)** — the best public receiver efficiency metric because it
divides by opportunity rather than by targets. Requires route-participation data.

**Target share / air yards share / WOPR** — WOPR weights target share and air-yards share
into a single opportunity number. Opportunity metrics are the backbone of prop projection.

---

## 2. The Stability Table

The central question for any stat: **how many games before it tells you about the future
instead of the past?** Approximate ordering, from fastest to slowest to stabilize:

| Speed | Metrics |
|---|---|
| **Fast (≈3–5 games)** | PROE, snap share, target share, aDOT, pressure rate, pace/seconds per play, rush attempt share |
| **Medium (≈6–10 games)** | Pass EPA/play, pass success rate, YPRR, offensive line pressure allowed, explosive pass rate |
| **Slow (a season or more)** | Rush EPA/play, defensive EPA/play, defensive success rate, sack rate |
| **Essentially never — regress hard** | Red zone TD rate, third-down conversion rate, turnover margin, fumble recovery rate, yards per carry, points allowed, close-game record |

**Treat these as directional, not gospel.** Stabilization points shift with rule changes
and scoring environment. When a bet hinges on one of them, recompute the split-half
correlation from nflverse play-by-play for the recent era and cite what you found.

**Two rules that follow directly from this table:**

1. Any argument built on red zone rate, third-down rate, turnover margin, or record is
   an argument built on noise. The public builds most of its arguments here. That
   asymmetry is a large share of where soft numbers come from.
2. Defense stabilizes slowly. Be far more confident describing an offense than a defense
   after the same number of games — and be skeptical of "elite defense" narratives built
   on 4 games against weak offenses.

---

## 3. Opponent Adjustment

Raw efficiency after 4 games is mostly a schedule readout. Adjust before comparing.

- **Minimum viable:** compare each team's per-play efficiency to the average that its
  opponents allowed to everyone else.
- **Better:** ridge regression of play-level EPA on offense identity, defense identity,
  and situation. Regularization is what keeps small samples from producing absurd
  coefficients — do not run this unregularized.
- Always report **strength of schedule faced** alongside any efficiency ranking. A top-5
  offensive EPA against the five worst defenses is not a top-5 offense.

---

## 4. Filtering the Sample

Before computing anything, decide what to exclude and say so:

- **Garbage time.** Filter to plays where win probability is roughly between 0.20 and
  0.80 (nflverse `wp`). Blowouts inflate the trailing team's passing efficiency (soft
  coverage) and destroy the leading team's (all runs). Unfiltered efficiency after a
  blowout is one of the most common ways to be confidently wrong.
- **Kneels, spikes, and two-minute end-of-half sequences.** Exclude explicitly.
- **Broken samples.** A backup QB, a play-caller change, three missing OL starters, or a
  post-trade-deadline roster change means the earlier sample describes a team that no
  longer exists. Say when a sample is broken and truncate it rather than averaging
  across the break.

---

## 5. Injury Valuation

The question is never "who is out." It is "**what is the point value of the downgrade,
and is it already in the price.**"

- **Quarterback is the only position with a routinely large spread impact.** The gap
  between a top-tier starter and a replacement-level backup is typically the largest
  single-player adjustment in the sport; the gap between an average starter and a
  competent backup is much smaller. Value it by the difference in expected EPA/play
  times expected dropbacks, converted to points — not by reputation.
- **Offensive line, cumulative.** One replacement lineman is a small effect; three is a
  large one, and it flows through to pressure rate, which flows to everything else.
- **Skill positions redistribute more than they subtract.** A missing WR1 moves targets
  to WR2 and TE — often a bigger prop edge than a team-level edge.
- **Defensive individual absences are usually overpriced by the public**, with the
  partial exception of a true shutdown CB against a one-receiver offense.
- **Timing is everything.** If the line already moved on the news, the value is gone.
  Compare the current number to the pre-news number before claiming an injury edge.

---

## 6. Situational Factors — Sized Honestly

- **Home field advantage** has declined substantially from its historical level and is
  now a small number, and it varies by venue (altitude and a few specific stadiums are
  real outliers). **Recompute it from recent nflverse results rather than using a legacy
  constant** — using a stale HFA value biases every game on your board in the same
  direction, which is the worst kind of error.
- **Rest and short weeks** — small, real, frequently overstated. Bye-week advantage is
  smaller than commonly claimed.
- **Travel and time zone** — small. West-coast teams playing early East-coast games is
  the most-cited version and the effect is modest.
- **Weather** — mostly overrated, with one exception: **sustained wind above roughly
  15 mph has a consistent and sizable negative effect on passing efficiency, kicking,
  and scoring.** Temperature and light rain matter far less than the broadcast implies.
  Snow matters mostly through wind and footing, not cold. Check actual forecast wind at
  the stadium, not the city's general forecast, and note that late-week forecast changes
  are one of the few genuinely actionable information edges available on totals.
- **Divisional games** — slightly lower scoring and slightly tighter margins on average;
  a small effect, not a thesis.

---

## 7. Descriptive vs. Predictive — the Quick Filter

| Use to predict | Use only to describe |
|---|---|
| EPA/play (split pass/run, opponent-adjusted, garbage-time filtered) | Total yards |
| Success rate | Points scored / allowed |
| PROE, pace | Win-loss record |
| Pressure rate | Sack totals |
| Target share, YPRR, WOPR, snap share | Receptions, receiving yards (raw) |
| Rush attempt share, goal-line carry share | Rushing TDs |
| Opponent-adjusted efficiency | Turnover margin |

If your argument depends on a stat in the right column, you do not have an argument yet.
Go find the left-column version of it.
