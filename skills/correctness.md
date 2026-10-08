# Skill: correctness

Does it work, and does it do what the PR says? Trace real execution in the
**language(s) of this PR** — do not assume Python, JavaScript, Go, or any other
single language. Apply language semantics that match the changed files.

## Checklist
- **Intent match:** Compare the diff to the PR description. Silent scope creep or a
  change that doesn't achieve the stated goal is a finding.
- **Edge cases:** null/nil/undefined/None (as appropriate for the language), empty
  collections, zero/negative numbers, first/last iteration, single-element vs many,
  timezone/DST, unicode, very large inputs.
- **Off-by-one & boundaries:** ranges, slicing/indexing, pagination, retries/backoff.
- **Error handling:** swallowed errors, errors logged but not handled, missing
  rollback on failure, partial writes, resource leaks (unclosed handles/connections),
  cleanup/`finally`/`defer`-style correctness — including paths the PR newly opens.
- **Control flow:** unreachable branches, inverted conditions, missing returns,
  fallthrough, early exit that skips cleanup; dead logic after an unconditional
  return/throw/raise (as the language expresses it).
- **Conditions & state:** wrong comparisons, incorrect state transitions,
  assumptions the PR introduces that contradict existing invariants.
- **Loop / iteration termination:** loops or recursive paths that cannot complete on
  realistic inputs; unbounded loops without a clear exit unless intentionally a
  long-running worker/event loop (only flag when termination is genuinely missing
  or wrong for the language).
- **State & idempotency:** operations assumed idempotent that aren't (retried
  webhooks, at-least-once queues); non-deterministic ordering assumed stable.
- **Data & types:** implicit coercion, float equality, overflow, serialization
  round-trips, enum/string drift between layers — using that language's type rules.
- **Contracts / API usage:** callers passing values callees don't handle; nullable
  results used as non-null; new paths that violate invariants asserted elsewhere.
- **Ordering & concurrency:** races, missing synchronization, wrong lock/order
  assumptions when the diff touches shared state.
- **Static hints:** If the prompt includes an "Advanced analysis hints" section,
  treat them as leads — confirm or dismiss with evidence from the diff; do not
  rubber-stamp them.

## Method
Pick the happy path + the two edge cases most likely to occur in production and
narrate them line by line in the language of the changed code. If a bug depends on
caller behavior, open the caller.

## Severity guidance
Wrong result, data loss/corruption, crash on a common path → `blocker`. Bug on an
uncommon-but-real path → `major`. Fragile-but-currently-correct → `minor`.
