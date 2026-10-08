#!/usr/bin/env python3
"""Neubodhi Ollama harness — one-shot PR review via a local/LAN Ollama server.

Reuses prompts/, skills/, memory/, config/argus.yml. Posts a summary review with
`gh`. Not a full Claude Code agent (no Read/Grep tool loop).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Argus log timestamps only (does not change system timezone).
_IST = ZoneInfo("Asia/Kolkata")


def _ist_stamp() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S IST")

ROOT = Path(os.environ.get("GITHUB_WORKSPACE") or Path(__file__).resolve().parents[1])
CONFIG_PATH = Path(os.environ.get("ARGUS_CONFIG", ROOT / "config" / "argus.yml"))
PR_NUMBER = os.environ.get("PR_NUMBER") or os.environ.get("ARGUS_PR_NUMBER")
SEV_RANK = {"blocker": 0, "major": 1, "minor": 2, "nit": 3}
SEV_ICON = {"blocker": "🔴", "major": "🟠", "minor": "🟡", "nit": "⚪"}
GATE_RANK = {"blocker": 0, "major": 1, "minor": 2}
# Local models drift off the schema; accept the usual synonyms rather than dropping
# the whole finding.
FINDING_TEXT_KEYS = ("finding", "description", "message", "issue", "detail", "comment")
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "65536"))
NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", "8192"))


def die(msg: str, code: int = 1) -> None:
    print(f"neubodhi-ollama: {msg}", file=sys.stderr)
    raise SystemExit(code)


def run(cmd: list[str], check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        die(f"$ {' '.join(cmd)}\n{r.stderr or r.stdout}")
    return r.stdout


def parse_simple_yaml(text: str) -> dict:
    """Minimal YAML subset reader for argus.yml (stdlib only)."""
    cfg: dict = {
        "backend": "ollama",
        "skills": [],
        "gate": "major",
        "model": "claude-sonnet-4-6",
        "verdict": {"allow_approve": False, "never_approve_authors": []},
        "limits": {
            "max_diff_lines": 8000,
            "max_inline_comments": 15,
            # 0 = review every file via sequential context-sized batches.
            "max_files": 0,
        },
        "ollama": {"host": "http://127.0.0.1:11434", "model": "qwen3.6:27b"},
        "paths": {"skip": [], "strict": []},
        "advanced_findings": {
            "enabled": False,
            "python": {"ruff": False, "ast": False},
        },
    }
    section: str | None = None
    list_key: str | None = None
    subsection: str | None = None  # nested dict under a section (e.g. advanced_findings.python)

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0 and line.endswith(":") and " " not in line[:-1]:
            section = line[:-1]
            list_key = None
            subsection = None
            if section in ("skills",):
                cfg[section] = []
                list_key = section
                section = None
            elif section not in cfg or not isinstance(cfg.get(section), dict):
                if section in ("verdict", "limits", "ollama", "paths", "advanced_findings"):
                    cfg.setdefault(section, {})
            continue
        if indent == 0 and ":" in line:
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in ("backend", "gate", "model", "version"):
                if k != "version":
                    cfg[k] = v
            section = None
            list_key = None
            subsection = None
            continue
        if list_key == "skills" and line.startswith("- "):
            cfg["skills"].append(line[2:].strip().strip('"').strip("'"))
            continue
        if section and indent >= 2:
            if line.startswith("- "):
                item = line[2:].strip().strip('"').strip("'")
                if section == "verdict" and list_key == "never_approve_authors":
                    cfg["verdict"].setdefault("never_approve_authors", []).append(item)
                elif section == "paths" and list_key in ("skip", "strict"):
                    cfg["paths"].setdefault(list_key, []).append(item)
                continue
            if line.endswith(":") and not line.startswith("-"):
                list_key = line[:-1].strip()
                if section == "paths" and list_key in ("skip", "strict"):
                    cfg["paths"][list_key] = []
                    subsection = None
                elif section == "verdict" and list_key == "never_approve_authors":
                    cfg["verdict"][list_key] = []
                    subsection = None
                elif section == "advanced_findings":
                    # Nested language block: python:, javascript:, …
                    subsection = list_key
                    cfg[section].setdefault(subsection, {})
                    list_key = None
                else:
                    list_key = None
                    subsection = None
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                list_key = None
                if v.lower() in ("true", "false"):
                    val: object = v.lower() == "true"
                elif v.isdigit():
                    val = int(v)
                else:
                    val = v
                if section in cfg and isinstance(cfg[section], dict):
                    if (
                        subsection
                        and indent >= 4
                        and isinstance(cfg[section].get(subsection), dict)
                    ):
                        cfg[section][subsection][k] = val
                    else:
                        cfg[section][k] = val
                        subsection = None
    return cfg


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def load_skills(names: list[str]) -> str:
    chunks = []
    for name in names:
        p = ROOT / "skills" / f"{name}.md"
        body = read_text(p)
        if body:
            chunks.append(f"## Skill: {name}\n\n{body}")
        else:
            print(f"neubodhi-ollama: warning: missing skill {p}", file=sys.stderr)
    return "\n\n---\n\n".join(chunks)


def load_memory() -> str:
    parts = []
    for rel in ("memory/conventions.md", "memory/accepted-patterns.md"):
        body = read_text(ROOT / rel)
        if body:
            parts.append(f"### {rel}\n\n{body}")
    knowledge = ROOT / "memory" / "knowledge"
    if knowledge.is_dir():
        for p in sorted(knowledge.glob("*.md")):
            body = read_text(p)
            if body:
                parts.append(f"### memory/knowledge/{p.name}\n\n{body}")
    return "\n\n".join(parts)


# ponytail: substring match on login — add bot names if your org renames the app user
_SKIP_COMMENT_AUTHORS = ("[bot]", "dependabot", "renovate")


def load_pr_comments(pr: str, limit: int = 15, max_chars: int = 8000) -> str:
    """Recent human PR conversation comments (issue comments on the PR thread)."""
    repo = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY") or ""
    if not repo:
        return ""
    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{pr}/comments"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return ""
    try:
        items = json.loads(r.stdout)
    except json.JSONDecodeError:
        return ""
    if not isinstance(items, list):
        return ""

    trigger = (os.environ.get("PR_COMMENT") or "").strip()
    lines: list[str] = []
    if trigger:
        lines.append(f"- _(trigger)_: {trigger[:1500]}")

    for c in items[-limit:]:
        login = ((c.get("user") or {}).get("login") or "").lower()
        if any(s in login for s in _SKIP_COMMENT_AUTHORS):
            continue
        body = (c.get("body") or "").strip()
        if not body:
            continue
        lines.append(f"- **{login}**: {body[:1500]}")

    blob = "\n".join(lines)
    return blob[:max_chars]


_WAIVER_MARKERS = (
    "intentional",
    "kindly ignore",
    "please ignore",
    "do not flag",
    "don't flag",
    "ignore this",
    "ignore the",
)


def extract_author_waivers(comments_blob: str) -> list[str]:
    waivers: list[str] = []
    for line in comments_blob.splitlines():
        lower = line.lower()
        if not any(m in lower for m in _WAIVER_MARKERS):
            continue
        text = re.sub(r"^- \*\*[^*]+\*\*: ", "", line).strip()
        text = re.sub(r"^- _\(trigger\)_: ", "", text).strip()
        if text:
            waivers.append(text)
    return waivers


def finding_waived_by_author(finding: dict, waivers: list[str]) -> bool:
    """True when a PR comment explicitly waived this topic."""
    fl = finding.get("finding", "").lower()
    loc_file = (finding.get("location") or "").split(":")[0].lower()
    for w in waivers:
        wl = w.lower()
        for quoted in re.findall(r'"([^"]+)"', w):
            if quoted.lower() in fl or quoted.lower() in finding.get("finding", ""):
                return True
        if loc_file and loc_file in wl:
            return True
        # ponytail: 3+ shared tokens of length 5+ — coarse topic match for waivers
        w_tokens = set(re.findall(r"[a-z]{5,}", wl))
        f_tokens = set(re.findall(r"[a-z]{5,}", fl))
        if len(w_tokens & f_tokens) >= 3:
            return True
    return False


def drop_waived_findings(
    findings: list[dict], comments_blob: str
) -> tuple[list[dict], list[str]]:
    waivers = extract_author_waivers(comments_blob)
    if not waivers:
        return findings, []
    kept: list[dict] = []
    notes: list[str] = []
    for f in findings:
        if finding_waived_by_author(f, waivers):
            notes.append(f"Author waived: {f.get('location', '—')}")
            print(
                f"neubodhi-ollama: dropped waived finding at {f.get('location')}",
                file=sys.stderr,
            )
        else:
            kept.append(f)
    return kept, notes


def diff_stats(diff: str) -> dict:
    """Count changed lines in a unified diff (after paths.skip), excluding +++/--- headers."""
    files = new_files = adds = dels = 0
    for ln in diff.splitlines():
        if ln.startswith("diff --git "):
            files += 1
        elif ln.startswith("new file mode"):
            new_files += 1
        elif ln.startswith("+++") or ln.startswith("---"):
            continue
        elif ln.startswith("+"):
            adds += 1
        elif ln.startswith("-"):
            dels += 1
    return {
        "files": files,
        "additions": adds,
        "deletions": dels,
        "new_files": new_files,
        "total": adds + dels,
    }


def format_diff_too_large(stats: dict, max_diff: int, *, note: str = "") -> str:
    total = stats["total"]
    limit_cell = (
        f"{total:,}+ (GitHub API cap; Neubodhi limit: {max_diff:,})"
        if stats.get("github_capped")
        else f"{total:,} (limit: {max_diff:,})"
    )
    body = (
        "## 🛡️ Neubodhi review\n\n"
        "**Verdict:** COMMENT · diff too large\n\n"
        "| Metric | Count |\n"
        "|--------|------:|\n"
        f"| Files changed | {stats['files']} |\n"
        f"| Lines added | +{stats['additions']:,} |\n"
        f"| Lines deleted | −{stats['deletions']:,} |\n"
        f"| New files | {stats['new_files']} |\n"
        f"| Total diff lines | {limit_cell} |\n\n"
    )
    if note:
        body += f"{note}\n\n"
    body += "Please split this PR so Neubodhi can review it properly.\n"
    return body


def is_github_diff_too_large(stderr: str) -> bool:
    s = (stderr or "").lower()
    return (
        "http 406" in s
        or "diff exceeded the maximum" in s
        or "pullrequest.diff too_large" in s
        or "too_large" in s
    )


def pr_stats_from_api(pr: str) -> dict:
    """Stats when `gh pr diff` is rejected (GitHub ~20k line cap)."""
    raw = run(
        [
            "gh",
            "pr",
            "view",
            pr,
            "--json",
            "additions,deletions,changedFiles,files",
        ]
    )
    data = json.loads(raw)
    files = data.get("files") or []
    new_files = sum(
        1
        for f in files
        if str(f.get("changeType") or "").upper() in ("ADDED", "ADD", "NEW")
    )
    adds = int(data.get("additions") or 0)
    dels = int(data.get("deletions") or 0)
    nfiles = int(data.get("changedFiles") or len(files) or 0)
    return {
        "files": nfiles,
        "additions": adds,
        "deletions": dels,
        "new_files": new_files,
        "total": adds + dels,
        "github_capped": True,
    }


def path_skipped(path: str, globs: list[str]) -> bool:
    # ponytail: fnmatch-style ** globs only — upgrade to pathspec if rules get fancy
    from fnmatch import fnmatch

    path = path.lstrip("./")
    for g in globs:
        g = g.strip()
        if fnmatch(path, g) or fnmatch(path, g.lstrip("/")):
            return True
        # also match basename-ish patterns
        if "**/" in g and fnmatch(path, g.split("**/", 1)[-1]):
            return True
    return False


def filter_diff(diff: str, skip: list[str]) -> str:
    if not skip:
        return diff
    out: list[str] = []
    keep = True
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            # diff --git a/foo b/foo
            m = re.search(r" b/(.+)$", line)
            path = m.group(1) if m else ""
            keep = not path_skipped(path, skip)
        if keep:
            out.append(line)
    return "\n".join(out)


def split_diff_by_file(diff: str) -> list[str]:
    """Split a unified diff into one string per `diff --git` file hunk."""
    parts: list[str] = []
    buf: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git ") and buf:
            parts.append("\n".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf and any(ln.strip() for ln in buf):
        parts.append("\n".join(buf))
    return parts


def take_first_n_files(diff: str, n: int) -> tuple[str, int, int]:
    """Keep only the first n file hunks. Returns (sliced, kept, total).

    ``n <= 0`` means unlimited (keep every file hunk).
    """
    parts = split_diff_by_file(diff)
    total = len(parts)
    if n <= 0 or total <= n:
        return diff, total, total
    kept = parts[:n]
    return "\n".join(kept) + "\n", n, total


def path_from_file_diff(file_diff: str) -> str:
    """Best-effort path from a `diff --git` hunk (prefers b/ path)."""
    for line in file_diff.splitlines():
        if line.startswith("diff --git "):
            m = re.search(r" b/(.+)$", line)
            if m:
                return m.group(1).strip()
            parts = line.split()
            if len(parts) >= 4:
                return parts[3].removeprefix("b/").strip()
        if line.startswith("+++ b/"):
            return line[6:].strip()
    return ""


def split_file_diff_by_hunk(
    file_diff: str,
    *,
    char_budget: int,
    max_lines: int | None,
) -> list[tuple[str, bool]]:
    """Split one file's unified diff into context-fitting pieces.

    Returns list of ``(chunk, truncated)``. ``truncated=True`` only when a single
    ``@@`` hunk still exceeds the budget (cannot review without chopping).
    """
    budget = max(1, char_budget)
    line_cap = max_lines if (max_lines is not None and max_lines > 0) else None
    lines = file_diff.splitlines()
    header: list[str] = []
    hunks: list[list[str]] = []
    cur: list[str] | None = None
    for line in lines:
        if line.startswith("@@"):
            if cur is not None:
                hunks.append(cur)
            cur = [line]
        elif cur is not None:
            cur.append(line)
        else:
            header.append(line)
    if cur is not None:
        hunks.append(cur)
    if not hunks:
        body = file_diff if file_diff.endswith("\n") else file_diff + "\n"
        if len(body) <= budget and (
            line_cap is None or int(diff_stats(body).get("total") or 0) <= line_cap
        ):
            return [(body, False)]
        return [(body[:budget], True)]

    header_text = "\n".join(header) + ("\n" if header else "")
    header_len = len(header_text)
    out: list[tuple[str, bool]] = []
    pack: list[str] = []
    pack_chars = header_len
    pack_lines = 0

    def flush_pack() -> None:
        nonlocal pack, pack_chars, pack_lines
        if not pack:
            return
        chunk = header_text + "\n".join(pack)
        if not chunk.endswith("\n"):
            chunk += "\n"
        out.append((chunk, False))
        pack = []
        pack_chars = header_len
        pack_lines = 0

    for hk in hunks:
        piece = "\n".join(hk)
        p_chars = len(piece) + 1
        p_lines = int(diff_stats(piece).get("total") or 0)
        alone = header_len + p_chars > budget or (
            line_cap is not None and p_lines > line_cap
        )
        if alone:
            flush_pack()
            raw = header_text + piece
            if not raw.endswith("\n"):
                raw += "\n"
            # truncated=True only when we actually chop bytes. Soft line_cap
            # overflow that still fits char budget is sent whole as its own pass.
            if len(raw) > budget:
                out.append((raw[:budget], True))
            else:
                out.append((raw, False))
            continue
        if pack and (
            pack_chars + p_chars > budget
            or (line_cap is not None and pack_lines + p_lines > line_cap)
        ):
            flush_pack()
        pack.append(piece)
        pack_chars += p_chars
        pack_lines += p_lines
    flush_pack()
    return out


def pack_file_batches(
    file_hunks: list[str],
    *,
    char_budget: int,
    max_lines: int | None,
) -> list[str]:
    """Pack file hunks into sequential batches that fit the model window.

    Oversized files are split on ``@@`` hunk boundaries when possible.
    Backward-compatible: returns joined diff strings only.
    """
    return [b["diff"] for b in build_review_batches(file_hunks, char_budget=char_budget, max_lines=max_lines)]


def build_review_batches(
    file_hunks: list[str],
    *,
    char_budget: int,
    max_lines: int | None,
) -> list[dict]:
    """Build proof-aware review batches.

    Each batch dict:
      ``diff`` (str), ``paths`` (list[str]), ``truncated`` (bool)
    Every input file path appears in ≥1 batch (possibly hunk-split).
    """
    if not file_hunks:
        return []
    budget = max(1, char_budget)
    line_cap = max_lines if (max_lines is not None and max_lines > 0) else None

    # Flatten to atomic units that each fit (or are marked truncated).
    units: list[dict] = []
    for file_diff in file_hunks:
        path = path_from_file_diff(file_diff) or "(unknown)"
        body = file_diff if file_diff.endswith("\n") else file_diff + "\n"
        b_chars = len(body)
        b_lines = int(diff_stats(body).get("total") or 0)
        fits = b_chars <= budget and (line_cap is None or b_lines <= line_cap)
        if fits:
            units.append({"diff": body, "paths": [path], "truncated": False})
            continue
        for chunk, trunc in split_file_diff_by_hunk(
            body, char_budget=budget, max_lines=line_cap
        ):
            units.append({"diff": chunk, "paths": [path], "truncated": trunc})

    batches: list[dict] = []
    cur_diffs: list[str] = []
    cur_paths: list[str] = []
    cur_chars = 0
    cur_lines = 0
    cur_trunc = False

    def flush() -> None:
        nonlocal cur_diffs, cur_paths, cur_chars, cur_lines, cur_trunc
        if not cur_diffs:
            return
        joined = "".join(cur_diffs)
        if not joined.endswith("\n"):
            joined += "\n"
        # Preserve path order, unique.
        seen: set[str] = set()
        paths: list[str] = []
        for p in cur_paths:
            if p not in seen:
                seen.add(p)
                paths.append(p)
        batches.append(
            {"diff": joined, "paths": paths, "truncated": cur_trunc}
        )
        cur_diffs, cur_paths, cur_chars, cur_lines, cur_trunc = [], [], 0, 0, False

    for unit in units:
        u_diff = unit["diff"]
        u_chars = len(u_diff)
        u_lines = int(diff_stats(u_diff).get("total") or 0)
        # Truncated/oversized units always get their own batch.
        if unit["truncated"] or u_chars > budget or (
            line_cap is not None and u_lines > line_cap and not cur_diffs
        ):
            flush()
            batches.append(
                {
                    "diff": u_diff if u_diff.endswith("\n") else u_diff + "\n",
                    "paths": list(unit["paths"]),
                    "truncated": bool(unit["truncated"]),
                }
            )
            continue
        would_exceed = cur_diffs and (
            cur_chars + u_chars > budget
            or (line_cap is not None and cur_lines + u_lines > line_cap)
        )
        if would_exceed:
            flush()
        cur_diffs.append(u_diff if u_diff.endswith("\n") else u_diff + "\n")
        cur_paths.extend(unit["paths"])
        cur_chars += u_chars
        cur_lines += u_lines
        cur_trunc = cur_trunc or bool(unit["truncated"])
    flush()
    return batches


def dedupe_findings(findings: list[dict]) -> list[dict]:
    """Drop duplicate findings across multi-pass batches (same loc+text)."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for f in findings:
        key = (
            str(f.get("severity") or ""),
            str(f.get("location") or ""),
            str(f.get("finding") or "")[:160],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def format_coverage_section(
    *,
    total_files: int,
    intended_paths: list[str],
    covered_paths: set[str],
    batch_count: int,
    batch_ok: int,
    truncated_paths: list[str],
    failed_batches: list[str],
) -> str:
    """Markdown proof of what the multi-pass review actually covered."""
    covered = len(covered_paths)
    intended = len(intended_paths)
    missing = [p for p in intended_paths if p not in covered_paths]
    complete = (
        not missing
        and not truncated_paths
        and not failed_batches
        and batch_ok == batch_count
        and batch_count > 0
    )
    lines = [
        "### Coverage (multi-pass)",
        f"- Files in scope: **{intended}/{total_files}**"
        + (" (file cap applied)" if intended < total_files else ""),
        f"- Files covered by successful batches: **{covered}/{intended}**",
        f"- Batches: **{batch_ok}/{batch_count}** succeeded",
        f"- Coverage complete: **{'yes' if complete else 'NO'}**",
    ]
    if truncated_paths:
        sample = ", ".join(f"`{p}`" for p in truncated_paths[:8])
        extra = f" (+{len(truncated_paths) - 8} more)" if len(truncated_paths) > 8 else ""
        lines.append(f"- Truncated (context overflow): {sample}{extra}")
    if missing:
        sample = ", ".join(f"`{p}`" for p in missing[:8])
        extra = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
        lines.append(f"- Missing coverage: {sample}{extra}")
    if failed_batches:
        lines.append(f"- Failed batches: {len(failed_batches)}")
    return "\n".join(lines) + "\n"


def parse_max_files(comment: str, default: int) -> int:
    """`@neubodhi … first 20 files …` overrides config.

    Caps at 500. Returns ``default`` unchanged when the phrase is absent
    (``0`` means unlimited / review all files).
    """
    m = re.search(r"\bfirst\s+(\d+)\s+files?\b", comment or "", re.I)
    if not m:
        return default
    return max(1, min(int(m.group(1)), 500))


def ollama_chat(
    host: str,
    model: str,
    system: str,
    user: str,
    *,
    num_predict: int | None = None,
) -> tuple[str, dict]:
    """Stream from Ollama so long prefill/generation doesn't hit a single read timeout.

    Returns (content, metrics) where metrics may include input_tokens, output_tokens,
    and duration_s from the final done chunk / client wall clock.
    """
    import time

    url = host.rstrip("/") + "/api/chat"
    overall_timeout = int(os.environ.get("OLLAMA_TIMEOUT", "1800"))
    # Time-to-first-token on big diffs can exceed 3m; overall_timeout is the hard cap.
    chunk_timeout = int(os.environ.get("OLLAMA_CHUNK_TIMEOUT", "600"))
    predict = NUM_PREDICT if num_predict is None else num_predict
    payload = {
        "model": model,
        "stream": True,
        # Full schema (not bare "json") — constrained decoding to the findings shape.
        "format": FINDINGS_JSON_SCHEMA,
        # qwen3.6 streams into message.thinking by default; that burns the
        # budget with 0 content chars. Force answer tokens into content.
        "think": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": NUM_CTX,
            "num_predict": predict,
        },
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    parts: list[str] = []
    thinking_chars = 0
    done_reason = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    started = time.monotonic()
    last_log = started
    try:
        with urllib.request.urlopen(req, timeout=chunk_timeout) as resp:
            while True:
                if time.monotonic() - started > overall_timeout:
                    raise RuntimeError(
                        f"ollama overall timeout after {overall_timeout}s "
                        f"(partial chars={sum(len(p) for p in parts)}, thinking={thinking_chars})"
                    )
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message") or {}
                piece = msg.get("content") or obj.get("response") or ""
                think_piece = msg.get("thinking") or ""
                if think_piece:
                    thinking_chars += len(think_piece)
                if piece:
                    parts.append(piece)
                now = time.monotonic()
                if now - last_log >= 30:
                    print(
                        f"neubodhi-ollama: still generating… {int(now - started)}s, "
                        f"{sum(len(p) for p in parts)} chars"
                        + (f", thinking={thinking_chars}" if thinking_chars else ""),
                        flush=True,
                    )
                    last_log = now
                if obj.get("done"):
                    done_reason = str(obj.get("done_reason") or "")
                    if "prompt_eval_count" in obj:
                        try:
                            input_tokens = int(obj["prompt_eval_count"])
                        except (TypeError, ValueError):
                            pass
                    if "eval_count" in obj:
                        try:
                            output_tokens = int(obj["eval_count"])
                        except (TypeError, ValueError):
                            pass
                    break
    except TimeoutError as e:
        raise RuntimeError(
            f"ollama chunk timeout ({chunk_timeout}s idle) talking to {url}: {e}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"ollama request failed ({url}): {e}") from e

    content = "".join(parts)
    duration_s = int(time.monotonic() - started)
    metrics = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_s": duration_s,
    }
    if not content:
        raise RuntimeError(
            "empty ollama response (stream produced no content"
            + (f"; saw {thinking_chars} thinking chars — set think:false" if thinking_chars else "")
            + ")"
        )
    print(
        f"neubodhi-ollama: model finished in {duration_s}s "
        f"({len(content)} chars, num_predict={predict}"
        + (f", done_reason={done_reason}" if done_reason else "")
        + (f", thinking={thinking_chars}" if thinking_chars else "")
        + ")",
        flush=True,
    )
    return content, metrics


# Constrained-decoding schema for Ollama `format` (P1). Bare "json" only guarantees
# some JSON object — which is how HRMS domain blobs slipped through as "reviews".
FINDINGS_JSON_SCHEMA: dict = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["severity", "skill", "location", "finding"],
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["blocker", "major", "minor", "nit"],
                    },
                    "skill": {"type": "string"},
                    "location": {"type": "string"},
                    "finding": {"type": "string"},
                    "suggested_fix": {"type": "string"},
                },
            },
        },
        "questions": {"type": "array", "items": {"type": "string"}},
        "memory_suggestions": {"type": "array", "items": {"type": "string"}},
    },
}


