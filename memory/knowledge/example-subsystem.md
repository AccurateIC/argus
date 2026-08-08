# Knowledge: <subsystem name>

> A durable note about a subsystem, past incident, or gotcha. Argus pulls this in
> when a PR touches the relevant area. Delete this example and add your own.

**Area:** `path/to/subsystem/**`

## What to know
- The one paragraph a new reviewer wishes they'd had before reviewing this area.

## Gotchas
- _Example:_ Webhook handlers here are retried at-least-once — every handler must be
  idempotent (dedupe on `event_id`). We had a double-charge incident from this in
  #1450.

## Invariants Argus should defend
- _Example:_ `TenantMembership.status` transitions are one-way to `removed`; a PR
  reactivating a removed row without going through `readmit()` is a finding.
