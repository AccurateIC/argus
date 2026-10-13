# Dependency graph (interactive HTML)

Argus builds a **single** interactive HTML file that shows how folders and files depend on each other in a TypeScript/JavaScript app repo.

## Automatic on every PR (no manual step)

When the Neubodhi / Argus Ollama workflow runs (PR open, push, or `@neubodhi`):

1. It checks out the **app PR head**.
2. Builds `dep-graph.html` with a clear name on the **self-hosted runner only**.
3. Then runs the normal code review.

Nothing about the graph is posted to the GitHub PR UI (no comment, no check annotation). Find the path in the Actions / runner log line `neubodhi-dep-graph: HTML=...`.

Graph build is `continue-on-error: true` so a graph failure never blocks the review.

### Where files land

```text
~/.cache/argus/dep-graphs/<owner>__<repo>/
  pr-<N>_head-<shortsha>_<title-slug>/
    dep-graph.html
```

Example:

```text
~/.cache/argus/dep-graphs/AccurateIC__gbas-web/
  pr-48_head-a1b2c3d_fix-onboarding-workflow/
    dep-graph.html
```

Each PR / each new head commit gets its own folder.

### How to open in Chrome (from SSH)

Copy to your laptop:

```bash
scp user@runner:~/.cache/argus/dep-graphs/AccurateIC__gbas-web/pr-48_*/dep-graph.html .
```

Or on the runner: `python3 -m http.server 8765` in that folder, and on your laptop `ssh -L 8765:127.0.0.1:8765 user@runner`, then open `http://127.0.0.1:8765/dep-graph.html`.

## What you see in the HTML

1. **Folder view** — folders as nodes; arrows = folder imports folder.
2. **Click a folder** — files + import edges (grey = outside that folder).
3. **← Folders** — back.

No CDN (offline SVG + force layout).

## Manual CLI (optional / debugging only)

```bash
python3 scripts/dep_graph.py build \
  --root /path/to/app \
  --repo AccurateIC/gbas-web \
  --pr 48 \
  --sha "$(git -C /path/to/app rev-parse HEAD)" \
  --title "Fix onboarding workflow"
```

Workflow entrypoint: [`scripts/build_pr_dep_graph.sh`](../scripts/build_pr_dep_graph.sh).

## Notes

- Skips `node_modules`, `dist`, `.next`, etc.
- Resolves relative imports and simple `tsconfig` `paths` aliases.
- npm packages are external (not expanded).
- Graph does **not** yet feed into the Qwen prompt; it is generated alongside the review for you to inspect.
