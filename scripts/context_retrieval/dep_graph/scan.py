"""Scan a full repo for file dependency edges (all text/source kinds)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .extractors import extract_specifiers, extract_specifiers_for_file
from .resolve import (
    is_test_path,
    lang_of,
    load_path_aliases,
    resolve_ref_or_dir,
    should_skip_dir,
    should_skip_file,
)

# Re-export for tests
__all__ = ["FileGraph", "build_file_graph", "extract_specifiers"]

_MAX_FILE_BYTES = 1_500_000
# Cap nodes for pathological monorepos (still "full" for normal apps).
_MAX_NODES = 25_000


@dataclass
class FileGraph:
    root: str
    commit: str = ""
    repo: str = ""
    built_at: str = ""
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    imports: dict[str, list[str]] = field(default_factory=dict)
    imported_by: dict[str, list[str]] = field(default_factory=dict)
    languages: list[str] = field(default_factory=list)
    # Directories that exist on disk (incl. binary-only / empty) so the tree
    # matches GitHub layout even when no source files live there.
    extra_folders: list[str] = field(default_factory=list)
    # File → directory "uses" edges (Path(...)/"data"/"uploads", mounts, …)
    dir_refs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "root": self.root,
            "repo": self.repo,
            "commit": self.commit,
            "built_at": self.built_at,
            "languages": self.languages,
            "nodes": self.nodes,
            "edges": self.edges,
            "imports": self.imports,
            "imported_by": self.imported_by,
            "extra_folders": self.extra_folders,
            "dir_refs": self.dir_refs,
        }


def iter_repo_layout(root: Path) -> tuple[list[Path], list[str]]:
    """Return (source files, directory ids) under root.

    Directories are collected even when they only contain skipped binaries
    (e.g. ``data/`` with PDFs) so the folder tree matches the real repo.
    """
    import os

    files: list[Path] = []
    dirs: set[str] = set()
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not should_skip_dir(d))
        dpath = Path(dirpath)
        try:
            rel_dir = dpath.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if rel_dir == ".":
            rel_dir = ""
        else:
            dirs.add(rel_dir)
            # ancestors
            parts = rel_dir.split("/")
            for i in range(1, len(parts)):
                dirs.add("/".join(parts[:i]))

        for name in filenames:
            path = dpath / name
            if should_skip_file(path):
                continue
            files.append(path)
            if len(files) >= _MAX_NODES:
                return files, sorted(dirs)
    return files, sorted(dirs)


def iter_repo_files(root: Path) -> list[Path]:
    """Every non-skipped file under root (full repo graph nodes)."""
    files, _ = iter_repo_layout(root)
    return files


def detect_git_commit(root: Path) -> str:
    """Return HEAD only when ``root`` is the git toplevel."""
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

    files, dir_shells = iter_repo_layout(root)
    g.extra_folders = list(dir_shells)
    langs: set[str] = set()

    for path in files:
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        lang = lang_of(rel)
        langs.add(lang)
        g.nodes[rel] = {
            "lang": lang,
            "kind": "test" if is_test_path(rel) else "source",
        }
        g.imports.setdefault(rel, [])

    g.languages = sorted(langs)

    seen_dir_refs: set[tuple[str, str]] = set()

    for path in files:
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        try:
            # binary-ish detection: skip if null bytes in first chunk
            raw = path.read_bytes()[:8192]
            if b"\x00" in raw:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > _MAX_FILE_BYTES:
            continue

        seen_targets: set[str] = set()
        for spec in extract_specifiers_for_file(path, text):
            file_tgt, dir_tgt = resolve_ref_or_dir(root, path, spec, aliases)
            if file_tgt and file_tgt != rel and file_tgt not in seen_targets:
                if file_tgt in g.nodes:
                    seen_targets.add(file_tgt)
                    g.edges.append({"from": rel, "to": file_tgt, "via": "ref"})
                    g.imports.setdefault(rel, []).append(file_tgt)
                    g.imported_by.setdefault(file_tgt, []).append(rel)
                continue
            if dir_tgt and dir_tgt != parent_of_file(rel):
                key = (rel, dir_tgt)
                if key not in seen_dir_refs:
                    seen_dir_refs.add(key)
                    g.dir_refs.append({"from": rel, "to": dir_tgt, "via": "path"})
                    if dir_tgt not in g.extra_folders and dir_tgt != "(root)":
                        g.extra_folders.append(dir_tgt)

    for k in g.imports:
        g.imports[k] = sorted(set(g.imports[k]))
    for k in g.imported_by:
        g.imported_by[k] = sorted(set(g.imported_by[k]))
    return g


def parent_of_file(path: str) -> str:
    p = path.replace("\\", "/").rstrip("/")
    if "/" not in p:
        return "(root)"
    return p.rsplit("/", 1)[0]
