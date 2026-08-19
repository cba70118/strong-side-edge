# RUNBOOK

Operational reference. Every loader gets a section here with exact commands,
expected row counts, and validation queries. Runs are local and manual.

Environment: Windows / PowerShell / Python 3.14 / DuckDB 1.5.5.
Use `py -3`, never `python3`. Avoid emoji in `print()` (cp1252).

---

## Architecture in one paragraph

DuckDB reads nflverse parquet **directly over HTTPS** — no `pandas`, no
`nflreadpy`, no `nfl_data_py` (which nflverse has deprecated). Ingestion is SQL
against a URL. The only runtime dependency is `duckdb` itself. Schemas:
`raw` (landing) → `ref` (dimensions + entity resolution) → `mart` (modeling) →
`odds` (append-only price path) → `model` (registry/projections/sims) →
`bet` (log + CLV) → `audit` (validation views).

---

## Secrets / API keys

Keys live in **`.env` in the project root**. It is gitignored; `.env.example`
is the committed template. Real OS environment variables override `.env`, so a
key can be exported for a one-off run without editing the file.

```powershell
Copy-Item .env.example .env     # then edit .env
py -3 scripts/config.py         # verify - masks keys, exits 1 if incomplete
```

Only `THE_ODDS_API_KEY` is required. `SPORTSGAMEODDS_API_KEY` is optional and
only needed for a Pinnacle CLV anchor (The Odds API carries no sharp books).

Scripts read it via `from config import require`; never hardcode a key and
never pass one on the command line, where it lands in shell history.

## Step 1 — Initialize the database

### Command

```powershell
py -3 scripts/init_db.py --reset --validate
```

Flags: `--reset` deletes and rebuilds. `--validate` runs live contract
validation plus the odds→model→bet→CLV round-trip. `--skip-remote` for an
offline DDL-only check. `--db <path>` overrides `data/nfl.duckdb`.

### Expected output

| Stage | Expected |
|---|---|
| DDL | `01_schema.sql`, `02_audit_views.sql` apply clean |
| Contract seed | **84 columns across 6 tables** |
| Live validation | 38/38 pbp, 22/22 games, 6/6 snap_counts, 6/6 injuries, 3/3 players, 9/9 stats_player_week |
| `ref.team` | **36 rows** (32 current + 4 relocation legacy) |
| `ref.team_alias` | **71 auto + 30 manual** |
| `ref.book_alias` | **17 manual** |
| Round-trip | all `audit.run_all` checks 0 except `snapshot_gaps` = 1 |
| Exit | `RESULT: clean` , code 0 |

`snapshot_gaps = 1` during the round-trip is **correct** — the synthetic
snapshot writes only an `opener` phase, so that season-week is legitimately
incomplete. All round-trip rows are rolled back; nothing persists.

Runtime ~5s. Live validation reads only parquet headers (`LIMIT 0`), not data.

### Validation queries

```sql
-- one-line health read; investigate any non-zero
SELECT * FROM audit.run_all;

-- contracted tables not yet loaded (expected non-zero until Step 2)
SELECT * FROM audit.contract_tables_missing;

-- confirm book access is encoded correctly: 6 placeable, 2 sharp, 1 derived
SELECT book_id, book_name, is_placeable, is_sharp, clv_priority
FROM ref.book ORDER BY is_placeable DESC, clv_priority;

-- entity resolution spot check
SELECT source, count(*) FROM ref.team_alias GROUP BY source ORDER BY 2 DESC;
```

Open an interactive shell:

```powershell
py -3 -c "import duckdb; con=duckdb.connect('data/nfl.duckdb'); print(con.execute('SELECT * FROM audit.run_all').fetchall())"
```

### Known state after Step 1

- `ref.team` includes historical franchises with `is_current = TRUE` for all
  rows. Relocation dating (`active_from`/`active_to`) is **not yet populated** —
  harmless until we join pre-2020 seasons, must be fixed before backtesting
  deep history.
- Book keys in `ref/overrides/book_aliases.json` are seeded from documented
  values and are **unverified against a live API response**. Verify before the
  first scan; `audit.unmapped_books` catches anything wrong.

---

## nflverse asset reference (verified 2026-08-16)

URL pattern:
`https://github.com/nflverse/nflverse-data/releases/download/{tag}/{file}`

| Tag | File | Notes |
|---|---|---|
| `pbp` | `play_by_play_{YYYY}.parquet` | 372 cols, ~49k rows/season. 1999+ |
| `schedules` | `games.parquet` | **all seasons in one file**, 46 cols |
| `stats_player` | `stats_player_week_{YYYY}.parquet` | 150 cols |
| `stats_team` | `stats_team_week_{YYYY}.parquet` | 138 cols |
| `weekly_rosters` | `roster_weekly_{YYYY}.parquet` | |
| `snap_counts` | `snap_counts_{YYYY}.parquet` | |
| `injuries` | `injuries_{YYYY}.parquet` | practice participation |
| `depth_charts` | `depth_charts_{YYYY}.parquet` | |
| `players` | `players.parquet` | canonical `gsis_id` |
| `teams` | `teams_colors_logos.parquet` | **not** `teams.parquet` |
| `nextgen_stats` | `ngs_passing` / `ngs_receiving` / `ngs_rushing`.parquet | **all seasons per file**, not per-year |
| `pbp_participation` | `pbp_participation_{YYYY}.parquet` | tag is `pbp_participation`, **2016–2025** |
| `ftn_charting` | `ftn_charting_{YYYY}.parquet` | 2022–2025; current season lags |
| `pfr_advstats` | `advstats_week_{pass,rush,rec,def}_{YYYY}.parquet` | |
| `espn_data` | `qbr_week_level.parquet` / `qbr_season_level.parquet` | |
| `contracts` | `historical_contracts.parquet` | |

`play_by_play_2026.parquet` returns 404 until Week 1 is played (Sept 9, 2026).

### `games.parquet` carries free closing lines

`spread_line` and `total_line` are populated **1999→present**; moneylines
**2006→present**; 100% coverage on completed seasons. Context columns:
`home_rest`, `away_rest`, `div_game`, `roof`, `surface`, `temp`, `wind`,
`stadium_id`, `result`, `total`.

**Implication:** the sides/totals backtest substrate is free. The Odds API's
10×-credit historical endpoint is only needed for derivative and prop markets
and for the intraweek path. Note these are a *single consensus close*, not
book-specific and not a path — they do not substitute for snapshotting.

**2026 status:** 272 games, 18 weeks, 32 teams, 52 dome games, Sept 9 → Jan 10.
**68 games already carry lookahead spreads.**

---

## Step 2 — nflverse loaders

### Commands

```powershell
py -3 scripts/load_nflverse.py --list                      # asset keys
py -3 scripts/load_nflverse.py --seasons 2020-2025         # full load
py -3 scripts/load_nflverse.py --seasons 2025 --only pbp,injuries,snap_counts
py -3 scripts/load_nflverse.py --seasons 2020-2025 --force  # ignore skip-unchanged
py -3 scripts/validate_load.py                              # substrate trust checks
```

Season-partitioned assets are HEAD-probed for availability, then read in a
single `read_parquet([...], union_by_name=true)` so cross-season column drift
unions instead of erroring. Re-running is cheap: a load is skipped when total
byte size matches the last successful ingest **and** the table still exists.

### Expected row counts — `--seasons 2020-2025`

| key | table | rows | cols |
|---|---|---:|---:|
| games | `raw.games` | 7,548 | 46 |
| players | `raw.players` | 25,033 | 39 |
| teams | `raw.teams` | 36 | 16 |
| ngs_pass / rec / rush | `raw.ngs_*` | 5,933 / 14,731 / 6,059 | 29 / 23 / 22 |
| qbr | `raw.qbr_week` | 10,709 | 30 |
| **pbp** | `raw.pbp` | **294,989** | **372** |
| stats_player | `raw.stats_player_week` | 112,450 | 150 |
| stats_team | `raw.stats_team_week` | 3,386 | 138 |
| rosters | `raw.roster_weekly` | 276,072 | 36 |
| snap_counts | `raw.snap_counts` | 157,615 | 16 |
| injuries | `raw.injuries` | 34,812 | 17 |
| depth_charts | `raw.depth_charts` | 740,289 | 26 |
| participation | `raw.pbp_participation` | 286,648 | 26 |
| ftn | `raw.ftn_charting` | 185,215 (4 files, 2022–25) | 29 |
| pfr_pass / rush / rec / def | `raw.pfr_*` | 4,138 / 14,124 / 27,163 / 47,783 | |

Cold run ~83s, ~166 MB. Warm re-run ~4s (all skipped). Exit 0 with
`column contract: clean` and every `audit.run_all` check at 0.

### Verified substrate facts

- `raw.games`: 269–285 games/season 2020–25, **100% closing spread/total/ML
  coverage**; 2026 has 272 games with **68 lookahead spreads** already posted.
  Closing spreads run back to **1999** — 7,344 games.
- Player id join integrity to `raw.players` is **100%** for passers (292),
  rushers (851) and receivers (1,149). Zero `raw.pbp` rows orphaned from
  `raw.games`.
- pbp null rates are benign: `epa`/`success`/`qb_epa` 1.1%, `wp` 0.6%.
  `cpoe` 63% and `air_yards` 61% null are **correct** — they only populate on
  pass attempts. Always filter `play_type` before modeling.

### SIGN CONVENTION — verified, do not re-derive

```
result      = home_score - away_score        (home margin)
spread_line = HOME perspective, POSITIVE means HOME IS FAVORED
home covers when  result > spread_line
```

Empirical proof over 5,431 games (2006+): `avg(result - spread_line) = -0.040`,
`corr(result, spread_line) = +0.433`, cover rates 45.8–52.6% across all spread
buckets, push rate 2.50%. **Getting this backwards inverts every edge in the
system.** `validate_load.py` asserts the correlation is positive and flags any
bucket more than 6 points off 50.

That −0.040 figure is also the benchmark: the market is unbiased to within
four hundredths of a point. Any projection model is competing with that.

### Empirical margin distribution (2006+)

