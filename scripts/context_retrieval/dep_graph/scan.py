"""Scan a repo for TS/JS import edges (file → file)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .resolve import (
    _SOURCE_SUFFIXES,
    is_test_path,
    load_path_aliases,
    resolve_import,
    should_skip_dir,
)

# Captures the module specifier from common ESM/CJS import forms.
_IMPORT_RE = re.compile(
    r"""(?x)
    (?:
        import\s+(?:type\s+)?(?:[\w*\s{},]+)\s+from\s+['"]([^'"]+)['"]
      | import\s+['"]([^'"]+)['"]
      | export\s+(?:type\s+)?(?:[\w*\s{},]+)\s+from\s+['"]([^'"]+)['"]
      | export\s+\*\s+from\s+['"]([^'"]+)['"]
      | require\s*\(\s*['"]([^'"]+)['"]\s*\)
      | import\s*\(\s*['"]([^'"]+)['"]\s*\)
    )
    """
)


@dataclass
class FileGraph:
    root: str
    commit: str = ""
    repo: str = ""
    built_at: str = ""
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)  # {from, to}
    imports: dict[str, list[str]] = field(default_factory=dict)
    imported_by: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "root": self.root,
            "repo": self.repo,
            "commit": self.commit,
            "built_at": self.built_at,
            "languages": ["typescript", "javascript"],
            "nodes": self.nodes,
            "edges": self.edges,
            "imports": self.imports,
            "imported_by": self.imported_by,
        }


def extract_specifiers(source: str) -> list[str]:
    specs: list[str] = []
    for m in _IMPORT_RE.finditer(source):
        for g in m.groups():
            if g:
                specs.append(g)
                break
    return specs


def iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os_walk_filtered(root):
        for name in filenames:
            if name.endswith(_SOURCE_SUFFIXES):
                files.append(Path(dirpath) / name)
    return files


def os_walk_filtered(root: Path):
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        yield dirpath, dirnames, filenames


def detect_git_commit(root: Path) -> str:
    """Return HEAD only when ``root`` itself is (or is inside) this repo's toplevel."""
    import subprocess

    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if top.returncode != 0:
            return ""
        toplevel = Path(top.stdout.strip()).resolve()
        if toplevel != root.resolve():
            # e.g. fixture folder nested under another git repo — don't steal parent SHA
            return ""
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            return (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def build_file_graph(
    root: Path,
    *,
    repo: str = "",
    commit: str | None = None,
) -> FileGraph:
    root = root.resolve()
    aliases = load_path_aliases(root)
    g = FileGraph(
        root=str(root),
        repo=repo,
        commit=commit if commit is not None else detect_git_commit(root),
        built_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    files = iter_source_files(root)
    for path in files:
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        g.nodes[rel] = {
            "lang": "ts" if path.suffix in {".ts", ".tsx"} else "js",
            "kind": "test" if is_test_path(rel) else "source",
        }
        g.imports.setdefault(rel, [])

    for path in files:
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Skip huge generated blobs
        if len(text) > 1_500_000:
            continue
        seen_targets: set[str] = set()
        for spec in extract_specifiers(text):
            target = resolve_import(root, path, spec, aliases)
            if not target or target == rel or target in seen_targets:
                continue
            if target not in g.nodes:
                # Imported file outside walk (e.g. .json) — skip
                continue
            seen_targets.add(target)
            g.edges.append({"from": rel, "to": target, "via": "import"})
            g.imports.setdefault(rel, []).append(target)
            g.imported_by.setdefault(target, []).append(rel)

    for k in g.imports:
        g.imports[k] = sorted(set(g.imports[k]))
    for k in g.imported_by:
        g.imported_by[k] = sorted(set(g.imported_by[k]))
    return g
