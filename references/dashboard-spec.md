# NFL Dashboard: Design Contract

Drop this at `references/dashboard-spec.md` in the NFL project and reference it by name in every UI request. It governs the dashboard only. It does not govern the weekly brief.

---

## 0. Artifact class

This is the rule that stops the paragraphs. The brief and the dashboard are different products and cannot share an editorial spec.

| | Weekly brief | Dashboard |
|---|---|---|
| Product | The narrative | The state of the board |
| Reader mode | Read once, start to finish | Scan, drill, act, leave |
| Prose role | The argument | The annotation on a number |
| Success test | "I understood the week" | "I found the spot and logged it in 15 seconds" |
| Default output | Sentences | Components |

If a request does not name the artifact class, assume dashboard and build components.

---

## 1. Prose budget

Hard caps. Enforce in the data layer, not by good intentions. A field over budget fails validation the same way a missing kill condition does.

| Slot | Cap | Placement |
|---|---|---|
| Board lede | 3 sentences | Top of board view only, above the fold |
| Game thesis | 120 characters | One line inside the game card |
| Kill condition | 90 characters | One line, always paired with the thesis |
| Pass reason | 60 characters | Single line in the pass list |
| Market note | 60 characters | Optional, one per market row |
| Everything else | 0 | Becomes a component or a number |

Two standing rules:

1. If a sentence can be a chip, a bar, a delta, or a badge, it must be. Prose is the fallback, not the default.
2. No paragraph below the fold. One text block per screen, maximum.

---

## 2. Component vocabulary

Build these as named components first, then compose screens out of them. Requests reference components by name. "Make it cleaner" is not a request. "Replace the game summary paragraph with a DisagreementLine plus a VerdictChip" is.

| Component | Shows | Data contract | Never |
|---|---|---|---|
| `DisagreementLine` | Market number, model number, error band, gap | `market`, `model`, `band_low`, `band_high`, `unit` | Render without the band |
| `VerdictChip` | One of PLAY / LEAN / PASS / NO EDGE / STALE | `verdict`, `market_type` | Invent a sixth verdict |
| `EdgeBar` | Edge magnitude against a common baseline | `edge_pct`, `threshold` | Show under-threshold edge as positive |
| `PriceRow` | Price, book, timestamp, no-vig fair, edge, stake | `price`, `book`, `taken_at`, `fair`, `edge_pct`, `stake_u` | Show a price without book and time |
| `KillCard` | Thesis line, kill condition line, kill status | `thesis`, `kill`, `kill_triggered` | Render with either line missing |
| `PassLine` | Game, market, one-line reason | `game`, `market_type`, `reason` | Hide the pass list behind a toggle |
| `KPITile` | One season metric, trend direction | `label`, `value`, `delta`, `weight` | Give P/L more visual weight than CLV |
| `CLVSpark` | Rolling CLV, zero line marked | `series[]`, `window` | Omit the zero line |
| `LineMoveTrack` | Open, current, your number, in one axis | `open`, `now`, `your_number` | Show current only |
| `StaleBadge` | Age of the price feed | `as_of` | Suppress when data is old |
| `SourceStamp` | Book, feed, retrieval time | `source`, `fetched_at` | Aggregate away the source |

---

## 3. Screen hierarchy

Four levels. Each answers exactly one question. Each has a prose ceiling and a required action.

| Level | Question | Primary components | Prose | Required action |
|---|---|---|---|---|
| Board | Where is the board soft this week? | KPITile strip, DisagreementLine per game, VerdictChip | Lede only | Drill into game |
| Game | What do market and process disagree about here? | LineMoveTrack, market sub-rows, KillCard | Thesis + kill | Open a market |
| Market | Is this priced wrong, and by how much? | PriceRow across books, EdgeBar, distribution | Market note | Log or pass |
| Bet | What did I take, at what number, and did it beat the close? | PriceRow, CLVSpark, kill status | None | Grade at close |

Sort the board by edge magnitude, not by kickoff time. Kickoff order is for the brief. The dashboard sorts by where the money is.

---

## 4. Language organization

### Canonical labels

One term per concept, identical on every screen and in every export. No synonyms, no expansion on second use.

| Concept | Label | Not |
|---|---|---|
| De-vigged probability | `Fair` | True odds, real probability, no-vig price |
| Model minus fair | `Edge` | Value, EV, advantage |
| Quarter Kelly stake | `Stake` | Size, units, bet amount |
| Closing line value | `CLV` | Beat the close, line value |
| Implied team total | `ITT` | Team total, projected points |
| Price movement | `Open → Now` | Line move, movement |
| Falsifier | `Kill` | Exit, red flag, risk |
| Declined play | `Pass` | Fade, skip, no bet |

### Label rules

- A label is a noun. An action is a verb. Never mix them.
- Headers name the answer, not the topic. "Market disagrees by 2.4 pts" beats "Market analysis."
- Sentence case everywhere except the verdict lexicon, which is uppercase and fixed.
- Units live in the column header, not repeated in every cell.

