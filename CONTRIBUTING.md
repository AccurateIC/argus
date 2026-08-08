# Contributing to Argus

Thanks for helping build a reviewer people actually trust. The highest-leverage
contributions are **new skills**, **sharper prompts**, and **CI adapters**.

## Ways to contribute
- **New skill.** A focused review lens (`skills/<name>.md`) — accessibility, i18n,
  IaC/Terraform, Dockerfile hygiene, ML-data-leakage, prompt-injection, etc. Follow
  the structure in [`docs/configuration.md`](docs/configuration.md#writing-a-skill):
  purpose → checklist → severity guidance → **Don't** (false-positive guardrails).
- **Prompt improvements.** Better calibration, fewer false positives, clearer
  verdicts. Include before/after examples on a real diff.
- **CI adapters.** Ports of the harness to GitLab CI, Bitbucket, Azure Pipelines,
  Buildkite — keep `prompts/`, `skills/`, `memory/`, `config/` untouched.
- **Docs & examples.** Real-world configs, governance case studies, screenshots.

## Bar for a new skill
- It encodes judgment a strong reviewer applies, not generic advice.
- Every checklist item is *specific and falsifiable*.
- It has a **Don't** section so it won't spam false positives.
- It states what's a `blocker` vs a `nit` for that lens.

## Principles (please keep these)
1. **Precision over recall.** We'd rather miss a nit than cry wolf. Trust is the
   product.
2. **Text you own.** Keep intelligence in Markdown skills/prompts/memory, not in
   workflow YAML.
3. **Never weaken review by default.** Approval stays off by default; anything that
   lets a bot clear a human gate must be opt-in and documented in
   [`docs/governance.md`](docs/governance.md).

## Dev loop
Open a PR — Argus reviews itself (dogfooding). Add or update tests/examples for
prompt or skill changes by including a sample diff and the expected findings in the
PR description.

## Code of conduct
Be excellent to each other. Review the code, not the person.