| margin | share | | margin | share |
|---:|---:|---|---:|---:|
| **3** | 14.64% | | 14 | 4.95% |
| **7** | 8.89% | | 2 | 4.25% |
| **6** | 6.33% | | 1 | 4.14% |
| **10** | 5.27% | | 8 | 3.89% |
| **4** | 5.05% | | 5 | 3.72% |

Margin 3 alone is **14.6%** of all games. A Gaussian sim around the spread
misprices every alt-spread, moneyline and teaser that crosses these numbers.
The simulation layer must reproduce this shape — see the null test in Step 5.

### Known substrate quirks

- `raw.injuries.season` is **DOUBLE**, `raw.pbp.season` is **BIGINT**. Cast on
  join or normalize in `mart`.
- `raw.pbp_participation` keys on **`nflverse_game_id`**, not `game_id`, and
  carries **no season column** — derive via
  `cast(split_part(nflverse_game_id, '_', 1) AS INTEGER)`.
  It holds `offense_formation`, `offense_personnel`, `defenders_in_box`,
  `number_of_pass_rushers`, `was_pressure`, `route`, `defense_man_zone_type`,
  `defense_coverage_type`. **`route` is the route-participation input the prop
  engine needs** — it is the reason this asset matters.
- `ftn_charting` covers 2022–2025 only; the current season lags.

## Odds architecture — two feeds, split by role

SportsGameOdds is subscribed for other leagues, so its NFL marginal cost is
**zero**. It therefore carries the placeable board; The Odds API is reduced to
the Pinnacle CLV anchor, which fits its **free** tier.

| | SportsGameOdds (Rookie) | The Odds API (free) |
|---|---|---|
| role | placeable board + props + derivatives | **Pinnacle CLV anchor only** |
| books | DK, FanDuel, BetMGM, **Caesars** | Pinnacle |
| cost | already paid for other leagues | $0 (3 credits/snapshot, ~52/mo) |

```powershell
py -3 scripts/snapshot_sgo.py  --phase opener --days 8      # placeable board
py -3 scripts/snapshot_odds.py --phase opener --pinnacle-only   # sharp anchor
```

**Caesars is the win here** — The Odds API cannot see it at any tier, and SGO
gates bet365/Fanatics/Pinnacle/Circa behind its $299 Pro plan. Downgrade The
Odds API to free; `--pinnacle-only` forces `regions=eu` and main markets.

### Three measured SGO behaviors the fetcher defends against

1. **`season` and `week` are SILENTLY IGNORED.** `season=2024/2025/2026` all
   return the same completed 2024 games, with no error. Only
   `startsAfter`/`startsBefore` work. Every returned event has its `startsAt`
   asserted against the requested window.
2. **`byBookmaker` does not populate without a `bookmakerID` filter** — books
   are fetched one at a time, 4 requests per snapshot.
3. **SGO stores the LAST-SEEN quote, which for a played game is IN-PLAY.**
   98% of sampled 2025 history was updated after kickoff. `book_last_updated`
   is captured on every row, `audit.inplay_contamination` flags violations, and
   **models must read `odds.line_pregame`, never `odds.line`.**

### Entity resolution — 100%

`scripts/sgo_resolve.py`, shared by the fetcher and the history loader.
Match order: name+team → name → squashed+team → squashed, with
`ref/overrides/player_overrides.json` winning outright.

The naive first version failed on **DeVonta Smith, Justin Jefferson and Lamar
Jackson** — two separate bugs:

- **Ambiguity.** `raw.players` holds duplicate rows for some names; requiring a
  unique candidate gave up on stars. SGO supplies `teamID`; use it.
- **Normalisation.** Replacing `.` with a space makes `"A.J. Dillon"` →
  `"a j dillon"` while `"AJ Dillon"` → `"aj dillon"` — never equal. Punctuation
  is now *removed*, and a space-squashed key catches `LambertSmith` vs
  `Lambert-Smith`.

The residue is nickname divergence, which no string metric should bridge:
Cameron→Cam, Kenneth→Kenny, Chigoziem→Chig, Jo'quavious→**Woody**,
Zonovan→**Bam**. Those get explicit override rows. A matcher loose enough to
join "Jo'quavious Marks" to "Woody Marks" would also join different people.

Two traps recorded in the override file: `raw.players.latest_team` is
**current**, not historical, so team matching cannot separate Michael Carter
(RB) from Michael Carter II (CB) using a 2025 record. And SGO's playerID for
Chig Okonkwo is `CHIGOZIEM_OKONOKWO_1_NFL` — **their ID misspells the surname
while their name field spells it correctly**. Always copy override keys from
`ref.player_xref`, never construct them from a name.

### Current state

`odds.line`: 4,868 rows from the_odds_api (28 books) + 605 from sportsgameodds
(4 books, 245 props, **245 resolved**). Placeable board now visible:
DraftKings 1,907 · FanDuel 282 · BetMGM 184 · **Caesars 150**.
All `audit.run_all` checks 0 except `snapshot_gaps` (phases incomplete).

## Step 3 — Odds snapshot pipeline (The Odds API)

### Commands

```powershell
py -3 scripts/probe_odds_api.py             # discovery; /sports + /events free
py -3 scripts/snapshot_odds.py --phase opener
py -3 scripts/snapshot_odds.py --phase close --props --max-events 16
py -3 scripts/snapshot_odds.py --phase adhoc --dry-run     # fetch, write nothing
py -3 scripts/snapshot_odds.py --phase adhoc --reresolve   # repair, 0 credits
```

Cadence per season-week: `opener` (Sun night/Mon) → `post_wed` (first injury
report) → `post_fri` (final report) → `close`. `audit.snapshot_gaps` reports
any season-week missing a phase. **Missed snapshots are unrecoverable.**

Each snapshot writes in a single transaction — a half-written phase can never
look complete.

### Credit economics (measured, not documented)

- `/sports` and `/events` cost **0 credits**. Re-resolution is therefore free.
- Cost = **[markets RETURNED] × [regions]**, not markets *requested*. A
  5-market prop request returned 1 market and cost 2 credits. **Over-requesting
  props is safe.**
- Main lines: 3 markets × 3 regions = **9 credits** per snapshot.
  4 phases × 18 weeks ≈ **650 credits/season**. Trivial.
- Props are **per-event**: ~16 events × ~6 markets × 2 regions ≈ **190 credits
  per full prop snapshot**, ~760/week at 4 phases, **~3,300/month**.

**The free tier is 500 credits/month. It funds main lines and cannot fund
props.** Since props are the stated edge target, the **$30/mo 20k tier is
required before Week 1.** That is the whole incremental cost of the system.

`ODDS_API_MAX_CREDITS_PER_RUN` in `.env` hard-stops a prop loop mid-run.

### Book availability — a hard constraint

Probed across `us, us2, us_ex, uk, eu, au`:

| book | placeable in LA | in feed |
|---|---|---|
| DraftKings | yes | **yes** — all 272 games |
| FanDuel | yes | **yes** — near-term only |
| BetMGM | yes | **yes** — near-term only |
| bet365 | yes | **NO** |
| Caesars | yes | **NO** |
| Fanatics | yes | **NO** |
| **Pinnacle** | no (sharp ref) | **yes, via `eu` region** |
| Circa | no (sharp ref) | **NO** |

**Half your placeable books are unreachable.** Line shopping is effectively
DK/FD/MGM only. Closing that gap needs manual entry (Unabated screen) or a
second feed. This is a real limit on Lens B and should not be papered over.

**Pinnacle via `eu` is the good news** — the sharp CLV anchor costs nothing
extra, so SportsGameOdds at $99/mo is **not** needed for grading. `eu` is in
`ODDS_API_REGIONS` solely for this; it also drags in ~12 European books that
are registered as unplaceable so `audit.unmapped_books` stays meaningful.

FanDuel/BetMGM covering only 16–32 games is normal book behavior (they price
near-term only), not a feed defect. DraftKings prices the full season.

### First snapshot (2026-08-16, phase=opener)

4,868 lines, 9 credits, 272/272 games resolved. Week 1 DK vs Pinnacle spreads
agree within 0.5–1.0 pts. Placeable-book coverage: DK 1,632 lines / FD 192 /
MGM 94.

### Two bugs the audit caught on the first run

1. **All 17 Rams games orphaned.** `ref.team` holds both `LA` and `LAR` with
   the full name "Los Angeles Rams"; the alias auto-seed picked `LAR` while
   nflverse schedules use `LA`. Fixed by seeding full-name aliases only from
   `is_current` franchises, marking LAR/OAK/SD/STL legacy, and pinning the Rams
   in `team_aliases.json`. Aliases now upsert so a correction overrides a guess.
2. **24 phantom duplicate lines.** `audit.duplicate_lines` grouped on
   `game_id`, which is NULL until resolution — so every unresolved game
   collapsed into one bucket and distinct games sharing a total looked like
   duplicates. Now keys on `source_game_id`, which is always present.
   **Never group odds rows on a nullable identifier.**

After the fixes: 272/272 resolved, all audit checks 0 except `snapshot_gaps`
(expected — one phase captured, and full-season pulls carry `week = NULL`).

### Gotcha: TIMESTAMPTZ needs pytz

Selecting a `TIMESTAMPTZ` column into Python raises
`Required module 'pytz' failed to import`. Rather than add the dependency,
`cast(col AS VARCHAR)` in the query. Applies to `odds.snapshot.captured_at`,
`odds.game_map.commence_time` and every `*_at` column.

## Step 4a — Mart layer + point-in-time team ratings

### Commands

```powershell
py -3 scripts/build_mart.py                    # tables + ratings
py -3 scripts/build_mart.py --skip-tables      # ratings only
py -3 scripts/build_mart.py --evaluate         # + out-of-sample signal test
```

### Expected output — seasons 2020–2025

| table | rows | cols |
|---|---:|---:|
| `mart.pbp_clean` | 210,716 | 43 |
| `mart.team_game` | 3,386 | 47 |
| `mart.player_game_usage` | 157,474 | 25 |
| `mart.team_rating` | 4,352 | 14 |

