#!/usr/bin/env python3
"""Self-check: python3 scripts/test_argus_ollama.py

Guards the two paths where a broken review used to look like a clean one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from argus_ollama import (  # noqa: E402
    diff_char_budget,
    filter_diff,
    format_summary,
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

print("ok")