### Number formatting

| Type | Format | Rule |
|---|---|---|
| Spread, total | `+3.5`, `47.5` | Always signed, always half-point precision |
| Probability | `54%` | Whole numbers, no decimals |
| Edge | `+2.4%` | One decimal, signed, gray below the 2% threshold |
| Stake | `1.4u` | One decimal |
| CLV | `+0.8 pt` | One decimal, signed, zero line always visible |
| Time | `Thu 4:12p` | Relative under 24h, absolute over |

Tabular figures in every numeric column. No fake precision anywhere: `62.7% confidence` is banned, ranges are not.

### Banned in the UI

Lock, hammer, max bet, free money, gift, due, must-win, revenge, should win comfortably, trending, insights, powerful, leverage.

---

## 5. Direct actions

Every card carries a verb. An element that does not change state, write to the log, or leave the app is a report, not a dashboard.

| Surface | Actions |
|---|---|
| Board row | Drill, Watch, Mark pass |
| Game card | Log bet, Set line alert, Copy card, Mark pass |
| Market row | Log at this price, Recheck price, Open book |
| Bet row | Grade at close, Edit stake, Void |
| KPI strip | Filter board by market type |

Rules:

- Action labels are verb-first and survive the whole flow. The button says Log bet, the toast says Logged, the row says Logged.
- One primary action per card, visually dominant. Everything else is secondary.
- Empty states carry the action, not an apology. "No plays yet. Log one from any market row."
- Errors state what happened and the fix. "Odds feed 40 minutes stale. Recheck price."

---

## 6. Visual system

Inherit the brief tokens so both products read as one system.

| Role | Light | Dark |
|---|---|---|
| Sides | `#2a78d6` | `#3987e5` |
| Totals | `#eb6834` | `#d95926` |
| Props | `#1baf7a` | `#199e70` |
| Positive | `#0ca30c` | `#2fbf4a` |
| Negative | `#d03b3b` | `#e05555` |
| Surface | `#fcfcfb` | `#1a1a19` |
| Plane | `#f9f9f7` | `#0d0d0d` |

Type: IBM Plex Sans Condensed for labels and headers, IBM Plex Sans for the few prose slots, IBM Plex Mono for every numeral. Numerals never render in the body face.

Signature element: the `DisagreementLine`. One horizontal axis per game, market number as a bone-white tick, model number as a market-colored tick, the gap between them shaded, the model error band drawn as a translucent span. When the band overlaps the market tick, the row is NO EDGE and desaturates automatically. This is the system's thesis drawn as a repeated primitive, and it is the only place to spend visual boldness. Everything else stays quiet.

Density: 40px board rows, 8px base grid, hairline dividers, no card shadows. Color never carries meaning alone, every state is also written.

Motion: state changes only. Value updates cross-fade, rows reorder with a 180ms move, nothing animates on load. Respect `prefers-reduced-motion`.

Screenshot rule: the board and any single game card must screenshot cleanly at 1200px wide with no cropped columns. That is how the card gets shared.

---

## 7. Reject on sight

- A paragraph where a table belongs
- Any narrative field over its character cap
- Meaning carried by color alone
- Pie charts, gauges, dual axes, donut KPIs
- A price without a book and a timestamp
- Sub-2% edge rendered as a positive edge
- A card with no verb on it
- Kickoff-ordered board
- Hidden pass list

---

## 8. How to request it

The output was prose because the input was adjectives. Replace "make the dashboard better" with a contract.

**Weak:** "Design a nice NFL dashboard, make it modern and clean and easy to read."

**Correct:**

```
Build the Game card for the NFL dashboard, per references/dashboard-spec.md.

Screen: Game (level 2 of 4)
Question it answers: what do market and process disagree about in this game

Data contract:
  game, kickoff, spread_open, spread_now, total_open, total_now,
  itt_home, itt_away, markets[{type, price, book, taken_at, fair,
  edge_pct, stake_u, verdict, thesis, kill}]

Components: LineMoveTrack, DisagreementLine per market, VerdictChip,
PriceRow, KillCard, PassLine
Prose: thesis 120 chars, kill 90 chars, nothing else
Actions: Log bet (primary), Set line alert, Copy card, Mark pass
Done when: renders with sample.json, no field over its cap, dark and
light both ship, screenshots clean at 1200px

Do not write summary paragraphs. Uncomponented text fails review.
```

Five things make the difference: the screen, the data contract, the named components, the prose budget, and the done condition. Give all five every time.

---

## 9. Definition of done

- [ ] Every screen answers its one question above the fold
- [ ] No prose field over its character cap, enforced in the validator
- [ ] Every card has one primary verb
- [ ] Every price shows book and time
- [ ] CLV weighted equal to or above P/L in the KPI strip
- [ ] Pass list visible without interaction
- [ ] Light and dark both ship
- [ ] Board sorted by edge magnitude
- [ ] Every state written in text, not color alone
- [ ] Keyboard focus visible, reduced motion respected
