# Architecture

Argus is three thin layers around a model. The intelligence is in the **skills**,
**memory**, and **prompts** — all plain text you own — not in the plumbing.

## Layers

### 1. Harness (`.github/workflows/argus-review.yml`, `action.yml`, `scripts/argus_ollama.py`)
Triggered by PR events or an `@argus` mention. Responsibilities:
- check out the repo with full history (needed for base-diff + conflict analysis),
- select backend from `config/argus.yml` (`claude` or `ollama`),
- **claude:** provide tools via Claude Code Action (`gh`, `Read`, `Grep`, `Glob`),
- **ollama:** one-shot review via [`scripts/argus_ollama.py`](../scripts/argus_ollama.py)
  against a LAN/local Ollama (self-hosted runner required).

Stateless. Swap backends with `backend:` in config — skills/prompts/memory stay put.

### 2. Reviewer (`prompts/`, `skills/`, `config/`, `memory/`)
The model, instructed by `prompts/system.md` + `prompts/review.md`, runs the 5-pass
protocol:
1. scope & context, apply `paths` rules;
2. per-skill sweep (loads each enabled `skills/*.md`);
3. memory reconciliation (`memory/*` — drop accepted patterns, enforce conventions);
4. correctness & intent (trace execution, check against the PR description);
5. dedupe, rank by severity, calibrate (cut low-confidence findings).

### 3. Reporter (`prompts/verdict.md`)
Turns findings into inline comments + a summary review, chooses the verdict
(`request-changes` / `comment` / `approve`) per `config` and `docs/governance.md`,
and emits an optional **📝 Memory suggestion**.

## Data flow

```
event ─▶ harness ─▶ [ system + review prompts ]
                     [ config: skills, gate, paths ]
                     [ skills/*.md rubrics ]
                     [ memory: conventions, accepted, knowledge ]
                     [ gh pr diff + repo reads ]
                            │
                            ▼
                     findings → dedupe → rank → verdict
                            │
                            ▼
        inline comments + summary review + memory suggestion
```

## Design principles
- **Text you own.** Skills, memory, prompts, config are Markdown/YAML in your repo,
  reviewed like code. No opaque state.
- **Precision beats recall.** A trusted reviewer says less, and is right. Calibration
  (Pass 4) is a first-class step.
- **Memory over cleverness.** The system improves by *recording decisions*, not by a
  bigger prompt.
- **Governance is explicit.** Approval power is off by default and documented.

## Extending
- **New CI system:** port the harness step; keep `prompts/`, `skills/`, `memory/`,
  `config/` unchanged.
- **New review lens:** add a skill (see [configuration](configuration.md)).
- **Org-wide policy:** vendor `skills/` and `config/` into a shared repo and
  reference it from each project’s workflow.
