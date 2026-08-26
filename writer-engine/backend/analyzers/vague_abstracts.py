from __future__ import annotations

from typing import Any

from backend.models import AnalyzerResult

from .pattern_helpers import regex_flags


class VagueAbstractAnalyzer:
    name = "vague_abstracts"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        suggestion = "Name the specific consequence, date, object, cost, person, or action."
        patterns = [
            ("stakes_high", r"\bthe\s+stakes\s+are\s+high\b", "context_flag", suggestion),
            ("consequences_real", r"\bthe\s+consequences\s+are\s+real\b", "context_flag", suggestion),
            ("this_matters", r"\bthis\s+matters(?:\s+because)?\b", "context_flag", suggestion),
            ("implications_significant", r"\bthe\s+implications\s+are\s+significant\b", "context_flag", suggestion),
            ("reasons_structural", r"\bthe\s+reasons\s+are\s+structural\b", "context_flag", suggestion),
            ("deepest_problem", r"\bthe\s+deepest\s+problem\b", "context_flag", suggestion),
            ("at_its_core", r"\bat\s+its\s+core\b", "context_flag", suggestion),
            ("the_reality_is", r"\bthe\s+reality\s+is\b", "context_flag", suggestion),
        ]
        return regex_flags(name=self.name, text=text, patterns=patterns)  # type: ignore[arg-type]
