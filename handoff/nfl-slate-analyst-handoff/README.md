# nfl-slate-analyst — Handoff Package

**Version 3.0 · Packaged 2026-08-16**

A portable agent skill: a disciplined NFL analyst that reasons from two independent
lenses — football process and betting market — and publishes a weekly HTML brief.
Covers **sides, totals, and player props**. Self-contained; no proprietary data required
to run the code.

**New in 3.0 — the modeler lineage layer.** The skill's methodology is now explicitly
grounded in the documented practice of the best public NFL modelers (Sean Koerner,
Massey-Peabody, Ben Baldwin, Aaron Schatz, Kevin Cole, ESPN FPI, FiveThirtyEight Elo):

- `skill/references/modeler-playbook.md` — each modeler's core principle and how the
  agent applies it, so the analyst reasons *the way they do*, not just cites them.
- Three new non-negotiable rules in `SKILL.md` (10–12): weight inputs by out-of-sample
  predictive power, never in-sample fit; present tiers when gaps are inside the error
  bar; treat early-season reads as priors that decay, with the blend labeled.
- `docs/modelers-guide.md` — the underlying research: who the modelers are, their
  processes, and links to every public source.
- `docs/architecture-spec.md` — a six-layer logic specification for building the
  surrounding *system* (data → ratings → market → decision → presentation → grading),
  with a build order that maps onto `ROADMAP.md` Tier 1.

This README is written for whoever or whatever receives this package next. Read it
first; everything you need is described below.

---

## 1. What this is

An **agent skill**: a folder containing a Markdown instruction file (`SKILL.md`), four
reference documents the agent loads on demand, and four runnable Python scripts. An LLM
agent reads `SKILL.md` to adopt the analytical posture and workflow, consults the
references for depth, and calls the scripts for anything numeric.

It is **not** a model, a prediction service, or a data feed. It supplies method,
discipline, and tooling. Data must be connected (see §5).

### The core thesis, in one paragraph

The market's closing line is the strongest single prior available and beats essentially
every public model. So the skill does not try to out-predict it from scratch. It builds
an independent football read, compares it to the vig-removed market price, and takes a
position only where the two disagree by more than model error **and the specific
mispricing can be named**. Performance is graded on **closing line value**, not win/loss,
because CLV separates skill from variance in dozens of bets rather than hundreds.

---

## 2. Contents

```
nfl-slate-analyst-handoff/
├── README.md                     ← you are here
├── MANIFEST.json                 ← machine-readable inventory + entry points
├── ROADMAP.md                    ← 10 companion skills, prioritized, with rationale
├── requirements.txt              ← Python dependencies
├── verify.sh                     ← runs every self-test; exit 0 == package healthy
├── nfl-slate-analyst.skill       ← the same skill as an installable zip
├── skill/                        ← the skill, unpacked
│   ├── SKILL.md                  ← agent instructions (entry point)
│   ├── references/
│   │   ├── market-math.md        ← devig, key numbers, Kelly, CLV, correlation
│   │   ├── football-metrics.md   ← metric stability, EPA, opponent adj., injuries
│   │   ├── props-modeling.md     ← usage tree, distributions, traps
│   │   ├── data-sources.md       ← verified URLs, API usage, refresh cadence
│   │   ├── brief-format.md       ← editorial spec for the weekly brief
│   │   └── modeler-playbook.md   ← how the best public modelers think (NEW in 3.0)
│   └── scripts/
│       ├── market.py             ← odds math                  (self-testing)
│       ├── props.py              ← prop projection            (self-testing)
│       ├── key_numbers.py        ← empirical constants from live data
│       └── build_brief.py        ← HTML brief renderer        (validating)
├── schema/
│   └── week.schema.json          ← JSON Schema for the brief input file
├── templates/
│   └── week_template.json        ← a complete, valid week file to copy
├── examples/
│   └── sample_brief.html         ← rendered output (open in a browser)
└── docs/                         ← NEW in 3.0
    ├── modelers-guide.md         ← research: top public NFL modelers + sources
    └── architecture-spec.md      ← six-layer logic spec for the surrounding system
```

---

## 3. Quick start

```bash
pip install -r requirements.txt
./verify.sh                                            # confirm the package is healthy

# render the example brief
python3 skill/scripts/build_brief.py --sample -o brief.html

# render from your own week file
python3 skill/scripts/build_brief.py templates/week_template.json -o week07.html
```

To install as an agent skill: point the agent at `skill/SKILL.md`, or install
`nfl-slate-analyst.skill` (a zip) into whatever skill directory the host uses.

---

## 4. Entry points

| Entry point | Purpose | Notes |
|---|---|---|
| `skill/SKILL.md` | Agent instructions | The primary interface. Everything else is called from here. |
| `build_brief.py <week.json> -o out.html` | Render the weekly brief | Validates input; **rejects any play missing a thesis or kill condition** |
| `build_brief.py --sample -o out.html` | Render the demo | No input needed |
| `market.py` | Import as a module, or run for a demo | `fair_probs`, `kelly`, `edge`, `clv`, `team_totals` |
| `props.py` | Import as a module, or run for a demo | `GameEnvironment`, `ReceiverProjection`, `RusherProjection` |
| `key_numbers.py --since 2018` | Recompute empirical constants | **Network required** — fetches nflverse |

