# Repo Conventions

> Argus enforces these as the local standard. A violation is at least a `minor`
> finding; a violation of a **security** convention is `major`+. Keep each entry
> short, with a *why* and a link where possible. This file ships with examples —
> replace them with your repo's real conventions.

## Architecture & patterns
- _Example:_ New evaluators subclass `BaseEvaluator` and register via
  `EvaluatorFactory.register()`. A new evaluator wired up any other way is a finding.
- _Example:_ All external-vendor integrations go through a `BaseConnector`
  subclass; don't call vendor SDKs directly from views.

## Data access
- _Example:_ Every query on a shared table is scoped by `organization` / `tenant`.
  An unscoped query on a shared table is a `major` security-convention violation.
- _Example:_ Money and credit mutations use `select_for_update()` inside a
  transaction — never read-modify-write.

## API & serialization
- _Example:_ Public serializers may only expose fields listed in
  `PUBLIC_SAFE_FIELDS`. Adding any other field to a public serializer is `major`.
- _Example:_ Enum values are append-only; never rename a value that may exist in the
  DB or a queue.

## Errors & logging
- _Example:_ Never `except: pass`. Log with a correlation id; never log tokens/PII.

## Style
- _Example:_ Match the surrounding file's conventions; no new formatter configs in a
  feature PR.

---
_Delete the examples above and fill this in for your repo. Argus is only as good as
what you teach it here._
