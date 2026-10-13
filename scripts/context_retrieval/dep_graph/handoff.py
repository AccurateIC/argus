"""Infer likely sibling handoff / pipeline order (repo-agnostic)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scan import FileGraph

# Folder looks like a staged worker if it has these markers (any 2).
_STAGE_MARKERS = frozenset(
    {
        "AGENT.md",
        "agent.md",
        "memory.md",
        "memory",
        "src",
        "run.py",
        "main.py",
    }
)


def _basename(folder_id: str) -> str:
    if folder_id == "(root)":
        return "repo"
    return folder_id.rsplit("/", 1)[-1]


def _child_names_on_disk(root: Path, folder_id: str) -> set[str]:
    if folder_id == "(root)":
        base = root
    else:
        base = root / folder_id
    if not base.is_dir():
        return set()
    try:
        return {p.name for p in base.iterdir()}
    except OSError:
        return set()


def is_stage_folder(root: Path, folder_id: str, folders: dict) -> bool:
    """True when a folder looks like a pipeline/agent stage (generic heuristics)."""
    names = _child_names_on_disk(root, folder_id)
    hits = sum(1 for m in _STAGE_MARKERS if m in names)
    if hits >= 2:
        return True
    # Also: has src/ plus any markdown brief
    if "src" in names and any(n.endswith(".md") for n in names):
        return True
    info = folders.get(folder_id) or {}
    kids = info.get("children") or []
    # nested src/memory as graph children
    bases = {_basename(k) for k in kids}
    if "src" in bases and ("memory" in bases or hits >= 1):
        return True
    return False


def _order_from_text(text: str, sibling_ids: list[str]) -> list[str] | None:
    """Pull numbered order of sibling folders from README / docs tables."""
    if not text or not sibling_ids:
        return None
    by_base = {_basename(s).lower(): s for s in sibling_ids}
    by_full = {s.lower(): s for s in sibling_ids}
    by_full.update({s.replace("\\", "/").lower(): s for s in sibling_ids})

    found: list[tuple[int, str]] = []
    # | 1 | `agents/ingestion` |  or  1. `ingestion`
    patterns = [
        re.compile(
            r"""\|\s*(\d+)\s*\|\s*`?([A-Za-z0-9_./-]+)`?""",
            re.M,
        ),
        re.compile(
            r"""^\s*(\d+)[.)]\s+`?([A-Za-z0-9_./-]+)`?""",
            re.M,
        ),
        re.compile(
            r"""^\s*(\d+)\s+\|?\s*`([A-Za-z0-9_./-]+)`""",
            re.M,
        ),
    ]
    for pat in patterns:
        for m in pat.finditer(text):
            n = int(m.group(1))
            token = m.group(2).strip().strip("`")
            token_l = token.lower().rstrip("/")
            match = by_full.get(token_l)
            if not match:
                # path suffix match
                for sid in sibling_ids:
                    if sid.lower().endswith("/" + token_l) or sid.lower() == token_l:
                        match = sid
                        break
            if not match:
                match = by_base.get(token_l.split("/")[-1])
            if match:
                found.append((n, match))

    if len(found) < 2:
        return None
    found.sort(key=lambda x: x[0])
    ordered: list[str] = []
    seen: set[str] = set()
    for _, sid in found:
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    # Must cover most stage siblings to trust the doc
    if len(ordered) < max(2, len(sibling_ids) // 2):
        return None
    # Append any missing siblings in name order
    for sid in sorted(sibling_ids):
        if sid not in seen:
            ordered.append(sid)
    return ordered


def _topo_from_edges(sibling_ids: list[str], folder_edges: list[dict]) -> list[str] | None:
    sib = set(sibling_ids)
    indeg = {s: 0 for s in sibling_ids}
    succ: dict[str, list[str]] = {s: [] for s in sibling_ids}
    useful = 0
    for e in folder_edges:
        a, b = e.get("from"), e.get("to")
        if a in sib and b in sib and a != b:
            succ[a].append(b)
            indeg[b] += 1
            useful += 1
    if useful < 1:
        return None
    queue = sorted([s for s in sibling_ids if indeg[s] == 0])
    out: list[str] = []
    while queue:
        n = queue.pop(0)
        out.append(n)
        for m in sorted(succ[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
                queue.sort()
    if len(out) != len(sibling_ids):
        return None
    return out


def detect_stage_groups(
    root: Path,
    graph: FileGraph,
    folders: dict,
    folder_edges: list[dict],
) -> dict[str, list[str]]:
    """Map parent folder → ordered list of stage-like child folders."""
    root = Path(root)
    groups: dict[str, list[str]] = {}

    # Candidate parents = any folder with 2+ stage-like children
    parents: dict[str, list[str]] = {}
    for fid, meta in folders.items():
        if fid == "(root)":
            continue
        parent = meta.get("parent")
        if parent is None:
            continue
        if is_stage_folder(root, fid, folders):
            parents.setdefault(parent, []).append(fid)

    for parent, kids in parents.items():
        if len(kids) < 2:
            continue
        kids = sorted(set(kids))
        ordered = None

        # Prefer order from parent README / root README
        doc_paths = []
        if parent == "(root)":
            doc_paths = [root / "README.md"]
        else:
            doc_paths = [
                root / parent / "README.md",
                root / "README.md",
            ]
        for doc in doc_paths:
            if doc.is_file():
                try:
                    text = doc.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                ordered = _order_from_text(text, kids)
                if ordered:
                    break

        if not ordered:
            ordered = _topo_from_edges(kids, folder_edges)
        if not ordered:
            ordered = sorted(kids)
        groups[parent] = ordered

    return groups


def handoff_edges(stage_groups: dict[str, list[str]]) -> list[dict]:
    """Consecutive stage pairs → handoff edges."""
    edges: list[dict] = []
    for _parent, ordered in stage_groups.items():
        for a, b in zip(ordered, ordered[1:]):
            edges.append({"from": a, "to": b, "kind": "handoff"})
    return edges
