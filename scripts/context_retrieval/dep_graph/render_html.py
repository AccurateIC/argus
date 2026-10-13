"""Emit one self-contained interactive HTML dependency diagram."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .aggregate import aggregate_folders

if TYPE_CHECKING:
    from .scan import FileGraph

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Argus dependency graph — __TITLE__</title>
<style>
  :root {
    --bg: #0f1419;
    --panel: #1a2332;
    --text: #e7ecf3;
    --muted: #8b9bb4;
    --accent: #3d9cf0;
    --folder: #5b8def;
    --file: #3ecf8e;
    --test: #e6a23c;
    --edge: #4a5d78;
    --ext: #7a8699;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
    background: var(--bg); color: var(--text); height: 100vh;
    display: flex; flex-direction: column;
  }
  header {
    padding: 10px 16px; background: var(--panel);
    border-bottom: 1px solid #2a3548;
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; }
  header .meta { color: var(--muted); font-size: 12px; }
  #toolbar { display: flex; gap: 8px; align-items: center; margin-left: auto; }
  button {
    background: #243044; color: var(--text); border: 1px solid #3a4a63;
    border-radius: 6px; padding: 6px 12px; cursor: pointer; font-size: 13px;
  }
  button:hover { background: #2d3c54; }
  button:disabled { opacity: 0.4; cursor: default; }
  #breadcrumb { font-size: 13px; color: var(--muted); width: 100%; }
  #breadcrumb strong { color: var(--accent); }
  #stage { flex: 1; position: relative; overflow: hidden; }
  svg { width: 100%; height: 100%; display: block; cursor: grab; }
  svg:active { cursor: grabbing; }
  .node circle { stroke: #0f1419; stroke-width: 2; cursor: pointer; }
  .node.folder circle { fill: var(--folder); }
  .node.file circle { fill: var(--file); }
  .node.test circle { fill: var(--test); }
  .node.external circle { fill: var(--ext); }
  .node text {
    fill: var(--text); font-size: 11px; pointer-events: none;
    text-anchor: middle; paint-order: stroke; stroke: var(--bg); stroke-width: 3px;
  }
  .link { stroke: var(--edge); stroke-opacity: 0.85; fill: none; }
  .hint {
    position: absolute; bottom: 12px; left: 12px; right: 12px;
    color: var(--muted); font-size: 12px; pointer-events: none;
  }
  .legend { display: flex; gap: 12px; font-size: 11px; color: var(--muted); }
  .legend span::before {
    content: ""; display: inline-block; width: 8px; height: 8px;
    border-radius: 50%; margin-right: 4px; vertical-align: middle;
  }
  .legend .l-folder::before { background: var(--folder); }
  .legend .l-file::before { background: var(--file); }
  .legend .l-test::before { background: var(--test); }
  .legend .l-ext::before { background: var(--ext); }
</style>
</head>
<body>
<header>
  <h1>Argus dependency graph</h1>
  <div class="meta" id="meta"></div>
  <div id="toolbar">
    <div class="legend">
      <span class="l-folder">folder</span>
      <span class="l-file">file</span>
      <span class="l-test">test</span>
      <span class="l-ext">outside folder</span>
    </div>
    <button type="button" id="btnBack" disabled>← Folders</button>
  </div>
  <div id="breadcrumb">View: <strong>All folders</strong> — click a folder to see its files</div>
</header>
<div id="stage">
  <svg id="canvas" xmlns="http://www.w3.org/2000/svg"></svg>
  <div class="hint" id="hint">Drag background to pan · Scroll to zoom · Drag nodes to rearrange</div>
</div>
<script>
const DATA = __DATA__;

const svg = document.getElementById("canvas");
const btnBack = document.getElementById("btnBack");
const breadcrumb = document.getElementById("breadcrumb");
const metaEl = document.getElementById("meta");

metaEl.textContent = [
  DATA.repo || "local",
  DATA.commit ? ("@" + DATA.commit.slice(0, 7)) : "",
  (DATA.stats && DATA.stats.files) ? (DATA.stats.files + " files") : "",
  (DATA.stats && DATA.stats.folders) ? (DATA.stats.folders + " folders") : "",
].filter(Boolean).join(" · ");

let mode = "folders"; // or "files"
let currentFolder = null;
let nodes = [];
let links = [];
let transform = { x: 0, y: 0, k: 1 };
let simTimer = null;

function shortLabel(id, max) {
  max = max || 28;
  if (id.length <= max) return id;
  const base = id.split("/").pop();
  if (base.length <= max) return base;
  return base.slice(0, max - 1) + "…";
}

function buildFolderScene() {
  const folders = DATA.folders || {};
  const edges = DATA.folder_edges || [];
  nodes = Object.keys(folders).sort().map((id) => ({
    id,
    label: shortLabel(id, 32),
    kind: "folder",
    title: id + " (" + folders[id].file_count + " files)",
    r: 14 + Math.min(18, Math.sqrt(folders[id].file_count || 1) * 2),
  }));
  const idset = new Set(nodes.map((n) => n.id));
  links = edges
    .filter((e) => idset.has(e.from) && idset.has(e.to))
    .map((e) => ({
      source: e.from,
      target: e.to,
      weight: e.weight || 1,
    }));
}

function buildFileScene(folderId) {
  const folders = DATA.folders || {};
  const info = folders[folderId];
  if (!info) return;
  const fileSet = new Set(info.files || []);
  const nodeMap = {};
  (info.files || []).forEach((p) => {
    const meta = (DATA.nodes && DATA.nodes[p]) || {};
    nodeMap[p] = {
      id: p,
      label: shortLabel(p, 36),
      kind: meta.kind === "test" ? "test" : "file",
      title: p,
      r: 10,
      external: false,
    };
  });
  const fileEdges = (DATA.edges || []).filter(
    (e) => fileSet.has(e.from) || fileSet.has(e.to)
  );
  fileEdges.forEach((e) => {
    [e.from, e.to].forEach((p) => {
      if (!nodeMap[p]) {
        nodeMap[p] = {
          id: p,
          label: shortLabel(p, 36),
          kind: "external",
          title: p + " (outside " + folderId + ")",
          r: 9,
          external: true,
        };
      }
    });
  });
  nodes = Object.values(nodeMap);
  links = fileEdges.map((e) => ({
    source: e.from,
    target: e.to,
    weight: 1,
  }));
}

function showFolders() {
  mode = "folders";
  currentFolder = null;
  btnBack.disabled = true;
  breadcrumb.innerHTML = "View: <strong>All folders</strong> — click a folder to see its files";
  buildFolderScene();
  restartSim();
}

function showFolder(folderId) {
  mode = "files";
  currentFolder = folderId;
  btnBack.disabled = false;
  breadcrumb.innerHTML =
    'View: folder <strong>' + folderId + "</strong> — files &amp; imports (dim nodes are outside)";
  buildFileScene(folderId);
  restartSim();
}

btnBack.addEventListener("click", showFolders);

function restartSim() {
  const W = svg.clientWidth || 900;
  const H = svg.clientHeight || 600;
  const cx = W / 2;
  const cy = H / 2;
  const n = nodes.length || 1;
  nodes.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / n;
    const rad = Math.min(W, H) * 0.28;
    node.x = cx + Math.cos(angle) * rad + (Math.random() - 0.5) * 20;
    node.y = cy + Math.sin(angle) * rad + (Math.random() - 0.5) * 20;
    node.vx = 0;
    node.vy = 0;
  });
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  links.forEach((l) => {
    l.sourceNode = byId[l.source];
    l.targetNode = byId[l.target];
  });
  transform = { x: 0, y: 0, k: 1 };
  if (simTimer) cancelAnimationFrame(simTimer);
  let tick = 0;
  function step() {
    tick++;
    const alpha = Math.max(0.02, 1 - tick / 180);
    // repulsion
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let dist2 = dx * dx + dy * dy || 1;
        let dist = Math.sqrt(dist2);
        const minD = (a.r + b.r) * 3.2;
        if (dist < minD * 3) {
          const f = (minD * minD) / dist2 * alpha * 0.6;
          dx = (dx / dist) * f;
          dy = (dy / dist) * f;
          a.vx += dx; a.vy += dy;
          b.vx -= dx; b.vy -= dy;
        }
      }
    }
    // springs
    links.forEach((l) => {
      const a = l.sourceNode, b = l.targetNode;
      if (!a || !b) return;
      let dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const ideal = 90 + Math.min(40, (l.weight || 1) * 4);
      const f = (dist - ideal) * 0.04 * alpha;
      dx = (dx / dist) * f;
      dy = (dy / dist) * f;
      a.vx += dx; a.vy += dy;
      b.vx -= dx; b.vy -= dy;
    });
    // center gravity
    nodes.forEach((n) => {
      n.vx += (cx - n.x) * 0.005 * alpha;
      n.vy += (cy - n.y) * 0.005 * alpha;
      n.vx *= 0.84;
      n.vy *= 0.84;
      if (!n.fixed) {
        n.x += n.vx;
        n.y += n.vy;
      }
    });
    draw();
    if (tick < 200) simTimer = requestAnimationFrame(step);
  }
  step();
}

function draw() {
  const ns = "http://www.w3.org/2000/svg";
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const g = document.createElementNS(ns, "g");
  g.setAttribute(
    "transform",
    "translate(" + transform.x + "," + transform.y + ") scale(" + transform.k + ")"
  );
  svg.appendChild(g);

  links.forEach((l) => {
    const a = l.sourceNode, b = l.targetNode;
    if (!a || !b) return;
    const line = document.createElementNS(ns, "line");
    line.setAttribute("class", "link");
    line.setAttribute("x1", a.x);
    line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x);
    line.setAttribute("y2", b.y);
    const w = Math.min(5, 1 + Math.log2((l.weight || 1) + 1));
    line.setAttribute("stroke-width", w);
    // arrow via marker-end skip for simplicity; direction from a→b
    g.appendChild(line);
  });

  nodes.forEach((n) => {
    const group = document.createElementNS(ns, "g");
    group.setAttribute("class", "node " + n.kind);
    group.setAttribute("transform", "translate(" + n.x + "," + n.y + ")");
    group.style.opacity = n.external ? "0.55" : "1";
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("r", n.r);
    c.setAttribute("title", n.title || n.id);
    const t = document.createElementNS(ns, "text");
    t.setAttribute("y", n.r + 12);
    t.textContent = n.label;
    group.appendChild(c);
    group.appendChild(t);
    group.addEventListener("mousedown", (ev) => startDragNode(ev, n, group));
    group.addEventListener("click", (ev) => {
      if (n._suppressClick) {
        n._suppressClick = false;
        return;
      }
      if (mode === "folders" && n.kind === "folder") {
        showFolder(n.id);
      }
    });
    g.appendChild(group);
  });
}

let dragNode = null;
let dragStart = null;
function startDragNode(ev, n, group) {
  ev.stopPropagation();
  dragNode = n;
  n.fixed = true;
  dragStart = { x: ev.clientX, y: ev.clientY, moved: false };
  const onMove = (e) => {
    if (!dragNode) return;
    const dx = (e.clientX - dragStart.x) / transform.k;
    const dy = (e.clientY - dragStart.y) / transform.k;
    if (Math.abs(e.clientX - dragStart.x) + Math.abs(e.clientY - dragStart.y) > 3) {
      dragStart.moved = true;
    }
    dragNode.x += dx;
    dragNode.y += dy;
    dragStart.x = e.clientX;
    dragStart.y = e.clientY;
    draw();
  };
  const onUp = () => {
    if (dragNode) {
      dragNode.fixed = false;
      if (dragStart && dragStart.moved) {
        dragNode._suppressClick = true;
      }
    }
    dragNode = null;
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
  };
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}

let panning = false;
let pan0 = null;
svg.addEventListener("mousedown", (ev) => {
  if (ev.target === svg || (ev.target && ev.target.tagName === "svg")) {
    panning = true;
    pan0 = { x: ev.clientX, y: ev.clientY, tx: transform.x, ty: transform.y };
  }
});
window.addEventListener("mousemove", (ev) => {
  if (!panning || !pan0) return;
  transform.x = pan0.tx + (ev.clientX - pan0.x);
  transform.y = pan0.ty + (ev.clientY - pan0.y);
  draw();
});
window.addEventListener("mouseup", () => { panning = false; });
svg.addEventListener(
  "wheel",
  (ev) => {
    ev.preventDefault();
    const factor = ev.deltaY > 0 ? 0.9 : 1.1;
    transform.k = Math.min(3, Math.max(0.25, transform.k * factor));
    draw();
  },
  { passive: false }
);

window.addEventListener("resize", () => { draw(); });

showFolders();
</script>
</body>
</html>
"""


def render_dep_graph_html(
    graph: FileGraph,
    *,
    folder_depth: int = 2,
    title: str = "",
) -> str:
    agg = aggregate_folders(graph, depth=folder_depth)
    payload = {
        "repo": graph.repo,
        "commit": graph.commit,
        "built_at": graph.built_at,
        "title": title or graph.repo or "dependency graph",
        "nodes": graph.nodes,
        "edges": graph.edges,
        "folders": {
            k: {
                "file_count": v["file_count"],
                "test_count": v["test_count"],
                "files": v["files"],
            }
            for k, v in agg["folders"].items()
        },
        "folder_edges": agg["folder_edges"],
        "stats": {
            "files": len(graph.nodes),
            "edges": len(graph.edges),
            "folders": len(agg["folders"]),
        },
    }
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Prevent </script> breakout
    data_json = data_json.replace("<", "\\u003c")
    html = _HTML_TEMPLATE.replace("__DATA__", data_json)
    html = html.replace("__TITLE__", _escape_title(payload["title"]))
    return html


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
    html = render_dep_graph_html(graph, folder_depth=folder_depth, title=title)
    out_path.write_text(html, encoding="utf-8")
    return out_path
