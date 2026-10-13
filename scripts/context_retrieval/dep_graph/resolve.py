"""Resolve TS/JS import specifiers to repo-relative file paths."""

from __future__ import annotations

import json
import re
from pathlib import Path

_SKIP_DIR_NAMES = frozenset(
    {
        "node_modules",
        "dist",
        "build",
        ".next",
        "coverage",
        ".git",
        "__pycache__",
        ".turbo",
        "out",
        ".cache",
        "vendor",
    }
)

_SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIR_NAMES or name.startswith(".")


def load_path_aliases(root: Path) -> list[tuple[str, str]]:
    """Return (pattern_prefix, target_prefix) from tsconfig/jsconfig paths."""
    aliases: list[tuple[str, str]] = []
    for name in ("tsconfig.json", "jsconfig.json", "tsconfig.base.json"):
        cfg = root / name
        if not cfg.is_file():
            continue
        try:
            text = cfg.read_text(encoding="utf-8")
            # Strip // comments loosely for typical tsconfig
            text = re.sub(r"//.*?$", "", text, flags=re.M)
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            data = json.loads(text)
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        compiler = data.get("compilerOptions") or {}
        base_url = str(compiler.get("baseUrl") or ".")
        paths = compiler.get("paths") or {}
        if not isinstance(paths, dict):
            continue
        base = (root / base_url).resolve()
        for pattern, targets in paths.items():
            if not isinstance(targets, list) or not targets:
                continue
            pat = str(pattern).replace("*", "")
            tgt = str(targets[0]).replace("*", "")
            try:
                tgt_abs = (base / tgt).resolve()
                rel = tgt_abs.relative_to(root.resolve()).as_posix()
            except ValueError:
                rel = tgt.replace("\\", "/").lstrip("./")
            aliases.append((pat, rel if rel.endswith("/") or not pat.endswith("/") else rel + "/"))
        break  # first config wins
    # Longer patterns first
    aliases.sort(key=lambda x: -len(x[0]))
    return aliases


def _candidate_files(root: Path, rel_no_ext: str) -> list[Path]:
    rel_no_ext = rel_no_ext.replace("\\", "/").lstrip("./")
    out: list[Path] = []
    base = root / rel_no_ext
    if base.is_file():
        out.append(base)
    for suf in _SOURCE_SUFFIXES:
        p = root / f"{rel_no_ext}{suf}"
        if p.is_file():
            out.append(p)
    for suf in _SOURCE_SUFFIXES:
        p = root / rel_no_ext / f"index{suf}"
        if p.is_file():
            out.append(p)
    return out


def resolve_import(
    root: Path,
    from_file: Path,
    specifier: str,
    aliases: list[tuple[str, str]],
) -> str | None:
    """Return repo-relative posix path or None (external / unresolved)."""
    spec = (specifier or "").strip().strip("'\"")
    if not spec or spec.startswith("node:"):
        return None
    # Relative
    if spec.startswith("."):
        base_dir = from_file.parent
        target = (base_dir / spec).resolve()
        try:
            rel = target.relative_to(root.resolve())
            rel_s = rel.as_posix()
        except ValueError:
            return None
        # May lack extension
        if target.is_file():
            return rel_s
        for c in _candidate_files(root, rel_s):
            try:
                return c.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
        # strip accidental .js → try without
        if rel_s.endswith((".js", ".jsx", ".ts", ".tsx")):
            stem = rel_s.rsplit(".", 1)[0]
            for c in _candidate_files(root, stem):
                try:
                    return c.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue
        return None

    # Alias
    for pat, tgt in aliases:
        if pat and (spec == pat.rstrip("/") or spec.startswith(pat)):
            rest = spec[len(pat) :] if spec.startswith(pat) else ""
            combined = (tgt.rstrip("/") + "/" + rest.lstrip("/")).replace("//", "/")
            for c in _candidate_files(root, combined):
                try:
                    return c.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue

    # Bare package — external
    return None


def is_test_path(path: str) -> bool:
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if re.search(r"\.(test|spec)\.[jt]sx?$", name):
        return True
    if "/__tests__/" in f"/{p}/":
        return True
    return False
