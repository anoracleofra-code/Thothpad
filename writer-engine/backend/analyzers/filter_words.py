from __future__ import annotations

import re
from typing import Any, cast

from backend.models import AnalyzerResult, Flag, Severity
from backend.text_utils import excerpt

from .dialogue import dialogue_spans, inside_dialogue

DEFAULT_CATEGORIES = {
    "perception": ["saw", "heard", "felt", "noticed", "watched"],
    "cognitive": ["realized", "knew", "thought", "wondered", "remembered", "decided"],
    "distancing": ["could see", "could hear", "began to", "started to"],
    "hedges": ["just", "really", "quite", "rather", "somewhat", "perhaps"],
    "vague_modifiers": ["very", "somehow", "almost", "slightly", "a little", "a bit"],
}

SUGGESTIONS = {
    "perception": "Present the perceived detail directly unless the act of perceiving matters.",
    "cognitive": "Express the conclusion, memory, or decision directly through thought, action, or consequence.",
    "distancing": "Remove the distancing construction and present the action or sensation directly.",
    "hedges": "Remove the hedge or replace it with the exact degree meant.",
    "vague_modifiers": "Use a precise description or omit the vague modifier.",
    "custom": "Rewrite or remove this profile-defined filter word.",
}


def _phrase_pattern(phrase: str) -> str:
    escaped = re.escape(phrase.strip()).replace(r"\ ", r"\s+")
    return rf"(?<!\w){escaped}(?!\w)"


class FilterWordsAnalyzer:
    name = "filter_words"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        settings = (profile or {}).get("filter_words", {}) or {}
        if settings.get("enabled", True) is False:
            return AnalyzerResult(name=self.name, score=0.0)

        severity = str(settings.get("severity", "taste_flag"))
        if severity not in {"context_flag", "taste_flag"}:
            severity = "taste_flag"
        custom_severity = str(settings.get("custom_severity", "hard_fail"))
        if custom_severity not in {"hard_fail", "context_flag", "taste_flag"}:
            custom_severity = "hard_fail"
        categories = settings.get("categories") or DEFAULT_CATEGORIES
        custom = settings.get("custom") or []
        excluded_dialogue = dialogue_spans(text) if settings.get("ignore_dialogue", True) else []
        flags: list[Flag] = []
        counts: dict[str, int] = {}

        entries = list(categories.items()) + [("custom", custom)]
        alternatives: list[tuple[str, str]] = []
        for category, phrases in entries:
            alternatives.extend(
                (category, phrase)
                for phrase in {str(item).strip() for item in phrases if str(item).strip()}
            )
        alternatives.sort(key=lambda item: len(item[1]), reverse=True)
        pattern = re.compile(
            "|".join(
                f"(?P<p{index}>{_phrase_pattern(phrase)})"
                for index, (_category, phrase) in enumerate(alternatives)
            ),
            re.I,
        ) if alternatives else None
        if pattern:
            for match in pattern.finditer(text):
                start, end = match.span()
                if inside_dialogue(start, end, excluded_dialogue):
                    continue
                category = alternatives[int(match.lastgroup[1:])][0]  # type: ignore[index]
                counts[category] = counts.get(category, 0) + 1
                flags.append(
                    Flag(
                        type=f"filter_{category}",
                        severity=cast(Severity, custom_severity if category == "custom" else severity),
                        start=start,
                        end=end,
                        excerpt=excerpt(text, start, end),
                        suggestion=SUGGESTIONS.get(category, SUGGESTIONS["custom"]),
                    )
                )

        flags.sort(key=lambda flag: flag.start)
        return AnalyzerResult(
            name=self.name,
            score=float(len(flags)),
            flags=flags,
            metrics={
                "counts_by_category": counts,
                "ignored_dialogue": bool(excluded_dialogue),
                "total_findings": sum(counts.values()),
                "findings_truncated": False,
            },
        )
