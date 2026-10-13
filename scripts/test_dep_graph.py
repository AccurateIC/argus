#!/usr/bin/env python3
"""Self-check: python3 scripts/test_dep_graph.py"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from context_retrieval.dep_graph.aggregate import aggregate_folders, folder_of  # noqa: E402
from context_retrieval.dep_graph.naming import pr_graph_dir  # noqa: E402
from context_retrieval.dep_graph.query import hop1_related  # noqa: E402
from context_retrieval.dep_graph.render_html import write_dep_graph_html  # noqa: E402
from context_retrieval.dep_graph.scan import build_file_graph, extract_specifiers  # noqa: E402

ROOT = SCRIPTS / "context_retrieval" / "testdata" / "mini_app"

# Specifier parsing
specs = extract_specifiers('import { x } from "./service";\nconst y = require("lodash");\n')
assert "./service" in specs
assert "lodash" in specs

assert folder_of("apps/api/src/foo.ts", 2) == "apps/api"
assert folder_of("packages/validation/src/a.ts", 2) == "packages/validation"

g = build_file_graph(ROOT, repo="AccurateIC/mini_app", commit="deadbeefcafebabe")
assert "apps/api/src/service.ts" in g.nodes
assert "apps/api/src/controller.ts" in g.nodes
assert g.nodes["apps/api/src/service.test.ts"]["kind"] == "test"
# Full-repo nodes: python / css / html too
assert "apps/api/src/app.py" in g.nodes
assert "apps/web/src/styles.css" in g.nodes
assert "apps/web/index.html" in g.nodes
assert "py" in g.languages and "css" in g.languages and "html" in g.languages

# controller → service
assert "apps/api/src/service.ts" in g.imports.get("apps/api/src/controller.ts", [])
# service imported_by controller + test
imps = set(g.imported_by.get("apps/api/src/service.ts", []))
assert "apps/api/src/controller.ts" in imps
assert "apps/api/src/service.test.ts" in imps

# web → validation (relative)
assert "packages/validation/src/schema.ts" in g.imports.get("apps/web/src/app.ts", [])

# Python relative import
assert "apps/api/src/util.py" in g.imports.get("apps/api/src/app.py", [])
# CSS @import
assert "apps/web/src/theme.css" in g.imports.get("apps/web/src/styles.css", [])
# HTML href/src
assert "apps/web/src/styles.css" in g.imports.get("apps/web/index.html", [])
assert "apps/web/src/app.ts" in g.imports.get("apps/web/index.html", [])

agg = aggregate_folders(g, depth=2)
assert "(root)" in agg["folders"]
assert "apps" in agg["folders"]
assert "apps/api" in agg["folders"]
assert "apps/web" in agg["folders"]
assert "packages/validation" in agg["folders"]
# Tree: root contains apps; apps contains apps/api
assert "apps" in agg["folders"]["(root)"]["children"]
assert "apps/api" in agg["folders"]["apps"]["children"]
assert any(e["kind"] == "contains" for e in agg["tree_edges"])
# Import edge between leaf folders (file dirs)
pairs = {(e["from"], e["to"]) for e in agg["folder_edges"]}
assert ("apps/web/src", "packages/validation/src") in pairs or (
    "apps/web",
    "packages/validation",
) in pairs or any(
    "validation" in b and "web" in a for a, b in pairs
), pairs

related = hop1_related(g, ["apps/api/src/service.ts"])
assert "apps/api/src/controller.ts" in related["dependents"]
assert "apps/api/src/service.test.ts" in related["tests"]

# Naming
d = pr_graph_dir(
    repo="AccurateIC/gbas-web",
    pr_number=48,
    head_sha="a1b2c3d4e5f6",
    title="Fix onboarding workflow",
    cache_root=Path("/tmp/argus-dep-test-cache"),
)
assert d.as_posix().endswith(
    "AccurateIC__gbas-web/pr-48_head-a1b2c3d_fix-onboarding-workflow"
)

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "dep-graph.html"
    write_dep_graph_html(g, out, folder_depth=2, title="mini")
    html = out.read_text(encoding="utf-8")
    assert "(root)" in html
    assert "showTree" in html
    assert "Show files" in html
    assert "DATA" in html
    # single file deliverable
    assert out.is_file()
    assert len(list(Path(tmp).iterdir())) == 1

print("ok")
