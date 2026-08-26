from __future__ import annotations

import re
from typing import Any

from backend.models import AnalyzerResult, Flag
from backend.text_utils import excerpt, paragraphs, words

CONCRETE_WORDS = {
    "door",
    "floor",
    "table",
    "chair",
    "phone",
    "screen",
    "car",
    "street",
    "glass",
    "paper",
    "shirt",
    "hand",
    "mouth",
    "kitchen",
    "office",
    "room",
    "motel",
    "rain",
    "ash",
    "coffee",
    "badge",
    "knife",
    "wallet",
    "key",
    "window",
    "server",
    "api",
    "database",
    "invoice",
    "contract",
    "dollar",
}


class ConcreteAnchorAnalyzer:
    name = "concrete_anchor"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        flags: list[Flag] = []
        for start, end, para in paragraphs(text):
            toks = words(para)
            if len(toks) < 25:
                continue
            has_number = bool(re.search(r"\b\d+(?:[.,]\d+)?\b", para))
            has_capital_name = bool(re.search(r"\b[A-Z][a-z]{2,}\b", para))
            concrete_hits = sum(1 for tok in toks if tok in CONCRETE_WORDS)
            if not has_number and not has_capital_name and concrete_hits == 0:
                flags.append(
                    Flag(
                        type="missing_concrete_anchor",
                        severity="context_flag",
                        start=start,
                        end=end,
                        excerpt=excerpt(text, start, end),
                        suggestion="Anchor this paragraph with an object, place, name, number, body action, or domain-specific noun.",
                        source="heuristic",
                    )
                )
        return AnalyzerResult(name=self.name, score=float(len(flags)), flags=flags)
