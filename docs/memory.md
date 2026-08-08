# The Memory Model

Most "AI reviewers" are amnesiac: they review every PR as if they've never seen your
codebase, so they repeat the same false positives forever and never learn what your
team actually cares about. Argus fixes this with a small, human-curated memory that
loads into every review.

## What memory is
Plain Markdown in [`memory/`](../memory), committed to your repo:

- **`conventions.md`** — the local standard Argus enforces.
- **`accepted-patterns.md`** — intentional decisions Argus must not re-flag
  (authoritative).
- **`knowledge/*.md`** — durable subsystem notes, past incidents, invariants.

Because it's in-repo Markdown, memory is: reviewable in a PR, diffable over time,
greppable, and 100% under your control. There is no hidden model state.

## The learning loop
```
   review runs ──▶ Argus flags pattern X ──▶ team says "that's intentional"
        ▲                                             │
        │                                             ▼
   next review honors it   ◀── human merges ──  Argus emits a
   (no more false positive)                     "📝 Memory suggestion"
```

1. **Surface.** When Argus repeatedly flags something the team accepts — or notices
   an undocumented convention — it appends a **📝 Memory suggestion** to its review.
2. **Curate.** A human opens/approves a PR editing `memory/`. Argus never writes to
   memory itself; that keeps a person in the loop and keeps memory trustworthy.
3. **Apply.** Every subsequent review loads the updated memory: accepted patterns are
   skipped, conventions are enforced, subsystem gotchas are considered.

## Why human-curated (not auto-written)
If the bot could silently edit its own rules, memory would drift and a bad entry
could permanently blind the reviewer to a real class of bug. Curation-by-PR means
every change to what Argus ignores or enforces is itself reviewed — including
security carve-outs, which require an explicit rationale and sign-off link.

## Keeping memory healthy
- **Specific > general.** "Allow raw SQL in `analytics/queries/**` — ORM can't do
  window functions, no user input, #1234" beats "raw SQL is fine sometimes."
- **Always a *why* and a *revisit-if*.** An exception without a trigger to reconsider
  it is tech debt.
- **Prune.** Delete entries that no longer hold. Wrong memory is worse than none.
- **Security exceptions are loud.** They live in `accepted-patterns.md` with a
  rationale and sign-off — never as a silent omission.

## Scaling across repos
Share a base `memory/conventions.md` from an org-level repo for standards that apply
everywhere, and keep repo-specific `accepted-patterns.md` / `knowledge/` local.
