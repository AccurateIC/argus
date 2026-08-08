<div align="center">

# 🛡️ Argus

### The AI code reviewer that *actually reviews.*

**Argus** is a self-hostable, model-agnostic PR-review agent for GitHub. It reads your diff the way a senior engineer does — across security, data-protection, correctness, performance, tests, and API-compatibility — remembers your codebase's conventions between reviews, and leaves a precise, cited, severity-ranked verdict instead of a rubber stamp.

Named for [Argus Panoptes](https://en.wikipedia.org/wiki/Argus_Panoptes), the hundred-eyed watchman who never slept.

[Quickstart](#-quickstart) · [How it works](#-how-it-works) · [Skills](#-skills) · [Memory](#-memory) · [Governance](docs/governance.md) · [Configuration](docs/configuration.md)

</div>

---

## Why Argus

Most "AI PR review" bots summarize the diff and post a few generic nits. Argus is built on three ideas that make it a reviewer you'd actually trust:

1. **Skills, not vibes.** Review is decomposed into explicit, versioned *skills* — each a focused rubric a specialist would apply (a security lens, a data-protection lens, a concurrency lens…). You enable the ones your repo needs and write your own. Findings are traceable to the skill that raised them.
2. **Memory.** Argus persists what it learns about *your* repo — conventions, accepted patterns, known false-positives, past decisions — and loads it into every review. It gets quieter and sharper over time instead of re-litigating the same nit on every PR.
3. **An honest verdict.** Every finding carries a **severity** (`blocker` / `major` / `minor` / `nit`) and a file:line citation. Argus **requests changes** when it finds a real blocker and **approves** only when it genuinely would sign off — and *never* to merely clear a gate. Whether that approval counts toward your branch protection is *your* governance decision, documented in [`docs/governance.md`](docs/governance.md).

> **Argus is a force-multiplier for human review, not a replacement for it.** It catches the boring 80% so your reviewers spend their attention on the 20% that needs judgment. See [Governance](docs/governance.md) for how to wire it in without weakening branch protection.

---

## ✨ Quickstart

**1. Add the token.** Argus runs on any Claude-compatible backend. Add one repo/org secret:

```bash
# easiest for Claude Code users — a long-lived OAuth token:
claude setup-token
gh secret set CLAUDE_CODE_OAUTH_TOKEN --org <your-org> --visibility all
# …or an API key:
gh secret set ANTHROPIC_API_KEY --repo <owner>/<repo>
```

**2. Drop in the workflow** (`.github/workflows/argus.yml`):

```yaml
name: Argus review
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
  issue_comment:
    types: [created]        # lets anyone type "@argus review" to re-run
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    uses: pdxlab/argus/.github/workflows/argus-review.yml@v1
    secrets: inherit
```

**3. Open a PR.** Argus posts a review within a couple of minutes. That's it.

Prefer to vendor it? Copy [`.github/workflows/argus-review.yml`](.github/workflows/argus-review.yml) and the [`skills/`](skills), [`prompts/`](prompts), [`config/`](config), and [`memory/`](memory) folders into your repo and tune [`config/argus.yml`](config/argus.yml).

---

## 🔭 How it works

```
        PR opened / synced / "@argus review"
                       │
                       ▼
        ┌──────────────────────────────┐
        │  Harness (GitHub Action)      │
        │   • fetch diff + changed files│
        │   • load config/argus.yml     │
        │   • load enabled skills/*.md  │
        │   • load memory/* (conventions,│
        │     accepted patterns, FPs)   │
        └──────────────┬───────────────┘
                       ▼
        ┌──────────────────────────────┐
        │  Reviewer (Claude)            │
        │   for each enabled skill:     │
        │     apply rubric → findings   │
        │   dedupe · rank by severity   │
        │   cross-check vs memory       │
        │   → structured verdict        │
        └──────────────┬───────────────┘
                       ▼
        ┌──────────────────────────────┐
        │  Reporter                     │
        │   • inline comments (file:line)│
        │   • summary review            │
        │   • verdict: request-changes  │
        │     / comment / approve       │
        │   • propose memory updates    │
        └──────────────────────────────┘
```

The full data-flow and extension points are in [`docs/architecture.md`](docs/architecture.md).

---

## 🧠 Skills

Each skill is a small, self-contained rubric in [`skills/`](skills). Ships with:

| Skill | What it hunts for |
|---|---|
| [`security`](skills/security.md) | authz/authn gaps, injection, SSRF, secrets, IDOR, missing tenant-scoping, unsafe deserialization |
| [`data-protection`](skills/data-protection.md) | PII exposure, over-broad querysets, cross-tenant leakage, sensitive logging, retention/consent |
| [`correctness`](skills/correctness.md) | logic bugs, null/empty/off-by-one, error handling, race-y assumptions, "does it do what the PR says" |
| [`concurrency`](skills/concurrency.md) | races, deadlocks, non-atomic read-modify-write, unguarded shared state |
| [`performance`](skills/performance.md) | N+1 queries, unbounded loops/allocations, missing indexes, sync work on hot paths |
| [`api-compat`](skills/api-compat.md) | breaking API/schema changes, migration collisions, client-contract drift |
| [`tests`](skills/tests.md) | is the change covered? are the tests meaningful or tautological? |
| [`observability`](skills/observability.md) | missing/incorrect logs, metrics, traces; silent failure paths |
| [`dependencies`](skills/dependencies.md) | risky new deps, license issues, version pins, supply-chain smell |

Writing your own is a markdown file and one line of config — see [`docs/configuration.md`](docs/configuration.md#writing-a-skill).

---

## 💾 Memory

Argus keeps a durable, human-readable memory in [`memory/`](memory) that it loads into every review:

- **`conventions.md`** — how *this* repo does things (patterns to enforce, patterns that are fine here).
- **`accepted-patterns.md`** — "yes, we know, this is intentional" — kills repeat false-positives.
- **`knowledge/`** — accumulated, curated learnings (past incidents, gotchas, subsystem notes).

Memory is just Markdown in your repo — reviewable, diff-able, and owned by you. Argus can *propose* additions (e.g. "you accepted this pattern 3× — add it to accepted-patterns?") as a suggestion; a human merges it. Full model in [`docs/memory.md`](docs/memory.md).

---

## 🔒 Governance & the "second reviewer" question

Argus renders a genuine verdict. Whether `argus[bot]`'s **approval** is allowed to *satisfy a required review* is a branch-protection setting **you** own — and there are honest, non-footgun ways to wire it (require Argus **plus** a human; require Argus only on low-risk paths; Argus as a required *status check* rather than an approver). We lay out the trade-offs, and the ways this can quietly defeat human review, in [`docs/governance.md`](docs/governance.md). Read it before you let a bot approve your own PRs.

---

## Configuration

Everything is driven by [`config/argus.yml`](config/argus.yml): which skills run, severity gates, path-based rules (`skip:` vendored dirs, `strict:` security-critical dirs), comment budget, and model selection. See [`docs/configuration.md`](docs/configuration.md).

---

## License

[MIT](LICENSE). Use it, fork it, ship it.

## Contributing

New skills, better prompts, adapters for other CI systems — all welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md).