Full build ~17s. Sanity: mean `off_rating` 0.0002, mean `def_rating` −0.0001
(centered), `net_rating` sd 0.0975 across 32 teams.

### Point-in-time discipline

A `mart.team_rating` row for `(season, week W)` is trained **only on games
played before week W**, with prior-season games carried in at a decayed weight
so week 1 is not an empty prior. The builder raises `AssertionError` if any
training row violates the cutoff — **108,614 training rows asserted clean** on
the current build. Never join a rating to a game whose week is ≥ the rating's
own week from a later snapshot; that is lookahead leakage and it makes any
backtest worthless.

Note `games_used` counts prior-season carry-in, so end-of-season rows read
37–39 rather than ~17. That is correct, not a bug.

Tuning constants live at the top of `build_mart.py`: `ITERATIONS=20`,
`SHRINK_PLAYS=250`, `PRIOR_SEASON=0.35`, `WEEK_DECAY=0.97`, `MIN_PLAYS=20`.

### Deliberate choice: `wp`, not `vegas_wp`

Neutral-script filtering uses `wp`. `vegas_wp` is derived from the closing
spread, so using it would leak market information into the projection lens
that is supposed to be an **independent** challenger. Keeping them separate is
the entire point of the two-lens design.

### THE HEADLINE RESULT — read before building any sides model

Out-of-sample, week ≥ 4, n = 1,405 games:

| measure | value |
|---|---:|
| `corr(rating_diff, margin)` | 0.375 |
| `corr(spread, margin)` | **0.462** ← benchmark |
| `corr(rating_diff, spread)` | 0.853 ← overlap |
| **partial corr(rating_diff, margin \| spread)** | **−0.0412** |
| `corr(rating_diff, market residual)` | −0.0040 |
| points of margin per 1.0 rating unit | 39.18 |

Raw correlation is the wrong question — the ratings and the spread overlap at
r=0.853, so naturally both correlate with margin. The right question is whether
the ratings carry information the spread does **not** already contain. Partial
correlation answers it: **−0.04, i.e. nothing.**

**A conventional opponent-adjusted EPA rating has no edge on sides over the
closing spread.** It is a noisier restatement of the market. Betting sides off
it would be betting the market against itself with extra variance.

This is not a failure of the build — it is the build working. It empirically
justifies the architecture: the betting engine targets **props and
derivatives**, and the projection lens stays a CLV-graded challenger until it
earns its way in. Any future sides model must clear this bar: **partial
correlation against the closing spread, not raw correlation against margin.**

### `mart.player_game_usage` notes

Bridges `snap_counts.pfr_player_id` → `raw.players.pfr_id` → `gsis_id`
(**99.5%** resolution; the 23 unbridged are fringe linemen/DBs, no
prop-relevant skill players).

`route_proxy_snaps` / `route_proxy_pct` are a **proxy, not charted routes** —
a player on the field for a dropback is presumed to have run a route, which
inflates blocking backs and TEs. Derived from
`pbp_participation.offense_players` (semicolon-delimited gsis_id list).
Real charted `route` values exist on participation but only 112,924 non-empty
rows, and they describe the targeted receiver's route, not participation.

## Step 4b — Usage-based prop projection engine

```powershell
py -3 scripts/usage_stability.py     # the measurement that dictates the design
py -3 scripts/build_props.py build
py -3 scripts/build_props.py validate
```

### The measurement that came first

Before modeling anything, `usage_stability.py` tested the founding claim on
**22,965 player-games** (2020–25, WR/TE/RB, snap% ≥ 25%): correlate each
metric against its own previous-4-game mean, on a common sample.

| metric | family | corr |
|---|---|---:|
| carry share | usage | **0.916** |
| carries | volume | 0.877 |
| rushing yards | production | 0.785 |
| route share | usage | **0.749** |
| snap % | usage | **0.707** |
| air-yard share | usage | **0.702** |
| target share | usage | **0.664** |
| targets | volume | 0.638 |
| receptions | production | 0.569 |
| receiving yards | production | 0.540 |
| red-zone touches | production | 0.497 |

Usage mean **0.748** vs production **0.598** — premise **confirmed**, project
usage and derive production.

**The decisive number: yards per target correlates 0.136 over an 8-game
prior.** Player-level efficiency is very nearly unpredictable week to week,
while *depth* (air-yard share, 0.702) is predictable. So the model uses
projected usage × a **baseline** efficiency, and shrinks player efficiency
hard. Multiplying projected targets by a player's own recent YPT — which many
public prop models do — is mostly injecting noise.

Target-share correlation saturates fast: 0.567 at a 1-game window, 0.664 at 4,
0.681 at 8. Hence **WINDOW = 6**.

### Model

Point-in-time throughout (`ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING` excludes
the current row by construction).

```
usage       shrink k = 2 games       (predictable -> shrink lightly)
efficiency  shrink k = 40 attempts   (corr 0.136 -> shrink hard)
production  = proj_share x proj_team_volume x shrunk_baseline_efficiency
```

Tables: `mart.usage_baseline` (3 rows), `mart.team_volume_projection` (3,386),
`mart.player_usage_projection` (38,334).

### Validation — 23,516 player-games, model vs naive baselines

| stat | last | prior6 | baseline | **model** |
|---|---:|---:|---:|---:|
| target share (MAE) | 0.059 | 0.048 | 0.062 | **0.047** |
| targets (MAE) | 2.371 | 1.937 | — | **1.903** |
| receiving yards (MAE) | 24.775 | 19.904 | 19.710 | **19.588** |
| rush yards (MAE) | 8.295 | 6.846 | — | **6.702** |

**Model wins 4/4 — but read the margins honestly.** It beats a plain 6-game
average by only ~1.5–2%. The large gain is over "use last week" (−21% MAE on
receiving yards); the shrinkage-and-decomposition machinery adds a further 1.6%
on top of simple averaging. Real, and modest.

Keeping shrunk player efficiency beats a flat positional constant by **0.122
yards of MAE** — negligible, exactly as the 0.136 correlation predicted. **The
model's value is entirely in the usage projection**, not in efficiency.

### Limitations — do not price props without reading these

- **No injury or inactive awareness.** Usage is projected from a player's own
  history; if a teammate is out, target share redistributes and the model does
  not know. This is the single largest gap for live use.
- **No opponent adjustment.** No defensive strength, scheme or matchup input.
- **No game-script adjustment.** `proj_pass_plays` is a 6-game team average and
  ignores the spread and total, though trailing teams pass materially more.
  The margin model and `mart.team_game` already hold what this needs.
- **Point estimates only, no distribution.** A prop fair price needs a
  distribution, not a mean. `model.player_projection` has `dist_family` and
  `dist_params_json` columns waiting; nothing fills them yet.
- **`air_yards_share` is unusable for RBs.** `air_yards` is legitimately
  negative on screens (19,390 of 113,391 pass plays, min −20), so **44% of RB
  games have a negative air-yard share** and the RB baseline is −0.001. Use it
  for WR/TE only. It is projected but not consumed by any production formula.
- Restricted to WR/TE/RB. No QB projections.

## Scheduled capture — capture.py

    py -3 scripts/capture.py --install-task    # registers Windows tasks
    py -3 scripts/capture.py --status
    py -3 scripts/capture.py --phase close --props

Two Windows Scheduled Tasks run on this machine: `NFL_capture_daily` 09:00 and
`NFL_capture_evening` 18:00. A **cloud cron cannot do this job** — the warehouse
is a local DuckDB file, so the capture has to run locally.

**This was the urgent item.** Line movement and pregame player props cannot be
reconstructed after the fact, and no feed carries historical pregame props at
all, so the prop model can only ever be validated on snapshots captured going
forward. Two snapshots existed, 78 minutes apart on one day.

Role split holds: The Odds API `--pinnacle-only` (3 credits a snapshot, ~52 a
month, fits the free tier) for the CLV anchor; SGO for the placeable board at
zero marginal cost. Game-week close captures are deliberately NOT scheduled yet
— add them once Week 1 kickoff times are final so the close window is hit
exactly.

Bug found on the first scheduled run: `snapshot_sgo.py` called `executemany`
with an empty list when a pull found nothing new, which crashes. **An empty
pull is a normal outcome** — the board does not move every hour. The snapshot
row is still written so a quiet period reads as "we looked and found nothing"
rather than as a missing capture.

## Next Gen Stats — ngs_stability.py, adot_prior.py

    py -3 scripts/ngs_stability.py
    py -3 scripts/adot_prior.py

**NGS was already loaded and never read.** `raw.ngs_passing` / `ngs_receiving`
/ `ngs_rushing`, 2016-2025, `week=0` rows are the league's own season
aggregates (mean 407 pass attempts vs 33 for a weekly row). gsis_id resolves
100%.

**The over-expected metrics are LESS stable than the raw numbers they refine**,
every pair, by 0.06 to 0.09:

    CPOE               0.353  vs  completion %      0.420   -0.067
    YAC above expected 0.347  vs  avg YAC           0.434   -0.087
    RYOE per attempt   0.201  vs  avg rush yards    0.264   -0.062

The mechanism is arithmetic. Expected completion % is itself highly stable
(0.452), so subtracting it removes the stable part and leaves a residual. **A
difference of two correlated quantities is less reliable than either.** Do not
assume a luck-adjusted stat is more predictive than its input.

What does carry is ROLE and STYLE, not skill-minus-luck: receiving aDOT
**0.781** (above target share at 0.748), air yards share 0.663, time to throw
0.657, avg separation 0.595, time to LOS 0.527, avg cushion 0.506.

**avg_separation is a trap.** Stable at 0.595, but its partial correlation with
next-season yards per target is **negative** (-0.136) — slot receivers separate
most and gain least per target. It measures assignment, not quality.

**The aDOT role prior was tested and NOT shipped.** aDOT looked ideal: most
stable metric in the project, partial +0.208 on next-season YPT, monotone
across every bucket (7.29 YPT at <7.5 air yards up to 8.79 at 15+). It still
lost out of sample — receiving-yards MAE 16.671 to 16.682, and 25.403 to 25.447
on the rows it actually touches. NGS publishes a season row only above 50
targets, so it covers precisely the players whose own 16-game history already
takes ~69% of the weight at k=40, and misses the thin-history players a better
prior would help. `mart.usage_baseline` stays at three position rows.

