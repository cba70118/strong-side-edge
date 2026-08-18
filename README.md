# Strong Side Edge

NFL 2026 analysis. A rolling season resource — weekly board and plays first,
team pages and season-long player projections behind them, the season record at
the back.

**The published site is encrypted.** This repository is public; the output is
not. `site/index.html` holds an AES-256-GCM payload with a PBKDF2-SHA256 key
(310,000 iterations), so the content is unreadable without the passphrase —
that is real encryption rather than a JavaScript password check, which protects
nothing on a static host.

## What this system claims, and what it does not

The honest summary, because most of the value here is knowing which side of the
line a number falls on:

| | measured |
|---|---|
| Sides | **No edge.** Opponent-adjusted ratings partial-correlate **−0.04** against the closing spread; the drive simulator **−0.030** over 816 games. A side listed as a play is a better *number* or a better *price*, never a prediction. |
| Totals | Market is unbiased at 50.4% under over 6,967 games. Any edge is the price or the number. |
| Props | The one market with a model view — the prop distribution passed a randomised coverage test per stat. |
| Season player projections | Beat a naive prior-season total by **14.6%** out of sample (receiving) and **8.1%** (rushing). Not tested against a market: no season-long player line exists in either feed. |

Bands are **measured, not modeled**. A 1,000-yard receiving projection honestly
spans 340–1,770, because that is the empirical 10th–90th percentile of
actual/projected on 409 out-of-sample player-seasons.

## Layout

```
scripts/     ingestion, models, and the report builders
sql/         schema, marts, audit views
brain/       priors, hypotheses and graded outcomes (the falsification loop)
references/  method docs and the dashboard spec
site/        the encrypted published page
```

Substrate quirks, verified commands and every trap worth not re-discovering are
in `RUNBOOK.md`. Rules earned from graded outcomes are in `brain/playbook.md`.

## Build

```
py -3 scripts/init_db.py --reset --validate
py -3 scripts/load_nflverse.py
py -3 scripts/build_mart.py
py -3 scripts/slate_data.py --season 2026 --week 1 -o week01_slate.json
py -3 scripts/season_log.py append -i week01_slate.json
py -3 scripts/slate_report.py -i week01_slate.json --dash week01_dash.json \
    -o week01_brief.html
py -3 scripts/publish_site.py -i week01_brief.html -o site/index.html
```

Requires `duckdb`, `Pillow`, `cryptography`. Copy `.env.example` to `.env` and
add your own keys — `.env` is gitignored, and `capture.py` redacts credentials
before anything reaches a log.

## Not included, deliberately

- **The warehouse** (`data/*.duckdb`, ~341 MB) — rebuildable from the loaders.
- **Verbatim third-party transcripts.** Move The Line show text is not ours to
  republish. `sql/08_analyst_take.sql` keeps the structured positions, which are
  short paraphrases; `sql/07_transcript.sql` and `handoff/transcripts/` are
  excluded.
- **Logs.** They can carry request URLs, and those carry API keys.

## Nothing here is betting advice

It is a record of what one model said, when it said it, and how often it was
wrong.
