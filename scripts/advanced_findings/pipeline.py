"""Language-aware orchestration for advanced findings."""

from __future__ import annotations

import sys

from .diff_lines import changed_lines_by_file
from .fetch import fetch_file, head_ref
from .languages import paths_by_language
from .merge import merge_findings
from .registry import analyzers_for_language


def on_changed_line(finding: dict, changed: dict[str, set[int]]) -> bool:
    """Generic changed-line filter (any language)."""
    path = str(finding.get("_path") or "")
    line = finding.get("_line")
    if not path or not isinstance(line, int):
        loc = str(finding.get("location") or "")
        if ":" in loc:
            path, _, rest = loc.partition(":")
            path = path.lstrip("./")
            try:
                line = int(rest.strip().split(":")[0])
            except ValueError:
                return False
        else:
            return False
    path = path.lstrip("./")
    lines = changed.get(path)
    if not lines:
        for p, ls in changed.items():
            if p == path or p.endswith("/" + path) or path.endswith("/" + p):
                return line in ls
        return False
    return line in lines


def run_advanced_findings(
    diff: str,
    pr: str,
    *,
    adv_cfg: dict | None = None,
) -> tuple[list[dict], list[str], str]:
    """Returns (reportable_findings, hint_lines_for_prompt, status_note).

    ``adv_cfg`` is the ``advanced_findings`` dict from argus.yml, e.g.::

        {enabled: true, python: {ruff: true, ast: true}}
    """
    adv_cfg = adv_cfg or {}
    if not bool(adv_cfg.get("enabled")):
        return [], [], ""

    changed = changed_lines_by_file(diff)
    by_lang = paths_by_language(changed)
    if not by_lang:
        return [], [], "advanced_findings: no changed files in a supported language"

    ref = head_ref(pr)
    if not ref:
        print(
            "neubodhi-ollama: advanced_findings: could not resolve PR head ref",
            file=sys.stderr,
        )
        return [], [], "advanced_findings: no head ref"

    reportable: list[dict] = []
    hints: list[dict] = []
    files_analyzed = 0

    for language, paths in by_lang.items():
        analyzers = analyzers_for_language(language)
        if not analyzers:
            continue
        # Per-language config block; fall back to empty (all analyzers disabled).
        lang_cfg = adv_cfg.get(language)
        if not isinstance(lang_cfg, dict):
            # Backward compat: flat ruff/ast keys meant python.
            if language == "python" and (
                "ruff" in adv_cfg or "ast" in adv_cfg
            ):
                lang_cfg = {
                    "ruff": bool(adv_cfg.get("ruff")),
                    "ast": bool(adv_cfg.get("ast")),
                }
            else:
                lang_cfg = {}

        active = [a for a in analyzers if a.enabled(lang_cfg)]
        if not active:
            continue

        for path in paths:
            source = fetch_file(path, ref)
            if source is None:
                print(
                    f"neubodhi-ollama: advanced_findings: skip {path} (fetch failed)",
                    file=sys.stderr,
                )
                continue
            files_analyzed += 1
            for analyzer in active:
                try:
                    raw = analyzer.analyze(path, source)
                except Exception as exc:
                    print(
                        f"neubodhi-ollama: advanced_findings: {language}/{analyzer.name} "
                        f"failed on {path}: {exc}",
                        file=sys.stderr,
                    )
                    continue
                for f in raw:
                    if not on_changed_line(f, changed):
                        continue
                    if f.get("_hint_only"):
                        hints.append(f)
                    else:
                        reportable.append(f)

    hint_lines = [f"- {h.get('location')}: {h.get('finding')}" for h in hints]
    langs = ",".join(sorted(by_lang))
    note = (
        f"advanced_findings: {len(reportable)} finding(s), {len(hints)} hint(s), "
        f"{files_analyzed} file(s), languages={langs}"
    )
    print(f"neubodhi-ollama: {note}", file=sys.stderr)
    return reportable, hint_lines, note


__all__ = ["run_advanced_findings", "merge_findings", "on_changed_line"]