Lesson worth more than the feature: **measure incremental signal against the
control the model actually uses.** The +0.208 partial was measured against one
season of prior YPT; the projection controls on a 16-game rolling sum.

## Metric stability — stability.py

    py -3 scripts/stability.py --min-season 2020

**Reads no market data anywhere.** Asks which football measurements carry year
over year (predictiveness) and which hold together within a season (split-half
reliability). Results are in the playbook and priors; the headline is that the
real axis is **offense vs defense**, not process vs outcome: offense mean 0.331
against defense 0.233, offense winning 5 of 6 paired same-metric comparisons.
Turnovers are noise on both axes. Only PROE (0.459) and offensive success rate
(0.404) clear 0.40 — point margin manages 0.384.

## Injuries — raw.injuries, context not signal

**The official NFL injury report was already loaded and unread**: 34,812 rows,
2020-2025, report status plus practice participation, gsis_id resolved. No news
feed was needed to answer "can we track injuries".

**h-0010: REFUTED.** Out-count differential has a partial correlation of
**+0.0425** with margin controlling for the closing spread on 1,609 games —
under the 0.05 bar set in advance and only 1.7 SE from zero. Questionable
differential is +0.0198. The market visibly prices it already:
corr(Out differential, closing spread) = **+0.157**.

It is still the *largest* partial correlation against the close measured
anywhere in this project (EPA ratings ran −0.04, the drive sim −0.030), so the
sign is sensible and the magnitude is still not actionable. Injuries are
CONTEXT on the club pages and a CORRECTNESS filter for the prop projection.
Never an edge.

**Two traps:**
- `season_type` is **NULL on 83% of rows** (28,744 of 34,812). Filtering on it
  drops five sixths of the table — it left me with 272 games instead of 1,609
  and a partial that looked like it cleared the bar. Filter on `game_type`.
- `season` and `week` are DOUBLE; cast to INT to join `raw.games`.

## Tab order is the weekly workflow

    Board · Plays · Live board · Middles · Projections · Simulation
    · Teams · Outside reads · Season · Method

**Front to back is what a reader needs during a game week.** This week's board
and what cleared lead; the season record sits second-from-last because it is a
REVIEW surface, and an almost-empty one until results land. It will be the most
valuable tab here by December and it still is not tab one — that ordering is
deliberate, not an oversight.

"Board" is this week's slate including the passes. "Live board" is the price
dashboard. They were briefly called Board and Board state, which nobody could
tell apart.

## Season player projections — season_proj.py, build_season_proj.py

    py -3 scripts/season_proj.py            # the backtest
    py -3 scripts/build_season_proj.py      # writes mart.player_season_projection

**Start here on the player layer, because it is the only part that is
backtestable.** No feed carries historical pregame props, so the per-game model
can only be graded forward. Season totals have **1,208 player-seasons** across
2020-2025.

**A season total is games x per-game, not a second model.** Same shrunk usage
(k=2 games) and shrunk efficiency (k=40 targets).

Result on 409 out-of-sample rows (fit 2021-22, scored 2023-25):

    naive_total   MAE 257.6   bias +126   last season's actual total
    naive_rate    MAE 272.8   bias +169   WORSE - rate x 17 amplifies it
    proj_fixed    MAE 222.0   bias  -24   projection x constant games
    proj_avail    MAE 220.0   bias  -19   projection x predicted games

**Aggregation confirmed (+14.6% vs naive). Availability REFUTED (+0.9%, bar was
2%).** Mechanism worth remembering: `games_S = 8.77 + 0.359 x games_prior`
spans only **4.7 games** across the whole prior range against a 14.42 constant.
Games played carries at +0.415 year over year and is still not usable — **a
correlation is not a slope.** Conditionally it inverts: +7.2% for the 31 players
at 4-11 prior games vs +0.3% for the 219 durable ones. That is suggestive on 31
rows, so availability is FLAGGED, not modeled.

**The band is measured.** Empirical actual/projected on those 409 rows: p10
0.34, p50 1.01, p90 1.77. A 1,000-yard projection honestly spans 340-1,770. A
ratio band is the right shape — a 1,200-yard and a 300-yard projection do not
share an absolute error.

**NOT validated against a market.** There is no season-long player line in this
warehouse and no ADP source, so the test that would decide whether this is an
EDGE cannot be run. Capture that market before claiming one.

## Rolling season — sql/14_season.sql, season_log.py

    py -3 scripts/season_log.py append -i week01_slate.json   # after each build
    py -3 scripts/season_log.py show

**This is a season resource, not a weekly report.** Everything built before
described one slate, and the slate payload is overwritten on the next run — by
week three there would have been no way to answer "what did the system say in
week one, and was it right".

`log.week_build` is **append-only and written BEFORE the games are played**, so
week N's row is what the system actually claimed at the time. Results join in
from `raw.games`. `mart.current_week` is the earliest week with an unplayed
game, so the current week advances on its own and nothing needs editing weekly.

The **Season** tab is the first tab: 18 rows, current week marked, plus weeks
built, games played, decisions logged, mean CLV and beat-close rate. Blank cells
are weeks not yet built or played — nothing is back-filled.

Traps: the slate's market vocabulary is `play / pass / thin / unpriceable /
unavailable` on `market.status` — guessing at `verdict` or `is_play` logged zero
plays against a slate with five. CLV summarises `clv_line_pts`, not
`clv_prob_delta`, because a probability delta is only valid at identical
handicaps.

## Voice — neutral, attributed, not a verdict on the market

The masthead used to read *"what is actually priced wrong"*. That asserts both
that the market is wrong and that we know it, and this project has measured
itself to have **no incremental signal over the close** on sides and totals. It
now names its contents: *NFL 2026 · Season Book*. Five other strings went the
same way — "these are wired to the market, not stranded beside it", "where each
club actually was", "the gap between them is the interesting part", "which is
fortunate, since we do not have one", "and that is the safety".

**Ratings display in POINTS.** `+0.1529` is seven glyphs of false precision that
read as broken at jersey scale; × 39.18 gives `+6.0`. Defense is flipped on
display so a positive number is a good unit on every surface.

## ONE artifact — slate_report.py mounts everything

    py -3 scripts/slate_report.py -i week01_slate.json         --dash week01_dash.json -o week01_brief.html

Nine tabs: Board, **Board state** (the whole dashboard), Plays, Simulation,
Projections, Middles, **Teams** (the 32-team ledger), Outside reads, Method.
One copy of the fonts and logo sprites, zero duplicate element ids, 1467 KB,
deploys fine.

**Why it matters beyond tidiness:** shipping three files made data look missing
that had never moved. Projections was intact — 32 tables, 192 rows — while the
reader was on the standalone ledger, which has no tabs at all. A product split
across files reads as a product with holes.

**Scoping is mandatory when mounting one tabbed surface inside another, and it
means EVERY element selector in the mounted file, not just the obvious ones.**
Audit with a regex for bare element selectors rather than fixing the visible
symptom — I scoped `section` and `nav button` first, shipped, and the screenshot
came back with the team rail laid out horizontally. Collisions found and fixed:
- `dash_report` did `querySelectorAll('section')` and styled bare `section` —
  in a merged document that matched the brief's 9 panes and all 32 team panes.
  Now `.board>section` and a `:scope>section` query.
- The dashboard's first pane was called `board` and so is one of the brief's
  tabs. Its ids are now `db-` prefixed.
- The ledger's team panes are `role=tabpanel`, matched by the brief's own pane
  rule. Now `section.pane[role=tabpanel]`.
- **`nav{display:flex}`** laid the ledger's 32-team rail out horizontally as a
  scrolling strip. Now `.board nav`.
- **`td{height:40px}`** forced 40px rows on every table in the document,
  including the league table and the brief's dense price tables. Now
  `.board td`. Also `table`, `th` and a `td` transition.

Everything left unscoped in the merged stylesheet (`table`, `th`, `td`, `body`,
`a`) belongs to `design_system` or to `slate_report` — the host's own base
styles, which is correct.

`dash_report.dash_body()` and `team_pages.build_pages()` are the mount points;
`EXTRA_CSS` exposes the dashboard's own rules without re-including the shared
design system.

**Theme:** `DS.TOGGLE_HTML` / `TOGGLE_JS` put an explicit light/dark switch in
the masthead. The artifact host stamps its own theme and that is not always what
the reader wants.

## Design system — design_system.py

**One file, three surfaces.** The brief, the dashboard and the ledger each had
their own token block and they had already drifted: the dashboard used
`--surface`/`--rule`, the brief used `--panel`/`--line`, and `team_charts.py`
rendered *invisible* inside the dashboard because it was authored against the
other set. All three now import `design_system.css()`; each keeps only its own
extra tokens (market-family hues, thin/gate states).

**The direction is SIGNAGE**, chosen from three built side by side
(`design_options.py`). Its bet: identity is the feature — 32 near-identical team
pages need to feel distinct, and the sport supplies the vocabulary. Block color,
condensed numerals at jersey scale, wide-tracked uppercase labels, heavy
horizontal rules.

**What Signage must not lose.** The first sketch dropped the head coach, the
tier and the context note, and the reaction was immediate — the other two
directions "have better data layers". Bold type is not a substitute for a
labeled number. Every figure carries its name and its rank.

Type roles: Condensed 700 for numerals and headings, Sans for text and labels,
Mono ONLY where exact character alignment beats voice (prices, timestamps, ids).
Both the brief and the dashboard had a `td.n{font-family:Mono}` rule overriding
this — removed, because it made the surfaces disagree about what a number
looks like.

**`logo_assets.COLORS` is a LIST per team, not a string.** Interpolating it
straight into a style attribute emitted `background:['#003594', '#FFD100']` —
invalid CSS that every browser silently drops, so the team color never rendered
at all in either the ledger stripe or the Signage hero. It looked like a design
that had not landed; it was a type error. `team_color()` returns
`(primary, secondary, text)` with the text color picked by **measured
contrast** — 30 of 32 primaries take white, 2 take ink, and with the pick made
per team all 32 clear 4.5:1.

## Team ledger — team_pages.py, team_viz.py

    py -3 scripts/team_pages.py -o team_pages.html      # standalone
    py -3 scripts/slate_report.py -i week01_slate.json  # mounts it as the
                                                        # brief's Teams tab

One page per team, ordered by blended Week 1 strength. `build_pages()` returns
nav + panes so the brief mounts the whole thing next to the betting data,
sharing ONE copy of the fonts and logo sprites — the merged file is 1176 KB and
deploys fine, so the old 502 was the duplicated payload, not raw size.

Four charts per team, palette validated with the dataviz validator against this
project's real surfaces (light `#0F7351 #63BE97 #DE7F55 #A82F2F`, dark
`#007852 #36a980 #c0851f #c34e54`). Punts are the unfilled track in the drive
chart, not a segment — a gray slot between two hues failed the adjacency check
and no reader could separate it.

