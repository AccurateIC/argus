"""Parse new-file line numbers that a unified diff adds or touches."""

from __future__ import annotations

import re
from collections import defaultdict


def changed_lines_by_file(diff: str) -> dict[str, set[int]]:
    """Map path -> set of line numbers in the *new* file that the PR changed.

    Counts added lines and the new-side line of each hunk context so Ruff/AST
    findings can be filtered to the PR window. Pure deletions contribute no lines.
    """
    result: dict[str, set[int]] = defaultdict(set)
    path = ""
    new_line = 0
    in_hunk = False

    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            path = ""
            in_hunk = False
            m = re.search(r" b/(.+)$", raw)
            if m:
                path = m.group(1).lstrip("./")
            continue
        if raw.startswith("+++ "):
            # +++ b/path or +++ /dev/null
            rest = raw[4:].strip()
            if rest.startswith("b/"):
                path = rest[2:].lstrip("./")
            elif rest != "/dev/null" and not rest.startswith("a/"):
                path = rest.lstrip("./")
            continue
        if raw.startswith("@@"):
            # @@ -a,b +c,d @@
            m = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            if not m or not path:
                in_hunk = False
                continue
            new_line = int(m.group(1))
            in_hunk = True
            continue
        if not in_hunk or not path:
            continue
        if raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        if raw.startswith("+"):
            result[path].add(new_line)
            new_line += 1
        elif raw.startswith("-"):
            continue
        else:
            # context line present in new file
            new_line += 1

    return dict(result)