def extract_json(text: str) -> dict:
    """Parse model output to a dict. Raises ValueError (does not exit)."""
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
        raise ValueError(f"JSON root must be an object, got {type(obj).__name__}")
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"model did not return JSON:\n{text[:800]}")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON from model: {e}\n{text[:800]}") from e
    if not isinstance(obj, dict):
        raise ValueError(f"JSON root must be an object, got {type(obj).__name__}")
    return obj


def salvage_findings_json(text: str) -> dict | None:
    """Keep complete finding objects when the model cuts mid-JSON (num_predict/ctx)."""
    m = re.search(r'"findings"\s*:\s*\[', text)
    if not m:
        return None
    i = m.end()
    findings: list[dict] = []
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\n\r,":
            i += 1
        if i >= n or text[i] != "{":
            break
        depth = 0
        in_str = False
        esc = False
        start = i
        j = i
        complete = False
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[start : j + 1])
                        except json.JSONDecodeError:
                            obj = None
                        if isinstance(obj, dict) and any(
                            str(obj.get(k) or "").strip() for k in FINDING_TEXT_KEYS
                        ):
                            findings.append(obj)
                        i = j + 1
                        complete = True
                        break
            j += 1
        if not complete:
            break
    if not findings:
        return None
    return {"findings": findings, "questions": [], "memory_suggestions": []}


