# Configuration

All behavior is driven by [`config/argus.yml`](../config/argus.yml).

## Keys

| Key | Meaning |
|---|---|
| `backend` | `ollama` (LAN/local via [`scripts/argus_ollama.py`](../scripts/argus_ollama.py)) or `claude` (Claude Code Action). |
| `skills` | Ordered list of enabled skills (each maps to `skills/<name>.md`). |
| `gate` | Lowest severity that triggers `request-changes` (`blocker`/`major`/`minor`). |
| `verdict.allow_approve` | Whether Argus may submit an APPROVE review. Default `false`. See [governance](governance.md). |
| `verdict.never_approve_authors` | Authors whose PRs are never approved (bots). |
| `paths.skip` | Globs excluded from review entirely. |
| `paths.strict` | Globs where every finding is bumped one severity and security/data-protection get extra scrutiny. |
| `limits.max_inline_comments` | Cap on inline comments before the rest roll into the summary. |
| `limits.max_diff_lines` | PRs larger than this get a "please split" note instead of a shallow review. |
| `model` | Claude model when `backend: claude`. |
| `ollama.host` | Ollama base URL (e.g. `http://192.168.10.46:11434`). |
| `ollama.model` | Ollama model tag (e.g. `qwen3.6:27b`). |

## Backends

### `backend: ollama` (default in this fork)
- Workflow runs on a **`self-hosted`** runner that can reach `ollama.host`.
- No Anthropic secret required.
- One-shot review (diff + skills + memory → JSON → `gh pr review`). No Claude-style tool loop.
- Override host/model with env `OLLAMA_HOST` / `OLLAMA_MODEL` if needed.

### `backend: claude`
- Set `backend: claude` in `config/argus.yml`.
- Add repo secret `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`.
- Uses GitHub-hosted `ubuntu-latest` and `anthropics/claude-code-action`.

## Choosing a model
- **Claude / paid:** `claude-sonnet-4-6` (default) or `claude-opus-4-8` for deep reviews.
- **Ollama / local:** prefer a strong coder that fits your GPU (e.g. `qwen3.6:27b` on ~48GB).
- **High PR volume / cost-sensitive:** a faster model; keep Opus for `strict:` paths
  by running two jobs with different configs.

## Writing a skill
A skill is a Markdown rubric. To add one:

1. Create `skills/my-skill.md`. Structure it like the built-ins:
   - a one-line purpose,
   - a **Checklist** of concrete things to look for,
   - **Severity guidance** (what's a blocker vs nit for this lens),
   - a **Don't** section to prevent false positives.
2. Add `- my-skill` to `skills:` in `config/argus.yml`.
3. Open a PR — Argus will review its own new skill.

Good skills are *specific* and *falsifiable*. "Check for bad code" is useless;
"flag a query inside a loop over rows (N+1)" is a skill. Encode the judgment your
best reviewer applies for that lens.

## Per-directory configs
For a monorepo, keep multiple configs (e.g. `config/argus.backend.yml`,
`config/argus.frontend.yml`) and pass `config-path` per job so each area runs the
skills that fit it.
