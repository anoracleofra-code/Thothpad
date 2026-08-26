from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from backend.text_utils import active_document_features, paragraphs, sentences, words

logger = logging.getLogger(__name__)

STOPWORDS = {
    "a", "about", "after", "all", "also", "am", "an", "and", "any", "are", "as", "at",
    "be", "because", "been", "before", "being", "between", "both", "but", "by", "can",
    "could", "did", "do", "does", "doing", "down", "each", "for", "from", "further",
    "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself",
    "just", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of",
    "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out",
    "over", "own", "same", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "theirs", "them", "themselves", "then", "there", "these", "they",
    "this", "those", "through", "to", "too", "under", "until", "up", "very", "was",
    "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why",
    "will", "with", "would", "you", "your", "yours", "yourself", "yourselves",
}

FUNCTION_WORDS = {
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "this", "that", "these", "those", "there", "here", "who", "whom", "whose", "which",
    "what", "when", "where", "why", "how",
}

IRREGULAR_LEMMAS = {
    "am": "be", "are": "be", "been": "be", "being": "be", "is": "be", "was": "be",
    "were": "be", "did": "do", "does": "do", "done": "do", "had": "have", "has": "have",
    "went": "go", "gone": "go", "made": "make", "said": "say", "saw": "see",
    "seen": "see", "took": "take", "taken": "take", "thought": "think", "felt": "feel",
    "knew": "know", "known": "know", "came": "come", "ran": "run", "brought": "bring",
    "bought": "buy", "caught": "catch", "held": "hold", "left": "leave", "found": "find",
    "told": "tell", "wrote": "write", "written": "write", "making": "make",
    "taking": "take", "writing": "write", "using": "use",
}


def estimate_syllables(word: str) -> int:
    value = re.sub(r"[^a-z]", "", word.lower())
    if len(value) <= 3:
        return 1
    groups = re.findall(r"[aeiouy]+", value)
    count = max(len(groups), 1)
    if value.endswith("e") and not value.endswith("le"):
        count -= 1
    if value.endswith("ed") and len(value) > 3 and not re.search(r"[aeiouy]ed$", value):
        count -= 1
    return max(count, 1)


def simple_lemma(word: str) -> str:
    value = word.lower().strip("'")
    if value in IRREGULAR_LEMMAS:
        return IRREGULAR_LEMMAS[value]
    if len(value) > 5 and value.endswith("ies"):
        return value[:-3] + "y"
    if len(value) > 5 and value.endswith("ing"):
        stem = value[:-3]
        if len(stem) > 2 and stem[-1] == stem[-2]:
            stem = stem[:-1]
        return stem + "e" if stem.endswith(("at", "iz")) else stem
    if len(value) > 4 and value.endswith("ied"):
        return value[:-3] + "y"
    if len(value) > 4 and value.endswith("ed"):
        stem = value[:-2]
        if len(stem) > 2 and stem[-1] == stem[-2]:
            stem = stem[:-1]
        return stem
    if len(value) > 4 and value.endswith("es") and not value.endswith(("ses", "xes")):
        return value[:-2]
    if len(value) > 3 and value.endswith("s") and not value.endswith(("ss", "us", "is")):
        return value[:-1]
    return value


def lemmatize_tokens(tokens: Iterable[str], use_spacy: bool = False) -> tuple[list[str], str]:
    values = list(tokens)
    if use_spacy:
        try:
            from backend.analyzers.parts_of_speech import _spacy_model

            nlp, model_name = _spacy_model()
            if nlp is None:
                raise ValueError("spaCy model is unavailable")
            chunks = [values[index:index + 10_000] for index in range(0, len(values), 10_000)]
            lemmas: list[str] = []
            for document in nlp.pipe((" ".join(chunk) for chunk in chunks), batch_size=4):
                lemmas.extend(
                    (token.lemma_ or token.text).lower()
                    for token in document
                    if not token.is_space
                )
            if len(lemmas) == len(values):
                return lemmas, model_name
        except (ImportError, ValueError, OSError):
            logger.warning(
                "spaCy lemmatization unavailable; falling back to builtin lemmatizer",
                exc_info=True,
            )
    return [simple_lemma(token) for token in values], "builtin"


def coefficient_of_variation(values: Iterable[int]) -> float:
    nums = list(values)
    if len(nums) < 2:
        return 0.0
    mean = statistics.fmean(nums)
    return statistics.pstdev(nums) / mean if mean else 0.0


def burstiness(values: Iterable[int]) -> float:
    nums = list(values)
    if len(nums) < 2:
        return 0.0
    mean = statistics.fmean(nums)
    diffs = [abs(nums[idx] - nums[idx - 1]) for idx in range(1, len(nums))]
    return statistics.fmean(diffs) / mean if mean else 0.0


def ngram_counts(tokens: Sequence[str], size: int) -> Counter[str]:
    if len(tokens) < size:
        return Counter()
    return Counter(" ".join(tokens[idx : idx + size]) for idx in range(len(tokens) - size + 1))


def ngram_repetition(tokens: Sequence[str], size: int = 3) -> float:
    counts = ngram_counts(tokens, size)
    if not counts:
        return 0.0
    return sum(1 for count in counts.values() if count > 1) / len(counts)


def mattr(tokens: Sequence[str], window: int = 100) -> float:
    if not tokens:
        return 0.0
    size = min(max(1, window), len(tokens))
    counts = Counter(tokens[:size])
    total = len(counts) / size
    windows = 1
    for index in range(size, len(tokens)):
        outgoing = tokens[index - size]
        counts[outgoing] -= 1
        if counts[outgoing] == 0:
            del counts[outgoing]
        counts[tokens[index]] += 1
        total += len(counts) / size
        windows += 1
    return total / windows