def is_review_payload(raw: dict) -> bool:
    return isinstance(raw, dict) and isinstance(raw.get("findings"), list)


def format_incomplete_review(reason: str, *, hint: str = "", preview: str = "") -> str:
    lines = [
        "## 🛡️ Neubodhi review",
        "",
        "**Verdict:** COMMENT · review incomplete",
        "",
        "Neubodhi could not produce a valid review for this PR.",
        "",
        f"**Reason:** {reason}",
        "",
        "This is **not** a clean bill of health.",
    ]
    if hint:
        lines += ["", hint]
    if preview:
        lines += [
            "",
            "<details><summary>Model output (truncated)</summary>",
            "",
            "```",
            preview[:600],
            "```",
            "",
            "</details>",
        ]
    lines += [
        "",
        "Please split large PRs or reduce scope, then comment `@neubodhi` to re-run.",
        "",
    ]
    return "\n".join(lines)


def normalize_findings(raw: dict) -> tuple[list[dict], int]:
    """Returns (findings, dropped). `dropped` matters: silently discarding every
    finding and reporting "No findings" is indistinguishable from a clean PR."""
    findings = []
    dropped = 0
    for f in raw.get("findings") or []:
        if not isinstance(f, dict):
            dropped += 1
            continue
        sev = str(f.get("severity", "nit")).lower().strip()
        if sev not in SEV_RANK:
            sev = "nit"
        loc = str(f.get("location") or "—").strip() or "—"
        finding = next(
            (str(f[k]).strip() for k in FINDING_TEXT_KEYS if str(f.get(k) or "").strip()), ""
        )
        if not finding:
            dropped += 1
            continue
        findings.append(
            {
                "severity": sev,
                "skill": str(f.get("skill") or "correctness").strip(),
                "location": loc,
                "finding": finding,
                "suggested_fix": str(f.get("suggested_fix") or "").strip(),
            }
        )
    findings.sort(key=lambda x: SEV_RANK[x["severity"]])
    return findings, dropped


