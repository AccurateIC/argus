"""Lightweight language → analyzer list registry."""

from __future__ import annotations

from .analyzers.python import python_analyzers


def analyzers_for_language(language: str) -> list:
    """Return analyzer instances for a language id. Empty if unsupported."""
    if language == "python":
        return python_analyzers()
    # Future: javascript, typescript, go, …
    return []


def supported_languages() -> list[str]:
    return ["python"]
