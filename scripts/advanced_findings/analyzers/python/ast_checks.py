"""Lightweight AST checks: high-confidence unreachable code + suspicious loops."""

from __future__ import annotations

import ast


_TERMINATORS = (ast.Break, ast.Return, ast.Raise, ast.Yield, ast.YieldFrom)


def _stmt_lineno(node: ast.AST) -> int | None:
    return getattr(node, "lineno", None)


def _block_has_terminator(body: list[ast.stmt]) -> bool:
    """True if any statement in this block (recursively) can exit a surrounding loop."""
    for stmt in body:
        if isinstance(stmt, _TERMINATORS):
            return True
        if isinstance(stmt, ast.If):
            if _block_has_terminator(stmt.body) or _block_has_terminator(stmt.orelse):
                return True
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            if _block_has_terminator(stmt.body) or _block_has_terminator(stmt.orelse):
                return True
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            if _block_has_terminator(stmt.body):
                return True
        elif isinstance(stmt, ast.Try):
            if _block_has_terminator(stmt.body):
                return True
            for h in stmt.handlers:
                if _block_has_terminator(h.body):
                    return True
            if _block_has_terminator(stmt.orelse) or _block_has_terminator(stmt.finalbody):
                return True
        elif isinstance(stmt, ast.Match):
            for case in stmt.cases:
                if _block_has_terminator(case.body):
                    return True
    return False


def _scan_unreachable(body: list[ast.stmt], path: str, out: list[dict]) -> None:
    for i, stmt in enumerate(body):
        if isinstance(stmt, ast.If):
            _scan_unreachable(stmt.body, path, out)
            _scan_unreachable(stmt.orelse, path, out)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            _scan_unreachable(stmt.body, path, out)
            _scan_unreachable(stmt.orelse, path, out)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            _scan_unreachable(stmt.body, path, out)
        elif isinstance(stmt, ast.Try):
            _scan_unreachable(stmt.body, path, out)
            for h in stmt.handlers:
                _scan_unreachable(h.body, path, out)
            _scan_unreachable(stmt.orelse, path, out)
            _scan_unreachable(stmt.finalbody, path, out)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_unreachable(stmt.body, path, out)
        elif isinstance(stmt, ast.ClassDef):
            for item in stmt.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _scan_unreachable(item.body, path, out)
        elif isinstance(stmt, ast.Match):
            for case in stmt.cases:
                _scan_unreachable(case.body, path, out)

        if isinstance(stmt, (ast.Return, ast.Raise)) and i + 1 < len(body):
            nxt = body[i + 1]
            if isinstance(nxt, ast.Pass):
                continue
            line = _stmt_lineno(nxt)
            if not line:
                continue
            kind = "return" if isinstance(stmt, ast.Return) else "raise"
            out.append(
                {
                    "severity": "major",
                    "skill": "correctness",
                    "location": f"{path}:{line}",
                    "finding": (
                        f"[ast] Unreachable code: statement after `{kind}` "
                        "in the same block can never execute."
                    ),
                    "suggested_fix": (
                        f"Remove the dead code, or move it before the `{kind}`."
                    ),
                    "_line": line,
                    "_path": path,
                    "_source": "ast",
                    "_code": "unreachable",
                }
            )


def find_unreachable_after_terminal(source: str, path: str) -> list[dict]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[dict] = []
    _scan_unreachable(tree.body, path, out)
    return out


def find_suspicious_infinite_loops(source: str, path: str) -> list[dict]:
    """`while True` with no break/return/raise/yield anywhere in the body tree.

    Hint-only (nit) — surfaced to Qwen; not a hard gate by themselves.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    findings: list[dict] = []

    class Visitor(ast.NodeVisitor):
        def visit_While(self, node: ast.While) -> None:
            if _is_forever_test(node.test) and not _block_has_terminator(node.body):
                line = _stmt_lineno(node) or 0
                if line:
                    findings.append(
                        {
                            "severity": "nit",
                            "skill": "correctness",
                            "location": f"{path}:{line}",
                            "finding": (
                                "[ast] Suspicious unbounded loop with no "
                                "`break`/`return`/`raise`/`yield` in the body — "
                                "confirm intentional long-running loop or add an exit."
                            ),
                            "suggested_fix": (
                                "Add a clear termination condition, or document why this "
                                "loop is intentionally unbounded."
                            ),
                            "_line": line,
                            "_path": path,
                            "_source": "ast",
                            "_code": "suspicious-loop",
                            "_hint_only": True,
                        }
                    )
            self.generic_visit(node)

    Visitor().visit(tree)
    return findings


def _is_forever_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Constant) and test.value is True:
        return True
    if isinstance(test, ast.Constant) and test.value == 1:
        return True
    if isinstance(test, ast.Name) and test.id == "True":
        return True
    return False