def diff_char_budget(num_ctx: int, num_predict: int, overhead_chars: int) -> int:
    """Chars of diff that still fit the model window. Ollama truncates an oversized
    prompt silently, which reads as "no findings" — so trim on purpose instead.
    ponytail: 3 chars/token (code is denser than chars/4); under-counting ate the
    generation budget and cut JSON mid-`suggested_fix` on gbas-web #59."""
    return max(0, (num_ctx - num_predict) * 3 - overhead_chars)


def md_table_cell(text: str) -> str:
    """Flatten text so GitHub Markdown tables stay one row per finding."""
    # Newlines end a table row; pipes start new columns.
    flat = " ".join(str(text or "").replace("\r", "\n").split())
    return flat.replace("|", "\\|")


def format_summary(
    findings: list[dict],
    questions: list[str],
    memory: list[str],
    verdict_label: str,
    warnings: list[str] | None = None,
    *,
    coverage_md: str = "",
    incomplete: bool = False,
) -> str:
    counts = {k: 0 for k in SEV_RANK}
    for f in findings:
        counts[f["severity"]] += 1
    count_bits = " · ".join(f"{counts[s]} {s}" for s in ("blocker", "major", "minor", "nit"))
    lines = [
        "## 🛡️ Neubodhi Review",
        "",
        f"**Verdict:** {verdict_label}  ·  {count_bits}",
        "",
        "_Backend: ollama_",
        "",
    ]
    for w in warnings or []:
        lines += [f"> ⚠️ {w}", ""]
    if coverage_md:
        lines += [coverage_md.rstrip(), ""]
    lines += [
        "### Findings",
        "| Sev | Skill | Location | Finding |",
        "|-----|-------|----------|---------|",
    ]
    if not findings:
        clean = not warnings and not incomplete
        lines.append(
            "| — | — | — | No findings. |"
            if clean
            else "| — | — | — | **Review incomplete** — treat this as unreviewed, not clean. |"
        )
    else:
        for f in findings:
            icon = SEV_ICON[f["severity"]]
            cell = md_table_cell(f["finding"])
            if f["suggested_fix"]:
                cell += " *Suggested:* " + md_table_cell(f["suggested_fix"])
            lines.append(
                f"| {icon} {f['severity']} | {md_table_cell(f['skill'])} "
                f"| {md_table_cell(f['location'])} | {cell} |"
            )
    if questions:
        lines += ["", "### Questions"]
        for q in questions:
            lines.append(f"- {q}")
    if memory:
        lines += ["", "### 📝 Memory suggestion"]
        for m in memory:
            lines.append(f"- {m}")
    return "\n".join(lines) + "\n"


