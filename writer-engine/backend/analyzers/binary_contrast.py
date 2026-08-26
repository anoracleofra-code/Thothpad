from __future__ import annotations

from typing import Any

from backend.models import AnalyzerResult

from .pattern_helpers import regex_flags


class BinaryContrastAnalyzer:
    name = "binary_contrast"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        patterns = [
            (
                "not_x_but_y",
                r"\bnot\s+(?:just\s+|only\s+|merely\s+)?[^.!?;,\n]{1,120}[,;:]?\s+but\s+(?:also\s+)?[^.!?\n]{1,160}",
                "context_flag",
                "State the positive assertion directly or embody it in action.",
            ),
            (
                "isnt_x_its_y",
                r"\b(?:isn't|aren't|wasn't|weren't|is\s+not|are\s+not|was\s+not|were\s+not)\s+[^.!?]{1,120}[.!?]\s+(?:it|that|this|they)(?:'s|'re|\s+is|\s+are|\s+was|\s+were)\s+[^.!?]{1,160}",
                "context_flag",
                "Remove the setup/reveal frame and lead with the actual claim.",
            ),
            (
                "not_because_because",
                r"\bnot\s+because\s+[^.!?]{1,120}[.!?;,]\s*(?:but\s+)?because\s+[^.!?]{1,160}",
                "context_flag",
                "Cut the negated runway and state the cause.",
            ),
            (
                "question_isnt",
                r"\bthe\s+(?:question|answer|problem)\s+(?:isn't|is\s+not)\s+[^.!?]{1,100}[.!?]\s+(?:it|the\s+\w+)\s+(?:is|'s)\s+[^.!?]{1,140}",
                "context_flag",
                "Replace the rhetorical pivot with a direct sentence.",
            ),
        ]
        return regex_flags(name=self.name, text=text, patterns=patterns)  # type: ignore[arg-type]
