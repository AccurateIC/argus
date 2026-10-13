"""Query helpers over a FileGraph (for later Argus hop-1 retrieval)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scan import FileGraph


def imports_of(graph: FileGraph, path: str) -> list[str]:
    return list(graph.imports.get(path, []))


def imported_by(graph: FileGraph, path: str) -> list[str]:
    return list(graph.imported_by.get(path, []))


def hop1_related(graph: FileGraph, changed_paths: list[str]) -> dict:
    """Variant A: direct importers + tests that touch those files."""
    dependents: set[str] = set()
    tests: set[str] = set()
    for p in changed_paths:
        for importer in graph.imported_by.get(p, []):
            dependents.add(importer)
            meta = graph.nodes.get(importer) or {}
            if meta.get("kind") == "test":
                tests.add(importer)
        meta = graph.nodes.get(p) or {}
        if meta.get("kind") == "test":
            tests.add(p)
    # Also include tests that import any changed file (already in dependents)
    return {
        "dependents": sorted(dependents),
        "tests": sorted(tests),
        "changed": sorted(set(changed_paths)),
    }
