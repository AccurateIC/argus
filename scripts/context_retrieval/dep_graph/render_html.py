"""Emit one self-contained static HTML flowchart (polished repo overview)."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .aggregate import aggregate_folders
from .flowchart import build_flowchart

if TYPE_CHECKING:
    from .scan import FileGraph


def _escape(s: str) -> str:
    return html.escape(s or "", quote=True)


def _svg_arrow_marker(mid: str, color: str, *, size: float = 10) -> str:
    # refX sits just before tip so the tip lands on the path end
    h = size * 0.8
    return (
        f'<marker id="{mid}" markerWidth="{size}" markerHeight="{h}" '
        f'refX="{size - 1}" refY="{h / 2}" orient="auto" markerUnits="userSpaceOnUse">'
        f'<path d="M0,0 L{size},{h / 2} L0,{h} Z" fill="{color}"/>'
        f"</marker>"
    )


def _icon_folder(x: float, y: float, color: str) -> str:
    # Simple folder glyph
    return (
        f'<path d="M{x:.1f},{y + 4:.1f} h6 l2,3 h10 v11 h-18 z" '
        f'fill="none" stroke="{color}" stroke-width="1.4" stroke-linejoin="round"/>'
    )


def _icon_file(x: float, y: float, color: str) -> str:
    return (
        f'<path d="M{x + 3:.1f},{y:.1f} h8 l5,5 v12 h-13 z" '
        f'fill="none" stroke="{color}" stroke-width="1.4" stroke-linejoin="round"/>'
        f'<path d="M{x + 11:.1f},{y:.1f} v5 h5" fill="none" stroke="{color}" stroke-width="1.4"/>'
    )


def _icon_branch(x: float, y: float, color: str) -> str:
    return (
        f'<circle cx="{x + 4:.1f}" cy="{y + 4:.1f}" r="2.5" fill="none" stroke="{color}" stroke-width="1.3"/>'
        f'<circle cx="{x + 4:.1f}" cy="{y + 16:.1f}" r="2.5" fill="none" stroke="{color}" stroke-width="1.3"/>'
        f'<circle cx="{x + 14:.1f}" cy="{y + 10:.1f}" r="2.5" fill="none" stroke="{color}" stroke-width="1.3"/>'
        f'<path d="M{x + 4:.1f},{y + 6.5:.1f} V{y + 13.5:.1f} M{x + 4:.1f},{y + 10:.1f} H{x + 11.5:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="1.3"/>'
    )


def _ortho_contains(a: dict, b: dict, rail_x: float | None) -> str:
    ax = a["x"] + a["w"]
    ay = a["y"] + a["h"] / 2
    bx = b["x"]
    by = b["y"] + b["h"] / 2
    if rail_x is None:
        rail_x = (ax + bx) / 2
    return f"M{ax:.1f},{ay:.1f} H{rail_x:.1f} V{by:.1f} H{bx:.1f}"


def _ortho_handoff(a: dict, b: dict) -> str:
    """Box-to-box handoff: bottom-center of A → tip just above top of B."""
    ax = a["x"] + a["w"] / 2
    ay = a["y"] + a["h"]  # bottom edge of A
    bx = b["x"] + b["w"] / 2
    # stop a few px above B so the arrow tip kisses the box (drawn after nodes)
    by = b["y"] - 1
    if abs(ax - bx) < 0.5:
        return f"M{ax:.1f},{ay:.1f} V{by:.1f}"
    mid_y = (ay + by) / 2
    return f"M{ax:.1f},{ay:.1f} V{mid_y:.1f} H{bx:.1f} V{by:.1f}"


def _ortho_uses_below(a: dict, b: dict, floor_y: float) -> str:
    ax = a["x"] + a["w"] / 2
    ay = a["y"] + a["h"]
    bx = b["x"] + b["w"] / 2
    by = b["y"] + b["h"]
    return f"M{ax:.1f},{ay:.1f} V{floor_y:.1f} H{bx:.1f} V{by:.1f}"


def _render_node(n: dict, colors: dict) -> str:
    kind = n["kind"]
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    label = n["label"]
    clickable = kind in {"repo", "folder", "stage"}
    cursor = "cursor:pointer;" if clickable else ""
    data_attr = f' data-folder-id="{_escape(n["id"])}"' if clickable else ""
    parts: list[str] = [f'<g class="node node-{kind}" style="{cursor}"{data_attr}>']

    if kind == "repo":
        fill, stroke = colors["repo_fill"], colors["repo_stroke"]
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="10" ry="10" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>'
        )
        parts.append(_icon_branch(x + 12, y + 24, colors["accent"]))
        # Title + subtitle lines
        title = label
        if len(title) > 26:
            title = title[:25] + "…"
        meta = n.get("subtitle") or ""
        parts.append(
            f'<text x="{x + 36:.1f}" y="{y + 28:.1f}" fill="{colors["text"]}" '
            f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="13" font-weight="600">'
            f"{_escape(title)}</text>"
        )
        if meta:
            parts.append(
                f'<text x="{x + 36:.1f}" y="{y + 48:.1f}" fill="{colors["muted"]}" '
                f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="11">'
                f"{_escape(meta)}</text>"
            )
    elif kind == "stage":
        fill, stroke = colors["stage_fill"], colors["stage_stroke"]
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="8" ry="8" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>'
        )
        num = n.get("stage_num") or 1
        cx, cy = x + 18, y + h / 2
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="10" fill="{colors["stage_badge"]}" '
            f'stroke="{colors["stage_stroke"]}" stroke-width="1.2"/>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" fill="#fff" '
            f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="11" font-weight="700">'
            f"{int(num)}</text>"
        )
        max_chars = max(10, int((w - 40) / 7.5))
        text = label if len(label) <= max_chars else label[: max_chars - 1] + "…"
        parts.append(
            f'<text x="{x + 36:.1f}" y="{y + h / 2 + 4:.1f}" fill="#fff" '
            f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="12.5" font-weight="600">'
            f"{_escape(text)}</text>"
        )
    elif kind == "file":
        fill, stroke = colors["file_fill"], colors["file_stroke"]
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="8" ry="8" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>'
        )
        parts.append(_icon_file(x + 10, y + 8, colors["file_icon"]))
        max_chars = max(10, int((w - 36) / 7.5))
        text = label if len(label) <= max_chars else label[: max_chars - 1] + "…"
        parts.append(
            f'<text x="{x + 34:.1f}" y="{y + h / 2 + 4:.1f}" fill="{colors["text"]}" '
            f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="12.5">'
            f"{_escape(text)}</text>"
        )
    else:  # folder
        fill, stroke = colors["folder_fill"], colors["folder_stroke"]
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="8" ry="8" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>'
        )
        parts.append(_icon_folder(x + 10, y + 8, colors["folder_icon"]))
        max_chars = max(10, int((w - 36) / 7.5))
        text = label if len(label) <= max_chars else label[: max_chars - 1] + "…"
        parts.append(
            f'<text x="{x + 34:.1f}" y="{y + h / 2 + 4:.1f}" fill="{colors["text"]}" '
            f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="12.5">'
            f"{_escape(text)}</text>"
        )

    parts.append(f'<title>{_escape(n["id"])}</title></g>')
    return "".join(parts)


def _legend_bar(
    x: float,
    y: float,
    width: float,
    colors: dict,
) -> str:
    """Five-column legend matching the reference UX."""
    font = 'font-family="Segoe UI, Helvetica, Arial, sans-serif"'
    h = 88
    pad = 16
    parts = [
        f'<g class="legend">'
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{h}" rx="10" '
        f'fill="{colors["panel"]}" stroke="{colors["panel_stroke"]}" stroke-width="1.2"/>'
        f'<text x="{x + pad:.1f}" y="{y + 22:.1f}" fill="{colors["text"]}" {font} '
        f'font-size="14" font-weight="700">How to read this graph</text>'
    ]

    # Five equal columns under the title
    cols = 5
    inner_x = x + pad
    inner_w = width - pad * 2
    col_w = inner_w / cols
    cy = y + 52  # icon vertical center
    text_y1 = y + 46
    text_y2 = y + 62

    def divider(i: int) -> None:
        if i <= 0:
            return
        dx = inner_x + i * col_w
        parts.append(
            f'<line x1="{dx:.1f}" y1="{y + 32:.1f}" x2="{dx:.1f}" y2="{y + h - 12:.1f}" '
            f'stroke="{colors["panel_stroke"]}" stroke-width="1"/>'
        )

    # --- Col 0: Folder ---
    divider(0)
    cx0 = inner_x + 14
    parts.append(_icon_folder_filled(cx0, cy - 8, "#3b82f6"))
    tx = inner_x + 36
    parts.append(
        f'<text x="{tx:.1f}" y="{text_y1:.1f}" fill="{colors["text"]}" {font} '
        f'font-size="12" font-weight="700">Folder:</text>'
    )
    parts.append(
        f'<text x="{tx:.1f}" y="{text_y2:.1f}" fill="{colors["muted"]}" {font} font-size="11">'
        f"holds files or other folders.</text>"
    )

    # --- Col 1: File ---
    divider(1)
    cx1 = inner_x + col_w + 14
    parts.append(_icon_file_filled(cx1, cy - 10, "#10b981"))
    tx = inner_x + col_w + 36
    parts.append(
        f'<text x="{tx:.1f}" y="{text_y1:.1f}" fill="{colors["text"]}" {font} '
        f'font-size="12" font-weight="700">File:</text>'
    )
    parts.append(
        f'<text x="{tx:.1f}" y="{text_y2:.1f}" fill="{colors["muted"]}" {font} font-size="11">'
        f"a document or code file.</text>"
    )

    # --- Col 2: Contains ---
    divider(2)
    cx2 = inner_x + 2 * col_w + 14
    parts.append(
        f'<line x1="{cx2:.1f}" y1="{cy:.1f}" x2="{cx2 + 22:.1f}" y2="{cy:.1f}" '
        f'stroke="{colors["contains"]}" stroke-width="2.2" marker-end="url(#arrow-contains)"/>'
    )
    tx = inner_x + 2 * col_w + 44
    parts.append(
        f'<text x="{tx:.1f}" y="{text_y1:.1f}" fill="{colors["text"]}" {font} '
        f'font-size="12" font-weight="700">Contains:</text>'
    )
    parts.append(
        f'<text x="{tx:.1f}" y="{text_y2:.1f}" fill="{colors["muted"]}" {font} font-size="11">'
        f"folder → what’s inside it.</text>"
    )

    # --- Col 3: Pipeline order (1 ↓ 2) ---
    divider(3)
    cx3 = inner_x + 3 * col_w + 16
    parts.append(
        f'<circle cx="{cx3:.1f}" cy="{cy - 11:.1f}" r="7" fill="#8b5cf6" stroke="#a78bfa"/>'
    )
    parts.append(
        f'<text x="{cx3:.1f}" y="{cy - 7.5:.1f}" text-anchor="middle" fill="#fff" '
        f'{font} font-size="9" font-weight="700">1</text>'
    )
    parts.append(
        f'<line x1="{cx3:.1f}" y1="{cy - 3:.1f}" x2="{cx3:.1f}" y2="{cy + 4:.1f}" '
        f'stroke="#8b5cf6" stroke-width="2" marker-end="url(#arrow-handoff)"/>'
    )
    parts.append(
        f'<circle cx="{cx3:.1f}" cy="{cy + 12:.1f}" r="7" fill="#8b5cf6" stroke="#a78bfa"/>'
    )
    parts.append(
        f'<text x="{cx3:.1f}" y="{cy + 15.5:.1f}" text-anchor="middle" fill="#fff" '
        f'{font} font-size="9" font-weight="700">2</text>'
    )
    tx = inner_x + 3 * col_w + 32
    parts.append(
        f'<text x="{tx:.1f}" y="{text_y1:.1f}" fill="{colors["text"]}" {font} '
        f'font-size="12" font-weight="700">Pipeline order:</text>'
    )
    parts.append(
        f'<text x="{tx:.1f}" y="{text_y2:.1f}" fill="{colors["muted"]}" {font} font-size="11">'
        f"step 1 runs before step 2.</text>"
    )

    # --- Col 4: Uses / imports ---
    divider(4)
    cx4 = inner_x + 4 * col_w + 14
    parts.append(
        f'<line x1="{cx4:.1f}" y1="{cy:.1f}" x2="{cx4 + 22:.1f}" y2="{cy:.1f}" '
        f'stroke="{colors["uses"]}" stroke-width="2.2" stroke-dasharray="5 3" '
        f'marker-end="url(#arrow-uses)"/>'
    )
    tx = inner_x + 4 * col_w + 44
    parts.append(
        f'<text x="{tx:.1f}" y="{text_y1:.1f}" fill="{colors["text"]}" {font} '
        f'font-size="12" font-weight="700">Uses / imports:</text>'
    )
    parts.append(
        f'<text x="{tx:.1f}" y="{text_y2:.1f}" fill="{colors["muted"]}" {font} font-size="11">'
        f"one part depends on another.</text>"
    )

    parts.append("</g>")
    return "".join(parts)


def _icon_folder_filled(x: float, y: float, color: str) -> str:
    return (
        f'<path d="M{x:.1f},{y + 5:.1f} h7 l2.5,3 h11.5 v10 h-21 z" '
        f'fill="{color}" stroke="{color}" stroke-width="0.5" stroke-linejoin="round"/>'
    )


def _icon_file_filled(x: float, y: float, color: str) -> str:
    return (
        f'<path d="M{x + 2:.1f},{y:.1f} h9 l5,5 v13 h-14 z" '
        f'fill="none" stroke="{color}" stroke-width="1.6" stroke-linejoin="round"/>'
        f'<path d="M{x + 11:.1f},{y:.1f} v5 h5" fill="none" stroke="{color}" stroke-width="1.6"/>'
        f'<path d="M{x + 5:.1f},{y + 11:.1f} h8 M{x + 5:.1f},{y + 14.5:.1f} h8 '
        f'M{x + 5:.1f},{y + 18:.1f} h5" stroke="{color}" stroke-width="1.2"/>'
    )


def _render_svg(scene: dict) -> str:
    by_id = {n["id"]: n for n in scene["nodes"]}
    meta = scene.get("meta") or {}
    rails = {int(k): float(v) for k, v in (scene.get("rails") or {}).items()}

    colors = {
        "bg": "#0b1220",
        "panel": "#121a2b",
        "panel_stroke": "#2a3650",
        "text": "#e8eef8",
        "muted": "#8b9bb4",
        "accent": "#5b9fd4",
        "repo_fill": "#152033",
        "repo_stroke": "#3d5a80",
        "folder_fill": "#132033",
        "folder_stroke": "#3b82c4",
        "folder_icon": "#5ba3e0",
        "file_fill": "#0f2420",
        "file_stroke": "#2dd4a8",
        "file_icon": "#3ecf8e",
        "stage_fill": "#4c3db8",
        "stage_stroke": "#8b7cf7",
        "stage_badge": "#3d2fa0",
        "contains": "#c5d0e0",
        "handoff": "#8b7cf7",
        "uses": "#5b9fd4",
    }

    # Enrich root node subtitle
    for n in scene["nodes"]:
        if n["kind"] == "repo":
            bits = []
            if meta.get("commit"):
                bits.append("@" + str(meta["commit"]))
            bits.append(f"{meta.get('files', 0)} files")
            bits.append(f"{meta.get('folders', 0)} folders")
            n["subtitle"] = " · ".join(bits)

    # Shift tall root so its vertical center matches its row mid
    for n in scene["nodes"]:
        if n["kind"] == "repo":
            # flowchart used NODE_H-based row; nudge so midlines align
            from .flowchart import NODE_H, ROOT_H

            n["y"] = n["y"] - (ROOT_H - NODE_H) / 2

    width = max(int(scene["width"]), 900)
    # Extra room for header text + bottom legend
    header_h = 56
    legend_h = 100
    body_bottom = 0.0
    if scene["nodes"]:
        body_bottom = max(n["y"] + n["h"] for n in scene["nodes"])
    floor_y = body_bottom + 36
    height = int(max(scene["height"], floor_y + legend_h + 28))

    # Bump all node Y down for in-SVG title
    dy = header_h
    for n in scene["nodes"]:
        n["y"] += dy
    floor_y += dy
    height += dy

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Argus Repository Dependency Graph">',
        f'<rect width="100%" height="100%" fill="{colors["bg"]}"/>',
        "<defs>",
        _svg_arrow_marker("arrow-contains", colors["contains"], size=9),
        _svg_arrow_marker("arrow-handoff", colors["handoff"], size=6),
        _svg_arrow_marker("arrow-uses", colors["uses"], size=9),
        "</defs>",
    ]

    # Header
    repo = meta.get("repo") or "local"
    commit = meta.get("commit") or ""
    parts.append(
        f'<text x="28" y="28" fill="{colors["text"]}" '
        f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="16" font-weight="700">'
        f"Argus Repository Dependency Graph</text>"
    )
    meta_line = "  ·  ".join(
        x
        for x in [
            f"Repository: {repo}",
            f"Commit: {commit}" if commit else "",
            f"Files: {meta.get('files', 0)}",
            f"File-links: {meta.get('file_links', 0)}",
            f"Folders: {meta.get('folders', 0)}",
            "click a folder to open",
        ]
        if x
    )
    parts.append(
        f'<text x="28" y="48" fill="{colors["muted"]}" '
        f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="11">'
        f"{_escape(meta_line)}</text>"
    )

    def _draw_edge(e: dict) -> None:
        a = by_id.get(e["from"])
        b = by_id.get(e["to"])
        if not a or not b:
            return
        kind = e.get("kind") or "contains"
        if kind == "handoff":
            stroke, marker, width_s, dash = colors["handoff"], "arrow-handoff", 2.6, ""
            d = _ortho_handoff(a, b)
        elif kind == "uses":
            stroke, marker, width_s, dash = (
                colors["uses"],
                "arrow-uses",
                1.8,
                ' stroke-dasharray="6 4"',
            )
            d = _ortho_uses_below(a, b, floor_y)
        else:
            stroke, marker, width_s, dash = colors["contains"], "arrow-contains", 1.6, ""
            rail = (a["x"] + a["w"] + b["x"]) / 2
            d = _ortho_contains(a, b, rail)
        parts.append(
            f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{width_s}"'
            f'{dash} marker-end="url(#{marker})"/>'
        )

    # Contains + uses under nodes; handoff on top so shafts stay visible box→box
    for e in scene["edges"]:
        if e.get("kind") != "handoff":
            _draw_edge(e)

    for n in scene["nodes"]:
        parts.append(_render_node(n, colors))

    for e in scene["edges"]:
        if e.get("kind") == "handoff":
            _draw_edge(e)

    # Note
    if scene.get("note"):
        parts.append(
            f'<text x="28" y="{floor_y - 10:.1f}" fill="{colors["muted"]}" '
            f'font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="11">'
            f'{_escape(scene["note"])}</text>'
        )

    # Bottom legend bar
    legend_y = height - 96
    parts.append(_legend_bar(24, legend_y, width - 48, colors))

    parts.append("</svg>")
    return "\n".join(parts)


_HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Argus Repository Dependency Graph — __TITLE__</title>
<style>
  :root { --bg: #0b1220; --text: #e8eef8; --muted: #8b9bb4; --panel: #121a2b; --line: #2a3650; }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: "Segoe UI", Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh;
  }
  #bar {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding: 10px 16px; background: var(--panel); border-bottom: 1px solid var(--line);
  }
  #btnBack {
    background: #1a2740; color: var(--text); border: 1px solid #3d5a80;
    border-radius: 6px; padding: 6px 12px; font-size: 13px; cursor: pointer;
  }
  #btnBack:disabled { opacity: 0.35; cursor: default; }
  #btnBack:not(:disabled):hover { background: #243552; }
  #crumb { color: var(--muted); font-size: 13px; flex: 1; }
  #wrap { padding: 8px 12px 12px; overflow: auto; }
  #stage svg { display: block; margin: 0 auto; max-width: none; }
  #stage g[data-folder-id] { cursor: pointer; }
  #legend {
    margin: 8px 12px 0; padding: 8px 10px 9px; max-width: 420px;
    background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  }
  #legend h2 {
    margin: 0 0 6px; font-size: 11px; font-weight: 700; color: var(--text);
  }
  #legend .cols {
    display: flex; flex-direction: column; gap: 5px;
  }
  #legend .col {
    display: flex; gap: 8px; align-items: center;
    border-left: none; padding-left: 0;
  }
  #legend .ico {
    flex: 0 0 22px; width: 22px; height: 16px;
    display: flex; align-items: center; justify-content: center;
  }
  #legend .ico svg { max-width: 20px; max-height: 16px; }
  #legend .txt { font-size: 10px; line-height: 1.25; color: var(--muted); }
  #legend .txt strong { color: var(--text); font-weight: 600; margin-right: 4px; }
</style>
</head>
<body>
<div id="bar">
  <button type="button" id="btnBack" disabled>Expand all</button>
  <div id="crumb">Folders start closed — click ▸ to open</div>
</div>
<div id="legend">
  <h2>How to read this graph</h2>
  <div class="cols">
    <div class="col">
      <div class="ico"><svg width="16" height="13" viewBox="0 0 22 18"><path d="M1 6 h7 l2.5 3 h11.5 v9 h-21 z" fill="#3b82f6"/></svg></div>
      <div class="txt"><strong>Folder:</strong>holds files/folders · click ▸/▾</div>
    </div>
    <div class="col">
      <div class="ico"><svg width="13" height="15" viewBox="0 0 18 20"><path d="M2 1 h9 l5 5 v13 h-14 z" fill="none" stroke="#10b981" stroke-width="1.8"/><path d="M11 1 v5 h5" fill="none" stroke="#10b981" stroke-width="1.8"/></svg></div>
      <div class="txt"><strong>File:</strong>document or code file</div>
    </div>
    <div class="col">
      <div class="ico"><svg width="20" height="10" viewBox="0 0 28 12"><line x1="0" y1="6" x2="20" y2="6" stroke="#c5d0e0" stroke-width="2"/><polygon points="20,2 28,6 20,10" fill="#c5d0e0"/></svg></div>
      <div class="txt"><strong>Contains:</strong>folder → what’s inside</div>
    </div>
    <div class="col">
      <div class="ico"><svg width="12" height="22" viewBox="0 0 20 36"><circle cx="10" cy="8" r="7" fill="#8b5cf6"/><text x="10" y="11.5" text-anchor="middle" fill="#fff" font-size="9" font-weight="700" font-family="sans-serif">1</text><line x1="10" y1="16" x2="10" y2="22" stroke="#8b5cf6" stroke-width="2"/><circle cx="10" cy="29" r="7" fill="#8b5cf6"/><text x="10" y="32.5" text-anchor="middle" fill="#fff" font-size="9" font-weight="700" font-family="sans-serif">2</text></svg></div>
      <div class="txt"><strong>Pipeline:</strong>step 1 before step 2</div>
    </div>
    <div class="col">
      <div class="ico"><svg width="20" height="10" viewBox="0 0 28 12"><line x1="0" y1="6" x2="20" y2="6" stroke="#5b9fd4" stroke-width="2" stroke-dasharray="4 3"/><polygon points="20,2 28,6 20,10" fill="#5b9fd4"/></svg></div>
      <div class="txt"><strong>Uses:</strong>one part depends on another</div>
    </div>
  </div>
</div>
<div id="wrap"><div id="stage">
__SVG__
</div></div>
<script type="application/json" id="graph-data">__DATA__</script>
<script>
__INTERACTIVE_JS__
</script>
</body>
</html>
"""


