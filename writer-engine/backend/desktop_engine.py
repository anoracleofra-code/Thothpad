from __future__ import annotations

import hashlib
import re
import time
from bisect import bisect_left
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from backend import config
from backend.analyzers import run_all_analyzers, run_analyzers, run_live_analyzers
from backend.analyzers.dialogue import _dialogue_spans
from backend.models import AnalyzerResult
from backend.profiles import load_profile
from backend.text_utils import DocumentFeatures, Utf16Index, document_features, words
from backend.validation import validate_text


def _valid_exclusions(values: Any) -> list[tuple[int, int]]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > config.MAX_EXCLUSION_RANGES:
        raise ValueError(
            f"exclusion_ranges must be an array with at most {config.MAX_EXCLUSION_RANGES} entries"
        )
    ranges: list[tuple[int, int]] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("each exclusion range must be an object")
        start = int(value.get("start_utf16", -1))
        end = int(value.get("end_utf16", -1))
        if start < 0 or end <= start:
            raise ValueError("exclusion ranges require non-negative start_utf16 < end_utf16")
        ranges.append((start, end))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _overlaps(start: int, end: int, exclusions: list[tuple[int, int]]) -> bool:
    index = bisect_left(exclusions, (end,))
    return index > 0 and exclusions[index - 1][1] > start


def _serialize(
    results: list[AnalyzerResult],
    text: str,
    *,
    base_offset_utf16: int,
    exclusions: list[tuple[int, int]],
    features: DocumentFeatures | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    analysis: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    index = features.utf16_index if features is not None else Utf16Index(text)
    for result in results:
        row = result.to_dict(text, base_offset_utf16, index)
        row["flags"] = [
            flag
            for flag in row["flags"]
            if not _overlaps(flag["start_utf16"], flag["end_utf16"], exclusions)
        ]
        if result.flags and len(row["flags"]) != len(result.flags):
            row["score"] = result.score * len(row["flags"]) / len(result.flags)
        diagnostics.extend(row["flags"])
        analysis.append(row)
    diagnostics.sort(key=lambda item: (item["start_utf16"], item["end_utf16"], item["rule_id"]))
    return analysis, diagnostics


def _filtered_score(analysis: list[dict[str, Any]], profile: dict[str, Any]) -> float:
    weights = profile.get("analyzer_weights", {}) or {}
    severity_weight = {"hard_fail": 5.0, "strong_flag": 3.0, "context_flag": 1.3, "taste_flag": 0.6}
    total = 0.0
    for result in analysis:
        analyzer_weight = float(weights.get(result["name"], 1.0))
        if result["name"] == "slop_score":
            total += min(float(result["score"]), 30.0) * analyzer_weight
        total += sum(severity_weight.get(flag["severity"], 1.0) * analyzer_weight for flag in result["flags"])
    return round(total, 3)


_TAG_VERBS = (
    "said", "asked", "replied", "shouted", "whispered", "muttered",
    "thought", "knew", "realized", "pondered", "wondered", "mused",
    "added", "cried", "called", "answered", "nodded", "shrugged",
    "paused", "smiled", "frowned", "laughed", "sighed", "snapped",
    "growled", "agreed", "admitted", "insisted", "offered", "noted",
)
_TAG_VERB_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(verb) for verb in _TAG_VERBS) + r")\b",
    re.IGNORECASE,
)


def _dialogue_metrics(text: str, spans: list[tuple[int, int]]) -> dict[str, Any]:
    """Dialogue balance block: coverage ratio, span count, tag-verb usage."""
    total_words = len(words(text))
    dialogue_words = sum(len(words(text[start:end])) for start, end in spans)
    ratio = round(dialogue_words / total_words, 4) if total_words else 0.0
    histogram: Counter[str] = Counter()
    for match in _TAG_VERB_PATTERN.finditer(text):
        histogram[match.group(1).lower()] += 1
    return {
        "span_count": len(spans),
        "dialogue_word_ratio": ratio,
        "tag_verb_histogram": dict(histogram.most_common(12)),
    }