**Three bugs worth not repeating:**
- **Two `role=tabpanel` systems in one document collide silently.** The brief's
  unscoped `section[role=tabpanel]{display:none}` matched all 32 team panes and
  pinned them to `display:none`; the ledger's own `hidden` toggle cannot
  override a display rule, so the tab rendered completely blank. Both CSS and
  JS are now scoped to `section.pane[role=tabpanel]`.
- **A blind American-spelling sweep corrupted `aria-labelledby` into
  `aria-labeledby`** on 32 elements. Spelling passes must skip identifiers —
  attribute names and CSS properties are spec tokens, not prose.
- **The first pane is now open in the markup**, so the ledger renders with
  script disabled. Relying on JS to reveal it means one script failure shows 32
  empty sections, which is indistinguishable from a blank tab.

Status without its subject is not context: the QB health flag used to render as
a bare chip reading "ankle rehab". Coach and quarterback are always named now
and the status hangs off the name.

## Team pages — team_pages.py

    py -3 scripts/team_pages.py -o team_pages.html

One page per club, ordered by blended Week 1 strength (rank is information; an
alphabetical list would be a lookup table). Reuses the brief's tokens, IBM Plex
faces and logo sprites — a sibling product, not a third visual language. Logos
go in as ONE background-image rule per club referenced by class; inlining at
each use site is what pushed an earlier artifact to 1.1 MB and a 502.

Built to accumulate: `mart.team_rating` is already point-in-time and weekly so
the sparkline extends as 2026 is played, and `ref.team_log` is append-only so
the season log grows without anything being rewritten in place.

**It immediately found a substrate bug that every aggregate audit had passed.**
nflverse ROSTER files abbreviate Arizona as **`AZ`** while every other asset in
the release uses `ARI`. It was the only unmapped code in the warehouse, and it
silently dropped all 24 Arizona skill players out of
`mart.player_projection_pre` — so the prop path had no projection for any
Cardinal either. `mart.player_projection_pre` still reported *32 distinct
teams*, which is exactly why a count check never caught it.

Fixed at the source: `AZ` → `ARI` in `ref/overrides/team_aliases.json` under
`legacy`, and `build_player_proj.py` now normalizes the roster team code
through `ref.team_alias` rather than leaving each consumer to do it. Also
mapped the `everygame` book, which was the last `orphan_refs` /
`unmapped_books` row — both audits now read 0.

**Lesson: a per-entity view finds gaps a per-slate view cannot.** 32 distinct
codes counts as complete when one of them is simply the wrong code.

## Prop path — prop_path_test.py

    py -3 scripts/prop_path_test.py

**No feed carries historical PREGAME props**, so the prop model can only be
graded forward. That left the plumbing between a posted line and a priced
number unexercised until game week. This synthesizes two-sided lines for real
players on the real slate and runs them through the REAL `price_props`. Nothing
is written to the warehouse.

**It found the pipeline broken.** `price_props` joins
`mart.player_usage_projection`, which is built from games already PLAYED and is
therefore empty before week 1 — every posted line would have resolved to
`no_projection`. It was invisible because all 245 Week 1 props are anytime-TD
and get refused as `unsupported` *before* reaching the join. Fixed with a
pre-season fallback crossing `mart.player_projection_pre` with the week's
schedule: 764 player-games, and 215 of 245 props now resolve a name and team
where none did. `slate_data` logs the projection source and warns on an empty
join, so this cannot go silent again.

**Second find: whole-number lines can push and the two-way pricing has no push
term**, so the entire tie mass was being paid to the under. Measured tie mass:
**17.9% on receptions**, 1.3-1.4% on yardage — all above the 1.0% `MIN_EDGE`.
Whole numbers are now refused as `pushable_line` with the measured mass in the
reason, rather than approximated.

Traps for anyone editing this:
- **On a discrete stat, `quantile(0.5)` returns a hair BELOW the atom** (3.99999
  for a median of 4) and `prob_over` is a step function there, so those two
  points differ by the whole 14-16pp atom. Use `round()`, never `int()`.
- **A discrete median does NOT price at 50%.** Receptions reads 43% at its own
  median and that is correct — P(X > median) excludes the atom. Assert that the
  half-points either side BRACKET 50%, which holds for both kinds of stat.
- The test covers the plumbing, not accuracy. Whether the numbers are RIGHT is
  still only gradeable forward on captured snapshots.

## Kill conditions — kill_check.py

    py -3 scripts/kill_check.py            # report
    py -3 scripts/kill_check.py --arm      # backfill structured triggers
    py -3 scripts/kill_check.py --fire     # record to bet.kill_event

**A mandatory kill in free text never fires.** Five decisions carried non-empty
kill conditions and not one had ever been evaluated. `sql/13_kill.sql` adds a
structured trigger (`kill_line` / `kill_dir` / `kill_book`) beside the prose;
the checker fires on the structured part and prints the prose next to the
actual line movement for the rest. It deliberately does NOT parse the English.

3 of 5 are armed. The other two reference a gap between books, or another
bet's CLV, and cannot be reduced to a line — the report says so rather than
pretending to cover them.

**Trap found on the first run:** a kill naming Pinnacle was being evaluated
against the placeable board, because the query filtered to placeable books
before applying `kill_book`. It fired a kill that had not happened. A kill
naming a book is now checked at that book, placeable or not.

## Substrate — audit.line_outlier

    SELECT * FROM audit.line_outlier ORDER BY abs(dev) DESC;

**A single book quoting far from its peers at the same instant.** A transient
bad tick manufactures a phantom middle and the middle finder would take it.

**Measure deviation from the cross-book MEDIAN, never raw span.** I first built
this on span >= 2.0 and it flagged 10 of 10 near-pick'em games while finding
one real problem. That is geometry, not a bug: cross-book dispersion is
0.5-0.7 pts at every line size, but near zero a normal 2.5-point range crosses
zero and reads as a sign disagreement. The median version names the culprit
immediately.

What it actually found: **62 outlier rows, and nordicbet is 14 of them** — that
book posted ATL/PIT home +2.5 at 18:34:04 and -3.0 at 18:34:52, a transient
that self-corrected in 48 seconds. Only **6 of 62 sit at a placeable or sharp
book** (betmgm 4, fanduel 2), so live exposure is small but not zero.

Ruled out, in order: not our game resolution (every wide game-snapshot has
exactly ONE `source_game_id`), not alt lines (`is_alt` false on all 3,164
spread rows), not our `side_key` assignment (exact team-name match; a
non-matching name falls through to the raw name and could never be labeled
home), not duplicate events.

## Drive sim — the scoring lattice, fixed

    py -3 scripts/drive_sim.py fit && py -3 scripts/drive_sim.py validate

Scores used to be built from flat 3s and 7s, so totals needing a 6 or an 8 ran
thin (46 was 0.94pp light and `validate` printed a NOTE about it instead of
fixing it). A touchdown is now **6 plus a try**, measured on 8,248 try plays
2020-2025: extra point good 94.73%, two-point good 47.61% on a 9.63% share.

**The try is a DECISION, not a coin flip — this is the whole lesson.** Drawing
6/7/8 independently per touchdown filled the lattice exactly as intended (46 to
0.04pp) and blew the margin key numbers from 2.17pp to 4.29pp, because a real
two-point try is taken to convert an awkward margin INTO a key one. Playing the
standard chart instead (trail by 2, 5, 10, 16, 18, from `TWO_PT_WINDOW` = 0.40
of possessions onward) keeps the lattice gain and leaves the margin alone.

Numbers to hold onto:
- Total lattice: worst exact-total error 0.94pp to 0.83pp, 46 to 0.04pp. Graded
  on 5,199 games, so this is the trustworthy signal.
- Margin key numbers: 2.48pp, against flat-7's 2.17pp. **Do not read that as a
  regression** — the benchmark is 510 near-pick'em games with a +/-1.54pp
  sampling error, so anything under ~3pp is unresolvable there.
- Two-point rate lands at 5.90% against a real 9.63%. Forcing it higher with a
  bigger random component made key numbers worse. Under-firing is deliberate.
- Mean total still runs ~0.8 pts hot. That predates this work (flat 7 ran +0.99)
  and is NOT fixed by the lattice.

## Rating calibration — rating_calibrate.py

    py -3 scripts/rating_calibrate.py

**Reads no market data.** Scores only against actual margin. Sweeps the two
rating knobs per side — `SHRINK_PLAYS` (within-season, set by reliability) and
`PRIOR_SEASON` (across-season, set by year-over-year stability) — tunes on
2020-2023 and scores on a 2024-2025 holdout that the sweep never sees.

**Result: asymmetric shrinkage does not pay, and build_mart.py is unchanged.**
All top 6 of 625 configs shrink defense harder and the optimum ran to the grid
edge, but the +0.0122 tuning gain went to -0.0009 on the holdout, where the
shipping symmetric config beat both tuned ones. The rating already discounts
defense endogenously: off sd 0.072 against def 0.0573, standalone predictive
correlation +0.352 against +0.190.

