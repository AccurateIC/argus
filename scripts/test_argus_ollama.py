#!/usr/bin/env python3
"""Self-check: python3 scripts/test_argus_ollama.py

Guards the two paths where a broken review used to look like a clean one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from argus_ollama import (  # noqa: E402
    FINDINGS_JSON_SCHEMA,
    build_review_batches,
    dedupe_findings,
    diff_char_budget,
    diff_stats,
    extract_json,
    filter_diff,
    format_coverage_section,
    format_diff_too_large,
    format_findings_metric,
    format_incomplete_review,
    format_summary,
    is_github_diff_too_large,
    is_review_payload,
    md_table_cell,
    normalize_findings,
    pack_file_batches,
    parse_max_files,
    path_from_file_diff,
    print_argus_review_summary,
    salvage_findings_json,
    split_diff_by_file,
    split_file_diff_by_hunk,
    take_first_n_files,
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

# Budget shrinks with overhead and never goes negative (3 chars/token).
assert diff_char_budget(32768, 4096, 0) == (32768 - 4096) * 3
assert diff_char_budget(32768, 4096, 10_000) == (32768 - 4096) * 3 - 10_000
assert diff_char_budget(4096, 4096, 999) == 0
# 8192/4096 cannot fit the ~24k-char harness prompt; 16384/4096 can plus a small diff.
assert diff_char_budget(8192, 4096, 24_000) == 0
assert diff_char_budget(16384, 4096, 24_000) > 0

# Truncated mid-suggested_fix still yields the complete findings before it.
_truncated = """
{
  "findings": [
    {
      "severity": "major",
      "skill": "correctness",
      "location": "a.ts:1",
      "finding": "complete finding one",
      "suggested_fix": "do the thing"
    },
    {
      "severity": "minor",
      "skill": "correctness",
      "location": "b.ts:2",
      "finding": "cut off mid fix",
      "sugges
"""
_salvaged = salvage_findings_json(_truncated)
assert _salvaged is not None and len(_salvaged["findings"]) == 1, _salvaged
assert _salvaged["findings"][0]["finding"] == "complete finding one"

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

# Newlines / pipes inside finding text must not break the Markdown table.
assert md_table_cell("a\nb | c") == "a b \\| c"
broken_finding = {
    "severity": "major",
    "skill": "correctness",
    "location": "frontend/src/App.tsx:315",
    "finding": "SSE parser splits on '\\n'\nand skips empty lines.",
    "suggested_fix": "use a state\nmachine | library",
}
table_body = format_summary([broken_finding], [], [], "REQUEST CHANGES")
finding_rows = [
    ln for ln in table_body.splitlines() if ln.startswith("| 🟠") or ln.startswith("| 🟡") or ln.startswith("| ⚪") or ln.startswith("| 🔴")
]
assert len(finding_rows) == 1, finding_rows
assert "\\n" in finding_rows[0] or "splits on" in finding_rows[0]
assert "\\| library" in finding_rows[0]
assert finding_rows[0].count("|") >= 5  # leading + 4 cols + trailing

# P1: schema must require findings + severity enum.
assert FINDINGS_JSON_SCHEMA["required"] == ["findings"]
assert "blocker" in FINDINGS_JSON_SCHEMA["properties"]["findings"]["items"]["properties"]["severity"]["enum"]

# P0 helpers: incomplete review message + payload detection.
inc = format_incomplete_review(
    "model returned invalid or non-JSON output",
    hint="Split the PR.",
    preview='{"key": "registration_cert"}',
)
assert "review incomplete" in inc
assert "clean bill of health" in inc
assert "registration_cert" in inc
assert is_review_payload({"findings": []})
assert not is_review_payload({"key": "registration_cert"})
assert not is_review_payload({"findings": "nope"})
assert extract_json('{"findings": []}') == {"findings": []}
try:
    extract_json("not json at all")
    raise AssertionError("expected ValueError")
except ValueError:
    pass

# File window: first N hunks only; comment override. n<=0 keeps all.
many = "".join(
    f"diff --git a/f{i}.py b/f{i}.py\n--- a/f{i}.py\n+++ b/f{i}.py\n@@ -1 +1 @@\n-old\n+new\n"
    for i in range(5)
)
sliced, kept, total = take_first_n_files(many, 2)
assert (kept, total) == (2, 5)
assert sliced.count("diff --git") == 2
assert "f0.py" in sliced and "f1.py" in sliced and "f4.py" not in sliced
assert take_first_n_files(many, 99)[1:] == (5, 5)
assert take_first_n_files(many, 0)[1:] == (5, 5)
assert parse_max_files("@neubodhi check only the first 20 files", 10) == 20
assert parse_max_files("@neubodhi", 20) == 20
assert parse_max_files("@neubodhi", 0) == 0
assert parse_max_files("first 999 files", 20) == 500

# Sequential packing: fit files into context-sized batches.
hunks = split_diff_by_file(many)
# Tiny budget → one file per batch.
tiny = pack_file_batches(hunks, char_budget=80, max_lines=None)
assert len(tiny) == 5, tiny
# Huge budget → single batch with all files.
one = pack_file_batches(hunks, char_budget=100_000, max_lines=None)
assert len(one) == 1 and one[0].count("diff --git") == 5
# Line cap packs by changed-line budget.
by_lines = pack_file_batches(hunks, char_budget=100_000, max_lines=4)
assert len(by_lines) >= 2

# Proof-aware batches carry paths and never drop files from the ledger.
built = build_review_batches(hunks, char_budget=80, max_lines=None)
assert len(built) == 5
assert {p for b in built for p in b["paths"]} == {f"f{i}.py" for i in range(5)}
assert all(not b["truncated"] for b in built)

# Oversized single @@ hunk is marked truncated only when bytes are chopped.
huge_hunk = (
    "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n"
    "@@ -1,1 +1,1 @@\n"
    + ("-x\n+y\n" * 200)
)
parts, trunc_flags = zip(*split_file_diff_by_hunk(huge_hunk, char_budget=120, max_lines=None))
assert any(trunc_flags), trunc_flags
assert path_from_file_diff(huge_hunk) == "big.py"
# Soft line_cap alone must NOT mark truncated if the full hunk fits chars.
soft = split_file_diff_by_hunk(huge_hunk, char_budget=100_000, max_lines=50)
assert len(soft) == 1 and soft[0][1] is False and soft[0][0].rstrip("\n") == huge_hunk.rstrip("\n")

# Multi-hunk file splits across passes without truncation when each @@ fits.
multi = (
    "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
    "@@ -1,1 +1,1 @@\n-a\n+b\n"
    "@@ -10,1 +10,1 @@\n-c\n+d\n"
    "@@ -20,1 +20,1 @@\n-e\n+f\n"
)
mh = split_file_diff_by_hunk(multi, char_budget=90, max_lines=None)
assert len(mh) >= 2, (len(mh), [len(c) for c, _ in mh])
assert all(not t for _, t in mh)

# Dedupe across passes.
assert len(
    dedupe_findings(
        [
            {"severity": "major", "location": "a.py:1", "finding": "bug"},
            {"severity": "major", "location": "a.py:1", "finding": "bug"},
            {"severity": "minor", "location": "a.py:2", "finding": "other"},
        ]
    )
) == 2

cov = format_coverage_section(
    total_files=5,
    intended_paths=["a.py", "b.py"],
    covered_paths={"a.py", "b.py"},
    batch_count=2,
    batch_ok=2,
    truncated_paths=[],
    failed_batches=[],
)
assert "Coverage complete: **yes**" in cov
cov_bad = format_coverage_section(
    total_files=5,
    intended_paths=["a.py", "b.py"],
    covered_paths={"a.py"},
    batch_count=2,
    batch_ok=1,
    truncated_paths=["b.py"],
    failed_batches=["pass 2/2: boom"],
)
assert "Coverage complete: **NO**" in cov_bad

assert format_findings_metric([]) == "Findings=0"
assert format_findings_metric(
    [
        {"severity": "major"},
        {"severity": "major"},
        {"severity": "minor"},
        {"severity": "minor"},
    ]
) == "Findings=4 (2 major, 2 minor)"
# Metrics printer must never raise (observability-only).
print_argus_review_summary(
    pr="55",
    repo="AccurateIC/hrms",
    author="rahul",
    branch="feature/login",
    model="qwen3.6:27b",
    num_ctx=16384,
    input_tokens=8421,
    output_tokens=1247,
    diff_files=6,
    diff_lines=1842,
    duration_s=142,
    findings=[
        {"severity": "major"},
        {"severity": "major"},
        {"severity": "minor"},
        {"severity": "minor"},
    ],
    status="success",
)
print_argus_review_summary(
    pr="1",
    repo="",
    author="",
    branch="",
    model="qwen3.6:27b",
    num_ctx=16384,
    input_tokens=None,
    output_tokens=None,
    diff_files=None,
    diff_lines=None,
    duration_s=None,
    findings=[],
    status="incomplete",
)

print("ok")
