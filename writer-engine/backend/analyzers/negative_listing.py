from __future__ import annotations

import re
from typing import Any

from backend.models import AnalyzerResult, Flag
from backend.text_utils import excerpt


class NegativeListingAnalyzer:
    name = "negative_listing"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        flags: list[Flag] = []
        patterns = [
            r"\b(?:it\s+)?was(?:n't|\s+not)\s+[^.!?]{1,80}[.!?]\s+(?:it\s+)?was(?:n't|\s+not)\s+[^.!?]{1,80}[.!?]\s+(?:it\s+)?was\s+[^.!?]{1,120}",
            r"\bnot\s+(?:a\s+|the\s+)?[^.!?]{1,60}[.!?]\s+not\s+(?:a\s+|the\s+)?[^.!?]{1,60}[.!?]\s+(?:a|an|the|it\s+was)\s+[^.!?]{1,120}",
            r"\bnot\s+[^,\n]{1,60},\s+not\s+[^,\n]{1,60},\s+(?:and\s+)?not\s+[^,\n]{1,60},?\s+(?:but|it\s+was)\s+[^.!?]{1,120}",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.I | re.S):
                flags.append(
                    Flag(
                        type="negative_reveal_list",
                        severity="context_flag",
                        start=match.start(),
                        end=match.end(),
                        excerpt=excerpt(text, match.start(), match.end()),
                        suggestion="Stop stripping away false options. Put the real thing on the page first.",
                    )
                )
        return AnalyzerResult(name=self.name, score=float(len(flags)), flags=flags)
