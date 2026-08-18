# The Analytical Second Brain

Stored 2026-08-16. Governs HOW this project thinks, not what any one page
looks like. The implementation is `brain/brain.py`; the store is `brain/*.jsonl`
and is project-local, never shared with any other project.

The three failure modes it exists to stop:

1. **Retrieval as thinking.** Once a table is in context, the cheapest next
   token is a description of that table. Take the table out of context and
   description becomes impossible.
2. **Memory of facts instead of memory of judgment.** Data is recomputable,
   conclusions are not. Most systems persist the wrong one.
3. **No falsification loop.** Nothing gets graded, so no belief updates. After
   a season you are not smarter, you are holding more unexamined opinions.

## The test for what gets remembered

**If a script can recompute it, it does not get remembered. If it cannot, it
must be.** Corpus and derived tables never enter context. Ledger, priors and
playbook are the second brain; everything else is a database.

## The loop, no skipping

    Question -> Hypothesis -> Query -> Verdict -> Write-back

Step 2 is the whole trick. Registering the hypothesis and its falsifier before
the data arrives is what makes the analysis falsifiable and stops a story being
written around whatever appeared first.

## The write-back rule

A session ends with a prior asserted, a prior challenged or retired, a
hypothesis resolved, or a decision graded. If none happened it was a lookup,
not analysis. Say so.

## Provenance

`source` is one of graded / data / judgment / narrative. **A narrative-sourced
prior can never be the sole support for a decision.** It can raise a question;
it cannot answer one.

`stance` exists so contradictions are detectable. Two active priors on the same
entity and class with opposing stance always surface together on recall — the
system is not allowed to hand back only the one that agrees with you.

## Decay

Confidence decays toward 0.5, which is no information, never toward zero.
Half-lives: form 3 weeks, personnel 8 weeks, fit and market 1 season,
structural 3 seasons.

## Commands

    py -3 brain/brain.py recall --entity JAX
    py -3 brain/brain.py open --q "..." --hyp "..." --falsifier "..."
    py -3 brain/brain.py resolve h-0001 --verdict confirmed --note "..."
    py -3 brain/brain.py assert --claim "..." --entity X --class structural         --conf 0.7 --source data --stance 1 --evidence "..."
    py -3 brain/brain.py challenge p-0004 --note "..."
    py -3 brain/brain.py review
    py -3 brain/brain.py status
