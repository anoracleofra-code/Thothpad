from __future__ import annotations

import re
from typing import Any

from backend.models import AnalyzerResult, Flag
from backend.text_utils import excerpt, paragraphs

METAPHOR_MARKERS = re.compile(r"\b(like|as if|as though|as a|became|becomes|turned into|was a|were a)\b", re.I)
IMAGE_NOUNS = re.compile(
    r"\b(storm|knife|ghost|shadow|hunger|silence|fire|ice|wound|river|ocean|machine|mirror|maze|monster|void)\b",
    re.I,
)


class MetaphorDensityAnalyzer:
    name = "metaphor_density"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        flags: list[Flag] = []
        for start, end, para in paragraphs(text):
            marker_count = len(METAPHOR_MARKERS.findall(para))
            image_count = len(IMAGE_NOUNS.findall(para))
            if marker_count >= 3 or (marker_count >= 2 and image_count >= 2):
                flags.append(
                    Flag(
                        type="metaphor_pileup",
                        severity="context_flag",
                        start=start,
                        end=end,
                        excerpt=excerpt(text, start, end),
                        suggestion="Keep the strongest image and replace the rest with physical action or plain description.",
                        source="heuristic",
                    )
                )
        return AnalyzerResult(name=self.name, score=float(len(flags)), flags=flags)
