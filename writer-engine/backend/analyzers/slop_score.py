from __future__ import annotations

import json
import re
from typing import Any

from backend import config
from backend.metrics import repeated_ngrams, text_statistics
from backend.models import AnalyzerResult, Flag
from backend.text_utils import words


def _load_slop_set(filename: str) -> set[str]:
    path = config.slop_score_data_dir() / filename
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {str(item[0]).lower() for item in data if item and isinstance(item, list)}


class SlopScoreAnalyzer:
    name = "slop_score"
    _word_set: set[str] | None = None
    _bigram_set: set[str] | None = None
    _trigram_set: set[str] | None = None

    def __init__(self) -> None:
        if SlopScoreAnalyzer._word_set is None:
            SlopScoreAnalyzer._word_set = _load_slop_set("slop_list.json")
        if SlopScoreAnalyzer._bigram_set is None:
            SlopScoreAnalyzer._bigram_set = _load_slop_set("slop_list_bigrams.json")
        if SlopScoreAnalyzer._trigram_set is None:
            SlopScoreAnalyzer._trigram_set = _load_slop_set("slop_list_trigrams.json")

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        toks = words(text)
        n = len(toks) or 1
        word_hits: dict[str, int] = {}
        bigram_hits: dict[str, int] = {}
        trigram_hits: dict[str, int] = {}

        word_set = self._word_set or set()
        bigram_set = self._bigram_set or set()
        trigram_set = self._trigram_set or set()
        for tok in toks:
            if tok in word_set:
                word_hits[tok] = word_hits.get(tok, 0) + 1
        for i in range(len(toks) - 1):
            bi = " ".join(toks[i : i + 2])
            if bi in bigram_set:
                bigram_hits[bi] = bigram_hits.get(bi, 0) + 1
        for i in range(len(toks) - 2):
            tri = " ".join(toks[i : i + 3])
            if tri in trigram_set:
                trigram_hits[tri] = trigram_hits.get(tri, 0) + 1

        stats = text_statistics(text)
        dialogue_frequency = len(re.findall(r'"[^"]{2,}"', text)) / max(stats["paragraph_count"], 1)
        contrast_matches = len(re.findall(r"\bnot\s+[^.!?]{1,120}\s+but\s+[^.!?]{1,120}", text, re.I))

        word_score = (sum(word_hits.values()) / n) * 1000
        bigram_score = (sum(bigram_hits.values()) / n) * 1000
        trigram_score = (sum(trigram_hits.values()) / n) * 1000
        contrast_rate = contrast_matches / max(len(text) / 1000, 1)
        score = word_score * 0.55 + bigram_score * 0.05 + trigram_score * 0.15 + contrast_rate * 0.25

        flags: list[Flag] = []
        for phrase, count in sorted(word_hits.items(), key=lambda x: x[1], reverse=True)[:25]:
            if count >= 2:
                match = re.search(rf"\b{re.escape(phrase)}\b", text, re.I)
                if match:
                    flags.append(
                        Flag(
                            type="slop_word_hit",
                            severity="context_flag",
                            start=match.start(),
                            end=match.end(),
                            excerpt=match.group(0),
                            suggestion=f"'{phrase}' appears {count} times and is over-represented in the comparison corpus.",
                            source="heuristic",
                        )
                    )
        for phrase, count in sorted(bigram_hits.items(), key=lambda x: x[1], reverse=True)[:25]:
            if count < 2:
                continue
            match = re.search(r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b", text, re.I)
            if match:
                flags.append(
                    Flag(
                        type="slop_bigram_hit",
                        severity="context_flag",
                        start=match.start(),
                        end=match.end(),
                        excerpt=match.group(0),
                        suggestion=f"'{phrase}' appears {count} times and is over-represented in sampled model output.",
                        source="heuristic",
                    )
                )
        for phrase, _count in sorted(trigram_hits.items(), key=lambda x: x[1], reverse=True)[:25]:
            match = re.search(r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b", text, re.I)
            if match:
                flags.append(
                    Flag(
                        type="slop_trigram_hit",
                        severity="context_flag",
                        start=match.start(),
                        end=match.end(),
                        excerpt=match.group(0),
                        suggestion=f"'{phrase}' is a commonly overused trigram. Rewrite the sentence around concrete action.",
                        source="heuristic",
                    )
                )

        return AnalyzerResult(
            name=self.name,
            score=round(score, 3),
            flags=flags,
            metrics={
                "word_count": len(toks),
                "unique_word_ratio": stats["type_token_ratio"],
                "mattr_100": stats["mattr_100"],
                "mattr_500": stats["mattr_500"],
                "mtld": stats["mtld"],
                "hdd_42": stats["hdd_42"],
                "slop_word_hits": sorted(word_hits.items(), key=lambda x: x[1], reverse=True)[:50],
                "slop_bigram_hits": sorted(bigram_hits.items(), key=lambda x: x[1], reverse=True)[:50],
                "slop_trigram_hits": sorted(trigram_hits.items(), key=lambda x: x[1], reverse=True)[:50],
                "slop_word_matches_per_1k_words": round(word_score, 3),
                "slop_bigram_matches_per_1k_words": round(bigram_score, 3),
                "slop_trigram_matches_per_1k_words": round(trigram_score, 3),
                "not_x_but_y_per_1k_chars": round(contrast_rate, 3),
                "avg_sentence_length": stats["avg_sentence_length"],
                "avg_paragraph_length": stats["avg_paragraph_length"],
                "sentence_length_cv": stats["sentence_length_cv"],
                "burstiness": stats["burstiness"],
                "trigram_repetition": stats["trigram_repetition"],
                "flesch_kincaid_grade": stats["flesch_kincaid_grade"],
                "repeated_ngrams": repeated_ngrams(toks, sizes=(2, 3, 4, 5), minimum=2, limit=30),
                "dialogue_frequency": round(dialogue_frequency, 3),
            },
        )
