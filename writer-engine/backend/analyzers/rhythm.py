from __future__ import annotations

from typing import Any

from backend.models import AnalyzerResult, Flag
from backend.text_utils import excerpt, safe_stdev, sentence_word_lengths, sentences, words

ABSTRACT_MIC_DROP_WORDS = {
    "truth",
    "choice",
    "memory",
    "silence",
    "grief",
    "hunger",
    "thing",
}


class RhythmAnalyzer:
    name = "rhythm"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        flags: list[Flag] = []
        lengths = sentence_word_lengths(text)
        if len(lengths) >= 5 and safe_stdev(lengths) < 3.0:
            flags.append(
                Flag(
                    type="metronomic_sentence_lengths",
                    severity="context_flag",
                    start=0,
                    end=min(len(text), 300),
                    excerpt=excerpt(text, 0, min(len(text), 300)),
                    suggestion="Vary sentence length. Combine one pair, cut one sentence, and let one sentence run longer.",
                    source="heuristic",
                )
            )

        sents = sentences(text)
        for start, end, sentence in sents:
            toks = words(sentence)
            if 1 <= len(toks) <= 4 and sentence.strip().endswith((".", "!", "?")):
                if ABSTRACT_MIC_DROP_WORDS.intersection(toks):
                    flags.append(
                        Flag(
                            type="abstract_mic_drop",
                            severity="context_flag",
                            start=start,
                            end=end,
                            excerpt=excerpt(text, start, end),
                            suggestion="End with image, decision, or consequence instead of an abstract reveal.",
                            source="heuristic",
                        )
                    )
        return AnalyzerResult(name=self.name, score=float(len(flags)), flags=flags, metrics={"sentence_lengths": lengths})