## preseason_prior — the off/def split bug, fixed

`blended_off` and `blended_def` used to be set as `blended * share` and
`-blended * (1 - share)`. Both proportional to the same signed net, so the two
sides were **forced to opposite signs** and the 2025 profile only ever set the
ratio. Seven of the nineteen teams with a below-average 2025 defense came out
rated above average defensively; Cincinnati's genuinely bad +0.089 became
-0.040.

The fix keeps the 2025 levels and allocates only the CHANGE, at the measured
0.560 offense / 0.440 defense (p-0018), with an in-code assert that
`blended_off - blended_def` still reproduces `blended_rating`.

Traps this leaves behind:
- The net identity means **margin is preserved in a linear model but not in the
  sim**, which is nonlinear. Week 1 totals moved up to 9.1 pts and CLE at JAX
  margin went 5.69 to 1.92. Rebuild the slate after touching this.
- Market agreement on Week 1 totals got slightly **worse** (3.08 to 3.43 mean
  absolute gap, better on 8 of 16). The fix is justified by correctness, not by
  market agreement. Do not "improve" it back.
- The sim runs hot on totals in both versions — that is the scoring-lattice
  issue (3s and 7s only), still open.

## The second brain — brain/brain.py

    py -3 brain/brain.py recall --entity <team|market|method>   # BEFORE analyzing
    py -3 brain/brain.py open --q "..." --hyp "..." --falsifier "..."
    py -3 brain/brain.py resolve h-0001 --verdict confirmed --note "..."
    py -3 brain/brain.py assert --claim "..." --class structural --conf 0.8         --source data --stance 1 --evidence "..."
    py -3 brain/brain.py review | status

Stores judgment, not data — the things no script can recompute. Project-local,
shares nothing with any other project. Method in
`references/analytical-second-brain.md`, earned rules in `brain/playbook.md`,
loaded each session through `CLAUDE.md`.

**Register the hypothesis and its falsifier before the query runs.** That is the
whole discipline; a read written after the data is a story about the data.

First result (h-0001) is worth knowing: Move The Line's claim that offensive
coordinator turnover drives early-season unders does not survive pooling. Their
2022 number is exactly right (56-37, 60.2% under, weeks 1-6) but across
1999-2025 weeks 1-5 close under **50.05%** against **50.70%** for weeks 6+ —
z = -0.49, and the early weeks go under *less*. 2023 repeated 2022 at 60.9%,
yet the pooled recent era is 50.4%. Totals are close to unbiased overall, so a
totals edge has to come from the price or the number, never a directional lean.

## Analyst transcripts — the qualitative lens

Move The Line (4for4, Connor Allen & Ryan Noonan) published ten 2026 preview
shows between 2026-07-29 and 2026-08-12: all eight division win-total previews,
a coaching-changes show and a preseason-injury show. These are **not the user's
opinions.** They are a record of how one respected public handicapping shop was
reasoning about this season, kept so a model output can be checked against a
human read.

Load:

    py -3 scripts/load_transcripts.py --json <apify_dump.json> --dump-md handoff/transcripts

Writes `ref.transcript` (10 rows) and `ref.transcript_segment` (11,360 verbatim
caption rows with timestamps), plus `ref.transcript_full` for keyword sweeps.
Segments stay verbatim on purpose - paraphrase destroys the hedge, and the hedge
is the bet size. `--dump-md` reflows captions into 40-second timestamped
paragraphs under `handoff/transcripts/`, which is the readable form.

`sql/08_analyst_take.sql` holds the structured extraction: **73 positions** in
`ref.analyst_take`, keyed to team, market, side, price-as-quoted, and a
`stance` of bet / lean / watch / pass / fade. Only `bet` means they claim money
down; the 12 `pass` rows are kept because "I like this team but I won't bet the
number until week five" is a testable market-timing claim. Prices are as quoted
on air and have moved - **never place off this table, re-pull the board.**

`ref.take_vs_our_rating` joins their win-total sides to `mart.preseason_prior`
and labels each `agree` / `conflict` / `neutral`. Read the conflicts with the
caveat that our rating is end-of-2025 and blind to the offseason: on a torn-down
roster they are looking at the 2026 team and we are looking at the 2025 one.

Where it is useful, and where it is not:

* **Sides — ignore it.** Our own evidence puts opponent-adjusted EPA at a −0.04
  partial correlation against the closing spread. A talk show will not beat the
  number either.
* **Props and win totals — this is the good stuff.** The signal is roster
  intent: who gets the routes, who loses the job, which coordinator changes the
  pace. A parquet file cannot see any of it.
* **Injuries and depth charts — treat as a dated snapshot.** Every claim carries
  its publish date. The 2026-08-12 show explicitly reversed the Washington bull
  case on the Tunsil tricep; that row is `status='revised'`, not deleted,
  because a reversed opinion is evidence about the source's process.

Nothing here has been validated against results and nothing can be until Week 1.
Grade it forward through `bet_log` exactly like our own decisions.

## Prop pricing — prop_model.py

    py -3 scripts/prop_model.py fit
    py -3 scripts/prop_model.py validate      # writes per-stat clearance

Turns a usage point estimate into a DISTRIBUTION, so a posted player line can be
priced. A mean cannot price an over/under. Fitted on 38,334 point-in-time
projection/actual pairs; the shape is a **mixture**:

* An atom at zero, 14–16% above the usage floor (inactive, or active and never
  involved).
* Conditional on producing: Negative Binomial for receptions, Gamma for yards,
  both with **volume-dependent** parameters. Conditional CV of receiving yards
  runs 0.802 at a 15–30 yard projection down to 0.495 at 90–105, and the
  conditional ratio drifts 1.145 → 0.858. A pooled value over-disperses exactly
  where books post lines.

**Two gates, both earned by measurement.**

1. **Usage floor** — 3 projected targets, 5 carries. Below that the projection
   is biased high (at 1.0 projected receptions the actual mean is 0.62) because
   it cannot see who is inactive. At 4/5/6 it is 3.88/5.01/6.00, so the bias is
   P(did not play), not a usage error.
2. **Quote band 25–75%.** Coverage passes in the central half and over-covers
   the 80% and 90% bands by ~10pp — the conditional tails are too fat. A line
   outside the band is refused, not guessed. Same lesson as the margin model:
   validate at the granularity you will use it.

`validate` writes `cleared` AND `band` per stat, walking a ladder widest-first
and keeping the widest width each stat earns. Currently **all three clear**:
rec_yds 0.3pp and rush_yds 1.1pp over 5–95%, receptions 2.3pp over 25–75%.

**A code review found the bug that made this look far worse.** `nb_sf` returned
`1 - P(0)` rather than `1.0` for a negative line, so the survival function
INCREASED from line −1 to line 0. The randomized PIT evaluates `F(a-)` at `a-1`,
so every zero outcome collapsed to a spike instead of being spread — fabricating
a 10pp tail miss that was then used to justify the narrow band and to refuse
receptions. Fixing it moved the 80% bands from 90.4/89.3/89.8 to 79.0/79.1/79.3.
**Assert that any survival function is non-increasing across its whole domain,
including below the support floor.** A second finding: `find_middle` took
`max(a_dec)` for the losing branch, assuming the better-priced leg always
survives; both tails must be weighted by their own probability. That flipped
BUF@HOU from +0.11pp to −1.47pp.

Test correctness note: coverage uses a **randomized PIT**. A plain PIT is only
uniform for a continuous distribution; with an atom at zero every zero maps to
the same value and piles a spike inside whatever band contains it, which alone
inflated 90% coverage to 95.1% and read as model failure. And PITs must be
computed with `gate=False`, or the test only sees rows the gate admitted and
reports 100% coverage of itself.

`slate_data.py` joins every posted player line to `mart.player_usage_projection`
by `gsis_id` and carries each through to an explicit outcome — priced, or
refused with the reason. Week 1 2026: **245 lines posted, 0 priced**, all
anytime/first touchdown, which have no distribution here. The projections panel
also publishes **our own line** (the distribution median) and the quote band per
player, so a posted number is comparable on sight.

## Dashboard — dash_data.py + dash_components.py + dash_report.py

    py -3 scripts/dash_data.py   --week 1 -o week01_dash.json
    py -3 scripts/dash_report.py -i week01_dash.json -o week01_dashboard.html

Built to `references/dashboard-spec.md`, which governs the DASHBOARD ONLY — the
weekly brief is a different product and keeps its own editorial spec.

**The two are COMPLEMENTARY, not alternatives, and they cross-link.** The first
build made the dashboard a standalone and silently dropped props, middles, team
season context and the outside reads. Anything the brief carries must have a
component form on the board or it is lost, not simplified. Seven panels now:
Board / Game / Market / Props / Middles / Teams / Bet. Four
screens (Board / Game / Market / Bet), each answering one question, composed
from named components. **Prose budget is enforced in the data layer**: `cap()`
raises on a thesis over 120 chars, kill over 90, pass reason or market note over
60, and the board lede over 3 sentences. A field over budget fails the build.

**Excluded on purpose, because the warehouse cannot supply them honestly:**
`stake_u` (sizing is deferred to year two; a quarter-Kelly figure exists only as
ILLUSTRATIVE) and live `kill_triggered` (kill text has never been evaluated, and
two snapshots 78 minutes apart cannot check most kills). Both absences are
stated on the page rather than filled with a plausible number. "Set line alert"
and "Open book" are omitted for the same reason — a verb that lies is worse than
a missing one. **Log bet emits the exact `bet_log.py` command**, which is a real
handoff rather than a button that does nothing.

The signature `DisagreementLine` draws the measured backtest error as its band —
10.32 pts on sides, 9.90 on totals. That band spans the market number on every
game, so **every side and total row correctly renders NO EDGE**. That is the
system's actual belief drawn as a repeated primitive, not a rendering failure.

`team_charts.py` components are reused on the Teams panel, but they were
authored against the BRIEF's token names. The dashboard palette uses different
ones, so its `:root` blocks alias `--play`, `--accent`, `--panel`, `--line`,
`--line2` onto the dashboard equivalents. Without those the sparklines and the
positive dumbbell dots inherit nothing and render invisible — a silent failure
that looks like missing data.

