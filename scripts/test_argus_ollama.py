#!/usr/bin/env python3
"""Self-check: python3 scripts/test_argus_ollama.py

Guards the two paths where a broken review used to look like a clean one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from argus_ollama import (  # noqa: E402
    diff_char_budget,
    diff_stats,
    filter_diff,
    format_diff_too_large,
    format_summary,
    is_github_diff_too_large,
    normalize_findings,
)

# A model that renames the text key must not be silently reduced to "no findings".
findings, dropped = normalize_findings(
    {"findings": [{"severity": "major", "location": "a.py:1", "description": "unscoped query"}]}
)
assert len(findings) == 1, findings
assert findings[0]["finding"] == "unscoped query"
assert dropped == 0

# Garbage entries are counted, not swallowed.
findings, dropped = normalize_findings({"findings": [{"severity": "major"}, "junk"]})
assert findings == []
assert dropped == 2, dropped

# Empty findings + a warning must not read as clean.
body = format_summary([], [], [], "COMMENT", ["model returned no usable findings"])
assert "Review incomplete" in body, body
assert format_summary([], [], [], "COMMENT").count("No findings.") == 1

# Budget shrinks with overhead and never goes negative.
assert diff_char_budget(32768, 4096, 0) == (32768 - 4096) * 4
assert diff_char_budget(32768, 4096, 10_000) == (32768 - 4096) * 4 - 10_000
assert diff_char_budget(4096, 4096, 999) == 0

# A new file whose only changed path is skipped leaves an empty diff, not a pass.
new_file_diff = "diff --git a/dist/app.js b/dist/app.js\nnew file mode 100644\n+var a = 1;\n"
assert filter_diff(new_file_diff, ["**/dist/**"]).strip() == ""
assert filter_diff(new_file_diff, ["**/vendor/**"]).strip() != ""

sample_diff = """\
diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+alpha
+beta
diff --git a/old.py b/old.py
--- a/old.py
+++ b/old.py
@@ -1,3 +1,2 @@
-removed
 kept
+added
"""
assert diff_stats(sample_diff) == {
    "files": 2,
    "additions": 3,
    "deletions": 1,
    "new_files": 1,
    "total": 4,
}

too_large = format_diff_too_large(
    {"files": 47, "additions": 3180, "deletions": 1420, "new_files": 12, "total": 4600},
    8000,
)
assert "Files changed | 47" in too_large
assert "+3,180" in too_large
assert "Total diff lines | 4,600 (limit: 8,000)" in too_large

# GitHub API 406 path: stats from PR metadata, note about 20k cap.
capped = format_diff_too_large(
    {
        "files": 120,
        "additions": 15000,
        "deletions": 8000,
        "new_files": 30,
        "total": 23000,
        "github_capped": True,
    },
    8000,
    note="GitHub could not return the full unified diff (HTTP 406 — over ~20,000 lines).",
)
assert "GitHub API cap" in capped
assert "HTTP 406" in capped
assert is_github_diff_too_large(
    "could not find pull request diff: HTTP 406: Sorry, the diff exceeded "
    "the maximum number of lines (20000)\nPullRequest.diff too_large"
)
assert not is_github_diff_too_large("HTTP 404: Not Found")

print("ok")