def _fmt_token_metric(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _journal(msg: str) -> None:
    """Best-effort write to systemd journal (journalctl -t argus). Never raises."""
    try:
        subprocess.run(
            ["logger", "-t", "argus", "--", msg],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


def format_findings_metric(findings: list[dict]) -> str:
    """Compact findings line: Findings=4 (2 major, 2 minor) or Findings=0."""
    if not findings:
        return "Findings=0"
    counts = {k: 0 for k in SEV_RANK}
    for f in findings:
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
    parts = [f"{counts[s]} {s}" for s in ("blocker", "major", "minor", "nit") if counts[s]]
    return f"Findings={len(findings)} ({', '.join(parts)})"


def print_argus_review_summary(
    *,
    pr: str,
    repo: str,
    author: str,
    branch: str,
    model: str,
    num_ctx: int,
    input_tokens: int | None,
    output_tokens: int | None,
    diff_files: int | None,
    diff_lines: int | None,
    duration_s: int | None,
    findings: list[dict] | None,
    status: str,
) -> None:
    """One compact [ARGUS] block for Actions + journalctl. Never raises."""
    try:
        inp = _fmt_token_metric(input_tokens)
        out = _fmt_token_metric(output_tokens)
        if input_tokens is None or output_tokens is None:
            total_s, used_s = "unknown", "unknown"
        else:
            total = input_tokens + output_tokens
            total_s = str(total)
            used_s = f"{(total / num_ctx) * 100:.1f}%" if num_ctx > 0 else "unknown"
        files_s = "unknown" if diff_files is None else str(diff_files)
        lines_s = "unknown" if diff_lines is None else str(diff_lines)
        time_s = "unknown" if duration_s is None else f"{duration_s}s"
        findings_s = format_findings_metric(findings or [])
        ts = _ist_stamp()
        lines = [
            f"[ARGUS] {ts} | PR #{pr} | {repo or 'unknown'} | author={author or 'unknown'} "
            f"| branch={branch or 'unknown'}",
            f"[ARGUS] {ts} | Model={model} | Context={num_ctx} | Input={inp} | Output={out} "
            f"| Total={total_s} | Used={used_s}",
            f"[ARGUS] {ts} | Diff={files_s} files / {lines_s} lines | Time={time_s} | "
            f"{findings_s} | Status={status}",
        ]
        print("\n".join(lines), flush=True)
        for line in lines:
            _journal(line)
    except Exception as e:
        print(f"neubodhi-ollama: warning: metrics summary failed: {e}", file=sys.stderr)


def choose_event(
    findings: list[dict],
    gate: str,
    allow_approve: bool,
    author: str,
    never_approve: list[str],
) -> str:
    threshold = GATE_RANK.get(gate, 1)
    for f in findings:
        if SEV_RANK[f["severity"]] <= threshold:
            return "REQUEST_CHANGES"
    if allow_approve and author not in never_approve and not any(
        SEV_RANK[f["severity"]] <= 1 for f in findings
    ):
        return "APPROVE"
    return "COMMENT"


def post_review(pr: str, event: str, body: str) -> None:
    flag = {
        "REQUEST_CHANGES": "--request-changes",
        "APPROVE": "--approve",
        "COMMENT": "--comment",
    }[event]
    # gh rejects empty body on some events; always pass body
    run(["gh", "pr", "review", pr, flag, "--body", body])


def post_incomplete_and_die(pr: str, reason: str, *, hint: str = "", preview: str = "") -> None:
    """P0: never leave the PR silent when the review harness fails to parse output."""
    body = format_incomplete_review(reason, hint=hint, preview=preview)
    try:
        post_review(pr, "COMMENT", body)
        print(f"neubodhi-ollama: posted incomplete COMMENT ({reason})", flush=True)
    except Exception as post_err:
        print(
            f"neubodhi-ollama: failed to post incomplete comment: {post_err}",
            file=sys.stderr,
        )
    die(f"review incomplete — {reason}")


def review_diff_with_ollama(
    host: str,
    model: str,
    system_full: str,
    user: str,
) -> tuple[list[dict], list[str], list[str], dict, int, str | None]:
    """One Ollama review call + JSON extract/normalize.

    Returns (findings, questions, memory_suggestions, metrics, dropped, error).
    ``error`` is None on success.
    """
    empty_metrics: dict = {
        "input_tokens": None,
        "output_tokens": None,
        "duration_s": None,
    }
    try:
        raw_text, metrics = ollama_chat(host, model, system_full, user)
    except Exception as e:
        return [], [], [], empty_metrics, 0, f"Ollama call failed: {type(e).__name__}: {e}"

    raw: dict | None = None
    parse_err: Exception | None = None
    try:
        raw = extract_json(raw_text)
    except ValueError as e:
        parse_err = e
        salvaged = salvage_findings_json(raw_text)
        if salvaged:
            print(
                f"neubodhi-ollama: salvaged {len(salvaged['findings'])} finding(s) "
                "from truncated JSON",
                flush=True,
            )
            raw = salvaged
        else:
            retry_predict = min(max(NUM_PREDICT * 2, 4096), 8192)
            print(
                f"neubodhi-ollama: JSON parse failed; retrying with num_predict={retry_predict}",
                flush=True,
            )
            retry_user = (
                user
                + "\n\nRetry: return COMPLETE valid JSON only. "
                "Max 8 findings. Keep each suggested_fix under 200 chars."
            )
            try:
                raw_text, metrics = ollama_chat(
                    host, model, system_full, retry_user, num_predict=retry_predict
                )
                raw = extract_json(raw_text)
            except Exception as e2:
                salvaged = salvage_findings_json(raw_text)
                if salvaged:
                    print(
                        f"neubodhi-ollama: salvaged {len(salvaged['findings'])} finding(s) "
                        "after retry",
                        flush=True,
                    )
                    raw = salvaged
                else:
                    parse_err = e2 if isinstance(e2, ValueError) else e

    if raw is None:
        return (
            [],
            [],
            [],
            metrics,
            0,
            f"model returned invalid or non-JSON output ({parse_err})",
        )
    if not is_review_payload(raw):
        return (
            [],
            [],
            [],
            metrics,
            0,
            "model JSON was not a Neubodhi review payload (missing `findings` array)",
        )

    findings, dropped = normalize_findings(raw)
    questions = [str(q) for q in (raw.get("questions") or []) if str(q).strip()]
    memory_sugs = [str(m) for m in (raw.get("memory_suggestions") or []) if str(m).strip()]
    if not findings and not questions:
        print(
            f"neubodhi-ollama: empty result; raw head: {raw_text[:600]!r}",
            file=sys.stderr,
        )
    return findings, questions, memory_sugs, metrics, dropped, None


def main() -> None:
    if not PR_NUMBER:
        die("PR_NUMBER (or ARGUS_PR_NUMBER) is required")

    if not CONFIG_PATH.is_file():
        die(f"config not found: {CONFIG_PATH}")

    cfg = parse_simple_yaml(read_text(CONFIG_PATH))
    if cfg.get("backend", "ollama") != "ollama":
        die("backend is not ollama; refusing to run ollama harness")

    ollama = cfg.get("ollama") or {}
    host = os.environ.get("OLLAMA_HOST") or ollama.get("host") or "http://127.0.0.1:11434"
    model = os.environ.get("OLLAMA_MODEL") or ollama.get("model") or "qwen2.5-coder:7b"
    print(
        f"neubodhi-ollama: repo={os.environ.get('GH_REPO') or os.environ.get('GITHUB_REPOSITORY')} "
        f"actor={os.environ.get('GITHUB_ACTOR')} "
        f"trigger={os.environ.get('GITHUB_TRIGGERING_ACTOR')} "
        f"event={os.environ.get('GITHUB_EVENT_NAME')} "
        f"pr=#{PR_NUMBER} model={model}",
        flush=True,
    )
    gate = cfg.get("gate") or "major"
    allow_approve = bool((cfg.get("verdict") or {}).get("allow_approve"))
    never_approve = list((cfg.get("verdict") or {}).get("never_approve_authors") or [])
    # Soft per-batch line budget when packing files (not a whole-PR skip).
    max_diff = int((cfg.get("limits") or {}).get("max_diff_lines") or 8000)
    # 0 = unlimited (review all files sequentially). Comment can still cap.
    max_files = int((cfg.get("limits") or {}).get("max_files") or 0)
    max_files = parse_max_files(os.environ.get("PR_COMMENT") or "", max_files)
    skip = list((cfg.get("paths") or {}).get("skip") or [])
    skills = cfg.get("skills") or []
    adv_cfg = cfg.get("advanced_findings") or {}
    adv_enabled = bool(adv_cfg.get("enabled"))

    meta = run(
        ["gh", "pr", "view", PR_NUMBER, "--json", "title,body,author,headRefName"]
    )
    meta_j = json.loads(meta)
    author = (meta_j.get("author") or {}).get("login") or ""
    branch = str(meta_j.get("headRefName") or "")
    title = meta_j.get("title") or ""
    body = meta_j.get("body") or ""
    repo_name = (
        os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY") or ""
    )

    diff_r = subprocess.run(
        ["gh", "pr", "diff", PR_NUMBER], capture_output=True, text=True
    )
    if diff_r.returncode != 0:
        err = (diff_r.stderr or diff_r.stdout or "").strip()
        if is_github_diff_too_large(err):
            # GitHub refuses the unified diff (~20k lines). Still post the table via API stats.
            try:
                stats = pr_stats_from_api(PR_NUMBER)
            except SystemExit:
                stats = {
                    "files": 0,
                    "additions": 0,
                    "deletions": 0,
                    "new_files": 0,
                    "total": 20000,
                    "github_capped": True,
                }
            post_review(
                PR_NUMBER,
                "COMMENT",
                format_diff_too_large(
                    stats,
                    max_diff,
                    note=(
                        "GitHub could not return the full unified diff "
                        "(HTTP 406 — over ~20,000 lines)."
                    ),
                ),
            )
            print(
                f"neubodhi-ollama: GitHub diff API too large — posted COMMENT "
                f"({stats['files']} files, +{stats['additions']}/−{stats['deletions']})",
                flush=True,
            )
            print_argus_review_summary(
                pr=PR_NUMBER,
                repo=repo_name,
                author=author,
                branch=branch,
                model=model,
                num_ctx=NUM_CTX,
                input_tokens=None,
                output_tokens=None,
                diff_files=stats.get("files"),
                diff_lines=stats.get("total"),
                duration_s=None,
                findings=[],
                status="skipped",
            )
            return
        die(f"$ gh pr diff {PR_NUMBER}\n{err}")

    raw_diff = diff_r.stdout
    diff = filter_diff(raw_diff, skip)
    warnings: list[str] = []
    diff, kept_files, total_files = take_first_n_files(diff, max_files)
    if total_files > kept_files:
        warnings.append(
            f"Considering first {kept_files} of {total_files} files "
            f"(limit `max_files={max_files}`). Use `@neubodhi first N files` or set "
            f"`limits.max_files: 0` to review all."
        )
        print(
            f"neubodhi-ollama: limiting to first {kept_files}/{total_files} files",
            file=sys.stderr,
        )
    stats = diff_stats(diff)
    nlines = stats["total"]
    if not diff.strip():
        reason = (
            "every changed path matched a `paths.skip` glob"
            if raw_diff.strip()
            else "the PR diff came back empty"
        )
        post_review(
            PR_NUMBER,
            "COMMENT",
            "## 🛡️ Neubodhi review\n\n"
            f"**Verdict:** COMMENT  ·  nothing reviewed — {reason}.\n\n"
            "This is **not** a clean bill of health.\n",
        )
        die(f"nothing to review — {reason}")

    system = read_text(ROOT / "prompts" / "system.md")
    protocol = read_text(ROOT / "prompts" / "review.md")
    verdict_fmt = read_text(ROOT / "prompts" / "verdict.md")
    skills_blob = load_skills(skills)
    memory_blob = load_memory()
    comments_blob = load_pr_comments(PR_NUMBER)
    if comments_blob:
        print(
            f"neubodhi-ollama: loaded PR comments ({len(comments_blob)} chars)",
            file=sys.stderr,
        )
    else:
        print("neubodhi-ollama: no PR comments loaded", file=sys.stderr)

    system_full = f"""{system}

You are running under the neubodhi Ollama harness (no interactive tools).
Apply the review protocol and skills below. Respect memory.
If PR conversation comments explain a change is intentional, do not re-flag it unless
there is a new concrete correctness or functionality issue. When the author quotes
a specific issue and says "intentional" / "ignore", omit that finding entirely.
Return ONLY valid JSON (no markdown fences) with this schema:
{{
  "findings": [
    {{
      "severity": "blocker|major|minor|nit",
      "skill": "<skill name>",
      "location": "path:line or —",
      "finding": "what's wrong — why it matters",
      "suggested_fix": "optional short fix"
    }}
  ],
  "questions": ["optional unsure items"],
  "memory_suggestions": ["optional"]
}}
Precision over volume. Do not re-flag accepted-patterns. Cite path:line.
Severity gate in config is `{gate}`.
Only review the diff provided (it may be one batch of a multi-batch PR review).
"""

    overhead = (
        len(system_full)
        + len(protocol)
        + len(verdict_fmt)
        + len(skills_blob)
        + len(memory_blob)
        + len(comments_blob)
        + len(title)
        + len(body)
        + 1024
    )
    budget = diff_char_budget(NUM_CTX, NUM_PREDICT, overhead)
    file_hunks = split_diff_by_file(diff)
    intended_paths = [path_from_file_diff(h) or f"(file-{i})" for i, h in enumerate(file_hunks)]
    batches = build_review_batches(
        file_hunks,
        char_budget=budget,
        max_lines=max_diff,
    )
    if not batches and diff.strip():
        batches = [
            {
                "diff": diff if diff.endswith("\n") else diff + "\n",
                "paths": intended_paths,
                "truncated": False,
            }
        ]

    print(
        f"neubodhi-ollama: host={host} model={model} pr=#{PR_NUMBER} "
        f"files={kept_files}/{total_files} lines≈{nlines} batches={len(batches)} "
        f"budget_chars≈{budget}",
        flush=True,
    )

    det_findings: list[dict] = []
    loop_hint_lines: list[str] = []
    if adv_enabled:
        try:
            _scripts_dir = Path(__file__).resolve().parent
            if str(_scripts_dir) not in sys.path:
                sys.path.insert(0, str(_scripts_dir))
            from advanced_findings import run_advanced_findings  # noqa: E402

            # Deterministic analyzers see the full (non-truncated) file set once.
            det_findings, loop_hint_lines, _adv_note = run_advanced_findings(
                diff,
                PR_NUMBER,
                adv_cfg=adv_cfg,
            )
        except Exception as adv_err:
            print(
                f"neubodhi-ollama: advanced_findings failed (continuing): {adv_err}",
                file=sys.stderr,
            )
            det_findings, loop_hint_lines = [], []

    findings: list[dict] = []
    questions: list[str] = []
    memory_sugs: list[str] = []
    dropped = 0
    ollama_metrics: dict = {
        "input_tokens": 0,
        "output_tokens": 0,
        "duration_s": 0,
    }
    batch_errors: list[str] = []
    path_successes: set[str] = set()
    path_failures: set[str] = set()
    truncated_paths: list[str] = []
    batch_ok = 0
    max_batch_retries = int(os.environ.get("ARGUS_BATCH_RETRIES", "1"))

    hints_section = ""
    if loop_hint_lines:
        hints_section = (
            "\n# Advanced analysis hints (confirm or dismiss — do not rubber-stamp)\n"
            + "\n".join(loop_hint_lines)
            + "\n"
        )

    for bi, batch in enumerate(batches, start=1):
        chunk = batch["diff"]
        paths = list(batch.get("paths") or [])
        was_truncated = bool(batch.get("truncated"))
        if was_truncated:
            for p in paths:
                if p not in truncated_paths:
                    truncated_paths.append(p)
            warnings.append(
                f"Batch {bi}/{len(batches)}: content for {', '.join(paths) or 'unknown'} "
                f"exceeds context budget (~{budget} chars) even after hunk-splitting — "
                "that slice was truncated; coverage incomplete."
            )

        batch_stats = diff_stats(chunk)
        batch_header = (
            f"\n# Automatic multi-pass batch {bi} of {len(batches)}\n"
            f"Files in this batch: {', '.join(paths) if paths else '(unknown)'}\n"
            f"Changed lines in this batch: ~{batch_stats.get('total')}\n"
            "Review only this batch's diff; remaining files are covered in other "
            "automatic passes in this same run.\n"
        )

        user = f"""# Review protocol
{protocol}

# Verdict format reference
{verdict_fmt}

# Enabled skills
{skills_blob}

# Repo memory
{memory_blob}

# PR #{PR_NUMBER}
Title: {title}
Author: {author}

Description:
{body}
{f'''
# PR conversation (author feedback — treat as authoritative for intent)
{comments_blob}
''' if comments_blob else ''}{hints_section}{batch_header}
# Diff
```diff
{chunk}
```
"""
        print(
            f"neubodhi-ollama: pass {bi}/{len(batches)} "
            f"files={len(paths)} lines≈{batch_stats.get('total')} "
            f"paths={paths[:5]}{'…' if len(paths) > 5 else ''}",
            flush=True,
        )

        b_findings: list[dict] = []
        b_questions: list[str] = []
        b_memory: list[str] = []
        b_metrics: dict = {}
        b_dropped = 0
        b_err: str | None = None
        attempts = 1 + max(0, max_batch_retries)
        for attempt in range(1, attempts + 1):
            b_findings, b_questions, b_memory, b_metrics, b_dropped, b_err = (
                review_diff_with_ollama(host, model, system_full, user)
            )
            if not b_err:
                break
            print(
                f"neubodhi-ollama: pass {bi} attempt {attempt}/{attempts} failed: {b_err}",
                file=sys.stderr,
            )

        for key in ("input_tokens", "output_tokens", "duration_s"):
            val = b_metrics.get(key)
            if isinstance(val, (int, float)):
                ollama_metrics[key] = int(ollama_metrics.get(key) or 0) + int(val)
        dropped += b_dropped
        if b_err:
            batch_errors.append(f"pass {bi}/{len(batches)}: {b_err}")
            path_failures.update(paths)
            continue
        if was_truncated:
            # Truncated slices are not proof of full file coverage.
            path_failures.update(paths)
        else:
            path_successes.update(paths)
            batch_ok += 1
        findings.extend(b_findings)
        questions.extend(b_questions)
        memory_sugs.extend(b_memory)

    # A path is covered only if every pass that included it succeeded without truncation.
    covered_paths = path_successes - path_failures
    coverage_md = format_coverage_section(
        total_files=total_files,
        intended_paths=intended_paths,
        covered_paths=covered_paths,
        batch_count=len(batches),
        batch_ok=batch_ok,
        truncated_paths=truncated_paths,
        failed_batches=batch_errors,
    )
    missing_paths = [p for p in intended_paths if p not in covered_paths]
    coverage_complete = (
        not missing_paths
        and not truncated_paths
        and not batch_errors
        and batch_ok == len(batches)
        and len(batches) > 0
    )
    print(
        f"neubodhi-ollama: coverage complete={coverage_complete} "
        f"covered={len(covered_paths)}/{len(intended_paths)} "
        f"passes_ok={batch_ok}/{len(batches)}",
        flush=True,
    )

    if batch_errors and not findings and not questions and not det_findings:
        print_argus_review_summary(
            pr=PR_NUMBER,
            repo=repo_name,
            author=author,
            branch=branch,
            model=model,
            num_ctx=NUM_CTX,
            input_tokens=ollama_metrics.get("input_tokens") or None,
            output_tokens=ollama_metrics.get("output_tokens") or None,
            diff_files=stats.get("files"),
            diff_lines=nlines,
            duration_s=ollama_metrics.get("duration_s") or None,
            findings=[],
            status="incomplete",
        )
        post_incomplete_and_die(
            PR_NUMBER,
            "; ".join(batch_errors[:3]),
            hint=(
                "Automatic multi-pass review failed before any findings. "
                "Check Ollama connectivity, then re-run with `@neubodhi`."
            ),
            preview=(batch_errors[0][:600] + "\n\n" + coverage_md)[:900],
        )
    if batch_errors:
        warnings.append(
            f"{len(batch_errors)} of {len(batches)} automatic pass(es) failed; "
            "coverage incomplete."
        )
    if missing_paths:
        warnings.append(
            f"{len(missing_paths)} file(s) lack successful coverage "
            f"(e.g. `{missing_paths[0]}`)."
        )
    if not coverage_complete:
        warnings.append(
            "Multi-pass coverage is incomplete — do not treat this as a full review."
        )

    if det_findings:
        try:
            _scripts_dir = Path(__file__).resolve().parent
            if str(_scripts_dir) not in sys.path:
                sys.path.insert(0, str(_scripts_dir))
            from advanced_findings import merge_findings  # noqa: E402

            findings = merge_findings(det_findings, findings)
        except Exception as merge_err:
            print(
                f"neubodhi-ollama: advanced_findings merge failed: {merge_err}",
                file=sys.stderr,
            )
            for f in det_findings:
                findings.append(
                    {
                        "severity": f.get("severity") or "nit",
                        "skill": f.get("skill") or "correctness",
                        "location": f.get("location") or "—",
                        "finding": f.get("finding") or "",
                        "suggested_fix": f.get("suggested_fix") or "",
                    }
                )
    findings = dedupe_findings(findings)
    findings, waived_notes = drop_waived_findings(findings, comments_blob)
    # Waivers are informational (author already addressed them) — not coverage failures.
    info_notes: list[str] = list(waived_notes)
    if dropped:
        warnings.append(f"{dropped} finding(s) from the model were unparseable and discarded.")
        print(f"neubodhi-ollama: dropped {dropped} malformed finding(s)", file=sys.stderr)
    findings.sort(key=lambda x: SEV_RANK.get(x.get("severity") or "nit", 99))
    if not findings and not questions and coverage_complete:
        print(
            "neubodhi-ollama: empty result across all passes (no findings/questions); "
            "coverage complete",
            file=sys.stderr,
        )

    for key in ("input_tokens", "output_tokens", "duration_s"):
        if ollama_metrics.get(key) == 0 and not findings and batch_errors:
            ollama_metrics[key] = None

    event = choose_event(findings, gate, allow_approve, author, never_approve)
    label = {"REQUEST_CHANGES": "REQUEST CHANGES", "APPROVE": "APPROVE", "COMMENT": "COMMENT"}[
        event
    ]
    # Proof-safe: never approve when multi-pass coverage is incomplete or real warnings exist.
    if not coverage_complete and event == "APPROVE":
        event, label = "COMMENT", "COMMENT"
    if warnings and event == "APPROVE":
        event, label = "COMMENT", "COMMENT"
    summary = format_summary(
        findings,
        questions,
        memory_sugs,
        label,
        info_notes + warnings,
        coverage_md=coverage_md,
        incomplete=not coverage_complete,
    )
    post_review(PR_NUMBER, event, summary)
    print(
        f"neubodhi-ollama: posted {event} with {len(findings)} finding(s) "
        f"coverage_complete={coverage_complete}",
        flush=True,
    )
    incomplete = not coverage_complete
    status = "incomplete" if incomplete else "success"
    print_argus_review_summary(
        pr=PR_NUMBER,
        repo=repo_name,
        author=author,
        branch=branch,
        model=model,
        num_ctx=NUM_CTX,
        input_tokens=ollama_metrics.get("input_tokens"),
        output_tokens=ollama_metrics.get("output_tokens"),
        diff_files=stats.get("files"),
        diff_lines=nlines,
        duration_s=ollama_metrics.get("duration_s"),
        findings=findings,
        status=status,
    )
    # Incomplete multi-pass must fail the Actions check (not a green false clean).
    if incomplete:
        die("review incomplete — multi-pass coverage not complete")
    if event == "REQUEST_CHANGES":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
