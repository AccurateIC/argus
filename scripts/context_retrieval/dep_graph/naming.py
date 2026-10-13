"""Clear per-PR / per-commit output paths for dep-graph.html."""

from __future__ import annotations

import os
import re
from pathlib import Path


def default_cache_root() -> Path:
    override = os.environ.get("ARGUS_DEP_GRAPH_CACHE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "argus" / "dep-graphs"


def _slug(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return (s[:max_len].rstrip("-") or "untitled")


def pr_graph_dir(
    *,
    repo: str,
    pr_number: str | int | None = None,
    head_sha: str | None = None,
    title: str | None = None,
    cache_root: Path | None = None,
) -> Path:
    """Return directory that will hold dep-graph.html.

    Example:
      ~/.cache/argus/dep-graphs/AccurateIC__gbas-web/
        pr-48_head-a1b2c3d_fix-onboarding-workflow/
    """
    root = cache_root or default_cache_root()
    owner_repo = (repo or "local").replace("/", "__")
    parts: list[str] = []
    if pr_number not in (None, ""):
        parts.append(f"pr-{pr_number}")
    if head_sha:
        parts.append(f"head-{head_sha[:7]}")
    if title:
        parts.append(_slug(title))
    if not parts:
        parts.append("latest")
    return root / owner_repo / "_".join(parts)
