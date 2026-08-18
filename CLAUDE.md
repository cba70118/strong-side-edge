# NFL 2026 — project instructions

## Memory and method

`brain/` holds priors, hypotheses and graded outcomes. **Recall before
analyzing, write back before ending.** Raw data never enters context — query it
through scripts that return a verdict and at most 20 rows, never a table.
**Register a hypothesis with its falsifier BEFORE pulling data.** This project
shares no priors with any other project.

    py -3 brain/brain.py recall --entity <team|market|method>
    py -3 brain/brain.py open --q "..." --hyp "..." --falsifier "..."
    py -3 brain/brain.py status        # ends with the write-back check

A session that asserts nothing, challenges nothing, resolves nothing and grades
nothing was a lookup. Say so rather than dressing it up as analysis.

See `references/analytical-second-brain.md` for the full method and
`brain/playbook.md` for the rules earned so far.

## Products

Two, and they are complementary — neither replaces the other.

- **Weekly brief** (`slate_data.py` + `slate_report.py`) — the narrative.
- **Dashboard** (`dash_*.py`) — the state of the board, governed by
  `references/dashboard-spec.md`: components not prose, hard character caps,
  fixed verdict lexicon PLAY / LEAN / PASS / NO EDGE / STALE. If a UI request
  does not name the artifact class, assume dashboard and build components.

## House rules

- American English throughout. "offense", not "offence".
- Windows: `py -3`, never `python3`. No emoji in `print()` (cp1252).
- stdlib + duckdb + Pillow. No pandas.
- `RUNBOOK.md` holds the verified commands, row counts and substrate traps.
