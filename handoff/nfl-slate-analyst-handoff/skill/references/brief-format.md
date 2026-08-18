# The Weekly Brief — Editorial Spec

The brief is the deliverable. Everything else in this skill exists to fill it in.

It is a **single self-contained HTML page**, organized **game by game, with market
sub-sections inside each game**, plus a market-level summary strip up top so the
by-market view is never lost. Rendered by `scripts/build_brief.py` from a week JSON.

---

## 1. Audience and Voice

Written for a **betting group** — people who bet seriously but are not quants.

**Do:**
- Lead with the read, then the number. "Houston's line has been rebuilt and the market
  hasn't repriced the pass rush it now faces — that's the bet" beats "HOU +3.5 has 3.1%
  edge."
- Define jargon on first use, once, inline and briefly. *No-vig* (the price with the
  book's cut removed) is enough. Do not re-define it in every section.
- Write in complete sentences with a point of view. The narrative is the product; the
  table is the receipt.
- Say "I" and "we." This is a person talking to their group, not a wire service.
- Name the disagreement in plain language: what does the market believe, and why do you
  think it's wrong?

**Never:**
- Tout language: "lock," "hammer," "max bet," "can't lose," "free money," "gift."
- Fake precision: "62.7% confidence" implies a calibration you do not have. Say
  "meaningful edge," "thin edge," or give a range.
- Filler certainty: "should win comfortably," "will move the ball at will."
- Narrative inputs as evidence — revenge games, "must-win," primetime records. The
  reasoning rules in SKILL.md apply to the writing, not just the analysis.
- Recapping last week's result as if it predicts this week's.

**Length discipline:** it is a **one-pager**. Every game block gets 2–4 sentences of
narrative, not an essay. If a game needs more than four sentences to justify, the edge
probably isn't there.

---

## 2. Required Structure

In order, top to bottom:

**A. Masthead** — brief name, week number, date, and the season record line
(units, ROI, CLV). Record goes at the top, always, win or lose. Hiding a bad
stretch is how a brief becomes a tout sheet.

**B. Season KPI strip** — 4–5 stat tiles: units P/L, ROI %, bets placed, % beating
the close, average CLV. **CLV tiles get equal or greater visual weight than P/L
tiles.** That hierarchy is the whole philosophy expressed as layout.

**C. The lede** — 2–4 sentences on the week as a whole. What is the market doing?
Where is the board soft? What is the single theme? This is the paragraph people
actually read.

**D. Market temperature strip** — one row per market type (Sides, Totals, Props):
how many plays this week, season CLV in that market, and a one-line read on whether
that market is currently offering anything. This is where "organized by market type"
lives. **Season CLV split by market tells you where your edge actually is** — usually
not where you think — so it belongs on the front page, not buried.

**E. Game blocks** — one per game analyzed, in kickoff order. Each contains:
   1. **Header row** — matchup, current spread, total, and both implied team totals.
   2. **Narrative** — 2–4 sentences. The football read and the market read, and the
      disagreement between them. This is the part that cannot be automated.
   3. **Market sub-sections** — Side, Total, Props. Each is either a play (with price,
      book, fair price, edge, stake, thesis, kill condition) or an explicit **PASS**
      with a one-line reason. **A game block with three passes is a valid and valuable
      entry** — it shows the board was examined.

**F. Passed on entirely** — games looked at and skipped, one line each. Mandatory.

**G. The card** — a single table of every play, so the group can screenshot one thing.

**H. Footer** — kill conditions summary, the "grade at close" reminder, data-as-of
timestamp, and a responsible-gambling line.

---

## 3. Narrative Requirements per Game

Each game's narrative must contain, in some order and in plain prose:

1. **What the market believes** — stated from the price, not guessed.
2. **What the process says** — the football read, with sample size acknowledged when
   it's thin.
3. **The named disagreement** — or an honest "these agree, so there's nothing here."
4. **The risk** — the thing most likely to make this wrong.

If you cannot write sentence 3, the game does not get a play. Write the block anyway
and mark all three markets PASS — the record of *not betting* is part of the product.

---

## 4. Visual Rules

Built on the `dataviz` skill's method. Parameters used:

| Role | Value |
|---|---|
| Market: Sides | categorical slot 1 — blue (`#2a78d6` light / `#3987e5` dark) |
| Market: Totals | categorical slot 2 — orange (`#eb6834` light / `#d95926` dark) |
| Market: Props | categorical slot 3 — aqua (`#1baf7a` light / `#199e70` dark) |
| Positive edge / beat the close | status good `#0ca30c` |
| Negative edge / lost CLV | status critical `#d03b3b` |
| Surfaces | light `#fcfcfb` / dark `#1a1a19`; page plane `#f9f9f7` / `#0d0d0d` |

Only **three** categorical slots are used, which clears the all-pairs CVD gate in both
modes — so market type may be carried by color. Even so, **every market label is also
written in text**, never color alone.

Other rules:
- Both light and dark mode ship, via `prefers-color-scheme` and a `data-theme` toggle.
- Tabular figures (`font-variant-numeric: tabular-nums`) in every numeric column.
- Edge is shown as a small horizontal bar plus the number — magnitude at a glance,
  precision on read. Bars are thin, 4px rounded ends, anchored to a common baseline.
- No pie charts, no dual axes, no gauge dials.
- Prints cleanly to PDF on one to two pages: `@media print` collapses interactivity
  and forces the light surface.

---

## 5. Honesty Constraints (these are not optional)

- If real odds were not retrieved, the brief must render a visible **SAMPLE DATA**
  banner. Never publish illustrative numbers that look live.
- The season record shown must come from the bet log, not from memory.
- Every play shows the **book and time the price was taken.** A price without a source
  is unverifiable.
- Include the data-as-of timestamp in the footer. A brief built Thursday and read
  Sunday is stale, and the reader needs to know which one they're holding.
- Ship the responsible-gambling line. Every issue. It costs one line.

---

## 6. Building It

```bash
python3 scripts/build_brief.py week.json -o brief.html
python3 scripts/build_brief.py --sample -o sample.html   # renders a demo week
```

The JSON schema is documented in the script's docstring and validated on load — it
will tell you exactly which field is missing rather than rendering a broken page.

Workflow: run the analysis per SKILL.md → write the JSON (narratives included) →
render → read the rendered page → send. **Read the rendered page before sending it.**
The validator checks color, not whether a sentence makes sense.