def _mtld_direction(tokens: Sequence[str], threshold: float) -> float:
    terms: set[str] = set()
    count = 0
    factors = 0.0
    last_ttr = 1.0
    for token in tokens:
        count += 1
        terms.add(token)
        last_ttr = len(terms) / count
        if last_ttr <= threshold:
            factors += 1
            count = 0
            terms.clear()
            last_ttr = 1.0
    if count:
        factors += (1 - last_ttr) / (1 - threshold)
    if factors == 0:
        factors = 1.0
    return len(tokens) / factors


def mtld(tokens: Sequence[str], threshold: float = 0.72) -> float:
    if not tokens:
        return 0.0
    return statistics.fmean(
        (_mtld_direction(tokens, threshold), _mtld_direction(list(reversed(tokens)), threshold))
    )


def _probability_no_successes(total: int, frequency: int, draws: int) -> float:
    if total - frequency < draws:
        return 0.0
    probability = 1.0
    for idx in range(draws):
        probability *= (total - frequency - idx) / (total - idx)
    return probability


def hdd(tokens: Sequence[str], draws: int = 42) -> float:
    if not tokens:
        return 0.0
    sample = min(max(1, draws), len(tokens))
    counts = Counter(tokens)
    return sum(
        (1 - _probability_no_successes(len(tokens), frequency, sample)) / sample
        for frequency in counts.values()
    )


def repeated_ngrams(
    tokens: Sequence[str],
    *,
    sizes: Iterable[int] = (2, 3, 4, 5),
    minimum: int = 2,
    limit: int = 30,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for size in sizes:
        for phrase, count in ngram_counts(tokens, size).items():
            phrase_tokens = phrase.split()
            if count < minimum or all(token in STOPWORDS for token in phrase_tokens):
                continue
            rows.append({"phrase": phrase, "count": count, "size": size})
    rows.sort(key=lambda item: (-item["count"], -item["size"], item["phrase"]))
    return rows[:limit]


def sentence_openings(text: str, size: int = 2) -> Counter[str]:
    openings: Counter[str] = Counter()
    for _, _, sentence in sentences(text):
        tokens = words(sentence)
        if len(tokens) >= size:
            openings[" ".join(tokens[:size])] += 1
    return openings


def paragraph_endings(text: str, size: int = 3) -> Counter[str]:
    endings: Counter[str] = Counter()
    for _, _, paragraph in paragraphs(text):
        tokens = words(paragraph)
        if len(tokens) >= size:
            endings[" ".join(tokens[-size:])] += 1
    return endings


def _text_statistics(text: str) -> dict[str, Any]:
    tokens = words(text)
    sentence_lengths = [len(words(sentence)) for _, _, sentence in sentences(text) if words(sentence)]
    paragraph_lengths = [len(words(paragraph)) for _, _, paragraph in paragraphs(text) if words(paragraph)]
    word_count = len(tokens)
    sentence_count = len(sentence_lengths)
    syllables = sum(estimate_syllables(token) for token in tokens)
    fk_grade = (
        0.39 * (word_count / sentence_count)
        + 11.8 * (syllables / word_count)
        - 15.59
        if word_count and sentence_count
        else 0.0
    )
    paragraph_cv = coefficient_of_variation(paragraph_lengths)
    return {
        "word_count": word_count,
        "unique_word_count": len(set(tokens)),
        "type_token_ratio": round(len(set(tokens)) / word_count, 4) if word_count else 0.0,
        "mattr_100": round(mattr(tokens, 100), 4),
        "mattr_500": round(mattr(tokens, 500), 4),
        "mtld": round(mtld(tokens), 3),
        "hdd_42": round(hdd(tokens), 4),
        "sentence_count": sentence_count,
        "paragraph_count": len(paragraph_lengths),
        "avg_word_length": round(statistics.fmean(map(len, tokens)), 3) if tokens else 0.0,
        "avg_sentence_length": round(statistics.fmean(sentence_lengths), 3) if sentence_lengths else 0.0,
        "sentence_length_stdev": round(statistics.pstdev(sentence_lengths), 3) if len(sentence_lengths) > 1 else 0.0,
        "sentence_length_cv": round(coefficient_of_variation(sentence_lengths), 4),
        "burstiness": round(burstiness(sentence_lengths), 4),
        "avg_paragraph_length": round(statistics.fmean(paragraph_lengths), 3) if paragraph_lengths else 0.0,
        "paragraph_length_cv": round(paragraph_cv, 4),
        "trigram_repetition": round(ngram_repetition(tokens, 3), 4),
        "function_word_ratio": round(
            sum(1 for token in tokens if token in FUNCTION_WORDS) / word_count, 4
        ) if word_count else 0.0,
        "flesch_kincaid_grade": round(fk_grade, 3),
        "sentence_lengths": sentence_lengths,
        "paragraph_lengths": paragraph_lengths,
    }


def text_statistics(text: str) -> dict[str, Any]:
    features = active_document_features(text)
    cached = (
        features.cached("text_statistics", lambda: _text_statistics(text))
        if features else _text_statistics(text)
    )
    return {
        **cached,
        "sentence_lengths": list(cached["sentence_lengths"]),
        "paragraph_lengths": list(cached["paragraph_lengths"]),
    }
