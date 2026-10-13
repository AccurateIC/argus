"""Resolve import / href / url specifiers to repo-relative file paths."""

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
        ".venv",
        "venv",
        "eggs",
        ".eggs",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

# Binary / non-graph assets — still skipped as nodes.
_SKIP_FILE_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".bmp",
        ".svgz",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".mp3",
        ".mp4",
        ".webm",
        ".wav",
        ".zip",
        ".gz",
        ".tar",
        ".7z",
        ".rar",
        ".pdf",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".wasm",
        ".pyc",
        ".pyo",
        ".class",
        ".o",
        ".a",
        ".lock",
        ".map",
    }
)

# Preferred extensions when resolving extension-less imports.
_RESOLVE_SUFFIXES = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".html",
    ".htm",
    ".json",
    ".vue",
    ".svelte",
    ".md",
    ".yaml",
    ".yml",
)


def should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIR_NAMES or name.startswith(".")


def should_skip_file(path: Path) -> bool:
    name = path.name
    if name in {".DS_Store", "Thumbs.db"}:
        return True
    suf = path.suffix.lower()
    if suf in _SKIP_FILE_SUFFIXES:
        return True
    # Huge lockfiles / packages
    if name in {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock"}:
        return True
    return False


def lang_of(path: str) -> str:
    suf = Path(path).suffix.lower()
    return {
        ".ts": "ts",
        ".tsx": "ts",
        ".js": "js",
        ".jsx": "js",
        ".mjs": "js",
        ".cjs": "js",
        ".py": "py",
        ".pyi": "py",
        ".css": "css",
        ".scss": "css",
        ".sass": "css",
        ".less": "css",
        ".html": "html",
        ".htm": "html",
        ".vue": "vue",
        ".svelte": "svelte",
        ".json": "json",
        ".md": "md",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".toml": "toml",
        ".sh": "shell",
        ".sql": "sql",
    }.get(suf, suf.lstrip(".") or "other")


def load_path_aliases(root: Path) -> list[tuple[str, str]]:
    """Return (pattern_prefix, target_prefix) from tsconfig/jsconfig paths."""
    aliases: list[tuple[str, str]] = []
    for name in ("tsconfig.json", "jsconfig.json", "tsconfig.base.json"):
        cfg = root / name
        if not cfg.is_file():
            continue
        try:
            text = cfg.read_text(encoding="utf-8")
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
        break
    aliases.sort(key=lambda x: -len(x[0]))
    return aliases


def _candidate_files(root: Path, rel_no_ext: str) -> list[Path]:
    rel_no_ext = rel_no_ext.replace("\\", "/").lstrip("./")
    out: list[Path] = []
    base = root / rel_no_ext
    if base.is_file():
        out.append(base)
    for suf in _RESOLVE_SUFFIXES:
        p = root / f"{rel_no_ext}{suf}"
        if p.is_file():
            out.append(p)
    for suf in _RESOLVE_SUFFIXES:
        p = root / rel_no_ext / f"index{suf}"
        if p.is_file():
            out.append(p)
    # Python package
    init_py = root / rel_no_ext / "__init__.py"
    if init_py.is_file():
        out.append(init_py)
    return out


def resolve_ref(
    root: Path,
    from_file: Path,
    specifier: str,
    aliases: list[tuple[str, str]] | None = None,
) -> str | None:
    """Return repo-relative posix path or None (external / unresolved)."""
    aliases = aliases or []
    spec = (specifier or "").strip().strip("'\"")
    if not spec or spec.startswith("node:") or spec.startswith("data:") or spec.startswith("http"):
        return None
    if spec.startswith("//") or spec.startswith("#"):
        return None

    # Query/hash strip for urls
    spec = spec.split("?", 1)[0].split("#", 1)[0]
    if not spec:
        return None

    # Relative
    if spec.startswith("."):
        base_dir = from_file.parent
        target = (base_dir / spec).resolve()
        try:
            rel_s = target.relative_to(root.resolve()).as_posix()
        except ValueError:
            return None
        if target.is_file():
            return rel_s
        for c in _candidate_files(root, rel_s):
            try:
                return c.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
        if "." in Path(rel_s).name:
            stem = rel_s.rsplit(".", 1)[0]
            for c in _candidate_files(root, stem):
                try:
                    return c.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue
        return None

    # Root-absolute web path: /styles/app.css → try from repo root / public /
    if spec.startswith("/"):
        trimmed = spec.lstrip("/")
        for prefix in ("", "public/", "static/", "src/"):
            for c in _candidate_files(root, prefix + trimmed):
                try:
                    return c.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue
            p = root / prefix / trimmed
            if p.is_file():
                try:
                    return p.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue
        return None

    # Alias (TS)
    for pat, tgt in aliases:
        if pat and (spec == pat.rstrip("/") or spec.startswith(pat)):
            rest = spec[len(pat) :] if spec.startswith(pat) else ""
            combined = (tgt.rstrip("/") + "/" + rest.lstrip("/")).replace("//", "/")
            for c in _candidate_files(root, combined):
                try:
                    return c.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue

    # Python-style dotted module → path
    if re.fullmatch(r"[A-Za-z_][\w.]*", spec) and "." in spec:
        dotted = spec.replace(".", "/")
        for c in _candidate_files(root, dotted):
            try:
                return c.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
        # try under common roots
        for base in ("src", "apps", "packages", "lib"):
            for c in _candidate_files(root, f"{base}/{dotted}"):
                try:
                    return c.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue

    return None


# Back-compat name used by older callers/tests
def resolve_import(
    root: Path,
    from_file: Path,
    specifier: str,
    aliases: list[tuple[str, str]],
) -> str | None:
    return resolve_ref(root, from_file, specifier, aliases)


def is_test_path(path: str) -> bool:
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if re.search(r"\.(test|spec)\.[jt]sx?$", name):
        return True
    if re.search(r"(^|/)test_[^/]+\.py$", p) or re.search(r"_test\.py$", p):
        return True
    if "/__tests__/" in f"/{p}/" or "/tests/" in f"/{p}/":
        return True
    return False
