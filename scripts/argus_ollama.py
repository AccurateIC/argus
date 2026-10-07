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
from pathlib import Path

ROOT = Path(os.environ.get("GITHUB_WORKSPACE") or Path(__file__).resolve().parents[1])
CONFIG_PATH = Path(os.environ.get("ARGUS_CONFIG", ROOT / "config" / "argus.yml"))
PR_NUMBER = os.environ.get("PR_NUMBER") or os.environ.get("ARGUS_PR_NUMBER")
SEV_RANK = {"blocker": 0, "major": 1, "minor": 2, "nit": 3}
SEV_ICON = {"blocker": "🔴", "major": "🟠", "minor": "🟡", "nit": "⚪"}
GATE_RANK = {"blocker": 0, "major": 1, "minor": 2}
# Local models drift off the schema; accept the usual synonyms rather than dropping
# the whole finding.
FINDING_TEXT_KEYS = ("finding", "description", "message", "issue", "detail", "comment")
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "16384"))
NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", "4096"))


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
            "max_diff_lines": 4000,
            "max_inline_comments": 15,
            "max_files": 20,
        },
        "ollama": {"host": "http://127.0.0.1:11434", "model": "qwen3.6:27b"},
        "paths": {"skip": [], "strict": []},
    }
    section: str | None = None
    list_key: str | None = None

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0 and line.endswith(":") and " " not in line[:-1]:
            section = line[:-1]
            list_key = None
            if section in ("skills",):
                cfg[section] = []
                list_key = section
                section = None
            elif section not in cfg or not isinstance(cfg.get(section), dict):
                if section in ("verdict", "limits", "ollama", "paths"):
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
                elif section == "verdict" and list_key == "never_approve_authors":
                    cfg["verdict"][list_key] = []
                else:
                    list_key = None
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
                    cfg[section][k] = val
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
    """Keep only the first n file hunks. Returns (sliced, kept, total)."""
    parts = split_diff_by_file(diff)
    total = len(parts)
    if n <= 0 or total <= n:
        return diff, total, total
    kept = parts[:n]
    return "\n".join(kept) + "\n", n, total