def _load_interactive_js() -> str:
    path = Path(__file__).resolve().parent / "interactive.js"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "/* interactive.js missing */"


def render_dep_graph_html(
    graph: FileGraph,
    *,
    folder_depth: int = 2,
    title: str = "",
) -> str:
    agg = aggregate_folders(graph, depth=folder_depth)
    folders = {
        k: {
            "file_count": v["file_count"],
            "test_count": v["test_count"],
            "files": v["files"],
            "children": v.get("children") or [],
            "parent": v.get("parent"),
        }
        for k, v in agg["folders"].items()
    }
    scene = build_flowchart(graph, folders, agg.get("folder_edges") or [])
    svg = _render_svg(scene)

    payload = {
        "repo": graph.repo,
        "commit": graph.commit,
        "built_at": graph.built_at,
        "title": title or graph.repo or "dependency graph",
        "nodes": graph.nodes,
        "edges": graph.edges,
        "folders": folders,
        "tree_edges": agg.get("tree_edges") or [],
        "folder_edges": agg.get("folder_edges") or [],
        "handoff_groups": scene.get("stage_groups") or {},
        "flowchart": {
            "nodes": scene["nodes"],
            "edges": scene["edges"],
            "expanded": scene.get("expanded"),
        },
        "languages": list(graph.languages or []),
        "stats": {
            "files": len(graph.nodes),
            "edges": len(graph.edges),
            "folders": len(folders),
        },
    }
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data_json = data_json.replace("<", "\\u003c")

    html_out = _HTML_SHELL
    html_out = html_out.replace("__TITLE__", _escape_title(payload["title"]))
    html_out = html_out.replace("__SVG__", svg)
    html_out = html_out.replace("__DATA__", data_json)
    html_out = html_out.replace("__INTERACTIVE_JS__", _load_interactive_js())
    return html_out


def _escape_title(s: str) -> str:
    return (
        (s or "dep-graph")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_dep_graph_html(
    graph: FileGraph,
    out_path: Path,
    *,
    folder_depth: int = 2,
    title: str = "",
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_out = render_dep_graph_html(graph, folder_depth=folder_depth, title=title)
    out_path.write_text(html_out, encoding="utf-8")
    return out_path
