from __future__ import annotations

from typing import Any

from backend.models import AnalyzerResult

from .pattern_helpers import regex_flags


class CinematicFogAnalyzer:
    name = "cinematic_fog"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        suggestion = "Trade atmospheric filler for a concrete object, sound, smell, cost, or action."
        patterns = [
            ("air_thick", r"\b(?:the\s+)?air\s+(?:was\s+)?thick\s+with\b", "context_flag", suggestion),
            ("silence_stretched", r"\b(?:the\s+)?silence\s+(?:stretched|settled|pressed|hung|fell)\b", "context_flag", suggestion),
            ("shadows_danced", r"\bshadows?\s+(?:danced|stretched|swallowed|crept|loomed)\b", "context_flag", suggestion),
            ("time_slowed", r"\btime\s+(?:slowed|seemed\s+to\s+slow|stopped|stretched)\b", "context_flag", suggestion),
            ("darkness_swallowed", r"\bdarkness\s+(?:swallowed|pressed|crept|wrapped)\b", "context_flag", suggestion),
            ("words_hung_air", r"\bwords?\s+hung\s+in\s+the\s+air\b", "context_flag", suggestion),
            ("felt_like_eternity", r"\bfelt\s+like\s+(?:an\s+)?eternity\b", "context_flag", suggestion),
        ]
        return regex_flags(name=self.name, text=text, patterns=patterns)  # type: ignore[arg-type]