def parse_max_files(comment: str, default: int) -> int:
    """`@neubodhi … first 20 files …` overrides config. Caps at 200."""
    m = re.search(r"\bfirst\s+(\d+)\s+files?\b", comment or "", re.I)
    if not m:
        return default
    return max(1, min(int(m.group(1)), 200))


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
    lines += [
        "### Findings",
        "| Sev | Skill | Location | Finding |",
        "|-----|-------|----------|---------|",
    ]
    if not findings:
        clean = not warnings
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
    """One compact [ARGUS] block for Actions logs. Never raises."""
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
        print(
            f"[ARGUS] PR #{pr} | {repo or 'unknown'} | author={author or 'unknown'} "
            f"| branch={branch or 'unknown'}\n"
            f"[ARGUS] Model={model} | Context={num_ctx} | Input={inp} | Output={out} "
            f"| Total={total_s} | Used={used_s}\n"
            f"[ARGUS] Diff={files_s} files / {lines_s} lines | Time={time_s} | "
            f"{findings_s} | Status={status}",
            flush=True,
        )
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
    max_diff = int((cfg.get("limits") or {}).get("max_diff_lines") or 4000)
    max_files = int((cfg.get("limits") or {}).get("max_files") or 20)
    max_files = parse_max_files(os.environ.get("PR_COMMENT") or "", max_files)
    skip = list((cfg.get("paths") or {}).get("skip") or [])
    skills = cfg.get("skills") or []

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
            f"Reviewed first {kept_files} of {total_files} files "
            f"(limit `max_files={max_files}`). Re-run after splitting, or "
            f"`@neubodhi first N files` with a higher N (cap 200)."
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
    if nlines > max_diff:
        # Stats here are for the sliced window so the table matches what we refused.
        full_note = (
            f"After taking the first {kept_files} files, still "
            f"{nlines:,} changed lines (limit {max_diff:,})."
            if total_files > kept_files
            else ""
        )
        post_review(
            PR_NUMBER,
            "COMMENT",
            format_diff_too_large(stats, max_diff, note=full_note),
        )
        print(
            f"neubodhi-ollama: skipped large diff "
            f"({nlines} lines, {stats['files']} files, +{stats['additions']}/−{stats['deletions']})"
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
            diff_lines=nlines,
            duration_s=None,
            findings=[],
            status="skipped",
        )
        return

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
Only review the diff provided (it may be a partial file window).
"""

    budget = diff_char_budget(
        NUM_CTX,
        NUM_PREDICT,
        len(system_full) + len(protocol) + len(verdict_fmt) + len(skills_blob)
        + len(memory_blob) + len(comments_blob) + len(title) + len(body) + 512,
    )
    if len(diff) > budget:
        warnings.append(
            f"Diff is {len(diff)} chars but only ~{budget} fit the {NUM_CTX}-token context — "
            f"it was truncated. Split this PR or raise `OLLAMA_NUM_CTX`."
        )
        print(f"neubodhi-ollama: truncating diff {len(diff)} -> {budget} chars", file=sys.stderr)
        diff = diff[:budget]

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
''' if comments_blob else ''}
# Diff
```diff
{diff}
```
"""

    print(f"neubodhi-ollama: host={host} model={model} pr=#{PR_NUMBER} diff_lines≈{nlines}")
    ollama_metrics: dict = {
        "input_tokens": None,
        "output_tokens": None,
        "duration_s": None,
    }
    try:
        raw_text, ollama_metrics = ollama_chat(host, model, system_full, user)
    except Exception as e:
        print(f"neubodhi-ollama: ollama call failed: {e}", file=sys.stderr)
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
            diff_lines=nlines,
            duration_s=None,
            findings=[],
            status="incomplete",
        )
        post_incomplete_and_die(
            PR_NUMBER,
            f"Ollama call failed: {type(e).__name__}: {e}",
            hint="Check the self-hosted runner can reach Ollama and that the model is loaded.",
        )

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
            # Industry pattern: bump num_predict once when structured output truncates.
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
                raw_text, ollama_metrics = ollama_chat(
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
            findings=[],
            status="incomplete",
        )
        post_incomplete_and_die(
            PR_NUMBER,
            "model returned invalid or non-JSON output",
            hint=(
                "Model cut off mid-JSON (generation budget / large prompt). "
                "Re-run with `@neubodhi`, or split the PR if it keeps failing."
            ),
            preview=str(parse_err or ""),
        )

    if not is_review_payload(raw):
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
            findings=[],
            status="incomplete",
        )
        post_incomplete_and_die(
            PR_NUMBER,
            "model JSON was not a Neubodhi review payload (missing `findings` array)",
            hint=(
                "The model echoed unrelated JSON from the diff instead of findings. "
                "Split large PRs or reduce scope, then re-run with `@neubodhi`."
            ),
            preview=json.dumps(raw, ensure_ascii=False)[:600],
        )

    findings, dropped = normalize_findings(raw)
    findings, waived_notes = drop_waived_findings(findings, comments_blob)
    if waived_notes:
        warnings.extend(waived_notes)
    questions = [str(q) for q in (raw.get("questions") or []) if str(q).strip()]
    memory_sugs = [str(m) for m in (raw.get("memory_suggestions") or []) if str(m).strip()]
    if dropped:
        warnings.append(f"{dropped} finding(s) from the model were unparseable and discarded.")
        print(f"neubodhi-ollama: dropped {dropped} malformed finding(s)", file=sys.stderr)
    if not findings and not questions:
        # A silent empty result is the failure mode that looks like success. Log the
        # response so "no findings" can always be told apart from "model said nothing".
        print(f"neubodhi-ollama: empty result; raw head: {raw_text[:600]!r}", file=sys.stderr)

    event = choose_event(findings, gate, allow_approve, author, never_approve)
    label = {"REQUEST_CHANGES": "REQUEST CHANGES", "APPROVE": "APPROVE", "COMMENT": "COMMENT"}[
        event
    ]
    if warnings and event == "APPROVE":
        event, label = "COMMENT", "COMMENT"
    summary = format_summary(findings, questions, memory_sugs, label, warnings)
    post_review(PR_NUMBER, event, summary)
    print(f"neubodhi-ollama: posted {event} with {len(findings)} finding(s)")
    status = "incomplete" if (warnings and not findings) else "success"
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
    # An incomplete review must not show up as a green check.
    if warnings and not findings:
        die("review incomplete — see warnings above")
    # Fail the Actions check so required status checks / branch protection can block merge.
    if event == "REQUEST_CHANGES":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
