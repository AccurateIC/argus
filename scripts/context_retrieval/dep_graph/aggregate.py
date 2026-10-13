"""Build a real directory tree + folder-level import edges."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scan import FileGraph


def parent_dir(path: str) -> str:
    """Directory containing a file, or '(root)' for top-level files."""
    p = path.replace("\\", "/").rstrip("/")
    if "/" not in p:
        return "(root)"
    return p.rsplit("/", 1)[0]


def folder_parent(folder: str) -> str | None:
    """Parent folder id, or None for '(root)'."""
    if folder == "(root)":
        return None
    if "/" not in folder:
        return "(root)"
    return folder.rsplit("/", 1)[0]


def aggregate_folders(graph: FileGraph, *, depth: int = 2) -> dict:
    """Return tree structure + import aggregation.

    ``depth`` is kept for API compat but the UI uses the full directory tree
    (every real directory that contains files or subfolders).
    """
    del depth  # unused — full tree

    file_to_folder: dict[str, str] = {}
    folders: dict[str, dict] = {
        "(root)": {
            "file_count": 0,
            "test_count": 0,
            "files": [],
            "children": [],
            "parent": None,
        }
    }

    def ensure_folder(fid: str) -> None:
        cur = fid
        while True:
            if cur not in folders:
                folders[cur] = {
                    "file_count": 0,
                    "test_count": 0,
                    "files": [],
                    "children": [],
                    "parent": folder_parent(cur),
                }
            if cur == "(root)":
                break
            nxt = folder_parent(cur)
            if nxt is None:
                break
            cur = nxt

    # Shell dirs from disk walk (binary-only folders, .github, …)
    for shell in getattr(graph, "extra_folders", None) or []:
        if shell and shell != ".":
            ensure_folder(shell)

    # Map each file to its immediate containing directory
    for path, meta in graph.nodes.items():
        folder = parent_dir(path)
        file_to_folder[path] = folder
        ensure_folder(folder)

        info = folders[folder]
        info["file_count"] += 1
        info["files"].append(path)
        if meta.get("kind") == "test":
            info["test_count"] += 1

    # Ensure ancestor folders exist even if they only contain subdirs
    for fid in list(folders.keys()):
        p = folder_parent(fid)
        while p is not None:
            if p not in folders:
                folders[p] = {
                    "file_count": 0,
                    "test_count": 0,
                    "files": [],
                    "children": [],
                    "parent": folder_parent(p),
                }
            p = folder_parent(p)

    # children lists
    children_map: dict[str, list[str]] = defaultdict(list)
    for fid, meta in folders.items():
        par = meta.get("parent")
        if par is not None and par in folders:
            children_map[par].append(fid)
        elif fid != "(root)":
            children_map["(root)"].append(fid)
            meta["parent"] = "(root)"

    for par, kids in children_map.items():
        folders[par]["children"] = sorted(set(kids))

    for f in folders.values():
        f["files"] = sorted(set(f["files"]))

    # Tree edges: parent contains child
    tree_edges = []
    for fid, meta in folders.items():
        for child in meta.get("children") or []:
            tree_edges.append({"from": fid, "to": child, "kind": "contains"})

    # Import / uses edges between folders (any depth)
    weights: dict[tuple[str, str], int] = defaultdict(int)
    for e in graph.edges:
        a = file_to_folder.get(e["from"])
        b = file_to_folder.get(e["to"])
        if not a or not b or a == b:
            continue
        weights[(a, b)] += 1

    # Path / mount refs: file → directory (e.g. backend → data/uploads)
    for e in getattr(graph, "dir_refs", None) or []:
        a = file_to_folder.get(e["from"])
        b = e.get("to")
        if not a or not b or a == b:
            continue
        ensure_folder(b)
        weights[(a, b)] += 1

    def _nested(a: str, b: str) -> bool:
        """True if one folder contains the other (tree relationship, not a use)."""
        if a == "(root)" or b == "(root)":
            return False
        return a.startswith(b + "/") or b.startswith(a + "/")

    folder_edges = [
        {"from": a, "to": b, "weight": w, "kind": "imports"}
        for (a, b), w in sorted(weights.items(), key=lambda x: (-x[1], x[0]))
        if not _nested(a, b)
    ]

    return {
        "depth": "tree",
        "folders": folders,
        "tree_edges": tree_edges,
        "folder_edges": folder_edges,
        "file_to_folder": file_to_folder,
    }


# Back-compat helper used in tests
def folder_of(path: str, depth: int = 2) -> str:
    """Legacy: first N path segments (kept for unit tests)."""
    parts = path.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return "(root)"
    d = max(1, depth)
    if len(parts) <= d:
        return "/".join(parts[:-1]) if len(parts) > 1 else "(root)"
    return "/".join(parts[:d])
