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

_CAPITALIZED_WORD_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")


def _has_proper_name_signal(paragraph: str) -> bool:
    """Return true for capitalized words that are not merely sentence-initial.

    A blanket capitalized-word test makes nearly every English paragraph look
    concretely anchored because its first word starts with a capital letter.
    Mid-sentence capitalization is a much stronger cheap signal for a name.
    Repeated capitalization also preserves common name-at-sentence-start cases
    without treating a single ordinary opening word as a proper noun.
    """
    matches = list(_CAPITALIZED_WORD_RE.finditer(paragraph))
    if not matches:
        return False

    counts: dict[str, int] = {}
    for match in matches:
        token = match.group(0)
        counts[token] = counts.get(token, 0) + 1
        prefix = paragraph[: match.start()].rstrip()
        if prefix and prefix[-1] not in ".!?\n":
            return True

    return any(count > 1 for count in counts.values())


class ConcreteAnchorAnalyzer:
    name = "concrete_anchor"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        flags: list[Flag] = []
        for start, end, para in paragraphs(text):
            toks = words(para)
            if len(toks) < 25:
                continue
            has_number = bool(re.search(r"\b\d+(?:[.,]\d+)?\b", para))
            has_capital_name = _has_proper_name_signal(para)
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
