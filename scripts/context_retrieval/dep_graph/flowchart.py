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
GAP_Y = 22
PAD = 32
PAD_TOP = 64
PAD_BOTTOM = 64


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


def build_flowchart(graph: FileGraph, folders: dict, folder_edges: list[dict]) -> dict:
    """Return nodes/edges with pixel positions for a static SVG.

    Clean column layout:
    - col 0: repo name (vertically centered on col 1)
    - col 1: top-level folders/files
    - col 2: pipeline stages under the pipeline parent only
    - col 3: one expanded stage example
    """
    root = Path(graph.root)
    root_label = _repo_label(graph)
    stage_groups = detect_stage_groups(root, graph, folders, folder_edges)
    stage_set = {s for kids in stage_groups.values() for s in kids}
    h_edges = handoff_edges(stage_groups)

    root_info = folders.get("(root)") or {}
    top_folders = sorted(
        root_info.get("children") or [],
        key=lambda fid: (0 if fid in stage_groups else 1, fid),
    )
    root_files = sorted(root_info.get("files") or [])

    # Expand second stage when present (common “example” slot), else richest
    expand_id: str | None = None
    for parent in top_folders:
        if parent not in stage_groups or not stage_groups[parent]:
            continue
        ordered = stage_groups[parent]
        if len(ordered) >= 2:
            expand_id = ordered[1]
        else:
            expand_id = max(
                ordered,
                key=lambda sid: len((folders.get(sid) or {}).get("children") or [])
                + len((folders.get(sid) or {}).get("files") or []),
            )
        break

    nodes: dict[str, dict] = {}
    contains: list[dict] = []

    def add_node(
        nid: str,
        label: str,
        kind: str,
        col: int,
        row: float,
        *,
        stage_num: int | None = None,
    ) -> None:
        if nid in nodes:
            return
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
            "stage_num": stage_num,
        }

    def link_contains(a: str, b: str) -> None:
        contains.append({"from": a, "to": b, "kind": "contains"})

    # --- Build columns with integer rows (top → bottom) ---
    # Col 2 first so we know how tall the stage stack is
    pipe_parents = [f for f in top_folders if f in stage_groups]
    stage_children: list[str] = []
    pipe_files: list[str] = []
    row = 0
    for parent in pipe_parents:
        info = folders.get(parent) or {}
        ordered = list(stage_groups[parent])
        rest = sorted(
            c for c in (info.get("children") or []) if c not in set(ordered)
        )
        for cid in ordered + rest:
            kind = "stage" if cid in stage_set else "folder"
            snum = (ordered.index(cid) + 1) if cid in stage_set else None
            add_node(cid, _label(cid, is_dir=True), kind, 2, row, stage_num=snum)
            link_contains(parent, cid)
            stage_children.append(cid)
            row += 1
        for fp in sorted(info.get("files") or []):
            add_node(fp, _file_label(fp), "file", 2, row)
            link_contains(parent, fp)
            pipe_files.append(fp)
            stage_children.append(fp)
            row += 1

    stage_top = 0
    stage_bottom = max(0, row - 1)

    # Col 3: expand one stage — children packed starting at that stage's row
    note = ""
    expand_kids: list[str] = []
    if expand_id and expand_id in nodes:
        info = folders.get(expand_id) or {}
        kids = sorted(info.get("children") or [])
        files = sorted(info.get("files") or [])
        items = [(c, "folder") for c in kids] + [(f, "file") for f in files]
        base_row = int(nodes[expand_id]["row"])
        # Keep stack aligned to expand stage: first child on same row
        for i, (item, kind) in enumerate(items):
            label = _label(item, is_dir=True) if kind == "folder" else _file_label(item)
            add_node(item, label, kind, 3, base_row + i)
            link_contains(expand_id, item)
            expand_kids.append(item)
        if items and pipe_parents and len(stage_groups.get(pipe_parents[0], [])) > 1:
            note = "Other stages under the same parent follow this same pattern"

    # Col 1: pipeline parent centered on its stage kids; siblings packed
    # tightly under it (same column — Y may overlap col-2 stages, that's fine).
    add_node("(root)", root_label, "repo", 0, 0)

    if pipe_parents:
        agents = pipe_parents[0]
        # Center on stages only (not the README/QC files under agents)
        stage_only_rows = [
            nodes[c]["row"]
            for c in stage_children
            if c in nodes and nodes[c]["kind"] == "stage"
        ]
        if stage_only_rows:
            mid = (min(stage_only_rows) + max(stage_only_rows)) / 2.0
        else:
            mid = (stage_top + stage_bottom) / 2.0
        add_node(agents, _label(agents, is_dir=True), "folder", 1, 0)
        nodes[agents]["row"] = mid
        # Pack other top-level folders/files right under agents — no huge gap
        next_row = mid + 1.0
    else:
        next_row = 0.0

    others = [f for f in top_folders if f not in pipe_parents] + root_files
    for oid in others:
        if oid in (folders.get("(root)") or {}).get("children") or []:
            add_node(oid, _label(oid, is_dir=True), "folder", 1, next_row)
        else:
            add_node(oid, _file_label(oid), "file", 1, next_row)
        link_contains("(root)", oid)
        next_row += 1.0

    # Link root → pipeline parent
    for parent in pipe_parents:
        if parent in nodes:
            link_contains("(root)", parent)

    # Align repo with the main trunk (pipeline parent)
    if pipe_parents and pipe_parents[0] in nodes:
        nodes["(root)"]["row"] = nodes[pipe_parents[0]]["row"]
    else:
        col1_rows = [n["row"] for n in nodes.values() if n["col"] == 1]
        if col1_rows:
            nodes["(root)"]["row"] = (min(col1_rows) + max(col1_rows)) / 2.0

    # Uses: top-level only, skip if either end is missing
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
        if a and b and a != b and a in nodes and b in nodes:
            rolled[(a, b)] = rolled.get((a, b), 0) + int(e.get("weight") or 1)
    uses = [
        {"from": a, "to": b, "kind": "uses", "weight": w, "route": "below"}
        for (a, b), w in sorted(rolled.items())
    ]

    visible = set(nodes)
    handoffs = [
        {**e, "route": "right"}
        for e in h_edges
        if e["from"] in visible and e["to"] in visible
    ]

    # Pixel positions — integer-ish rows, shared column x
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

    # Rail x between columns (for orthogonal contains)
    rails = {
        c: col_x[c] + col_widths[c] + GAP_X * 0.38
        for c in range(max_col)
    }

    for n in nodes.values():
        n["x"] = col_x[n["col"]] + (col_widths[n["col"]] - n["w"]) / 2.0
        n["y"] = PAD_TOP + n["row"] * (NODE_H + GAP_Y)

    width = int(col_x[max_col] + col_widths[max_col] + PAD)
    # Extra bottom space for below-routed uses edges
    height = int(
        PAD_TOP
        + PAD_BOTTOM
        + (max_row + 1) * (NODE_H + GAP_Y)
        + (36 if uses else 0)
    )

    n_files = len(graph.nodes)
    n_folders = max(0, len(folders) - 1)  # exclude (root)
    return {
        "nodes": list(nodes.values()),
        "edges": contains + handoffs + uses,
        "rails": rails,
        "stage_groups": {k: v for k, v in stage_groups.items()},
        "expanded": expand_id,
        "note": note,
        "width": max(width, 860),
        "height": max(height, 520),
        "meta": {
            "repo": graph.repo or Path(graph.root).name or "local",
            "commit": (graph.commit or "")[:7],
            "files": n_files,
            "file_links": len(graph.edges),
            "folders": n_folders,
        },
    }