---

## 5. Data dependencies

**Nothing is bundled.** Two connections are needed for live use:

1. **nflverse** (free, no key) — play-by-play, snap counts, rosters, injuries, schedules
   with historical spreads and totals. Direct parquet/CSV downloads over HTTPS. All URLs
   in `references/data-sources.md` were verified to resolve at packaging time.
2. **An odds feed** (paid, key required) — The Odds API is the documented default
   (`americanfootball_nfl`; markets `h2h`, `spreads`, `totals`, plus player props). Any
   feed works; the skill only needs American odds per outcome per book.

**No API key is included in this package.** The receiving system must supply its own.

The scripts have no hardcoded secrets, no telemetry, and no writes outside the paths
given on the command line. `key_numbers.py` is the only script that touches the network.

---

## 6. The week file

`build_brief.py` consumes one JSON file per week. Formally specified in
`schema/week.schema.json`; a complete valid instance is `templates/week_template.json`.

Shape:

```
meta          — title, season, week, data_as_of, sample flag
season_stats  — record, units, roi_pct, bets, beat_close_pct, avg_clv_pts
lede          — 2–4 sentence written opening
markets[]     — per market TYPE (Sides/Totals/Props): plays, season CLV, one-line read
games[]       — per game: teams, spread, total, narrative, markets[]
                markets[] — type (Side|Total|Prop), status (play|pass), price, book,
                            fair, edge_pct, stake_units, thesis, kill  — or reason
passed[]      — games skipped entirely, with reasons
```

**Two fields are enforced, not optional.** A market entry with `status: "play"` must
carry a `thesis` and a `kill` (what would make the bet wrong, written *before* kickoff).
The renderer exits with a named error otherwise. This is the package's one opinionated
constraint and it is deliberate: unfalsifiable theses are how bad process survives.

The narrative fields cannot be generated from the data — they are the analytical product
and must be written by the agent or a person.

---

## 7. Design constraints a receiving system should preserve

These are load-bearing. Changing them changes what the skill is.

1. **Price before opinion.** Convert odds to no-vig probability *before* forming a view.
2. **Edge is measured against no-vig prices**, never raw prices.
3. **Fractional Kelly only** — quarter Kelly default, hard cap 2–3% of bankroll per bet.
4. **Sub-2% edges against sharp prices are reported as "no edge."**
5. **Passes are published**, not hidden. Selectivity is only verifiable if declines are on
   the record.
6. **Grade at the close, not the final score.**
7. **Empirical constants are recomputed, not memorized** — `key_numbers.py` exists because
   key-number frequencies, home field advantage, and margin dispersion all drift.
8. **If real odds were not retrieved, the brief renders a SAMPLE DATA banner.** Illustrative
   numbers must never look live.
9. **Model inputs are weighted by out-of-sample predictive power, never in-sample fit.**
   Unvalidated factors are named as unvalidated and downweighted.
10. **Rankings collapse into tiers when gaps are inside the error bar.** Within-tier
    order is presented as noise, not signal.
11. **Early-season output is prior-dominated and labeled as such**, with in-season data
    phased in as metrics stabilize.

---

## 8. Verified at packaging time

- All four scripts execute and pass their embedded self-tests (`./verify.sh`).
- All nflverse asset URLs in `data-sources.md` returned HTTP 200.
- Empirical constants recomputed from 2,227 real games (2018–2025): margin lands on 3 in
  14.8% of games and on 7 in 8.5%; home field advantage averaged +1.7 points; residual SD
  of margin around the spread is 12.76. These are **in the code and docs, replacing the
  stale conventional values** (HFA 2.5–3, SD ~14) that bias every game the same direction.
- Brief renders correctly in light mode, dark mode, and print.
- Chart/accent palette passes colorblind-safety validation all-pairs in both modes.
- Input validation confirmed to reject a play missing its kill condition.

---

## 9. Known limitations — state these downstream

- Weeks 1–4 samples are weak on their own; the skill leans on prior-season priors and
  preseason market prices, and says so.
- Team-level defensive metrics are the noisiest common inputs in football analytics.
- CLV is a valid scorecard only against markets efficient at close. On a low-limit prop
  that never sees sharp action, beating the close means little.
- There is no automated data pipeline in this package. Week files are assembled manually
  or by a companion system. `ROADMAP.md` §Tier 1 specifies what to build first.
- Nothing here predicts game outcomes on its own. It is a method for finding mispriced
  markets, and it will have losing stretches that are indistinguishable from variance.

---

## 10. Provenance and use

Built 2026-08-16. Version 3.0 (adds the modeler-lineage layer — playbook reference,
rules 10–12, research guide, and system architecture spec — to the 2.0 brief-publishing
package; 2.0 added the weekly-brief output layer to the 1.0 analysis core). Contains no
third-party code — the Python scripts are original and depend only on `numpy`, `scipy`,
and `pandas`. The modeler research in `docs/modelers-guide.md` cites only publicly
available sources, linked inline.

Intended for personal and analytical use. Sports betting is legal in some jurisdictions
and not others; compliance is the operator's responsibility. Nothing in this package is
financial advice or a guarantee of results.
