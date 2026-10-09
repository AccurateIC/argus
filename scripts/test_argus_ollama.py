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
    classify_previous_findings,
    dedupe_findings,
    diff_char_budget,
    diff_stats,
    extract_json,
    filter_diff,
    findings_to_ledger,
    format_coverage_section,
    format_diff_too_large,
    format_findings_metric,
    format_incomplete_review,
    format_pass_summary,
    format_summary,
    is_github_diff_too_large,
    is_review_payload,
    md_table_cell,
    normalize_findings,
    pack_file_batches,
    parse_findings_marker,
    parse_max_files,
    path_from_file_diff,
    path_from_location,
    print_argus_review_summary,
    salvage_findings_json,
    serialize_findings_marker,
    split_diff_by_file,
    subtract_matching_findings,
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
assert "incomplete" in body.lower() or "unreviewed" in body.lower(), body
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

# Whole-file rule: never put half a file in pass 1 and half in pass 2.
huge_hunk = (
    "diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n"
    "@@ -1,1 +1,1 @@\n"
    + ("-x\n+y\n" * 200)
)
assert path_from_file_diff(huge_hunk) == "big.py"
# File alone over char budget → one pass, truncated (not multi-pass hunk split).
alone = build_review_batches([huge_hunk], char_budget=120, max_lines=None)
assert len(alone) == 1 and alone[0]["truncated"] is True and alone[0]["paths"] == ["big.py"]
# Soft line_cap alone → still one whole-file pass, not truncated if chars fit.
soft_b = build_review_batches([huge_hunk], char_budget=100_000, max_lines=50)
assert len(soft_b) == 1 and soft_b[0]["truncated"] is False

# Multi-hunk file stays in ONE pass (whole file), never split across passes.
multi = (
    "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n"
    "@@ -1,1 +1,1 @@\n-a\n+b\n"
    "@@ -10,1 +10,1 @@\n-c\n+d\n"
    "@@ -20,1 +20,1 @@\n-e\n+f\n"
)
# Small leftover budget with another file first → m.py moves wholly to pass 2.
small = (
    "diff --git a/s.py b/s.py\n--- a/s.py\n+++ b/s.py\n"
    "@@ -1 +1 @@\n-o\n+n\n"
)
packed = build_review_batches(
    [small, multi], char_budget=len(small) + 30, max_lines=None
)
assert len(packed) >= 2
assert packed[0]["paths"] == ["s.py"]
assert "m.py" in packed[1]["paths"]
assert all(p.count("diff --git") == len(p_paths) for p, p_paths in
           ((b["diff"], b["paths"]) for b in packed))

pass_md = format_pass_summary(
    pass_i=1,
    pass_n=3,
    paths=["a.py"],
    findings=[{"severity": "minor", "skill": "correctness", "location": "a.py:1",
               "finding": "x", "suggested_fix": ""}],
    questions=[],
)
assert "Pass 1 of 3" in pass_md and "a.py" in pass_md

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

# Same-PR follow-up ledger: serialize ↔ parse round-trip.
assert path_from_location("api/invites.py:212") == "api/invites.py"
assert path_from_location("README.md") == "README.md"
_ledger_src = [
    {
        "severity": "major",
        "skill": "security",
        "location": "api/invites.py:212",
        "finding": "Invite lookup not scoped to the caller's org",
        "suggested_fix": "filter by org_id",
    }
]
_marker = serialize_findings_marker(findings_to_ledger(_ledger_src))
assert "<!-- neubodhi-findings" in _marker
_parsed = parse_findings_marker("## review\n\n" + _marker + "\n")
assert _parsed is not None and len(_parsed) == 1
assert _parsed[0]["path"] == "api/invites.py"
assert "scoped" in _parsed[0]["finding"]

# Hidden marker is appended only when persist_findings is set and review is complete.
_with_persist = format_summary(
    _ledger_src, [], [], "COMMENT", persist_findings=_ledger_src, incomplete=False
)
assert "<!-- neubodhi-findings" in _with_persist
_no_persist_incomplete = format_summary(
    _ledger_src, [], [], "COMMENT", persist_findings=_ledger_src, incomplete=True
)
assert "<!-- neubodhi-findings" not in _no_persist_incomplete

# Classify: file gone from PR diff → resolved; uncovered path → not_rechecked.
_prev = findings_to_ledger(
    [
        {
            "severity": "major",
            "skill": "security",
            "location": "gone.py:1",
            "finding": "old bug in gone file",
            "suggested_fix": "",
        },
        {
            "severity": "major",
            "skill": "security",
            "location": "skip_me.py:2",
            "finding": "other bug still maybe",
            "suggested_fix": "",
        },
    ]
)
_full = (
    "diff --git a/skip_me.py b/skip_me.py\n--- a/skip_me.py\n+++ b/skip_me.py\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)
_buckets = classify_previous_findings(
    _prev,
    full_diff=_full,
    covered_paths=set(),  # skip_me not covered this run
    comments_blob="",
    current_findings=[],
    use_model_recheck=False,
)
assert len(_buckets["resolved"]) == 1 and _buckets["resolved"][0]["path"] == "gone.py"
assert len(_buckets["not_rechecked"]) == 1 and _buckets["not_rechecked"][0]["path"] == "skip_me.py"

# Covered + matching current finding → still_open via heuristic.
_prev2 = findings_to_ledger(
    [
        {
            "severity": "major",
            "skill": "correctness",
            "location": "a.py:1",
            "finding": "Balance update is not atomic",
            "suggested_fix": "",
        }
    ]
)
_full2 = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+Balance update race\n"
_buckets2 = classify_previous_findings(
    _prev2,
    full_diff=_full2,
    covered_paths={"a.py"},
    comments_blob="",
    current_findings=[
        {
            "severity": "major",
            "skill": "correctness",
            "location": "a.py:3",
            "finding": "Balance update is not atomic under concurrency",
            "suggested_fix": "",
        }
    ],
    use_model_recheck=False,
)
assert len(_buckets2["still_open"]) == 1, _buckets2

# New findings omit ones already listed as still open.
_new_only = subtract_matching_findings(
    [
        {
            "severity": "major",
            "skill": "correctness",
            "location": "a.py:3",
            "finding": "Balance update is not atomic under concurrency",
            "suggested_fix": "",
        },
        {
            "severity": "minor",
            "skill": "tests",
            "location": "b.py:1",
            "finding": "missing test for expiry",
            "suggested_fix": "",
        },
    ],
    _buckets2["still_open"],
)
assert len(_new_only) == 1 and "expiry" in _new_only[0]["finding"]

_follow_body = format_summary(
    _new_only,
    [],
    [],
    "COMMENT",
    followup={
        "resolved": _buckets["resolved"],
        "still_open": _buckets2["still_open"],
        "not_rechecked": [],
        "waived": [],
        "new": _new_only,
    },
    persist_findings=_new_only,
)
assert "Resolved since last review" in _follow_body
assert "Still open" in _follow_body
assert "New findings" in _follow_body
assert "Open findings (still open + new)" in _follow_body

print("ok")
