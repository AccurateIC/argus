"""CLI: build a single interactive dep-graph.html for a repo / PR."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .naming import default_cache_root, pr_graph_dir
from .query import hop1_related
from .render_html import write_dep_graph_html
from .scan import build_file_graph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m context_retrieval.dep_graph",
        description=(
            "Build one interactive HTML dependency graph "
            "(folders first → click folder → files)."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build", help="Scan repo and write dep-graph.html")
    build.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Path to the application repo checkout",
    )
    build.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output HTML path (default: cache dir …/dep-graph.html)",
    )
    build.add_argument("--repo", default="", help="owner/name for labeling + cache path")
    build.add_argument("--pr", default="", help="PR number for folder naming")
    build.add_argument("--sha", default="", help="Head commit SHA (short or full)")
    build.add_argument("--title", default="", help="PR title (slugified into folder name)")
    build.add_argument(
        "--folder-depth",
        type=int,
        default=2,
        help="Path segments that define a folder node (default 2 → apps/api)",
    )
    build.add_argument(
        "--cache-root",
        type=Path,
        default=None,
        help="Override ~/.cache/argus/dep-graphs",
    )
    build.add_argument(
        "--also-index",
        action="store_true",
        help="Also write graph-index.json next to the HTML (for Argus hop-1 later)",
    )

    hop = sub.add_parser("hop1", help="Print hop-1 dependents for changed files (needs index)")
    hop.add_argument("--index", type=Path, required=True, help="graph-index.json from --also-index")
    hop.add_argument(
        "--changed",
        nargs="+",
        required=True,
        help="Changed file paths (repo-relative)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "hop1":
        return _cmd_hop1(args)
    return _cmd_build(args)


def _cmd_build(args: argparse.Namespace) -> int:
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"error: --root is not a directory: {root}", file=sys.stderr)
        return 2

    repo = args.repo or root.name
    print(f"neubodhi-dep-graph: scanning {root} …", flush=True)
    graph = build_file_graph(root, repo=repo, commit=args.sha or None)
    print(
        f"neubodhi-dep-graph: {len(graph.nodes)} files, {len(graph.edges)} import edges "
        f"commit={graph.commit[:7] or '?'}",
        flush=True,
    )

    if args.out:
        out_html = args.out.expanduser().resolve()
        if out_html.suffix.lower() != ".html":
            out_html = out_html / "dep-graph.html"
    else:
        out_dir = pr_graph_dir(
            repo=repo,
            pr_number=args.pr or None,
            head_sha=graph.commit or args.sha or None,
            title=args.title or None,
            cache_root=args.cache_root,
        )
        out_html = out_dir / "dep-graph.html"

    write_dep_graph_html(
        graph,
        out_html,
        folder_depth=args.folder_depth,
        title=args.title or repo,
    )
    print(f"neubodhi-dep-graph: wrote {out_html}", flush=True)

    if args.also_index:
        index_path = out_html.with_name("graph-index.json")
        index_path.write_text(
            json.dumps(graph.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"neubodhi-dep-graph: wrote {index_path}", flush=True)

    print(
        "Open the HTML in a browser: folders view first, click a folder for files.",
        flush=True,
    )
    return 0


def _cmd_hop1(args: argparse.Namespace) -> int:
    from .scan import FileGraph

    raw = json.loads(args.index.read_text(encoding="utf-8"))
    g = FileGraph(
        root=str(raw.get("root") or ""),
        repo=str(raw.get("repo") or ""),
        commit=str(raw.get("commit") or ""),
        built_at=str(raw.get("built_at") or ""),
        nodes=dict(raw.get("nodes") or {}),
        edges=list(raw.get("edges") or []),
        imports={k: list(v) for k, v in (raw.get("imports") or {}).items()},
        imported_by={k: list(v) for k, v in (raw.get("imported_by") or {}).items()},
    )
    result = hop1_related(g, list(args.changed))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
