from __future__ import annotations

import re
from typing import Any

from backend.metrics import repeated_ngrams, sentence_openings, text_statistics
from backend.models import AnalyzerResult, Flag
from backend.text_utils import excerpt, sentences, words


class StylometryAnalyzer:
    name = "stylometry"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        stats = text_statistics(text)
        flags: list[Flag] = []

        if stats["sentence_count"] >= 6 and stats["sentence_length_cv"] < 0.28:
            flags.append(
                Flag(
                    type="uniform_sentence_length",
                    severity="context_flag",
                    start=0,
                    end=min(len(text), 320),
                    excerpt=excerpt(text, 0, min(len(text), 320)),
                    suggestion="The sentence lengths are unusually uniform. Revise only where the cadence feels mechanical.",
                    source="heuristic",
                )
            )
        if stats["sentence_count"] >= 6 and stats["burstiness"] < 0.25:
            flags.append(
                Flag(
                    type="low_burstiness",
                    severity="context_flag",
                    start=0,
                    end=min(len(text), 320),
                    excerpt=excerpt(text, 0, min(len(text), 320)),
                    suggestion="Nearby sentences change length very little. Vary syntax where the passage sounds metronomic.",
                    source="heuristic",
                )
            )
        if stats["paragraph_count"] >= 4 and stats["paragraph_length_cv"] < 0.20:
            flags.append(
                Flag(
                    type="uniform_paragraph_shape",
                    severity="context_flag",
                    start=0,
                    end=min(len(text), 400),
                    excerpt=excerpt(text, 0, min(len(text), 400)),
                    suggestion="Paragraphs have nearly identical lengths. Check whether each break follows thought and action.",
                    source="heuristic",
                )
            )

        repeated = repeated_ngrams(words(text), sizes=(3, 4, 5), minimum=3, limit=12)
        for row in repeated[:8]:
            match = re.search(r"\b" + r"\s+".join(map(re.escape, row["phrase"].split())) + r"\b", text, re.I)
            if match:
                flags.append(
                    Flag(
                        type="repeated_phrase",
                        severity="context_flag",
                        start=match.start(),
                        end=match.end(),
                        excerpt=match.group(0),
                        suggestion=f"This {row['size']}-word phrase appears {row['count']} times. Check every occurrence before varying it.",
                        source="heuristic",
                    )
                )

        openings = sentence_openings(text, 2)
        repeated_openings = {opening: count for opening, count in openings.items() if count >= 3}
        for opening, count in sorted(repeated_openings.items(), key=lambda item: -item[1])[:8]:
            for start, end, sentence in sentences(text):
                if " ".join(words(sentence)[:2]) == opening:
                    flags.append(
                        Flag(
                            type="repeated_sentence_opening",
                            severity="context_flag",
                            start=start,
                            end=min(end, start + len(sentence.split(" ", 2)[0]) + len(opening) + 2),
                            excerpt=excerpt(text, start, min(end, start + 100)),
                            suggestion=f"{count} sentences begin with '{opening}'. Keep deliberate anaphora; vary accidental repetition.",
                            source="heuristic",
                        )
                    )
                    break

        for match in re.finditer(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]", text):
            flags.append(
                Flag(
                    type="hidden_unicode",
                    severity="context_flag",
                    start=match.start(),
                    end=match.end(),
                    excerpt=repr(match.group(0)),
                    suggestion="Remove the hidden Unicode control character.",
                    source="deterministic",
                )
            )

        uniformity_score = 0.0
        if stats["sentence_count"] >= 3:
            uniformity_score += 25 if stats["burstiness"] < 0.2 else 18 if stats["burstiness"] < 0.35 else 10 if stats["burstiness"] < 0.5 else 0
            uniformity_score += 25 if stats["sentence_length_cv"] < 0.2 else 18 if stats["sentence_length_cv"] < 0.35 else 10 if stats["sentence_length_cv"] < 0.5 else 0
            uniformity_score += 15 if stats["trigram_repetition"] > 0.15 else 10 if stats["trigram_repetition"] > 0.1 else 5 if stats["trigram_repetition"] > 0.05 else 0
            if stats["word_count"] > 100:
                uniformity_score += 20 if stats["type_token_ratio"] < 0.35 else 12 if stats["type_token_ratio"] < 0.45 else 5 if stats["type_token_ratio"] < 0.55 else 0
            if stats["paragraph_count"] >= 4 and stats["paragraph_length_cv"] < 0.2:
                uniformity_score += 15

        stats["uniformity_score"] = min(round(uniformity_score, 3), 100.0)
        stats["repeated_ngrams"] = repeated
        stats["repeated_sentence_openings"] = sorted(
            repeated_openings.items(), key=lambda item: -item[1]
        )
        return AnalyzerResult(
            name=self.name,
            score=stats["uniformity_score"],
            flags=flags,
            metrics=stats,
        )
