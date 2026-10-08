"""Python-specific deterministic analyzers (Ruff + AST)."""

from __future__ import annotations

from . import ast_checks, ruff_runner


class PythonRuffAnalyzer:
    name = "ruff"
    language = "python"

    def enabled(self, lang_cfg: dict) -> bool:
        return bool(lang_cfg.get("ruff"))

    def analyze(self, path: str, source: str) -> list[dict]:
        if not path.endswith((".py", ".pyi")):
            return []
        return ruff_runner.run_ruff_on_source(path, source)


class PythonDeadCodeAnalyzer:
    """Unreachable statements after return/raise (AST)."""

    name = "dead_code"
    language = "python"

    def enabled(self, lang_cfg: dict) -> bool:
        return bool(lang_cfg.get("ast"))

    def analyze(self, path: str, source: str) -> list[dict]:
        if not path.endswith((".py", ".pyi")):
            return []
        return ast_checks.find_unreachable_after_terminal(source, path)


class PythonTerminationAnalyzer:
    """Suspicious non-terminating loops (hints for Qwen, not hard gates)."""

    name = "termination"
    language = "python"

    def enabled(self, lang_cfg: dict) -> bool:
        return bool(lang_cfg.get("ast"))

    def analyze(self, path: str, source: str) -> list[dict]:
        if not path.endswith((".py", ".pyi")):
            return []
        return ast_checks.find_suspicious_infinite_loops(source, path)


def python_analyzers() -> list:
    return [
        PythonRuffAnalyzer(),
        PythonDeadCodeAnalyzer(),
        PythonTerminationAnalyzer(),
    ]
