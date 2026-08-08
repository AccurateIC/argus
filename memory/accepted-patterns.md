# Accepted Patterns

> **Authoritative.** Argus must NOT re-flag anything listed here — these are known,
> intentional decisions in this repo. Each entry needs a rationale and, ideally, a
> link to the discussion, so the exception is auditable. Security exceptions require
> an explicit sign-off reference.

Format:

```
### <short name of the pattern>
- **Where:** <path/glob>
- **Why it's fine:** <rationale>
- **Decided:** <date> · <PR/issue link> · <who>
- **Revisit if:** <condition that would make this no longer OK>
```

## Examples (replace with real entries)

### Raw SQL in analytics queries
- **Where:** `analytics/queries/**`
- **Why it's fine:** window/rollup queries the ORM can't express; inputs are
  server-side constants, no user interpolation.
- **Decided:** 2026-06-14 · #1234 · @lead
- **Revisit if:** any of these queries starts taking user input.

### Broad `select` in the admin export
- **Where:** `admin/export.py`
- **Why it's fine:** admin-only, permission-gated, intentionally returns all
  columns for the ops CSV.
- **Decided:** 2026-05-02 · #987 · @security
- **Revisit if:** the export is exposed to non-admin roles.

---
_Empty by default is fine. Add entries as Argus surfaces intentional patterns it
keeps flagging._
