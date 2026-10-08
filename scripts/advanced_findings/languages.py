"""Map file paths to language ids (generic; not analyzer logic)."""

from __future__ import annotations

from pathlib import Path

# Extension → language id used by the analyzer registry.
_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".rs": "rust",
    ".rb": "ruby",
}


def language_for_path(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    return _EXT_TO_LANG.get(ext)


def paths_by_language(changed: dict[str, set[int]]) -> dict[str, list[str]]:
    """Group changed paths that have at least one changed line by language id."""
    out: dict[str, list[str]] = {}
    for path, lines in changed.items():
        if not lines:
            continue
        lang = language_for_path(path)
        if not lang:
            continue
        out.setdefault(lang, []).append(path)
    for lang in out:
        out[lang].sort()
    return out
