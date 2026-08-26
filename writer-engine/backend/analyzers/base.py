from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from backend.models import AnalyzerResult
from backend.text_utils import cancellation_checkpoint, document_features

from .dialogue import dialogue_spans, inside_dialogue


class Analyzer(Protocol):
    name: str

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        ...


def _analyzers() -> dict[str, Analyzer]:
    from .binary_contrast import BinaryContrastAnalyzer
    from .body_cliches import BodyClicheAnalyzer
    from .calibration import CalibrationAnalyzer
    from .cinematic_fog import CinematicFogAnalyzer
    from .cliches import ClicheAnalyzer
    from .concrete_anchor import ConcreteAnchorAnalyzer
    from .false_agency import FalseAgencyAnalyzer
    from .filter_words import FilterWordsAnalyzer
    from .metaphor_density import MetaphorDensityAnalyzer
    from .negative_listing import NegativeListingAnalyzer
    from .parts_of_speech import PossibleAdjectiveAnalyzer, PossibleAdverbAnalyzer, PossibleVerbAnalyzer
    from .repetition import RepetitionAnalyzer
    from .rhythm import RhythmAnalyzer
    from .rules_library import RulesLibraryAnalyzer
    from .slop_score import SlopScoreAnalyzer
    from .stylometry import StylometryAnalyzer
    from .triad_cadence import TriadCadenceAnalyzer
    from .vague_abstracts import VagueAbstractAnalyzer

    analyzers: list[Analyzer] = [
        SlopScoreAnalyzer(),
        CalibrationAnalyzer(),
        StylometryAnalyzer(),
        BinaryContrastAnalyzer(),
        NegativeListingAnalyzer(),
        TriadCadenceAnalyzer(),
        MetaphorDensityAnalyzer(),
        BodyClicheAnalyzer(),
        CinematicFogAnalyzer(),
        FalseAgencyAnalyzer(),
        FilterWordsAnalyzer(),
        VagueAbstractAnalyzer(),
        RhythmAnalyzer(),
        ConcreteAnchorAnalyzer(),
        RulesLibraryAnalyzer(),
        ClicheAnalyzer(),
        PossibleAdverbAnalyzer(),
        PossibleAdjectiveAnalyzer(),
        PossibleVerbAnalyzer(),
        RepetitionAnalyzer(),
    ]
    return {analyzer.name: analyzer for analyzer in analyzers}


LIVE_ANALYZERS = (
    "binary_contrast",
    "negative_listing",
    "triad_cadence",
    "body_cliches",
    "cinematic_fog",
    "filter_words",
    "rules_library",
    "cliches",
    "repetition",
    "possible_adverbs",
    "possible_adjectives",
    "possible_verbs",
)

_LEVEL_RANK = {"taste_flag": 0, "context_flag": 1, "strong_flag": 2, "hard_fail": 3}


def _apply_thresholds(results: list[AnalyzerResult], profile: dict[str, Any]) -> None:
    thresholds = profile.get("thresholds", {}) or {}
    if not isinstance(thresholds, dict):
        return
    for result in results:
        original_count = len(result.flags)
        kept = []
        for flag in result.flags:
            rule_id = flag.rule_id or f"{result.name}.{flag.type}"
            setting = next(
                (
                    thresholds[key]
                    for key in (rule_id, f"{result.name}.{flag.type}", result.name, "all")
                    if key in thresholds
                ),
                None,
            )
            enabled = True
            minimum_confidence = 0.0
            minimum_level = "taste_flag"
            if isinstance(setting, (int, float)) and not isinstance(setting, bool):
                minimum_confidence = max(0.0, min(float(setting), 1.0))
            elif isinstance(setting, dict):
                enabled = setting.get("enabled", True) is not False
                value = setting.get("minimum_confidence", 0.0)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    minimum_confidence = max(0.0, min(float(value), 1.0))
                requested_level = setting.get("minimum_level", "taste_flag")
                if requested_level in _LEVEL_RANK:
                    minimum_level = requested_level
            if (
                enabled
                and flag.confidence >= minimum_confidence
                and _LEVEL_RANK.get(flag.severity, 0) >= _LEVEL_RANK[minimum_level]
            ):
                kept.append(flag)
        result.flags = kept
        removed = original_count - len(kept)
        if removed:
            result.metrics["threshold_findings_removed"] = removed
            if original_count:
                result.score *= len(kept) / original_count


def run_analyzers(
    text: str,
    profile: dict[str, Any] | None = None,
    names: Iterable[str] | None = None,
) -> list[AnalyzerResult]:
    from .pattern_helpers import with_profile_patterns

    with document_features(text):
        registry = _analyzers()
        selected = tuple(dict.fromkeys(names)) if names is not None else tuple(registry)
        unknown = sorted(set(selected) - set(registry))
        if unknown:
            raise ValueError(f"unknown analyzers: {', '.join(unknown)}")
        active_profile = profile or {}
        results = []
        for name in selected:
            cancellation_checkpoint()
            results.append(registry[name].analyze(text, active_profile))
        spans: list[tuple[int, int]] | None = None
        dialogue_settings = active_profile.get("dialogue_exclusions", {}) or {}
        for result in results:
            analyzer_settings = active_profile.get(result.name, {}) or {}
            ignore_dialogue = analyzer_settings.get(
                "ignore_dialogue",
                dialogue_settings.get(result.name, dialogue_settings.get("all", False)),
            )
            if not ignore_dialogue:
                continue
            if spans is None:
                spans = dialogue_spans(text)
            original_count = len(result.flags)
            result.flags = [
                flag for flag in result.flags
                if not inside_dialogue(flag.start, flag.end, spans)
            ]
            removed = original_count - len(result.flags)
            if removed:
                result.score = max(0.0, result.score - removed)
            result.metrics["ignored_dialogue"] = True
            result.metrics["dialogue_findings_removed"] = removed
        results.append(with_profile_patterns(AnalyzerResult(name="profile_patterns", score=0.0), text, profile))
        _apply_thresholds(results, active_profile)

        for result in results:
            result.metrics.setdefault("total_findings", len(result.flags))
            result.metrics.setdefault("findings_truncated", False)
            for flag in result.flags:
                flag.analyzer = result.name
        return results


def run_all_analyzers(text: str, profile: dict[str, Any] | None = None) -> list[AnalyzerResult]:
    return run_analyzers(text, profile)


def run_live_analyzers(text: str, profile: dict[str, Any] | None = None) -> list[AnalyzerResult]:
    return run_analyzers(text, profile, LIVE_ANALYZERS)