def analyze_text(
    text: str,
    *,
    profile_name: str = config.DEFAULT_PROFILE,
    overrides: dict[str, Any] | None = None,
    preset: str = "full",
    base_offset_utf16: int = 0,
    exclusion_ranges: Any = None,
    confirm_adverbs: bool = False,
    document_revision: int | None = None,
    external_tools: dict[str, Any] | None = None,
    grammar: dict[str, Any] | None = None,
    language: str | None = None,
    analyzers: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if preset not in {"live", "full"}:
        raise ValueError("preset must be 'live' or 'full'")
    live = preset == "live"
    validate_text(text, live=live)
    if base_offset_utf16 < 0:
        raise ValueError("base_offset_utf16 must be non-negative")
    if analyzers is not None:
        if (
            not isinstance(analyzers, (list, tuple))
            or not analyzers
            or len(analyzers) > 32
            or any(not isinstance(name, str) or not name for name in analyzers)
        ):
            raise ValueError("analyzers must contain 1-32 analyzer names")
    profile = load_profile(profile_name, overrides)
    if confirm_adverbs:
        for analyzer_name in ("possible_adverbs", "possible_adjectives", "possible_verbs"):
            profile.setdefault(analyzer_name, {})["confirm_pos"] = True
    if live:
        profile["_live_lexical_only"] = True
    exclusions = _valid_exclusions(exclusion_ranges)
    started = time.perf_counter()
    language_code = (language or "en").replace("_", "-").casefold()
    lexical_rules_enabled = language_code in {"", "und"} or language_code.startswith("en")
    grammar_allowed = bool(
        grammar and (grammar.get("provider") != "harper" or lexical_rules_enabled)
    )
    parallel_harper = bool(
        grammar_allowed and grammar.get("provider") == "harper"  # type: ignore[union-attr]
        and not live and not external_tools and len(text) > 100_000
    )
    grammar_result = None
    selected = tuple(analyzers) if analyzers is not None else None
    with document_features(text, exclusions) as features:
        analyzer_started = time.perf_counter()
        if parallel_harper:
            from backend.grammar import analyze_grammar

            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="writer-analysis") as executor:
                grammar_future = executor.submit(analyze_grammar, text, grammar)  # type: ignore[arg-type]
                results = (
                    run_analyzers(text, profile, selected)
                    if lexical_rules_enabled else run_analyzers(text, profile, ())
                )
                grammar_result = grammar_future.result()
        elif lexical_rules_enabled:
            if selected is not None:
                results = run_analyzers(text, profile, selected)
            else:
                results = (
                    run_live_analyzers(text, profile)
                    if live else run_all_analyzers(text, profile)
                )
        else:
            results = run_analyzers(text, profile, ())
        if external_tools:
            if live:
                raise ValueError("external tools are unavailable in the live preset")
            from backend.analyzers.external_tools import ExternalToolsAnalyzer

            results.append(ExternalToolsAnalyzer().analyze(
                text, {"_approved_external_tools": external_tools}
            ))
            for flag in results[-1].flags:
                flag.analyzer = results[-1].name
        if grammar_result is not None:
            results.append(grammar_result)
        elif grammar_allowed:
            from backend.grammar import analyze_grammar

            results.append(analyze_grammar(text, grammar))  # type: ignore[arg-type]
        analyzer_ms = round((time.perf_counter() - analyzer_started) * 1000, 3)
        serialization_started = time.perf_counter()
        analysis, diagnostics = _serialize(
            results,
            text,
            base_offset_utf16=base_offset_utf16,
            exclusions=exclusions,
            features=features,
        )
        serialization_ms = round(
            (time.perf_counter() - serialization_started) * 1000, 3
        )
        # Dialogue balance block: spans come from the shared per-document
        # cache so live and full lanes pay for the scan exactly once.
        spans = features.cached("dialogue_spans", lambda: _dialogue_spans(text))
        dialogue_metrics = _dialogue_metrics(text, spans)
    for diagnostic in diagnostics:
        diagnostic["revision"] = document_revision
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "mode": "diagnose",
        "profile": profile.get("name", profile_name),
        "preset": preset,
        "score": _filtered_score(analysis, profile),
        "analysis": analysis,
        "diagnostics": diagnostics,
        "dialogue": dialogue_metrics,
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "duration_ms": duration_ms,
        "stage_timings_ms": {
            "analysis": analyzer_ms,
            "serialization": serialization_ms,
        },
        "truncated": False,
        "persisted": False,
        "document_revision": document_revision,
        "language": language_code or "und",
        "lexical_rules_enabled": lexical_rules_enabled,
        "excluded_count": sum(len(result.flags) for result in results) - len(diagnostics),
    }
