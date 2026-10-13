"""File-level TS/JS dependency graph → single interactive HTML diagram."""

from .aggregate import aggregate_folders
from .query import hop1_related, imported_by, imports_of
from .scan import build_file_graph
from .naming import pr_graph_dir, default_cache_root

__all__ = [
    "aggregate_folders",
    "build_file_graph",
    "hop1_related",
    "imported_by",
    "imports_of",
    "pr_graph_dir",
    "default_cache_root",
]
