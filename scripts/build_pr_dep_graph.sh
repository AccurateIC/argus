#!/usr/bin/env bash
# Clone the app PR head and write one interactive dep-graph.html per PR/commit.
# Runs from the Argus harness checkout on the Actions runner (no manual CLI).
set -euo pipefail

PR_NUMBER="${PR_NUMBER:-${ARGUS_PR_NUMBER:-}}"
GH_REPO="${GH_REPO:-${GITHUB_REPOSITORY:-}}"
GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"

if [[ -z "$PR_NUMBER" || -z "$GH_REPO" ]]; then
  echo "neubodhi-dep-graph: skip — PR_NUMBER and GH_REPO are required" >&2
  exit 0
fi
if [[ -z "$GH_TOKEN" ]]; then
  echo "neubodhi-dep-graph: skip — GH_TOKEN missing" >&2
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARGUS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_BASE="${RUNNER_TEMP:-/tmp}"
APP_DIR="${TMP_BASE}/argus-app-${GH_REPO//\//_}-pr-${PR_NUMBER}"

echo "neubodhi-dep-graph: fetching PR #${PR_NUMBER} from ${GH_REPO} …"

META="$(gh pr view "$PR_NUMBER" --repo "$GH_REPO" --json title,headRefOid)"
TITLE="$(printf '%s' "$META" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("title") or "")')"

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"

CLONE_URL="https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git"
git clone --depth 1 "$CLONE_URL" "$APP_DIR"
git -C "$APP_DIR" fetch --depth 1 origin "pull/${PR_NUMBER}/head:pr-head"
git -C "$APP_DIR" checkout -q pr-head
SHA="$(git -C "$APP_DIR" rev-parse HEAD)"

echo "neubodhi-dep-graph: building graph at ${SHA:0:7} …"
python3 "$ARGUS_ROOT/scripts/dep_graph.py" build \
  --root "$APP_DIR" \
  --repo "$GH_REPO" \
  --pr "$PR_NUMBER" \
  --sha "$SHA" \
  --title "$TITLE"

OUT_HTML="$(
  PR_NUMBER="$PR_NUMBER" GH_REPO="$GH_REPO" SHA="$SHA" TITLE="$TITLE" \
  ARGUS_ROOT="$ARGUS_ROOT" python3 - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["ARGUS_ROOT"]) / "scripts"))
from context_retrieval.dep_graph.naming import pr_graph_dir

p = pr_graph_dir(
    repo=os.environ["GH_REPO"],
    pr_number=os.environ["PR_NUMBER"],
    head_sha=os.environ["SHA"],
    title=os.environ.get("TITLE") or "",
)
print(p / "dep-graph.html")
PY
)"

echo "neubodhi-dep-graph: HTML=${OUT_HTML}"
echo "neubodhi-dep-graph: stored locally only (not posted to GitHub UI)"
if [[ -n "${GITHUB_ENV:-}" ]]; then
  echo "DEP_GRAPH_HTML=${OUT_HTML}" >> "$GITHUB_ENV"
fi

echo "neubodhi-dep-graph: done"
