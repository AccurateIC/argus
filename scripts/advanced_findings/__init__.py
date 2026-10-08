"""Config-gated, language-aware static analysis for Argus."""

from .merge import merge_findings
from .pipeline import run_advanced_findings

__all__ = ["run_advanced_findings", "merge_findings"]
