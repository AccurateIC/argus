"""Merge deterministic findings with LLM findings; prefer tools on overlap."""

from __future__ import annotations

import re


def _loc_key(finding: dict) -> tuple[str, int] | None:
    loc = str(finding.get("location") or "")
    if ":" not in loc or loc.strip() in {"", "—", "-"}:
        return None
    path, _, rest = loc.partition(":")
    m = re.match(r"(\d+)", rest.strip())
    if not m:
        return None
    return path.lstrip("./"), int(m.group(1))


def _norm_text(finding: dict) -> str:
    t = str(finding.get("finding") or "").lower()
    t = re.sub(r"\[(ruff|ast)[^\]]*\]\s*", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def _public_finding(f: dict) -> dict:
    return {
        "severity": f.get("severity") or "nit",
        "skill": f.get("skill") or "correctness",
        "location": f.get("location") or "—",
        "finding": f.get("finding") or "",
        "suggested_fix": f.get("suggested_fix") or "",
    }


def _text_overlap(a: str, b: str) -> bool:
    ta, tb = set(a.split()), set(b.split())
    if len(ta) < 2 or len(tb) < 2:
        return False
    return len(ta & tb) >= 3


def merge_findings(deterministic: list[dict], llm: list[dict]) -> list[dict]:
    """Keep deterministic findings; drop LLM items at the same file:line or with
    clear same-file text overlap on unused/unreachable themes.
    """
    out = [_public_finding(f) for f in deterministic if str(f.get("finding") or "").strip()]
    det_locs = {k for f in deterministic if (k := _loc_key(f))}

    for f in llm:
        if not str(f.get("finding") or "").strip():
            continue
        key = _loc_key(f)
        if key and key in det_locs:
            continue
        nt = _norm_text(f)
        if key and nt:
            path = key[0]
            skip = False
            for d in deterministic:
                dk = _loc_key(d)
                if not dk or dk[0] != path:
                    continue
                dn = _norm_text(d)
                if _text_overlap(nt, dn):
                    skip = True
                    break
            if skip:
                continue
        out.append(_public_finding(f))
    return out
