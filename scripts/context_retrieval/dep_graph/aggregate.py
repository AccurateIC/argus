"""Aggregate file edges into folder-level nodes and edges."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scan import FileGraph


def folder_of(path: str, depth: int = 2) -> str:
    """e.g. apps/api/src/foo.ts → apps/api when depth=2."""
    parts = path.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return "(root)"
    d = max(1, depth)
    if len(parts) <= d:
        return "/".join(parts[:-1]) if len(parts) > 1 else "(root)"
    return "/".join(parts[:d])


def aggregate_folders(graph: FileGraph, *, depth: int = 2) -> dict:
    """Return {folders: {id: meta}, folder_edges: [{from,to,weight}], file_to_folder}."""
    file_to_folder: dict[str, str] = {}
    folders: dict[str, dict] = {}
    weights: dict[tuple[str, str], int] = defaultdict(int)

    for path, meta in graph.nodes.items():
        folder = folder_of(path, depth)
        file_to_folder[path] = folder
        info = folders.setdefault(
            folder,
            {"file_count": 0, "test_count": 0, "files": []},
        )
        info["file_count"] += 1
        info["files"].append(path)
        if meta.get("kind") == "test":
            info["test_count"] += 1

    for e in graph.edges:
        a = file_to_folder.get(e["from"])
        b = file_to_folder.get(e["to"])
        if not a or not b or a == b:
            continue
        weights[(a, b)] += 1

    folder_edges = [
        {"from": a, "to": b, "weight": w}
        for (a, b), w in sorted(weights.items(), key=lambda x: (-x[1], x[0]))
    ]
    for f in folders.values():
        f["files"] = sorted(f["files"])
    return {
        "depth": depth,
        "folders": folders,
        "folder_edges": folder_edges,
        "file_to_folder": file_to_folder,
    }
