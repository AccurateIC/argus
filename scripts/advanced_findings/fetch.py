"""Fetch file contents at the PR head via gh (no full repo checkout)."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.parse


def _repo() -> str:
    return os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY") or ""


def head_ref(pr: str) -> str:
    """Prefer commit OID; fall back to branch name."""
    r = subprocess.run(
        ["gh", "pr", "view", pr, "--json", "headRefOid,headRefName"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return ""
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return ""
    return str(data.get("headRefOid") or data.get("headRefName") or "")


def fetch_file(path: str, ref: str) -> str | None:
    """Return UTF-8 file text at ref, or None on failure / binary / missing."""
    repo = _repo()
    if not repo or not path or not ref:
        return None
    enc = urllib.parse.quote(path, safe="/")
    r = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/contents/{enc}?ref={urllib.parse.quote(ref)}",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("type") != "file":
        return None
    encoding = data.get("encoding") or ""
    content = data.get("content") or ""
    if encoding == "base64" and content:
        try:
            return base64.b64decode(content).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    if isinstance(content, str) and content and encoding != "base64":
        return content
    return None
