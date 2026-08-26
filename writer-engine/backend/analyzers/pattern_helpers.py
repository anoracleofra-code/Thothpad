from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from backend.models import AnalyzerResult, Flag, Severity
from backend.text_utils import excerpt


def _literal_present(literals: str | Iterable[str], lowered: str) -> bool:
    if isinstance(literals, str):
        return literals in lowered
    return any(literal in lowered for literal in literals)


def regex_flags(
    *,
    name: str,
    text: str,
    patterns: Iterable[
        tuple[str, str, Severity, str] | tuple[str, str, Severity, str, str | Iterable[str]],
    ],
    flags: int = re.I | re.S,
) -> AnalyzerResult:
    found: list[Flag] = []
    occupied = bytearray(len(text))
    total = 0
    lowered = text.lower()
    for flag_type, pattern, severity, suggestion, *extra in patterns:
        if extra and not _literal_present(extra[0], lowered):
            continue
        for match in re.finditer(pattern, text, flags):
            start, end = match.span()
            if occupied.find(1, start, end) != -1:
                continue
            occupied[start:end] = b"\x01" * (end - start)
            total += 1
            found.append(
                Flag(
                    type=flag_type,
                    severity=severity,
                    start=start,
                    end=end,
                    excerpt=excerpt(text, start, end),
                    suggestion=suggestion,
                )
            )
    return AnalyzerResult(
        name=name,
        score=float(total),
        flags=found,
        metrics={
            "total_findings": total,
            "findings_truncated": False,
        },
    )


def with_profile_patterns(
    result: AnalyzerResult,
    text: str,
    profile: dict[str, Any] | None,
) -> AnalyzerResult:
    profile = profile or {}
    patterns: list[tuple[str, str]] = []
    for item in profile.get("hard_bans", []) or []:
        patterns.append(("hard_ban", str(item)))
    for item in profile.get("soft_flags", []) or []:
        patterns.append(("soft_flag", str(item)))

    for kind, phrase in patterns:
        if not phrase:
            continue
        severity: Severity = "taste_flag" if kind == "soft_flag" else "hard_fail"
        phrase_pattern = rf"(?<!\w){re.escape(phrase)}(?!\w)"
        for match in re.finditer(phrase_pattern, text, re.I):
            result.flags.append(
                Flag(
                    type=kind,
                    severity=severity,
                    start=match.start(),
                    end=match.end(),
                    excerpt=excerpt(text, match.start(), match.end()),
                    suggestion="Rewrite or remove this user-profile phrase.",
                    source="profile",
                )
            )
    result.score = float(len(result.flags))
    return result
