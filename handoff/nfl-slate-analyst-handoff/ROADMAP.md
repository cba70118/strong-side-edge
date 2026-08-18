# NFL Analyst Agent — Skill Roadmap

`nfl-slate-analyst` (built, attached) is the flagship. It carries the reasoning
discipline, the market math, and the props method. Everything below is a companion
skill that either **feeds** it, **checks** it, or **operationalizes** it.

Build in this order. Each one is worth building only after the previous is actually
being used.

---

## Tier 1 — Build next (these make the flagship real)

### 1. `nfl-data-pipeline`
**Problem it solves:** the flagship assumes fresh data exists. Right now it doesn't.

Pulls nflverse parquet + The Odds API on the cadence in `data-sources.md`, writes to
local parquet, computes opponent-adjusted EPA, PROE, snap/target shares, and
garbage-time-filtered efficiency. Emits one weekly `slate.parquet` the analyst reads.

Why separate: data engineering and analysis have different failure modes. When the
pipeline breaks you want it to break loudly and separately, not silently poison a bet.

### 2. `nfl-bet-log`
**Problem it solves:** without this, nothing learns.

Owns `bets.csv`. Enforces that `thesis` and `kill_condition` are written **before**
kickoff. Records closing lines on placed *and passed* bets. Computes weekly CLV, CLV by
market type, and Kelly-adjusted P/L. Refuses to log a bet missing a thesis.

This is the single highest-value companion skill. It is also the least fun to build,
which is why most bettors never have one.

### 3. `nfl-power-ratings`
Maintains team power ratings (ridge-regularized EPA, opponent-adjusted, with preseason
market priors as the Week 1 anchor) and converts them to a projected spread and total
per game. Outputs your number next to the market number for every game — which is the
input to step 4 of the flagship's workflow.

Two requirements from `docs/architecture-spec.md` are load-bearing here: an
**out-of-sample validation gate** (a feature enters the rating only if it improves
prediction on held-out seasons — keep a registry of feature → validated lift → date
tested), and a **dumb Elo baseline** running in parallel as a sanity check. When the
main rating and Elo disagree sharply, flag it; the burden of proof rises.

---

## Tier 2 — Build once Tier 1 is running

### 4. `nfl-injury-monitor`
Watches practice reports and inactives, and — critically — answers the *right* question:
did the line move on this news already? Values the downgrade in points, compares
pre-news and post-news lines, and flags only the gaps.

### 5. `nfl-prop-scanner`
Automates the props workflow: pulls all player props, runs the usage tree from
`props.py` across every projected player, devigs with the right method per market, and
surfaces only spots above the edge threshold. Includes the prop-sum-vs-team-total
consistency check, which finds mispricings a per-player scan never will.

### 6. `nfl-backtest`
Uses The Odds API historical endpoint plus nflverse results to test any rule before it
costs money. Reports ROI, CLV, and — most importantly — sample size and confidence
interval, so a 58% hit rate on 40 bets gets reported as the noise it is.

---

## Tier 3 — Depth, once the process is proven

### 7. `nfl-matchup-charting` — FTN/participation data: personnel groupings, coverage
shell tendencies, blitz rate, play-action rate, and individual matchup exploitation.
This is where PFF would slot in if you ever pay for it.

### 8. `nfl-weather-totals` — stadium-level wind forecasts against total lines, with a
model of how much wind actually moves scoring. Narrow but genuinely edgy on a handful of
games per season.

### 9. `nfl-live-betting` — in-game win probability against live lines. The largest
theoretical edge in football betting and by far the hardest to execute. Do not build
this until CLV is consistently positive pregame.

### 10. `nfl-report-builder` — turns the analyst's output into the weekly deliverable:
HTML dashboard or PDF, with the bet card, the CLV chart, and the passed-on log. Pairs
with the `dataviz` skill you already have.

---

## What this deliberately does NOT include

- **A DFS skill.** You said sides/totals and props. DFS is a different optimization
  problem (correlated lineup construction under ownership leverage), not an extension of
  this one. It deserves its own agent if you want it.
- **A "picks" or "consensus" skill.** Aggregating other people's picks is the opposite
  of the flagship's prime directive.
- **Anything that scrapes a sportsbook.** Use a licensed odds feed.

---

## The one thing to get right

The flagship's value isn't the football knowledge — that's commodity. It's the two rules
most bettors can't follow: **price before opinion**, and **grade on CLV, not on wins**.
`nfl-bet-log` is what makes the second one real. If you build one companion skill, build
that one.

---

**Sources for the data stack:**

- [nflverse-data releases](https://github.com/nflverse/nflverse-data/releases) — all URLs in the skill were verified to resolve
- [nflverse games.csv (spreads, totals, results)](https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv)
- [The Odds API v4 guide](https://the-odds-api.com/liveapi/guides/v4/)
- [nflfastR package docs](https://cran.r-project.org/web/packages/nflfastR/nflfastR.pdf)
