#!/usr/bin/env python3
"""Launcher: python3 scripts/dep_graph.py build --root … --out dep-graph.html"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from context_retrieval.dep_graph.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
