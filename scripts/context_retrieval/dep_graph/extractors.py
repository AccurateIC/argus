"""Pull dependency specifiers from source text by language."""

from __future__ import annotations

import ast
import re
from pathlib import Path

# JS / TS / Vue script-like
_JS_IMPORT_RE = re.compile(
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

_CSS_RE = re.compile(
    r"""(?x)
    (?:
        @import\s+(?:url\s*\(\s*)?['"]([^'"]+)['"]
      | @import\s+url\s*\(\s*([^)'"]+)\s*\)
      | url\s*\(\s*['"]?([^)'"\s]+)['"]?\s*\)
    )
    """
)

_HTML_RE = re.compile(
    r"""(?ix)
    (?:
        (?:src|href|data|poster|content)\s*=\s*['"]([^'"]+)['"]
    )
    """
)

# Relative path literals that often appear in code/config
_REL_PATH_RE = re.compile(r"""['"](\.\.?/[^'"]+)['"]""")

# Path-like strings: "app/frontend", "data/uploads", "frontend/index.html"
_PATHISH_RE = re.compile(
    r"""['"]((?:[A-Za-z0-9_.-]+/)+\.?[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9]+)?)['"]"""
)

_FALSE_PATH_PREFIXES = (
    "http://",
    "https://",
    "application/",
    "text/",
    "image/",
    "multipart/",
)


def extract_js_specifiers(source: str) -> list[str]:
    specs: list[str] = []
    for m in _JS_IMPORT_RE.finditer(source):
        for g in m.groups():
            if g:
                specs.append(g.strip())
                break
    return specs


def extract_css_specifiers(source: str) -> list[str]:
    specs: list[str] = []
    for m in _CSS_RE.finditer(source):
        for g in m.groups():
            if g:
                g = g.strip().strip("'\"")
                if g and not g.startswith("data:"):
                    specs.append(g)
                break
    return specs


def extract_html_specifiers(source: str) -> list[str]:
    specs: list[str] = []
    for m in _HTML_RE.finditer(source):
        g = (m.group(1) or "").strip()
        if not g or g.startswith("data:") or g.startswith("javascript:"):
            continue
        if g.startswith("http://") or g.startswith("https://") or g.startswith("mailto:"):
            continue
        specs.append(g)
    return specs


def extract_python_specifiers(source: str, from_file: Path) -> list[str]:
    """Return relative-ish module paths / relative import paths for resolution."""
    specs: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Fallback: simple from .x import / import .
        for m in re.finditer(
            r"^\s*from\s+(\.+[\w.]*)\s+import\s+", source, flags=re.M
        ):
            specs.append(m.group(1))
        return specs

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            level = node.level or 0
            if level > 0:
                # relative: build ./ or ../ chain + module
                prefix = "/".join([".."] * (level - 1)) if level > 1 else "."
                if level == 1:
                    rel = f"./{mod.replace('.', '/')}" if mod else "./"
                else:
                    rel = f"{prefix}/{mod.replace('.', '/')}" if mod else prefix + "/"
                specs.append(rel.replace("//", "/"))
            elif mod:
                specs.append(mod)  # absolute dotted — resolve_ref may map to file
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    specs.append(alias.name)
    return specs


def extract_generic_rel_paths(source: str) -> list[str]:
    return [m.group(1) for m in _REL_PATH_RE.finditer(source)]


def extract_pathish_strings(source: str) -> list[str]:
    """Catch repo-relative path strings used in Path(...), mounts, docs, etc."""
    out: list[str] = []
    for m in _PATHISH_RE.finditer(source):
        s = m.group(1)
        if any(s.startswith(p) for p in _FALSE_PATH_PREFIXES):
            continue
        if s.count("/") < 1:
            continue
        # skip version-like or URL-ish
        if "://" in s:
            continue
        out.append(s)
    return out


# REPO_ROOT / "data" / "uploads" / run_id   →  data/uploads
# APP_DIR / "frontend"                     →  frontend (resolved from file later)
_PATH_CHAIN_RE = re.compile(
    r"""(?x)
    (?:Path\s*\([^)]*\)|[A-Za-z_][\w.]*)
    (?P<chain>(?:\s*/\s*['"][^'"]+['"]){1,8})
    """
)
_PATH_SEG_RE = re.compile(r"""['"]([^'"]+)['"]""")


def extract_path_chains(source: str) -> list[str]:
    """Join successive Path / \"seg\" segments into repo-ish path strings."""
    out: list[str] = []
    for m in _PATH_CHAIN_RE.finditer(source):
        segs = _PATH_SEG_RE.findall(m.group("chain"))
        # Keep only simple path segments (not f-strings / variables)
        clean = [s for s in segs if s and "/" not in s and not s.startswith("{")]
        if not clean:
            continue
        joined = "/".join(clean)
        out.append(joined)
        # Also emit prefixes: data, data/uploads
        for i in range(1, len(clean)):
            out.append("/".join(clean[:i]))
    return out


def extract_specifiers_for_file(path: Path, source: str) -> list[str]:
    suf = path.suffix.lower()
    specs: list[str] = []
    if suf in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte"}:
        specs.extend(extract_js_specifiers(source))
    if suf in {".css", ".scss", ".sass", ".less"}:
        specs.extend(extract_css_specifiers(source))
    if suf in {".html", ".htm"}:
        specs.extend(extract_html_specifiers(source))
        # HTML often embeds script with imports
        specs.extend(extract_js_specifiers(source))
        specs.extend(extract_css_specifiers(source))
    if suf in {".py", ".pyi"}:
        specs.extend(extract_python_specifiers(source, path))
        specs.extend(extract_path_chains(source))
    # Always harvest obvious relative path strings
    specs.extend(extract_generic_rel_paths(source))
    specs.extend(extract_pathish_strings(source))
    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for s in specs:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# Back-compat for tests
def extract_specifiers(source: str) -> list[str]:
    return extract_js_specifiers(source)
