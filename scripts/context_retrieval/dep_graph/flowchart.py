"""Build a static left-to-right repo flowchart (positions computed in Python)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .handoff import detect_stage_groups, handoff_edges

if TYPE_CHECKING:
    from .scan import FileGraph

NODE_W = 158
NODE_H = 36
STAGE_W = 168
STAGE_H = 36
ROOT_H = 68
GAP_X = 96
GAP_Y = 16
PAD = 32
PAD_TOP = 64
PAD_BOTTOM = 64

# Keep diagrams readable but show real structure (not just top-level leaves).
_MAX_CHILDREN_PER_FOLDER = 16
_MAX_FILES_BESIDE = 10  # files drawn beside each col-2 folder so none look empty
_MAX_NODES = 220


def _label(fid: str, *, is_dir: bool) -> str:
    if fid == "(root)":
        return "repo"
    base = fid.rsplit("/", 1)[-1]
    return base + ("/" if is_dir else "")


def _file_label(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _repo_label(graph: FileGraph) -> str:
    """Short name for the root box — prefer owner/name, else directory name."""
    if graph.repo:
        name = graph.repo.strip().rstrip("/")
        if "/" in name:
            if len(name) <= 28:
                return name
            return name.split("/", 1)[1]
        return name
    try:
        return Path(graph.root).name or "repo"
    except (TypeError, ValueError):
        return "repo"


def _child_items(folders: dict, folder_id: str) -> list[tuple[str, str]]:
    """Return (id, kind) for immediate children: folders first, then files."""
    info = folders.get(folder_id) or {}
    items: list[tuple[str, str]] = []
    for cid in sorted(info.get("children") or []):
        items.append((cid, "folder"))
    for fp in sorted(info.get("files") or []):
        items.append((fp, "file"))
    return items


def build_flowchart(graph: FileGraph, folders: dict, folder_edges: list[dict]) -> dict:
    """Return nodes/edges with pixel positions for a static SVG.

    Layout:
    - col 0: repo
    - col 1: every top-level folder + root files
    - col 2: children of each top-level folder (always — not only pipelines)
    - col 3: one extra detail expansion (richest package / pipeline example)
    """
    root = Path(graph.root)
    root_label = _repo_label(graph)
    stage_groups = detect_stage_groups(root, graph, folders, folder_edges)
    stage_set = {s for kids in stage_groups.values() for s in kids}
    stage_num = {
        sid: i + 1 for _parent, ordered in stage_groups.items() for i, sid in enumerate(ordered)
    }
    h_edges = handoff_edges(stage_groups)

    root_info = folders.get("(root)") or {}
    top_folders = sorted(root_info.get("children") or [])
    # Prefer pipeline parents first when present
    top_folders = sorted(top_folders, key=lambda fid: (0 if fid in stage_groups else 1, fid))
    root_files = sorted(root_info.get("files") or [])

    nodes: dict[str, dict] = {}
    contains: list[dict] = []

    def add_node(
        nid: str,
        label: str,
        kind: str,
        col: int,
        row: float,
        *,
        stage_n: int | None = None,
    ) -> bool:
        if nid in nodes:
            return False
        if len(nodes) >= _MAX_NODES:
            return False
        if kind == "repo":
            w = max(200, min(280, 36 + len(label) * 8))
            h = ROOT_H
        elif kind == "stage":
            w, h = STAGE_W, STAGE_H
        else:
            w, h = NODE_W, NODE_H
        nodes[nid] = {
            "id": nid,
            "label": label,
            "kind": kind,
            "col": col,
            "row": float(row),
            "w": w,
            "h": h,
            "stage_num": stage_n,
        }
        return True

    def link_contains(a: str, b: str) -> None:
        contains.append({"from": a, "to": b, "kind": "contains"})

    add_node("(root)", root_label, "repo", 0, 0)

    def _files_of(folder_id: str) -> list[str]:
        return [i for i, k in _child_items(folders, folder_id) if k == "file"]

    def _dirs_of(folder_id: str) -> list[str]:
        return [i for i, k in _child_items(folders, folder_id) if k == "folder"]

    # --- Col 1 + Col 2 + Col 3 ---
    # Every col-2 folder gets its files drawn beside it (col 3) so folders
    # like workflows/ / config/ never look empty.
    row = 0.0
    col2_ids: list[str] = []
    expand_id: str | None = None
    note = ""

    for fid in top_folders:
        add_node(fid, _label(fid, is_dir=True), "folder", 1, row)
        link_contains("(root)", fid)

        items = _child_items(folders, fid)
        if fid in stage_groups:
            ordered = stage_groups[fid]
            ordered_set = set(ordered)
            rest = [it for it in items if it[0] not in ordered_set]
            items = [(sid, "folder") for sid in ordered] + rest

        if len(items) > _MAX_CHILDREN_PER_FOLDER:
            items = items[:_MAX_CHILDREN_PER_FOLDER]

        if not items:
            row += 1.0
            continue

        start = row
        for cid, ckind in items:
            if ckind == "file":
                if add_node(cid, _file_label(cid), "file", 2, row):
                    link_contains(fid, cid)
                    col2_ids.append(cid)
                    row += 1.0
                continue

            # Folder in col 2
            kind = "stage" if cid in stage_set else "folder"
            if not add_node(
                cid,
                _label(cid, is_dir=True),
                kind,
                2,
                row,
                stage_n=stage_num.get(cid),
            ):
                continue
            link_contains(fid, cid)
            col2_ids.append(cid)

            # Build detail list for col 3: immediate files, plus files inside
            # leaf subfolders (subfolders that only contain files).
            detail: list[tuple[str, str, str]] = []  # id, kind, parent_for_edge
            for fp in _files_of(cid)[:_MAX_FILES_BESIDE]:
                detail.append((fp, "file", cid))

            for sub in _dirs_of(cid):
                sub_files = _files_of(sub)
                sub_dirs = _dirs_of(sub)
                if not sub_files and not sub_dirs:
                    continue
                if sub_dirs:
                    # Nested package dir (e.g. collision_analyzer/collision_analyzer/)
                    # — show the folder + a sample of its files
                    detail.append((sub, "folder", cid))
                    for fp in sub_files[:_MAX_FILES_BESIDE]:
                        detail.append((fp, "file", sub))
                else:
                    # Leaf folder with only files (workflows/, config/, …)
                    detail.append((sub, "folder", cid))
                    for fp in sub_files[:_MAX_FILES_BESIDE]:
                        detail.append((fp, "file", sub))

            # Dedupe while preserving order
            seen_d: set[str] = set()
            uniq: list[tuple[str, str, str]] = []
            for item, ikind, parent in detail:
                if item in seen_d or item in nodes:
                    continue
                seen_d.add(item)
                uniq.append((item, ikind, parent))
            detail = uniq[: max(_MAX_FILES_BESIDE + 4, 14)]

            span = max(1, len(detail))
            for i, (item, ikind, parent) in enumerate(detail):
                dkind = "stage" if item in stage_set else ikind
                dlabel = (
                    _label(item, is_dir=True)
                    if ikind == "folder" or dkind == "stage"
                    else _file_label(item)
                )
                if add_node(item, dlabel, dkind, 3, row + i, stage_n=stage_num.get(item)):
                    link_contains(parent, item)

            nodes[cid]["row"] = row + (span - 1) / 2.0
            if detail and expand_id is None:
                expand_id = cid
            row += float(span)

        end = row - 1.0
        if end >= start:
            nodes[fid]["row"] = (start + end) / 2.0
        row += 0.4  # gap between top-level packages

    for fp in root_files:
        if add_node(fp, _file_label(fp), "file", 1, row):
            link_contains("(root)", fp)
            row += 1.0

    if expand_id:
        note = "Files are shown beside each folder — open a package block to see its contents"

    # Center repo on col-1 stack
    col1_rows = [n["row"] for n in nodes.values() if n["col"] == 1]
    if col1_rows:
        nodes["(root)"]["row"] = (min(col1_rows) + max(col1_rows)) / 2.0

    # Uses: only between different columns (same-column below-route looks like
    # fake vertical “pipeline” arrows between siblings).
    top_set = set(top_folders)

    def top_of(fid: str) -> str | None:
        cur = fid
        guard: set[str] = set()
        while cur and cur not in guard:
            guard.add(cur)
            if cur in top_set:
                return cur
            cur = (folders.get(cur) or {}).get("parent")
        return None

    rolled: dict[tuple[str, str], int] = {}
    for e in folder_edges:
        a = top_of(e.get("from") or "")
        b = top_of(e.get("to") or "")
        if not a or not b or a == b:
            continue
        if a not in nodes or b not in nodes:
            continue
        # Prefer linking the expanded detail nodes when both sides visible deeper
        rolled[(a, b)] = rolled.get((a, b), 0) + int(e.get("weight") or 1)

    uses: list[dict] = []
    for (a, b), w in sorted(rolled.items()):
        # Skip same-column pairs — they render as confusing vertical arrows
        if nodes[a]["col"] == nodes[b]["col"]:
            continue
        uses.append({"from": a, "to": b, "kind": "uses", "weight": w, "route": "below"})

    # Also keep file-level uses between visible nodes in different columns
    for e in graph.edges:
        frm, to = e.get("from"), e.get("to")
        if frm in nodes and to in nodes and nodes[frm]["col"] != nodes[to]["col"]:
            uses.append({"from": frm, "to": to, "kind": "uses", "weight": 1, "route": "below"})

    visible = set(nodes)
    handoffs = [
        {**e, "route": "box"}
        for e in h_edges
        if e["from"] in visible and e["to"] in visible
    ]

    # Pixel positions
    min_row = min((n["row"] for n in nodes.values()), default=0.0)
    if min_row != 0:
        for n in nodes.values():
            n["row"] -= min_row

    max_col = max((n["col"] for n in nodes.values()), default=0)
    max_row = max((n["row"] for n in nodes.values()), default=0.0)

    col_widths = {c: NODE_W for c in range(max_col + 1)}
    for n in nodes.values():
        col_widths[n["col"]] = max(col_widths[n["col"]], n["w"])

    col_x: dict[int, float] = {0: float(PAD)}
    for c in range(1, max_col + 1):
        col_x[c] = col_x[c - 1] + col_widths[c - 1] + GAP_X

    rails = {c: col_x[c] + col_widths[c] + GAP_X * 0.38 for c in range(max_col)}

    for n in nodes.values():
        n["x"] = col_x[n["col"]] + (col_widths[n["col"]] - n["w"]) / 2.0
        n["y"] = PAD_TOP + n["row"] * (NODE_H + GAP_Y)

    width = int(col_x[max_col] + col_widths[max_col] + PAD)
    height = int(
        PAD_TOP
        + PAD_BOTTOM
        + (max_row + 1) * (NODE_H + GAP_Y)
        + (40 if uses else 0)
    )

    n_files = len(graph.nodes)
    n_folders = max(0, len(folders) - 1)
    return {
        "nodes": list(nodes.values()),
        "edges": contains + handoffs + uses,
        "rails": rails,
        "stage_groups": {k: v for k, v in stage_groups.items()},
        "expanded": expand_id,
        "note": note,
        "width": max(width, 900),
        "height": max(height, 520),
        "meta": {
            "repo": graph.repo or Path(graph.root).name or "local",
            "commit": (graph.commit or "")[:7],
            "files": n_files,
            "file_links": len(graph.edges),
            "folders": n_folders,
        },
    }
