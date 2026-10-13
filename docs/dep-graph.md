# Dependency graph (static flowchart HTML)

Argus builds a **single** static HTML flowchart that shows how folders relate in an app repo: containment tree, inferred pipeline handoff, and top-level uses.

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

Left-to-right diagram with **instant folder drill-down** (no animations):

1. **Header** — “Argus Repository Dependency Graph” plus repo / commit / file counts.
2. **Root card** — repository name, commit, file & folder counts.
3. **Col 1** — every top-level folder/file; **col 2** — children of each (packages, `config/`, `launch/`, …).
4. **Col 3** — one package expanded deeper as an example (others follow the same pattern).
5. **Blue folders · teal files · purple numbered pipeline stages** (when a pipeline is detected).
6. **Solid light arrows** = Contains · **Dashed blue** = Uses/imports · **Purple ↓** = Pipeline order.
7. **Legend bar** at the bottom.
8. **Click any folder** to collapse / expand its children in place (full tree stays on screen). **Expand all** resets.

No CDN — pure SVG + small inline JS in one HTML file.

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

- **Full repo scan:** every non-binary file is indexed (Python, JS/TS, CSS, HTML, JSON, Markdown, Vue, etc.).
- Edges from language-aware refs: JS/TS `import`/`require`, Python `import`/`from`, CSS `@import`/`url()`, HTML `src`/`href`, Path chains, quoted paths.
- Folder shells include binary-only dirs (e.g. `data/` with PDFs) and `.github`.
- Skips junk dirs (`node_modules`, `dist`, `.next`, `.git`, venvs, …) and binaries (images, fonts, archives, …).
- Resolves relative paths and simple `tsconfig` `paths` aliases.
- npm/PyPI packages are external (not expanded into site-packages).
- Graph is stored on the runner only; it does **not** yet feed into the Qwen prompt.
