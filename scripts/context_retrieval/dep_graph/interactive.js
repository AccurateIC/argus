/* In-place folder collapse/expand on the full tree — no animations, no navigation away. */
(function () {
  var dataEl = document.getElementById("graph-data");
  var stage = document.getElementById("stage");
  var btnBack = document.getElementById("btnBack");
  var crumb = document.getElementById("crumb");
  if (!dataEl || !stage) return;

  var DATA = JSON.parse(dataEl.textContent);
  /** @type {Object.<string, boolean>} folders that are collapsed (children hidden) */
  var collapsed = {};

  var C = {
    bg: "#0b1220",
    text: "#e8eef8",
    muted: "#8b9bb4",
    folderFill: "#132033",
    folderStroke: "#3b82c4",
    folderIcon: "#5ba3e0",
    fileFill: "#0f2420",
    fileStroke: "#2dd4a8",
    fileIcon: "#3ecf8e",
    stageFill: "#4c3db8",
    stageStroke: "#8b7cf7",
    contains: "#c5d0e0",
    uses: "#5b9fd4",
    handoff: "#8b5cf6",
  };

  var NODE_W = 168;
  var NODE_H = 34;
  var GAP_X = 88;
  var GAP_Y = 12;
  var PAD = 36;
  var PAD_TOP = 24;
  var MAX_FILES = 40;

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function baseName(id) {
    if (!id || id === "(root)") return shortRepo();
    var parts = id.split("/");
    return parts[parts.length - 1] || id;
  }

  function shortRepo() {
    var r = DATA.repo || "repo";
    if (r.indexOf("/") >= 0) {
      var bits = r.split("/");
      return bits[bits.length - 1] || r;
    }
    return r;
  }

  function folderInfo(id) {
    return (DATA.folders && DATA.folders[id]) || { children: [], files: [], parent: null };
  }

  function isStage(id) {
    var groups = DATA.handoff_groups || {};
    for (var k in groups) {
      if (Object.prototype.hasOwnProperty.call(groups, k)) {
        if ((groups[k] || []).indexOf(id) >= 0) return true;
      }
    }
    return false;
  }

  function hasKids(id) {
    var info = folderInfo(id);
    return (info.children || []).length + (info.files || []).length > 0;
  }

  function childEntries(id) {
    var info = folderInfo(id);
    var out = [];
    (info.children || []).forEach(function (c) {
      out.push({ id: c, kind: isStage(c) ? "stage" : "folder" });
    });
    (info.files || []).slice(0, MAX_FILES).forEach(function (f) {
      out.push({ id: f, kind: "file" });
    });
    return out;
  }

  function folderLabel(id, visibleIds) {
    if (id === "(root)") return shortRepo();
    var bn = baseName(id);
    // Disambiguate duplicate names (.github/workflows vs github/workflows)
    var label = bn;
    if (visibleIds) {
      var dup = 0;
      for (var i = 0; i < visibleIds.length; i++) {
        var vid = visibleIds[i];
        if (vid !== id && baseName(vid) === bn && folderInfo(vid).parent != null) {
          dup++;
        }
      }
      // also count self
      if (dup > 0) {
        var parts = id.split("/");
        label = parts.length >= 2 ? parts.slice(-2).join("/") : bn;
      }
    }
    if (label.charAt(label.length - 1) !== "/") label += "/";
    if (hasKids(id)) {
      label = (collapsed[id] ? "▸ " : "▾ ") + label;
    }
    return label;
  }

  function iconFolder(x, y, color) {
    return (
      '<path d="M' + x + "," + (y + 5) + " h7 l2.5,3 h11.5 v10 h-21 z\" fill=\"" +
      color + '" stroke="' + color + '" stroke-width="0.5"/>'
    );
  }

  function iconFile(x, y, color) {
    return (
      '<path d="M' + (x + 2) + "," + y + " h9 l5,5 v13 h-14 z\" fill=\"none\" stroke=\"" +
      color + '" stroke-width="1.6"/>' +
      '<path d="M' + (x + 11) + "," + y + " v5 h5\" fill=\"none\" stroke=\"" +
      color + '" stroke-width="1.6"/>'
    );
  }

  function drawBox(n) {
    var fill = C.folderFill;
    var stroke = C.folderStroke;
    var clickable = n.kind !== "file" && hasKids(n.id);
    if (n.kind === "file") {
      fill = C.fileFill;
      stroke = C.fileStroke;
    } else if (n.kind === "stage") {
      fill = C.stageFill;
      stroke = C.stageStroke;
    }
    var label = n.label;
    if (label.length > 28) label = label.slice(0, 27) + "…";
    var g =
      '<g class="node" ' +
      (n.kind !== "file" ? 'data-folder-id="' + esc(n.id) + '" style="cursor:pointer"' : "") +
      ">" +
      '<rect x="' + n.x + '" y="' + n.y + '" width="' + n.w + '" height="' + n.h +
      '" rx="8" fill="' + fill + '" stroke="' + stroke + '" stroke-width="1.4"/>';
    if (n.kind === "file") {
      g += iconFile(n.x + 10, n.y + 8, C.fileIcon);
    } else {
      g += iconFolder(n.x + 10, n.y + 8, n.kind === "stage" ? "#c4b5fd" : C.folderIcon);
    }
    g +=
      '<text x="' + (n.x + 34) + '" y="' + (n.y + n.h / 2 + 4) + '" fill="' +
      (n.kind === "stage" ? "#fff" : C.text) +
      '" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="12">' +
      esc(label) +
      "</text><title>" +
      esc(n.id) +
      (clickable ? (collapsed[n.id] ? " (click to expand)" : " (click to collapse)") : "") +
      "</title></g>";
    return g;
  }

  /**
   * Tidy tree: each parent's children stay in one contiguous vertical block
   * so edges never look like they belong to a sibling folder.
   */
  function buildVisibleTree() {
    var nodes = [];
    var edges = [];
    var leafRow = 0;

    function walk(id, depth, kind) {
      var node = {
        id: id,
        label: "", // filled after we know visible set
        kind: kind,
        depth: depth,
        row: 0,
        w: NODE_W,
        h: NODE_H,
      };
      nodes.push(node);

      if (kind === "file" || collapsed[id] || !hasKids(id)) {
        node.row = leafRow;
        leafRow += 1;
        return node;
      }

      var kids = childEntries(id);
      var childNodes = [];
      kids.forEach(function (k) {
        var child = walk(k.id, depth + 1, k.kind);
        childNodes.push(child);
        edges.push({ from: id, to: k.id });
      });
      if (childNodes.length) {
        node.row = (childNodes[0].row + childNodes[childNodes.length - 1].row) / 2;
      } else {
        node.row = leafRow;
        leafRow += 1;
      }
      return node;
    }

    walk("(root)", 0, "folder");

    var visibleIds = nodes.map(function (n) {
      return n.id;
    });
    nodes.forEach(function (n) {
      n.label = n.kind === "file" ? baseName(n.id) : folderLabel(n.id, visibleIds);
    });

    return { nodes: nodes, edges: edges };
  }

  function layout(tree) {
    var maxDepth = 0;
    var maxRow = 0;
    tree.nodes.forEach(function (n) {
      n.x = PAD + n.depth * (NODE_W + GAP_X);
      n.y = PAD_TOP + n.row * (NODE_H + GAP_Y);
      if (n.depth > maxDepth) maxDepth = n.depth;
      if (n.row > maxRow) maxRow = n.row;
    });
    return {
      width: PAD * 2 + (maxDepth + 1) * NODE_W + maxDepth * GAP_X,
      height: PAD_TOP + PAD + (maxRow + 1) * (NODE_H + GAP_Y) + 36,
    };
  }

  function render() {
    var tree = buildVisibleTree();
    var size = layout(tree);
    var byId = {};
    tree.nodes.forEach(function (n) {
      byId[n.id] = n;
    });

    // Contains edges (parent-local rail)
    var edges = "";
    tree.edges.forEach(function (e) {
      var a = byId[e.from];
      var b = byId[e.to];
      if (!a || !b) return;
      var ax = a.x + a.w;
      var ay = a.y + a.h / 2;
      var bx = b.x;
      var by = b.y + b.h / 2;
      var rail = ax + Math.min(28, GAP_X * 0.35);
      edges +=
        '<path d="M' + ax + "," + ay + " H" + rail + " V" + by + " H" + bx +
        '" fill="none" stroke="' + C.contains +
        '" stroke-width="1.5" marker-end="url(#iv-arrow)"/>';
    });

    // File→file / folder uses: roll endpoints up to a currently visible node
    function visibleAnchor(pathId) {
      if (!pathId) return null;
      if (byId[pathId]) return pathId;
      // walk up folder parents until something visible
      var cur = pathId;
      var guard = 0;
      while (cur && guard++ < 40) {
        if (byId[cur]) return cur;
        if (cur.indexOf("/") < 0) {
          return byId["(root)"] ? "(root)" : null;
        }
        cur = cur.split("/").slice(0, -1).join("/");
        if (!cur) return byId["(root)"] ? "(root)" : null;
      }
      return null;
    }

    var usesDrawn = {};
    var usesSvg = "";
    var usesExtra = 0;
    var useIdx = 0;
    var fileEdges = DATA.edges || [];
    fileEdges.forEach(function (e) {
      var aId = visibleAnchor(e.from);
      var bId = visibleAnchor(e.to);
      if (!aId || !bId || aId === bId) return;
      var key = aId + "=>" + bId;
      if (usesDrawn[key]) return;
      usesDrawn[key] = true;
      var a = byId[aId];
      var b = byId[bId];
      if (!a || !b) return;
      useIdx += 1;
      // Side arc so it never looks like a contains rail (solid elbow)
      var ax = a.x + a.w;
      var ay = a.y + a.h / 2;
      var bx = b.x + b.w;
      var by = b.y + b.h / 2;
      var bump = 36 + (useIdx % 7) * 10;
      var cx = Math.max(ax, bx) + bump;
      usesExtra = Math.max(usesExtra, bump + 8);
      usesSvg +=
        "<g><title>" +
        esc(aId) +
        " uses " +
        esc(bId) +
        "</title>" +
        '<path d="M' +
        ax +
        "," +
        ay +
        " C" +
        cx +
        "," +
        ay +
        " " +
        cx +
        "," +
        by +
        " " +
        bx +
        "," +
        by +
        '" fill="none" stroke="' +
        C.uses +
        '" stroke-width="1.6" stroke-dasharray="5 3" marker-end="url(#iv-uses)"/></g>';
    });

    // Pipeline handoff between visible stage folders
    var handoffSvg = "";
    var groups = DATA.handoff_groups || {};
    Object.keys(groups).forEach(function (parent) {
      var ordered = groups[parent] || [];
      for (var i = 0; i < ordered.length - 1; i++) {
        var a = byId[ordered[i]];
        var b = byId[ordered[i + 1]];
        if (!a || !b) continue;
        var x = Math.max(a.x + a.w, b.x + b.w) + 14;
        handoffSvg +=
          '<path d="M' + x + "," + (a.y + a.h) + " V" + b.y +
          '" fill="none" stroke="' + C.handoff +
          '" stroke-width="2" marker-end="url(#iv-handoff)"/>';
      }
    });

    var extraW = usesExtra;
    stage.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + (size.width + extraW) + '" height="' + size.height +
      '" viewBox="0 0 ' + (size.width + extraW) + " " + size.height + '">' +
      '<rect width="100%" height="100%" fill="' + C.bg + '"/>' +
      "<defs>" +
      '<marker id="iv-arrow" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto" markerUnits="userSpaceOnUse">' +
      '<path d="M0,0 L9,3.5 L0,7 Z" fill="' + C.contains + '"/></marker>' +
      '<marker id="iv-uses" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto" markerUnits="userSpaceOnUse">' +
      '<path d="M0,0 L9,3.5 L0,7 Z" fill="' + C.uses + '"/></marker>' +
      '<marker id="iv-handoff" markerWidth="7" markerHeight="6" refX="6" refY="3" orient="auto" markerUnits="userSpaceOnUse">' +
      '<path d="M0,0 L7,3 L0,6 Z" fill="' + C.handoff + '"/></marker>' +
      "</defs>" +
      edges +
      usesSvg +
      handoffSvg +
      tree.nodes.map(drawBox).join("") +
      "</svg>";

    bindClicks();
    setChrome();
  }

  function setChrome() {
    var nCollapsed = Object.keys(collapsed).filter(function (k) {
      return collapsed[k];
    }).length;
    if (btnBack) {
      btnBack.textContent = "Expand all";
      btnBack.disabled = nCollapsed === 0;
    }
    if (crumb) {
      crumb.textContent =
        nCollapsed === 0
          ? "All folders open — dashed blue = file/folder uses (imports)"
          : "Solid = folder contains · dashed blue = uses — expand ▸ to see file→file";
    }
  }

  function toggleFolder(id) {
    if (!id || id === "(root)") {
      // Root: collapse all top-level children as a group, or expand all
      var kids = childEntries("(root)");
      var anyOpen = kids.some(function (k) {
        return k.kind !== "file" && !collapsed[k.id] && hasKids(k.id);
      });
      kids.forEach(function (k) {
        if (k.kind === "file") return;
        if (!hasKids(k.id)) return;
        collapsed[k.id] = anyOpen; // if any open, collapse all; else expand all
      });
      render();
      return;
    }
    if (!hasKids(id)) return;
    collapsed[id] = !collapsed[id];
    render();
  }

  function expandAll() {
    collapsed = {};
    render();
  }

  function bindClicks() {
    var nodes = stage.querySelectorAll("[data-folder-id]");
    for (var i = 0; i < nodes.length; i++) {
      (function (el) {
        var id = el.getAttribute("data-folder-id");
        if (!id) return;
        el.onclick = function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          toggleFolder(id);
        };
      })(nodes[i]);
    }
  }

  if (btnBack) {
    btnBack.onclick = expandAll;
  }

  // Start: every folder closed (collapsed). Click ▸ to open in place.
  collapsed = {};
  Object.keys(DATA.folders || {}).forEach(function (id) {
    if (id === "(root)") return;
    if (hasKids(id)) collapsed[id] = true;
  });
  render();
})();
