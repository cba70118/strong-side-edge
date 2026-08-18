# Playbook

Rules earned from graded outcomes only. Keep under 40 lines.
Delete anything that has not paid.

## Before analyzing
1. `recall` first. If nothing matches, say "blank slate" out loud rather than
   inventing a read.
2. Register the hypothesis and its falsifier BEFORE the query runs. A read
   written after the data is a story about the data.
3. One question, one entity, one market. A broad question returns a table, and
   a table in context becomes description instead of analysis.

## What counts as an answer
4. Verdict first, then the three to five rows that justify it. Never the table.
5. Inconclusive is a real verdict and must stay usable. Most honest answers
   about a 50/50 market are inconclusive.
6. A narrative source may raise a question, never answer one. Move The Line
   quote real numbers sized from single seasons — test them, do not cite them.

## What the record says so far
7. No sides edge. Two independent routes both land near -0.03 partial
   correlation against the close. Sides are price shopping at a matched
   number, nothing else. (p-0004)
8. Center from the market, shape from the model. Passing an aggregate null
   test is not permission to quote a per-game number. (p-0005)
9. Validate at the granularity you will use it. Two models have now been right
   in aggregate and wrong where it mattered. (p-0006)
10. Totals are close to unbiased at 50.4% under over 6,967 games. Any totals
    edge comes from the price or the number, never a directional lean.
    (p-0002)

## Before shipping new math
11. Assert monotonicity on every survival and CDF function across its whole
    domain, including out-of-support inputs. One check would have caught the
    bug that corrupted its own test. (p-0007)
12. Suspect the validation when its result conveniently justifies a
    restriction. (p-0008)
13. Run an independent adversarial review. A test written by the author of the
    code shares the code's blind spots.

## What the football data says
15. Defense is far less stable than offense year over year (0.233 vs 0.331).
    That is a FACT about football, not a license to reshrink the rating — the
    opponent adjustment already discounts defense on its own, and forcing more
    failed out of sample. (p-0012 challenged, h-0003 refuted)
16. Turnovers are noise both across and within seasons. Never let them carry
    weight in a forward rating. (p-0013)
17. Only pass rate over expected (0.459) and offensive success rate (0.404)
    clear 0.40 year over year. Point margin manages 0.384. (p-0014)
18. Reliability caps predictiveness — check split-half before blaming a model
    for a weak year-over-year number. (p-0015)
19. A re-rating splits 0.560 offense / 0.440 defense. Move a team's strength
    level, never its identity of which side it is good at. (p-0018)

## Falsifiers
22. Bound the CHANGE, not the level. A falsifier that caps drift at 0.5 when
    the untouched baseline drifts 0.99 can only ever fail. (p-0021)
23. Know your benchmark's sampling error before reading a delta. The sim's key
    numbers are graded on 510 games: anything under ~3pp is unresolvable.
    (p-0022)

## Before shipping a decomposition
20. When you split one number into parts, assert the parts still rebuild it,
    and check the SIGNS independently. preseason_prior preserved the total
    perfectly and inverted seven defenses. (p-0017)
21. A term that cancels in the linear case is not cosmetic downstream of a
    nonlinear model. The split moved sim totals 9 pts with the net fixed.
    (p-0019)

## Write-back
14. A session ends with a prior asserted, challenged or retired, a hypothesis
    resolved, or a decision graded. Otherwise it was a lookup — log it as one
    and move on.
