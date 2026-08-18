# Data Sources

Recommended stack, cheapest-first. All nflverse URLs below were verified to resolve.

---

## 1. nflverse — the free backbone (use this for everything football)

nflverse publishes NFL data as GitHub release assets. Every file is available as
`.parquet` (preferred — smaller and typed) or `.csv.gz`. No API key, no rate limit.

**URL pattern:**
```
https://github.com/nflverse/nflverse-data/releases/download/<release>/<file>_<season>.parquet
```

**Verified assets:**

| What | URL |
|---|---|
| Play-by-play (EPA, WP, air yards, situation) | `.../releases/download/pbp/play_by_play_2025.parquet` |
| Weekly player stats | `.../releases/download/stats_player/stats_player_week_2025.parquet` |
| Snap counts | `.../releases/download/snap_counts/snap_counts_2025.parquet` |
| Rosters | `.../releases/download/rosters/roster_2025.parquet` |
| Depth charts | `.../releases/download/depth_charts/depth_charts_2025.parquet` |
| Injuries | `.../releases/download/injuries/injuries_2025.parquet` |
| FTN charting (personnel, coverage, blitz) | `.../releases/download/ftn_charting/ftn_charting_2025.parquet` |
| Participation (routes, personnel) | `.../releases/download/pbp_participation/pbp_participation_2024.parquet` |
| Schedules + closing lines + results | `https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv` |

**The schedules file is the sleeper asset.** It carries historical spread, total, and
result for every game — which means you can compute real key-number frequencies, real
home field advantage, real spread→win-probability curves, and backtest devig assumptions,
all without paying for anything. Do that instead of quoting constants from articles.

**Python:** `pip install nfl_data_py` wraps these, or just read the parquet URLs directly
with pandas + pyarrow. Reading directly is faster and has fewer version headaches.
**R:** `nflreadr` / `nflfastR`.

Swap the season in the filename. Some releases (e.g. participation) lag the current
season — check which years exist before assuming.

---

## 2. Odds — you need a real feed

**The Odds API** (`the-odds-api.com`) is the recommended starting point.
- Sport key: `americanfootball_nfl`
- Markets: `h2h`, `spreads`, `totals`, `outrights`, plus player props
  (e.g. `player_pass_tds`, `player_pass_yds`, `player_rush_yds`, `player_reception_yds`,
  `player_receptions`, `player_anytime_td`) via the single-event odds endpoint
- Historical snapshots back to June 2020, at 5-minute granularity since Sept 2022
- Quota: current odds cost 1 credit per region per market; historical cost 10. The
  `/sports` and `/events` endpoints are free.

**Budget consciously.** Pulling every prop market for every game every hour will burn a
quota fast. Recommended pattern: poll sides and totals frequently, poll props on a
schedule tied to news windows (Friday practice reports, Saturday inactive rumors, Sunday
90-minutes-to-kickoff).

**The historical endpoint is what makes the subscription worth it** — it is how you
backtest, how you measure your own CLV honestly, and how you learn which markets move
after news versus before it.

Alternatives worth knowing: OddsJam and Unabated (more expensive, more books, built-in
devig and EV tooling); direct book APIs where available; a free tier exists on The Odds
API for prototyping.

---

## 3. Advanced metrics — free tiers first

| Source | What it adds | Cost |
|---|---|---|
| **rbsdm.com/stats** | Ready-made opponent-adjusted team EPA tables | Free |
| **NFL Next Gen Stats** (public pages) | Tracking-derived separation, time to throw, expected rush yards | Free |
| **Sumer Sports** | Public advanced team/player metrics and projections | Free tier |
| **FTN charting** (via nflverse) | Personnel groupings, blitz, coverage shell, play action | Free |
| **PFF** | Player grades, true pressure, coverage matchups | Paid, expensive |
| **Sports Info Solutions** | Charting depth, defensive scheme detail | Paid |

**Recommendation for starting from scratch:** nflverse + FTN charting + The Odds API
covers the great majority of what a sides/totals/props process needs. Add PFF only after
you have measured positive CLV without it and can name the specific decision the grades
would change — otherwise you are paying for confidence, not edge.

---

## 4. Refresh Cadence

| When | Pull |
|---|---|
| Tuesday | Full nflverse refresh; recompute opponent-adjusted efficiency; rebuild power ratings |
| Wednesday | Openers vs. your numbers; flag the biggest gaps; first injury report |
| Thursday–Friday | Practice participation; line movement vs. openers; first prop pull |
| Saturday | Inactive rumors; weather forecast v1; prop re-pull |
| Sunday AM | Final inactives; **stadium wind forecast**; last prop scan; place |
| Sunday PM / Monday | Record **closing lines for every bet placed and every bet passed** |
| Monday | Grade CLV; update bet log with results and post-mortem verdicts |

Recording the close on bets you **passed** matters as much as on bets you made — it is
the only way to find out whether your no-bet decisions were right.

---

## 5. Storage Shape

Keep it boring: parquet files on disk, one table per concept, plus an append-only
`bets.csv` with these columns —

```
bet_id, date, week, game, market, selection, book, price_taken, stake,
model_prob, novig_prob_at_bet, edge, kelly_fraction, closing_price,
novig_prob_at_close, clv_pts, result, pnl, thesis, kill_condition, verdict
```

`thesis`, `kill_condition`, and `verdict` are not paperwork. They are the only mechanism
by which the process learns anything, and they must be written **before** the game, not
reconstructed after it.
