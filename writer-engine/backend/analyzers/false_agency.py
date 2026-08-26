from __future__ import annotations

import re
from typing import Any

from backend.models import AnalyzerResult, Flag
from backend.text_utils import excerpt

ABSTRACTIONS = (
    "decision",
    "culture",
    "conversation",
    "data",
    "market",
    "complaint",
    "question",
    "answer",
    "problem",
    "story",
    "silence",
    "fear",
    "grief",
    "memory",
    "truth",
    "choice",
)
HUMAN_VERBS = (
    "tells",
    "speaks",
    "whispers",
    "demands",
    "rewards",
    "punishes",
    "decides",
    "chooses",
    "moves",
    "shifts",
    "becomes",
    "emerges",
    "reveals",
    "remembers",
    "knows",
    "wants",
)


class FalseAgencyAnalyzer:
    name = "false_agency"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        flags: list[Flag] = []
        pattern = re.compile(
            rf"\b(?:the\s+)?({'|'.join(ABSTRACTIONS)})\s+({'|'.join(HUMAN_VERBS)})\b(?:\s+[^.!?]{{0,100}})?",
            re.I,
        )
        for match in pattern.finditer(text):
            flags.append(
                Flag(
                    type="false_agency",
                    severity="context_flag",
                    start=match.start(),
                    end=match.end(),
                    excerpt=excerpt(text, match.start(), match.end()),
                    suggestion="Name the person acting, deciding, reading, paying, refusing, or changing behavior.",
                    source="heuristic",
                )
            )
        return AnalyzerResult(name=self.name, score=float(len(flags)), flags=flags)
