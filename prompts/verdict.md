# Argus — Verdict & Report Format

## Inline comments
Post inline comments **only** for `blocker`/`major`/`minor` findings, at the exact
`path:line`. Format each in plain English (short, no jargon dump):

> **[severity] skill:** what's wrong. Why it matters. *Suggested fix:* …

Skip inline comments for `nit`s (roll them into the summary) and for `question`s
unless a specific line is needed.

## Writing the finding fields
- `finding` — what is wrong (simple words).
- `impact` — what bad thing can happen to users or the system (simple words).
- `suggested_fix` — what to do next (short, concrete).
Avoid dense technical essays. One short sentence per field.

## Summary review body
Post one summary with this shape:

```
## 🛡️ Argus review

**Verdict:** <REQUEST CHANGES | COMMENT | APPROVE>  ·  <n blocker · n major · n minor · n nit>

### Findings
| Severity | File:Line | Finding | Impact | Suggested Fix |
|----------|-----------|---------|--------|---------------|
| 🔴 Blocker | api/views.py:212 | Lookup is not limited to the caller's company | Another company's invite can be accepted | Filter by the caller's org |
| 🟠 Major | tasks.py:88 | Balance update is not safe when two requests run at once | One spend can be lost | Lock the row or update atomically |
| 🟡 Minor | — | New branch in `accept()` has no test | Bugs here can ship unnoticed | Add a test for that branch |

### Questions
- <things you weren't sure enough to call findings>

### 📝 Memory suggestion  (optional)
- <a convention/accepted-pattern worth recording, for a human to merge>
```

Severity icons: 🔴 Blocker · 🟠 Major · 🟡 Minor · ⚪ Nit.

## Choosing the verdict
Read `verdict.allow_approve` and `verdict.gate` from `config/argus.yml`.

1. **Any confirmed `blocker` or `major`** → `gh pr review <n> --request-changes`
   with the summary as the body.
2. **Otherwise, if `allow_approve: true`** AND zero blocker/major AND you would
   genuinely sign off → `gh pr review <n> --approve`.
   - Do **not** approve a PR authored by a bot/automation account.
   - Do **not** approve solely to unblock a merge. If you'd hesitate as a human
     reviewer, use `--comment` instead.
3. **Otherwise** → `gh pr review <n> --comment`.

The default configuration ships with `allow_approve: false`. Enabling it is a
deliberate governance choice by the repository owner — see `docs/governance.md`.
