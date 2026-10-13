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


# Dot-dirs we still want in the graph (repo layout like GitHub).
_KEEP_DOT_DIRS = frozenset({".github"})


def should_skip_dir(name: str) -> bool:
    if name in _KEEP_DOT_DIRS:
        return False
    if name in _SKIP_DIR_NAMES:
        return True
    # Skip other hidden/tooling dirs (.git, .venv, .idea, …)
    if name.startswith("."):
        return True
    return False


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
        for prefix in (
            "",
            "public/",
            "static/",
            "src/",
            "app/frontend/",
            "app/backend/",
            "frontend/",
            "backend/",
        ):
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

    # Repo-relative pathish: "app/frontend/index.html", "data/uploads"
    if "/" in spec and not spec.startswith(".") and re.fullmatch(
        r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)+", spec
    ):
        for c in _candidate_files(root, spec):
            try:
                return c.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
        p = (root / spec).resolve()
        try:
            if p.is_file() and p.is_relative_to(root.resolve()):
                return p.relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            pass

    # Python-style dotted module → path (agents.extraction.foo → agents/extraction/foo.py)
    if re.fullmatch(r"[A-Za-z_][\w.]*", spec):
        dotted = spec.replace(".", "/")
        candidates_rel = [dotted]
        # ROS2 / nested pkg layout: collision_analyzer.core
        # → collision_analyzer/collision_analyzer/core.py
        top = spec.split(".", 1)[0]
        if (root / top).is_dir():
            candidates_rel.append(f"{top}/{dotted}")
            # utilities → utilities/utilities.py or utilities/utilities/__init__.py
            if "." not in spec:
                candidates_rel.append(f"{top}/{top}")
        for base in ("src", "apps", "packages", "lib", "backend", "app"):
            candidates_rel.append(f"{base}/{dotted}")

        seen_c: set[str] = set()
        for rel in candidates_rel:
            if rel in seen_c:
                continue
            seen_c.add(rel)
            for c in _candidate_files(root, rel):
                try:
                    return c.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    continue

    return None


def _as_repo_dir(root: Path, abs_path: Path) -> str | None:
    try:
        rel = abs_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    if abs_path.is_dir():
        return rel if rel != "." else "(root)"
    return None


def resolve_ref_or_dir(
    root: Path,
    from_file: Path,
    specifier: str,
    aliases: list[tuple[str, str]] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve specifier to (file_rel, dir_rel). At most one is set."""
    aliases = aliases or []
    file_tgt = resolve_ref(root, from_file, specifier, aliases)
    if file_tgt:
        return file_tgt, None

    spec = (specifier or "").strip().strip("'\"")
    if not spec or spec.startswith("node:") or spec.startswith("data:") or spec.startswith("http"):
        return None, None
    if spec.startswith("//") or spec.startswith("#"):
        return None, None
    spec = spec.split("?", 1)[0].split("#", 1)[0]
    if not spec:
        return None, None

    root_r = root.resolve()

    if spec.startswith("."):
        target = (from_file.parent / spec).resolve()
        d = _as_repo_dir(root, target)
        if d and d != "(root)":
            return None, d
        return None, None

    if spec.startswith("/"):
        trimmed = spec.lstrip("/")
        for prefix in (
            "",
            "public/",
            "static/",
            "src/",
            "app/frontend/",
            "app/backend/",
            "frontend/",
            "backend/",
        ):
            cand = (root / prefix / trimmed).resolve()
            d = _as_repo_dir(root, cand)
            if d and d != "(root)":
                return None, d
        return None, None

    if "/" in spec and re.fullmatch(r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)+", spec):
        cand = (root / spec).resolve()
        d = _as_repo_dir(root, cand)
        if d and d != "(root)":
            return None, d
        # Also accept directory that only exists as a path prefix we know
        # (e.g. data/uploads when PDFs were skipped but dir shell exists)
        try:
            if cand.is_relative_to(root_r) and (root / spec).is_dir():
                return None, spec.replace("\\", "/").rstrip("/")
        except (ValueError, OSError):
            pass

    # Single path segment from Path chains: APP_DIR / "frontend"
    if re.fullmatch(r"[A-Za-z0-9_.@+-]+", spec) and spec not in {
        "utf-8",
        "ascii",
        "latin-1",
        "POST",
        "GET",
        "PUT",
        "DELETE",
        "PATCH",
    }:
        # Prefer sibling / parent dirs near the source file, then repo root
        for base in [from_file.parent, *list(from_file.parents)[:4], root]:
            try:
                if not str(base.resolve()).startswith(str(root_r)):
                    continue
            except OSError:
                continue
            cand = (base / spec).resolve()
            d = _as_repo_dir(root, cand)
            if d and d != "(root)":
                return None, d

    return None, None


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