Two verdict bugs worth remembering: letting "no model view" outrank a price
advantage put the whole board on NO EDGE and buried all five real plays — an
edge measured against the sharp no-vig number is real whether or not the
simulator has an opinion. And treating any non-latest price as STALE flagged 4
of 5 plays; staleness is age (>60 min), not "not the newest capture".

## Week brief — slate_data.py + slate_report.py

    py -3 scripts/build_logos.py --zip "raiders-com-logo (2).zip"   # once
    py -3 scripts/build_fonts.py                                    # once
    py -3 scripts/slate_data.py  --week 1 --sims 20000 -o week01_slate.json   # ~4 min
    py -3 scripts/slate_report.py -i week01_slate.json -o week01_brief.html

`slate_data.py` prices every game/market against the Pinnacle reference and
emits JSON; `slate_report.py` renders it. Week 1 2026: 16 games, 29 books,
**5 plays, 5 thin, 6 middles (none positive)**.

The report is **seven tabbed panels**, not one scroll: Board (every game and
market, sortable), Plays, Simulation, Projections, Middles, Outside reads,
Method. The layers have different jobs and very different reliability, so
stacking them in one card per game implied they were the same kind of claim.

**Typography and palette.** `build_fonts.py` embeds the **IBM Plex** superfamily
(Sans 400/500/600/700, Mono 400/500/600, Serif 600) as @font-face data URIs.
Chosen over Inter Tight / Big Shoulders, which read as generic product-UI: a
condensed uppercase display face is striking once and tiring in a table, and
Inter is the default everyone reaches for. Plex was drawn for technical and
financial interfaces, holds up at 11px, has unambiguous figures (slashed zero,
distinct 1/l/I), and its three cuts share one skeleton so the page reads as one
voice. Accent is navy `#1F4E8C` (light) / `#6FA3E0` (dark), leaving green and
red free to mean only good and bad.

## Team narrative log — team_profile.py

    py -3 scripts/team_profile.py seed --season 2026          # week-0 read, all 32
    py -3 scripts/team_profile.py week --season 2026 --week 3 # append once played
    py -3 scripts/team_profile.py note --team SEA --season 2026 --week 3         --headline "..." --body "..."                         # human entry
    py -3 scripts/team_profile.py show --team SEA

Writes `ref.team_log` (`sql/09_team_log.sql`), append-only, primary key
(team, season, week, kind). Re-running a command rewrites its own row and never
touches an earlier week, so the log is a RECORD rather than a snapshot — the
point is being able to read in week 12 what was believed in week 3.

