#!/usr/bin/env python3
"""Argus Ollama harness — one-shot PR review via a local/LAN Ollama server.

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


def die(msg: str, code: int = 1) -> None:
    print(f"argus-ollama: {msg}", file=sys.stderr)
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
        "limits": {"max_diff_lines": 4000, "max_inline_comments": 15},
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
            print(f"argus-ollama: warning: missing skill {p}", file=sys.stderr)
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


def count_diff_lines(diff: str) -> int:
    return sum(1 for ln in diff.splitlines() if ln.startswith("+") or ln.startswith("-"))


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


def ollama_chat(host: str, model: str, system: str, user: str) -> str:
    """Call Ollama with streaming so long generations don't hit a single read timeout."""
    import time

    url = host.rstrip("/") + "/api/chat"
    # Overall wall clock; per-chunk socket timeout. Override via env if needed.
    overall_timeout = int(os.environ.get("OLLAMA_TIMEOUT", "1800"))
    chunk_timeout = int(os.environ.get("OLLAMA_CHUNK_TIMEOUT", "120"))
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        # qwen3.6 streams into message.thinking by default; that burns the
        # budget with 0 content chars. Force answer tokens into content.
        "think": False,
        "options": {
            "temperature": 0.1,
            "num_ctx": 32768,
            # Cap output so a runaway model can't sit for hours.
            "num_predict": int(os.environ.get("OLLAMA_NUM_PREDICT", "4096")),
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
    started = time.monotonic()
    last_log = started
    try:
        with urllib.request.urlopen(req, timeout=chunk_timeout) as resp:
            while True:
                if time.monotonic() - started > overall_timeout:
                    die(
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
                        f"argus-ollama: still generating… {int(now - started)}s, "
                        f"{sum(len(p) for p in parts)} chars"
                        + (f", thinking={thinking_chars}" if thinking_chars else ""),
                        flush=True,
                    )
                    last_log = now
                if obj.get("done"):
                    break
    except TimeoutError as e:
        die(f"ollama chunk timeout ({chunk_timeout}s idle) talking to {url}: {e}")
    except urllib.error.URLError as e:
        die(f"ollama request failed ({url}): {e}")

    content = "".join(parts)
    if not content:
        die(
            "empty ollama response (stream produced no content"
            + (f"; saw {thinking_chars} thinking chars — set think:false" if thinking_chars else "")
            + ")"
        )
    print(
        f"argus-ollama: model finished in {int(time.monotonic() - started)}s "
        f"({len(content)} chars"
        + (f", thinking={thinking_chars}" if thinking_chars else "")
        + ")",
        flush=True,
    )
    return content


def extract_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        die(f"model did not return JSON:\n{text[:800]}")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        die(f"invalid JSON from model: {e}\n{text[:800]}")


def normalize_findings(raw: dict) -> list[dict]:
    findings = []
    for f in raw.get("findings") or []:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity", "nit")).lower().strip()
        if sev not in SEV_RANK:
            sev = "nit"
        loc = str(f.get("location") or "—").strip() or "—"
        finding = str(f.get("finding") or "").strip()
        if not finding:
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
    return findings


def format_summary(
    findings: list[dict],
    questions: list[str],
    memory: list[str],
    verdict_label: str,
) -> str:
    counts = {k: 0 for k in SEV_RANK}
    for f in findings:
        counts[f["severity"]] += 1
    count_bits = " · ".join(f"{counts[s]} {s}" for s in ("blocker", "major", "minor", "nit"))
    lines = [
        "## 🛡️ Argus review",
        "",
        f"**Verdict:** {verdict_label}  ·  {count_bits}",
        "",
        "_Backend: ollama_",
        "",
        "### Findings",
        "| Sev | Skill | Location | Finding |",
        "|-----|-------|----------|---------|",
    ]
    if not findings:
        lines.append("| — | — | — | No findings. |")
    else:
        for f in findings:
            icon = SEV_ICON[f["severity"]]
            cell = f["finding"].replace("|", "\\|")
            if f["suggested_fix"]:
                cell += f" *Suggested:* {f['suggested_fix']}".replace("|", "\\|")
            lines.append(
                f"| {icon} {f['severity']} | {f['skill']} | {f['location']} | {cell} |"
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
    model = os.environ.get("OLLAMA_MODEL") or ollama.get("model") or "qwen3.6:27b"
    gate = cfg.get("gate") or "major"
    allow_approve = bool((cfg.get("verdict") or {}).get("allow_approve"))
    never_approve = list((cfg.get("verdict") or {}).get("never_approve_authors") or [])
    max_diff = int((cfg.get("limits") or {}).get("max_diff_lines") or 4000)
    skip = list((cfg.get("paths") or {}).get("skip") or [])
    skills = cfg.get("skills") or []

    meta = run(["gh", "pr", "view", PR_NUMBER, "--json", "title,body,author"])
    meta_j = json.loads(meta)
    author = (meta_j.get("author") or {}).get("login") or ""
    title = meta_j.get("title") or ""
    body = meta_j.get("body") or ""

    diff = run(["gh", "pr", "diff", PR_NUMBER])
    diff = filter_diff(diff, skip)
    nlines = count_diff_lines(diff)
    if nlines > max_diff:
        msg = (
            "## 🛡️ Argus review\n\n"
            f"**Verdict:** COMMENT  ·  diff too large ({nlines} lines > {max_diff})\n\n"
            "Please split this PR so Argus can review it properly.\n"
        )
        post_review(PR_NUMBER, "COMMENT", msg)
        print(f"argus-ollama: skipped large diff ({nlines} lines)")
        return

    system = read_text(ROOT / "prompts" / "system.md")
    protocol = read_text(ROOT / "prompts" / "review.md")
    verdict_fmt = read_text(ROOT / "prompts" / "verdict.md")
    skills_blob = load_skills(skills)
    memory_blob = load_memory()

    system_full = f"""{system}

You are running under the Argus Ollama harness (no interactive tools).
Apply the review protocol and skills below. Respect memory.
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
"""

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

# Diff
```diff
{diff}
```
"""

    print(f"argus-ollama: host={host} model={model} pr=#{PR_NUMBER} diff_lines≈{nlines}")
    try:
        raw_text = ollama_chat(host, model, system_full, user)
    except SystemExit:
        raise
    except Exception as e:
        print(f"argus-ollama: ollama call failed: {e}", file=sys.stderr)
        try:
            post_review(
                PR_NUMBER,
                "COMMENT",
                "## 🛡️ Argus review\n\n"
                f"**Verdict:** COMMENT  ·  Ollama call failed\n\n"
                f"`{type(e).__name__}: {e}`\n\n"
                "Check the self-hosted runner can reach Ollama and that the model is loaded.\n",
            )
        except Exception as post_err:
            print(f"argus-ollama: also failed to post failure comment: {post_err}", file=sys.stderr)
        raise SystemExit(1) from e
    raw = extract_json(raw_text)
    findings = normalize_findings(raw)
    questions = [str(q) for q in (raw.get("questions") or []) if str(q).strip()]
    memory_sugs = [str(m) for m in (raw.get("memory_suggestions") or []) if str(m).strip()]

    event = choose_event(findings, gate, allow_approve, author, never_approve)
    label = {"REQUEST_CHANGES": "REQUEST CHANGES", "APPROVE": "APPROVE", "COMMENT": "COMMENT"}[
        event
    ]
    summary = format_summary(findings, questions, memory_sugs, label)
    post_review(PR_NUMBER, event, summary)
    print(f"argus-ollama: posted {event} with {len(findings)} finding(s)")
    # Fail the Actions check so required status checks / branch protection can block merge.
    if event == "REQUEST_CHANGES":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
