"""Analyzer contract — language plugins implement this shape."""

from __future__ import annotations

from typing import Protocol


class LanguageAnalyzer(Protocol):
    """Runs against one source file; returns Argus-shaped finding dicts.

    Optional internal keys: _line, _path, _source, _hint_only.
    Findings with _hint_only=True are passed to Qwen as hints, not auto-posted.
    """

    name: str
    language: str

    def enabled(self, lang_cfg: dict) -> bool:
        """Whether this analyzer should run given advanced_findings.<language> config."""
        ...

    def analyze(self, path: str, source: str) -> list[dict]:
        ...