**The rule the generator obeys: every sentence traces to a number in the
warehouse or an attributed quote.** Descriptive words ("elite offense", "played
slow") are chosen by threshold against that season's own league distribution,
and the thresholds sit in the code where they can be argued with. A season-long
log of confident invented prose is worse than none, because it reads exactly
like the real thing.

Strength claims use `mart.team_rating` (opponent-adjusted), NOT raw
`mart.team_game` averages — for consistency with the re-rating chart on the same
panel, and because a simple mean of per-game neutral-down rates equally weights
a 2-play sample against a 50-play one. That produced +0.170 net EPA for Arizona
through week nine of a 3-14 season. Style metrics are play-weighted; the
first-half/second-half split uses point margin, which has no such sample problem.

`team_charts.py` holds the season-level figures for the Teams panel:
`quadrant` (all 32 by offensive vs defensive EPA, with defense inverted so up
and right is better on both axes, crests plotted as marks), `sparkline` (weekly
net rating inside a table row, no axes) and `rerating` (a dumbbell of our
measured rating against the market-implied one, sorted by the size of the move
— the long ones are almost all coaching or quarterback changes). Team data is
assembled by `team_season()` in `slate_data.py` and lands under `teams` in the
JSON.

`slate_charts.py` holds the three inline-SVG figures: `margin_chart` (full
simulated margin distribution with the cover line drawn on it), `book_strip`
(how many books sit on each number — a COUNT histogram, because 26 books over
three distinct lines overflowed a per-book dot strip) and `proj_bullet` (a
player's quote band with our line on it). No chart library: the artifact CSP
forbids it and these shapes are clearer hand-built.

`build_logos.py` turns the club logo pack into `scripts/logo_assets.py` —
crests alpha-trimmed then **padded back onto a square canvas**, as data URIs,
plus official team colors from `raw.teams` as a fallback. The padding matters:
pack aspect ratios run 0.73 to 2.26 because some clubs ship a crest and others a
wide wordmark, and dropping a 2.26:1 mark into a square icon box renders it as a
smear (New Orleans was unrecognisable). `build_fonts.py` embeds Inter Tight, Big
Shoulders Display and Roboto Mono as @font-face data URIs — the Artifact CSP
blocks font CDNs, so a linked stylesheet fails silently to system faces. Emit each crest as
ONE CSS rule and reference it by class; inlining the data URI at every use took
the page from 326 KB to 1.2 MB. Loose PNGs in the project root are picked up
alongside the zip.

Layout trap: `.tw{overflow-x:auto}` makes the box a scroll container on BOTH
axes, so a `position:sticky` `th` resolves against it rather than the viewport.
Any `top` offset then opens a gap at the top of the box and hides the first row
behind the header. Keep `top:0`.

**The simulator is tuned, not shifted.** Two controls, near-orthogonal:
a symmetric strength split `d` moves the margin, a drive-count multiplier
`drive_scale` moves the total. Both slopes are measured at fit time
(`points_per_strength` ≈ 35, `total_per_drive_scale` ≈ 44). 14 of 16 games
converge exactly; the two that do not are large favorites (CLE@JAX -7.5,
ARI@LAC -10.5) where strength clamps at the fitted range, and their shape
products are suppressed.

Four bugs found building this, all of which silently manufacture edge:

1. **Sign convention.** `raw.games.spread_line` is home-PERSPECTIVE (+3.5 =
   home favored); `odds.line.line_value` is the HANDICAP (-3.5 on the home
   side). Opposite signs. Mixing them put a home favorite at 31% to win.
2. **Recentering by rounding.** Shifting integer margins by a fractional amount
   and re-rounding snapped the distribution by whole points; P(margin=3) swung
   3.8%–13.6% across near-identical spreads. Tune the inputs instead — key
   numbers belong to the score lattice, not to the mean.
3. **Line-gap conversion anchored on the model.** Reading P(under 42.5) off our
   own recentered distribution invented 5.3pp of edge, because recentering
   forces our probability at the line to ~50% while the sharp price implied
   47.1%. Use the model for the INCREMENT only, added to the sharp fair.
4. **Integer drive counts.** `int(round(n * drive_scale))` made the total a step
   function of the scale — a 0.013 change swung it 3.2 points and the solver
   oscillated. Stochastic rounding fixes it.

**Known limitation, measured:** the sim scores only in 3s and 7s, so totals that
are awkward to build from those run thin — 0.80pp light at a total of 39, 0.72pp
at 42, against 5,199 games. Margin key numbers do not share this (2.5pp). Total
middles are therefore conservative. `drive_sim.py validate` now reports it.

Middles are held to a significance test: an edge under 2 simulation standard
errors is reported as "line ball", not positive. That took Week 1 from one
"+EV" middle (BUF@HOU, +0.11pp on an SE of 0.17pp) to none.

## Step 5 — EV scanner  *(not yet built)*

Must filter `ref.book.is_placeable = TRUE`. `audit.unplaceable_bets` is the
backstop and must always read 0.

## Devig — shared primitive

`scripts/devig.py`. Every fair price, EV number and CLV grade depends on it.

```powershell
py -3 scripts/devig.py        # self-test, must print "all passed"
```

Four methods: `multiplicative`, `additive`, `power`, `shin`.
`default_method(family, n_outcomes)` routes on **outcome count**: two-way →
`power`, genuine multiway → `shin`.

**Multiplicative devig preserves favorite-longshot bias** — it strips margin
proportionally, so longshots stay overstated and favorites understated
relative to Shin or power. On -110/-110 all four agree to ~0.1%; on -600/+425
they spread 2.4 percentage points; on a futures book they diverge more. It is
the default nearly everywhere and a common source of phantom longshot edges.

**Anytime TD is NOT a multiway market.** It is *n* independent Yes/No two-way
markets, one per player. Their raw probabilities sum to ~1.8 across five
players because many players score in the same game — devigging them jointly is
meaningless. Same for every Over/Under prop. True NFL multiway markets are
futures: division winner, conference winner, MVP.

Note: Shin falls back to multiplicative when `sum(p) <= 1`, so a test market
must have **positive hold** or the comparison is silently vacuous.

## Margin model — line to probability

`scripts/margin_model.py`. Turns a LINE into a PROBABILITY. Required by CLV
grading whenever our line differs from the reference, and by alt-spread,
moneyline and teaser pricing. It is also the key-number distribution the
simulation layer must reproduce.

```powershell
py -3 scripts/margin_model.py build
py -3 scripts/margin_model.py validate
```

### Method

```
mu(s)       = s                    LOCATION COMES FROM THE MARKET
sigma       = 13.20                (flat; see below)
P_norm(m|s) = Phi(m+0.5) - Phi(m-0.5)
mult(m)     = observed/expected, pooled, SIGN-SYMMETRIC
P(m|s)      = P_norm(m|s) * mult(m), renormalized
```

Two obvious approaches fail: raw empirical tabulation is far too sparse
(7,344 games over ~40 spreads × ~80 margins), and a plain normal erases the
key numbers when a margin of 3 is 14.6% of games.

**Location is taken from the market, deliberately.** Unconstrained OLS returns
slope **1.0430**, which amplifies every handicap. For a market this efficient
`E[margin | spread] = spread` by definition, and we measured that bias at
−0.040 pts. The market owns the mean; this model owns the **shape**. The
unconstrained fit is still printed as a diagnostic — if that slope ever drifts
far from 1.0, something changed.

**Multipliers are sign-symmetric.** Estimated asymmetrically they came out
3:2.74 vs −3:2.51, which re-injects home-field advantage the spread already
contains. Landing on 3 is a property of football scoring, not of who is home.

**Sigma is effectively flat**: 13.19 at pick'em, 13.23 at |spread| 14. The
heteroskedasticity term is fitted and retained but contributes almost nothing.

### Validation

| check | result |
|---|---|
| key numbers, population-averaged | max error **0.40pp** (3: 14.53% vs 14.64%) |
| spread→moneyline, \|spread\| ≤ 3 | MAE **1.6pp** |
| spread→moneyline, \|spread\| > 6 | MAE 3.8–4.5pp, bias −2.0 to −2.4pp |
| cover calibration | ~50% predicted by construction; actual within ~2 SE |

**Known limitation.** The model systematically gives big favorites ~2pp less
win probability than the moneyline market does, and the error grows with
favorite size. Consistent with the 1.043 slope and with documented
favorite-longshot bias in NFL moneylines. Near pick'em it agrees to 1.6pp.
**Trust it for line-to-line deltas near the bet's own number; treat its
absolute moneyline for a double-digit favorite as approximate.**

Key-number value, home favored by 3.5 (odds-feed handicap):

| handicap | fair | Δ vs prev |
|---|---:|---:|
| −2.5 | 55.33% | |
| −3.0 | 51.29% | −4.04pp |
| −3.5 | 47.03% | −4.25pp |
| −4.0 | 45.60% | −1.43pp |
| −4.5 | 44.40% | −1.20pp |

Crossing 3 costs **8.3pp**; the next full point costs **2.6pp**. A Gaussian sim
flattens that away, which is why teasers through 3 and 7 are priced the way
they are.

## Totals model + EV scan — and what is GATED

```powershell
py -3 scripts/totals_model.py build
py -3 scripts/totals_model.py validate
py -3 scripts/ev_scan.py --min-edge 0.5
```

`scripts/totals_model.py` is the margin model's twin: market-anchored mean,
era-fitted sigma that **scales with the total** (12.45 at 38 → 13.81 at 52),
weak key-number multipliers. **Null test: 0.97pp MAE against the market's own
over/under prices.** Trustworthy.

### Sigma was stale — refit on the modern era

Residual SD of (margin − spread) **drifts**: 13.25 (1999-05) · 13.77 (2006-11)
· 13.18 (2012-17) · **12.75 (2018-25)** · 12.66 (2020-25). Our original
full-history 13.20 was ~0.45 points too wide and was the main driver of the
model understating big favorites. `SIGMA_SEASON = 2018` cut moneyline MAE
from 3.03pp → 2.70pp and |err|>5pp from 15.8% → 10.2%. Key-number multipliers
keep the long window — they need the sample and do not drift.

### THE MARGIN MODEL IS GATED FOR HALF-POINT PRICING

It reproduces the **unconditional** key-number distribution (0.40pp) and the
moneyline (2.70pp). It is **not valid for absolute cover probability at a
specific half-point.** Against 2006+ history:

| across | actual swing | model swing |
|---|---:|---:|
| line 2.5 → 3.5 | **−2.0pp** | **+5.3pp** |
| line 6.5 → 7.5 | **−1.9pp** | **+2.4pp** |

Sample-weighted error on half-point lines: **4.10pp** — larger than any edge
worth betting, and it manufactured a plausible "+2.19pp" on every +7.5 dog
before the gate existed.

**Cause:** the multiplier profile is attached to ABSOLUTE margin and then
translated to the line, breaking the symmetry a market-anchored model must
have — at the market's own number, cover probability should be ~50%. Line
placement is itself informative and the market has already absorbed the
key-number interaction. The real fix is multipliers estimated **conditional on
the spread**, which needs more data or heavy smoothing. Not a patch.

Totals do **not** have this pathology: predicted over rate stays ~50% at every
line (correct), and only 2 of 14 half-point buckets exceed 2 SE — chance.

Consequently `ev_scan.py` suppresses spread rows bridged by the margin model,
and `bet_log.py` grades spreads in **points only**. Totals grade through the
totals model.

### What the scan will show you

- **`novig`** rows — our price vs the sharp no-vig price **at the same line**.
  Pure price shopping, no model risk. Currently the only spread edge this
  system can honestly claim.
- **`MODEL*`** rows — totals bridged across a line difference. Valid, but the
  asterisk is a reminder that a model sits in the path.
- Rows more than **1 point** off the reference are suppressed as probable
  stale quotes.

### Live finding (2026-08-16 capture)

All four US books hang **44.5** on NE @ SEA while **Pinnacle simultaneously
prices 43.5** — same 09:53:24 capture, so not a timing artifact. The pattern
repeats across the 16 games Pinnacle prices. Either a real edge or a Pinnacle
pricing convention for low-limit early markets. **Logged as paper bet 5, with
bet 6 deliberately passed to keep the test clean.** CLV at close decides it —
which is exactly what the bet log is for.

## Step 6 — Bet log + CLV grading

```powershell
py -3 scripts/bet_log.py log --game 2026_01_NE_SEA --market spread_game \
    --side home --book draftkings --decision placed \
    --thesis "..." --kill "..."
py -3 scripts/bet_log.py grade
py -3 scripts/bet_log.py report
py -3 scripts/bet_log.py show
```

Price is read from the latest `odds.line` snapshot unless `--price` is given,
so a logged decision reflects a price actually observed.

### Two enforced commitments

**Passes are logged.** `bet.wager` records a *decision*, not a bet:
`decision` is `placed` or `passed`, and both are CLV-graded. Logging only
placements gives a censored sample in which the passes that would have won are
invisible, so you can never learn the filter is too tight.
`audit.placed_vs_passed` is the payoff — **if passes beat the close as often as
placements, the selection filter is adding nothing.**

**Thesis and kill condition are mandatory and pre-kickoff.** The CLI refuses a
thesis under 20 chars or a kill condition under 10, and `audit.bets_missing_thesis`
/ `audit.bets_logged_after_kickoff` / `audit.bets_bad_decision` must all read 0.
Placing at a non-placeable book is also refused outright.

### CLV metric — the subtlety that matters

A probability comparison is valid **only when both sides refer to the same
bet**. Grading SEA -3.5 against a sharp SEA -4.5 by differencing probabilities
is meaningless: they are different bets, and because our price still carries
the book's vig while the reference is de-vigged, the difference lands near
minus-half-the-hold on *every* wager and reads as a uniform loss. The first
implementation did exactly that and reported "+1.0 points" and "−4.29pp" for
the same bet.

So `clv_metric` records which basis was used:

| lines | metric | measures |
|---|---|---|
| match | `prob_at_same_line` | `close_novig_prob − our_raw_implied` |
| differ, model available | `prob_via_margin_model` | fair prob of **our** handicap under the sharp line's margin distribution, − our implied |
| differ, no model | `line_pts` | `clv_line_pts`; `clv_prob_delta` left **NULL** |

`clv_prob_delta` is **expected edge, not line movement.** A -110 price implies
52.38%, so a coin-flip starts at −2.38pp. Most decisions will be negative and
that is correct — the vig is real. **The benchmark is average CLV pp above
zero, not a 50% hit rate.**

The two views can disagree, and that is the point. Bet 2 below won **+1.0 point**
of line value and is still **−2.48pp**: a full point on a 3.5 is worth ~1.8pp,
which does not cover the ~2.4pp of vig at -110. Line value alone does not make
a bet +EV.

`grade_basis` is `close` when a `phase='close'` snapshot exists, otherwise
`reference` (most recent sharp price, provisional). Pre-season grades are all
`reference`.

Line-CLV sign convention: spreads use `our_line − close_line` (correct for both
favorite and dog); totals use `close − our` for over/yes and `our − close` for
under/no.

### First run (2026-08-16, paper)

3 decisions against the real Week 1 board, graded vs Pinnacle:

```
bet 2 PLACED  SEA -3.5 vs Pinnacle -4.5  -> +1.0 pts = -2.48pp  [margin model]
bet 3 PASSED  CIN -3.5 vs Pinnacle -3.5  ->           -4.44pp  [same line]
bet 4 PASSED  HOU +1.5 vs Pinnacle +1.0  -> +0.5 pts = -3.11pp  [margin model]
```

All three are −EV against Pinnacle, which is the honest answer for a board
where we hold DK/FD/MGM and the sharp number is a point better. Both passes
were correct. The placement had genuine line value and still did not clear the
vig — exactly the distinction the margin model exists to make visible, and one
the line-points view alone would have hidden.

## Pressure rate (mart.team_pressure)

    py -3 scripts/build_pressure.py      # 3,386 team-games, 32 teams

Substrate trap. `raw.pbp_participation.was_pressure` runs 2020-2025 but before
2023 it is populated on **non-sack dropbacks only** - every sack carries NULL.
Read naively this drops the most-pressured plays in football from both numerator
and denominator and reports a 0.0% sack rate for 2020-2022 while otherwise
looking fine. Recovered by treating a sack as a pressure: in 2023-2025, where
the field is native on every dropback, 4,201 of 4,203 sacks are flagged
pressured (99.95%). Uncharted dropbacks (~1,000 a year pre-2023) stay OUT of the
denominator rather than being assumed clean. Rows carry `native`; restrict to
2023+ for any model. League pressure rate reads 33-34% in 2020-22 vs 29-31% in
2023-25, so the eras are not safely comparable in level.

Not available, so stop looking: true yards per route run. `participation.route`
describes the play, not each player, so routes run per receiver cannot be
recovered. `route_proxy_snaps` in `mart.player_game_usage` stays the best
opportunity denominator.

## Metric screens

    py -3 scripts/metric_stability.py    # -> mart.metric_stability (25 rows)
    py -3 scripts/metric_edge_test.py    # -> mart.metric_edge (17 rows)

Stability = first N games vs the rest of the same season. Edge = partial
correlation with margin holding the closing spread fixed. Nothing clears the
edge screen; largest |t| is 1.54 across 17 metrics, where multiplicity needs
about 3.0.
