"""Score-semantics pins: AnalyzerResult.score is not always a flag count.

Most analyzers report score == len(flags), but stylometry reports a 0-100
uniformity composite and slop_score a weighted per-1000-words rate. The
orchestration post-passes in base.py (thresholds, dialogue exclusion) and the
serialization rescale in desktop_engine.py historically applied flag-count
arithmetic to every result, corrupting those non-count scores. These pins
hold the classification field and the semantics-aware post-pass behavior.
"""

from __future__ import annotations

from backend.analyzers.base import _apply_thresholds, run_analyzers
from backend.analyzers.dialogue import _dialogue_spans
from backend.analyzers.filter_words import FilterWordsAnalyzer
from backend.analyzers.slop_score import SlopScoreAnalyzer
from backend.analyzers.stylometry import StylometryAnalyzer
from backend.models import AnalyzerResult, Flag


def _flag(start: int, end: int, confidence: float = 1.0) -> Flag:
    return Flag(
        type="synthetic",
        severity="context_flag",
        start=start,
        end=end,
        excerpt="x",
        suggestion="",
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Classification pins: analyzer -> score_semantics
# ---------------------------------------------------------------------------


def test_stylometry_score_semantics_is_composite() -> None:
    result = StylometryAnalyzer().analyze("Mara opened the door. " * 7 + "The wind changed\u200b.")
    assert result.score_semantics == "composite"
    serialized = result.to_dict()
    assert serialized["score_semantics"] == "composite"
    assert 0.0 <= serialized["score"] <= 100.0


def test_slop_score_semantics_is_per_1000() -> None:
    result = SlopScoreAnalyzer().analyze("It was not fear but memory. She really knew it.")
    assert result.score_semantics == "per_1000"
    assert result.to_dict()["score_semantics"] == "per_1000"


def test_count_analyzer_score_semantics_is_count() -> None:
    result = FilterWordsAnalyzer().analyze("She really felt it.")
    assert result.score == 2.0
    assert result.score_semantics == "count"
    default = AnalyzerResult(name="synthetic", score=3.0)
    assert default.score_semantics == "count"


# ---------------------------------------------------------------------------
# Dialogue exclusion: flags inside dialogue drop, non-count scores survive
# ---------------------------------------------------------------------------


def test_dialogue_exclusion_keeps_stylometry_score_and_drops_in_dialogue_flags() -> None:
    """Stylometry flags CAN sit inside dialogue: repeated-phrase and
    repeated-sentence-opening flags are span-level, and a dialogue span
    itself can be the site of the repetition. Here the quoted sentence
    repeats in narration, so four flags land inside the dialogue span and
    one (hidden_unicode) stays outside.
    """
    text = (
        '"Mara opened the door," she said. '
        "Mara opened the door again. "
        "Mara opened the door once more. "
        "The wind changed\u200b."
    )
    raw = StylometryAnalyzer().analyze(text)
    spans = _dialogue_spans(text)
    in_dialogue = [flag for flag in raw.flags if spans and flag.start >= spans[0][0] and flag.end <= spans[0][1]]
    outside = [flag for flag in raw.flags if flag not in in_dialogue]
    assert in_dialogue, "fixture must produce stylometry flags inside the dialogue span"
    assert outside, "fixture must keep at least one flag outside dialogue"

    results = run_analyzers(text, {"name": "test", "dialogue_exclusions": {"stylometry": True}})
    stylometry = next(result for result in results if result.name == "stylometry")

    # Composite score is untouched even though flags were removed.
    assert stylometry.score == raw.score
    assert stylometry.score_semantics == "composite"
    # Every in-dialogue flag is gone; the outside flag survives.
    assert not any(spans and flag.start >= spans[0][0] and flag.end <= spans[0][1] for flag in stylometry.flags)
    assert [flag.type for flag in stylometry.flags] == [flag.type for flag in outside]
    # The removal is still recorded for consumers.
    assert stylometry.metrics["ignored_dialogue"] is True
    assert stylometry.metrics["dialogue_findings_removed"] == len(in_dialogue)


def test_dialogue_exclusion_post_pass_direct_on_synthetic_composite() -> None:
    """Direct post-pass pin through run_analyzers with a synthetic result,
    exercising the base.py dialogue-exclusion branch on a composite score.

    Uses a selected-analyzers run replaced by a stub registry so the
    post-pass input is fully controlled: score 57.3 must survive the removal
    of an in-dialogue flag while dialogue_findings_removed records it.
    """
    from backend.analyzers import base

    class StubAnalyzer:
        name = "stylometry"

        def analyze(self, text: str, profile: dict | None = None) -> AnalyzerResult:
            return AnalyzerResult(
                name=self.name,
                score=57.3,
                score_semantics="composite",
                flags=[_flag(2, 8), _flag(40, 46)],
                metrics={},
            )

    text = '"Hidden flag," he said. Outside dialogue stands alone.'
    spans = _dialogue_spans(text)
    assert spans and spans[0] == (0, 14), "the first flag span must sit inside dialogue"

    original_registry = base._analyzers
    base._analyzers = lambda: {"stylometry": StubAnalyzer()}  # type: ignore[assignment]
    try:
        results = run_analyzers(
            text,
            {"name": "test", "dialogue_exclusions": {"stylometry": True}},
            ["stylometry"],
        )
    finally:
        base._analyzers = original_registry  # type: ignore[assignment]

    stylometry = next(result for result in results if result.name == "stylometry")
    assert stylometry.score == 57.3
    assert [flag.start for flag in stylometry.flags] == [40]
    assert stylometry.metrics["ignored_dialogue"] is True
    assert stylometry.metrics["dialogue_findings_removed"] == 1


def test_dialogue_exclusion_subtracts_for_count_scores() -> None:
    """The same post-pass keeps the subtract rule for count scores."""
    from backend.analyzers import base

    class StubAnalyzer:
        name = "filter_words"

        def analyze(self, text: str, profile: dict | None = None) -> AnalyzerResult:
            return AnalyzerResult(
                name=self.name,
                score=5.0,
                score_semantics="count",
                flags=[_flag(2, 8), _flag(40, 46), _flag(50, 56)],
                metrics={},
            )

    text = '"Hidden flag," he said. Outside dialogue stands alone.'
    original_registry = base._analyzers
    base._analyzers = lambda: {"filter_words": StubAnalyzer()}  # type: ignore[assignment]
    try:
        results = run_analyzers(
            text,
            {"name": "test", "dialogue_exclusions": {"filter_words": True}},
            ["filter_words"],
        )
    finally:
        base._analyzers = original_registry  # type: ignore[assignment]

    result = next(row for row in results if row.name == "filter_words")
    assert result.score == 4.0
    assert result.metrics["dialogue_findings_removed"] == 1


# ---------------------------------------------------------------------------
# Thresholds: findings filter, non-count scores stay
# ---------------------------------------------------------------------------


def test_thresholds_keep_non_count_score_and_filter_flags() -> None:
    result = AnalyzerResult(
        name="stylometry",
        score=57.3,
        score_semantics="composite",
        flags=[_flag(0, 1, confidence=0.3), _flag(2, 3, confidence=0.8)],
    )
    _apply_thresholds([result], {"thresholds": {"stylometry": {"minimum_confidence": 0.5}}})
    assert result.score == 57.3
    assert [flag.confidence for flag in result.flags] == [0.8]
    assert result.metrics["threshold_findings_removed"] == 1


def test_thresholds_rescale_count_score_proportionally() -> None:
    result = AnalyzerResult(
        name="filter_words",
        score=8.0,
        score_semantics="count",
        flags=[_flag(0, 1, confidence=0.3), _flag(2, 3, confidence=0.6), _flag(4, 5, confidence=0.9)],
    )
    _apply_thresholds([result], {"thresholds": {"filter_words": {"minimum_confidence": 0.5}}})
    assert result.score == 8.0 * 2 / 3
    assert result.metrics["threshold_findings_removed"] == 1
    assert [flag.confidence for flag in result.flags] == [0.6, 0.9]


# ---------------------------------------------------------------------------
# Serialization: exclusion rescale respects semantics
# ---------------------------------------------------------------------------


def test_serialize_exclusion_rescale_keeps_non_count_score() -> None:
    from backend.desktop_engine import _serialize

    composite = AnalyzerResult(
        name="stylometry",
        score=57.3,
        score_semantics="composite",
        flags=[_flag(2, 8), _flag(40, 46)],
    )
    count = AnalyzerResult(
        name="filter_words",
        score=4.0,
        score_semantics="count",
        flags=[_flag(2, 8), _flag(40, 46)],
    )
    text = '"Hidden flag," he said. Outside dialogue stands alone.'
    analysis, diagnostics = _serialize([composite, count], text, base_offset_utf16=0, exclusions=[(2, 9)])
    rows = {row["name"]: row for row in analysis}
    # One flag excluded per result: the count score halves, the composite does not.
    assert rows["stylometry"]["score"] == 57.3
    assert rows["filter_words"]["score"] == 2.0
    assert [row["score_semantics"] for row in analysis] == ["composite", "count"]
    assert len(diagnostics) == 2
