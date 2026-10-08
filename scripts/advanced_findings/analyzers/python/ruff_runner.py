"""Run Ruff on a Python source string; return Argus-shaped findings."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# High-signal only: pyflakes + runtime syntax/IO errors. Not a style linter.
_RUFF_SELECT = "F401,F841,F821,F822,F823,F811,E9"


def _severity_for_code(code: str) -> str:
    if code.startswith("E9") or code in {"F821", "F822", "F823"}:
        return "major"
    if code in {"F401", "F841"}:
        return "minor"
    if code.startswith("F"):
        return "minor"
    return "nit"


def _skill_for_code(code: str) -> str:
    if code in {"F401", "F841"}:
        return "correctness"
    if code.startswith("E9") or code.startswith("F8") or code == "F811":
        return "correctness"
    return "correctness"


def ruff_available() -> bool:
    return shutil.which("ruff") is not None


def run_ruff_on_source(path: str, source: str) -> list[dict]:
    """Lint `source` as if it lived at `path`. Empty list if ruff missing/fails."""
    if not source or not ruff_available():
        return []

    findings: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="argus-ruff-") as tmp:
        # Preserve basename so messages look natural; nest under tmp only.
        dest = Path(tmp) / Path(path).name
        dest.write_text(source, encoding="utf-8")
        env = os.environ.copy()
        env["RUFF_NO_CACHE"] = "1"
        proc = subprocess.run(
            [
                "ruff",
                "check",
                str(dest),
                f"--select={_RUFF_SELECT}",
                "--output-format=json",
                "--exit-zero",
                "--no-fix",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if not proc.stdout.strip():
            return []
        try:
            rows = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return []
        if not isinstance(rows, list):
            return []

        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "")
            loc = row.get("location") or {}
            line = loc.get("row")
            if not isinstance(line, int) or line < 1:
                continue
            msg = str(row.get("message") or "").strip()
            if not msg:
                continue
            fix_hint = ""
            if row.get("fix"):
                fix_hint = "Apply the Ruff autofix for this rule, or remove the unused symbol."
            findings.append(
                {
                    "severity": _severity_for_code(code),
                    "skill": _skill_for_code(code),
                    "location": f"{path}:{line}",
                    "finding": f"[ruff {code}] {msg}",
                    "suggested_fix": fix_hint,
                    "_line": line,
                    "_path": path,
                    "_source": "ruff",
                    "_code": code,
                }
            )
    return findings
