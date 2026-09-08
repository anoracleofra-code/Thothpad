"""Golden equivalence: the sidecar live path vs the base.py orchestration.

`sidecar._analyze_live_cancellable` historically re-implemented the analyzer
orchestration that `analyzers.base.run_analyzers` already provides (registry
loop, dialogue-exclusion post-pass, thresholds, serialization) as an inline
byte-compatibility-driven copy. These tests pin the two paths together: for a
corpus of diverse fixture texts and a parameter matrix, the sidecar live path
(invoked directly with a real cancellation context, exactly as dispatch calls
it) and a reference companion composed from `base.run_live_analyzers` /
`base.run_analyzers` plus the envelope-construction steps must produce
byte-identical envelopes (JSON with sort_keys).

Because the P3b refactor makes the two paths share one implementation, two
kinds of golden assertions live here:

1. Equivalence tests (sidecar vs reference companion) - the original premise
   check and regression net for any *unintentional* divergence.
2. Absolute pin tests (sidecar path pinned to concrete expected content) -
   the mutation harness for shared code: post-refactor, a semantic mutation in
   base.py moves both envelopes together, so equivalence alone cannot catch
   it; the pins go red when the shared orchestration's observable behavior
   changes (dialogue-exclusion boundaries, threshold application, exclusion
   ranges, score weighting, profile_patterns appending).

Mutation-harness disclosures (J3): the pre-fix mutation run left one
mutation alive in base.py's threshold pass - the proportional rescale
`score *= kept / original`, which the corpus and the live pins never
distinguish from the subtract formula `score = max(0, score - removed)`
because every pinned removal is a full removal on a unit score, where
both formulas give 0.0. The partial-removal pin
(test_live_pin_threshold_rescale_is_proportional) kills the survivor by
asserting score 8.0 -> 6.0 when one of four flags is removed. Corrections
to the round-1 mutation report: registry-reverse killed 1 test (the
report said 2) and score+0.001 killed 16 tests (the report said 15).

Grammar fixtures require the Harper binary; they are skipped when the bridge
has not been built.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

import pytest

from backend.analyzers.base import (
    LIVE_ANALYZERS,
    _apply_thresholds,
    run_analyzers,
    run_live_analyzers,
)
from backend.analyzers.dialogue import _dialogue_spans, inside_dialogue
from backend.desktop_engine import (
    _dialogue_metrics,
    _filtered_score,
    _serialize,
    _valid_exclusions,
)
from backend.grammar import harper_path
from backend.models import AnalyzerResult, Flag
from backend.profiles import load_profile
from backend.sidecar import _analyze_live_cancellable
from backend.text_utils import AnalysisCancelled, document_features

HARPER_GRAMMAR = {
    "provider": "harper",
    "dialect": "en-US",
    "include_spelling": False,
    "max_findings": 100000,
    "timeout": 10,
}

# Corpus: clean prose, dialogue-heavy, adverb-heavy, cliche-laden,
# unicode/emoji (including astral-plane characters), empty, single-word,
# and long (near the live text bound).
CORPUS: list[tuple[str, str]] = [
    (
        "clean",
        "Mara counted the crates. Two were split. She carried the third to "
        "the porch and set it down beside the ladder.",
    ),
    (
        "dialogue",
        '"She really felt it," he said. "It was not fear but memory," she replied.',
    ),
    (
        "adverb",
        "She moved quickly and spoke softly, smiling warmly at the quietly waiting crowd.",
    ),
    (
        "cliche",
        "It was a dime a dozen. Her breath caught in her throat. At the end "
        "of the day, everything clicked into place. Let that sink in.",
    ),
    (
        "unicode",
        "Mara \U0001f642 noticed the \U0001d538\U0001d560\U0001d552\U0001d551\U0001d552 door. "
        "\u201cShe felt it,\u201d said the caf\u00e9 owner, smiling warmly.",
    ),
    ("empty", ""),
    ("single", "Run."),
    (
        "long",
        ('She moved quickly through the crowded market. "I really felt it," he admitted. ' * 55)[:7900],
    ),
]

_BASE_CASE: dict[str, Any] = {
    "profile_name": "creative-default",
    "overrides": None,
    "base_offset_utf16": 0,
    "exclusion_ranges": None,
    "confirm_adverbs": False,
    "document_revision": None,
    "grammar": None,
    "language": None,
    "analyzers": None,
}

# Parameter matrix layered on the corpus: selected-subset analyzers,
# thresholds/dialogue-exclusion/weights overrides, confirm_pos, non-English
# (lexical rules disabled), unknown-language fallback, and exclusions with a
# non-zero base offset plus document revision.
CASES: list[tuple[str, str, dict[str, Any]]] = [
    *[(name, "base", {}) for name, _ in CORPUS],
    (
        "dialogue",
        "selected-analyzers",
        {"analyzers": ["binary_contrast", "filter_words", "possible_adverbs"]},
    ),
    (
        "adverb",
        "thresholds-dialogue-exclusions-weights",
        {
            "overrides": {
                "thresholds": {"possible_adverbs": {"enabled": False}},
                "dialogue_exclusions": {"all": True},
                "analyzer_weights": {"cliches": 2.0},
            }
        },
    ),
    ("adverb", "confirm-adverbs", {"confirm_adverbs": True}),
    (
        "cliche",
        "non-english-keeps-profile-patterns",
        {"language": "es", "overrides": {"hard_bans": ["Sin embargo"]}},
    ),
    ("unicode", "unknown-language", {"language": "und"}),
    (
        "unicode",
        "exclusions-base-offset-revision",
        {
            "base_offset_utf16": 40,
            "exclusion_ranges": [{"start_utf16": 5, "end_utf16": 9}],
            "document_revision": 7,
        },
    ),
]


def _reference_envelope(
    text: str,
    *,
    profile_name: str,
    overrides: dict[str, Any] | None,
    base_offset_utf16: int,
    exclusion_ranges: Any,
    confirm_adverbs: bool,
    document_revision: int | None,
    grammar: dict[str, Any] | None,
    language: str | None,
    analyzers: Any,
) -> dict[str, Any]:
    """The base.py-composed companion of the sidecar live envelope.

    Mirrors the sidecar's envelope-construction steps but sources the
    analysis from the shared orchestration: run_live_analyzers when the
    request uses the live preset without an analyzer selection,
    run_analyzers with the explicit selection otherwise, and the empty
    registry when lexical rules are disabled for the language.
    """
    profile = load_profile(profile_name, overrides)
    if confirm_adverbs:
        for analyzer_name in ("possible_adverbs", "possible_adjectives", "possible_verbs"):
            profile.setdefault(analyzer_name, {})["confirm_pos"] = True
    profile["_live_lexical_only"] = True
    exclusions = _valid_exclusions(exclusion_ranges)
    language_code = (language or "en").replace("_", "-").casefold()
    lexical_rules_enabled = language_code in {"", "und"} or language_code.startswith("en")
    with document_features(text, exclusions) as features:
        if not lexical_rules_enabled:
            results = run_analyzers(text, profile, ())
        elif analyzers is not None:
            results = run_analyzers(text, profile, tuple(dict.fromkeys(analyzers)))
        else:
            results = run_live_analyzers(text, profile)
        grammar_allowed = bool(grammar and (grammar.get("provider") != "harper" or lexical_rules_enabled))
        if grammar_allowed:
            assert grammar is not None
            from backend.grammar import analyze_grammar

            results.append(analyze_grammar(text, grammar))
        analysis, diagnostics = _serialize(
            results,
            text,
            base_offset_utf16=base_offset_utf16,
            exclusions=exclusions,
            features=features,
        )
        spans_cached = features.cached("dialogue_spans", lambda: _dialogue_spans(text))
        dialogue_metrics = _dialogue_metrics(text, spans_cached)
    for diagnostic in diagnostics:
        diagnostic["revision"] = document_revision
    return {
        "mode": "diagnose",
        "profile": profile.get("name", profile_name),
        "preset": "live",
        "score": _filtered_score(analysis, profile),
        "analysis": analysis,
        "dialogue": dialogue_metrics,
        "diagnostics": diagnostics,
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "truncated": False,
        "persisted": False,
        "document_revision": document_revision,
        "language": language_code or "und",
        "lexical_rules_enabled": lexical_rules_enabled,
        "excluded_count": sum(len(result.flags) for result in results) - len(diagnostics),
    }


def _sidecar_envelope(text: str, case: dict[str, Any]) -> dict[str, Any]:
    """Direct call with a real cancellation context, as dispatch invokes it."""
    return _analyze_live_cancellable(
        text,
        cancelled=threading.Event(),
        profile_name=case["profile_name"],
        overrides=case["overrides"],
        base_offset_utf16=case["base_offset_utf16"],
        exclusion_ranges=case["exclusion_ranges"],
        confirm_adverbs=case["confirm_adverbs"],
        document_revision=case["document_revision"],
        grammar=case["grammar"],
        language=case["language"],
        analyzers=case["analyzers"],
    )


def _canonical(envelope: dict[str, Any]) -> str:
    """Byte-identical comparison: drop wall-clock fields, sort_keys JSON."""
    core = dict(envelope)
    core.pop("duration_ms", None)
    core.pop("stage_timings_ms", None)
    return json.dumps(core, sort_keys=True, ensure_ascii=False)


def _assert_golden(text: str, case: dict[str, Any]) -> None:
    sidecar = _sidecar_envelope(text, case)
    reference = _reference_envelope(text, **case)
    assert _canonical(sidecar) == _canonical(reference), (
        f"live-path divergence for text={text[:40]!r} case={case}: sidecar and base.run_live_analyzers envelopes differ"
    )


# ---------------------------------------------------------------------------
# Equivalence tests: sidecar live path vs base.py reference companion
# ---------------------------------------------------------------------------


def test_live_corpus_golden_equivalence() -> None:
    for _, text in CORPUS:
        _assert_golden(text, dict(_BASE_CASE))


def test_live_parameter_matrix_golden_equivalence() -> None:
    for text_name, _case_name, overrides in CASES:
        text = dict(CORPUS)[text_name]
        _assert_golden(text, dict(_BASE_CASE, **overrides))


@pytest.mark.parametrize("text_name", [name for name, _ in CORPUS])
def test_live_corpus_golden_equivalence_with_harper(text_name: str) -> None:
    if not harper_path().is_file():
        pytest.skip("Harper grammar bridge has not been built")
    text = dict(CORPUS)[text_name]
    _assert_golden(text, dict(_BASE_CASE, grammar=dict(HARPER_GRAMMAR)))


def test_live_golden_equivalence_harper_non_english() -> None:
    if not harper_path().is_file():
        pytest.skip("Harper grammar bridge has not been built")
    text = "Sin embargo, Mara cont\u00f3 las cajas. Let that sink in."
    _assert_golden(text, dict(_BASE_CASE, grammar=dict(HARPER_GRAMMAR), language="es"))


# ---------------------------------------------------------------------------
# Absolute pins on the sidecar path: the shared-code mutation harness
# ---------------------------------------------------------------------------


def _sidecar_flag_tuples(envelope: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(item["analyzer"], item["rule_id"], item["excerpt"]) for item in envelope["diagnostics"]]


def test_live_pin_dialogue_exclusion_boundary() -> None:
    """The dialogue-exclusion post-pass drops flags fully inside dialogue.

    Pinned expectations (verified against the pre-refactor sidecar output):
    the binary_contrast flag spanning 'not fear, but rather memory itself'
    lies entirely inside the quoted span and disappears; its score drops to
    0 via the max(0, score - removed) rule, dialogue_findings_removed is
    recorded, and the envelope score falls from 3.36 to 1.8. Words in
    narration (really, felt) keep their flags.
    """
    text = '"It was not fear, but rather memory itself.", she replied. Mara really felt the cold.'
    envelope = _sidecar_envelope(text, dict(_BASE_CASE))
    assert [
        (item["analyzer"], item["excerpt"], item["start_utf16"], item["end_utf16"]) for item in envelope["diagnostics"]
    ] == [
        ("binary_contrast", "not fear, but rather memory itself", 8, 42),
        ("filter_words", "really", 64, 70),
        ("possible_adverbs", "really", 64, 70),
        ("filter_words", "felt", 71, 75),
    ]
    excluded = _sidecar_envelope(text, dict(_BASE_CASE, overrides={"dialogue_exclusions": {"all": True}}))
    assert [(item["analyzer"], item["excerpt"]) for item in excluded["diagnostics"]] == [
        ("filter_words", "really"),
        ("possible_adverbs", "really"),
        ("filter_words", "felt"),
    ]
    rows = {row["name"]: row for row in excluded["analysis"]}
    assert rows["binary_contrast"]["metrics"]["dialogue_findings_removed"] == 1
    assert rows["binary_contrast"]["metrics"]["ignored_dialogue"] is True
    assert rows["binary_contrast"]["score"] == 0.0
    assert envelope["score"] == 3.36
    assert excluded["score"] == 1.8


def test_live_pin_thresholds_application() -> None:
    """The thresholds pass disables an analyzer and rescales its score.

    Pinned expectations: disabling possible_adverbs removes exactly its one
    flag, records threshold_findings_removed=1, zeroes its score, and drops
    the envelope score from 1.8 to 1.2.
    """
    text = 'Mara really felt the cold. "She really knew it all," he said.'
    envelope = _sidecar_envelope(
        text, dict(_BASE_CASE, overrides={"thresholds": {"possible_adverbs": {"enabled": False}}})
    )
    rows = {row["name"]: row for row in envelope["analysis"]}
    assert rows["possible_adverbs"]["metrics"]["threshold_findings_removed"] == 1
    assert rows["possible_adverbs"]["score"] == 0.0
    assert _sidecar_flag_tuples(envelope) == [
        ("filter_words", "filter_words.filter_hedges", "really"),
        ("filter_words", "filter_words.filter_perception", "felt"),
    ]
    assert envelope["score"] == 1.2
    baseline = _sidecar_envelope(text, dict(_BASE_CASE))
    assert baseline["score"] == 1.8


def _threshold_flag(confidence: float) -> Flag:
    return Flag(
        type="taste_flag",
        severity="taste_flag",
        start=0,
        end=1,
        excerpt="x",
        suggestion="",
        confidence=confidence,
    )


def test_live_pin_threshold_rescale_is_proportional() -> None:
    """Partial threshold removal rescales the score proportionally, not subtractively.

    Direct _apply_thresholds pin (the envelope pins only ever exercise full
    removal on a unit score, where proportional and subtract coincide):
    a result with score 8.0 and four flags at confidences 0.3/0.6/0.9/1.2
    under minimum_confidence 0.5 keeps exactly three flags, so the
    proportional rule gives score == 6.0 (8.0 * 3/4) with
    threshold_findings_removed == 1; the subtract formula would give 7.0.
    """
    result = AnalyzerResult(
        name="stylometry",
        score=8.0,
        flags=[_threshold_flag(0.3), _threshold_flag(0.6), _threshold_flag(0.9), _threshold_flag(1.2)],
    )
    _apply_thresholds([result], {"thresholds": {"stylometry": {"minimum_confidence": 0.5}}})
    assert result.metrics["threshold_findings_removed"] == 1
    assert [flag.confidence for flag in result.flags] == [0.6, 0.9, 1.2]
    assert result.score == 6.0


def test_live_pin_inside_dialogue_boundary() -> None:
    """inside_dialogue excludes a flag fully contained in a span, keeps anything past its end.

    For the span (10, 20), a flag ending exactly at the span end is inside
    (end == span end is contained); a flag ending one past it, or starting
    at the span end, extends past the span and is kept.
    """
    spans = [(10, 20)]
    assert inside_dialogue(10, 20, spans) is True
    assert inside_dialogue(10, 21, spans) is False
    assert inside_dialogue(19, 20, spans) is True
    assert inside_dialogue(20, 25, spans) is False
    multi = [(10, 20), (30, 40)]
    assert inside_dialogue(32, 38, multi) is True
    assert inside_dialogue(22, 28, multi) is False
    assert inside_dialogue(18, 32, multi) is False


def test_live_pin_profile_patterns_always_appended() -> None:
    """profile_patterns is the final analysis row in every live envelope."""
    for _, text in CORPUS:
        envelope = _sidecar_envelope(text, dict(_BASE_CASE))
        assert envelope["analysis"][-1]["name"] == "profile_patterns"


def test_live_pin_registry_loop_preset_order() -> None:
    """The live preset runs the documented analyzers in the documented order."""
    text = CORPUS[0][1]
    envelope = _sidecar_envelope(text, dict(_BASE_CASE))
    assert [row["name"] for row in envelope["analysis"][:-1]] == list(LIVE_ANALYZERS)


def test_live_pin_serialization_offsets_and_exclusions() -> None:
    """UTF-16 offsets, base offset shifting, and exclusion-range filtering.

    Pinned expectations: the emoji is 2 UTF-16 units, so 'quickly' sits at
    42..49 with no base offset; with base_offset_utf16=40 it moves to 82..89
    while flag start/end stay codepoint-based; an exclusion range covering
    'noticed' drops exactly that flag and halves the score.
    """
    text = "Mara \U0001f642 noticed the green door. She moved quickly."
    envelope = _sidecar_envelope(text, dict(_BASE_CASE))
    assert [
        (item["analyzer"], item["excerpt"], item["start_utf16"], item["end_utf16"]) for item in envelope["diagnostics"]
    ] == [
        ("filter_words", "noticed", 8, 15),
        ("possible_adverbs", "quickly", 42, 49),
    ]
    shifted = _sidecar_envelope(text, dict(_BASE_CASE, base_offset_utf16=40))
    assert [(item["analyzer"], item["start_utf16"], item["end_utf16"]) for item in shifted["diagnostics"]] == [
        ("filter_words", 48, 55),
        ("possible_adverbs", 82, 89),
    ]
    excluded = _sidecar_envelope(
        text,
        dict(_BASE_CASE, base_offset_utf16=40, exclusion_ranges=[{"start_utf16": 41, "end_utf16": 62}]),
    )
    assert [
        (item["analyzer"], item["excerpt"], item["start_utf16"], item["end_utf16"]) for item in excluded["diagnostics"]
    ] == [("possible_adverbs", "quickly", 82, 89)]
    assert excluded["excluded_count"] == 1
    assert excluded["score"] == 0.6


def test_live_pin_non_english_runs_profile_patterns_only() -> None:
    """Lexical rules disabled for non-English: only profile_patterns runs."""
    text = "Sin embargo, Mara cont\u00f3 las cajas. Let that sink in."
    envelope = _sidecar_envelope(text, dict(_BASE_CASE, language="es", overrides={"hard_bans": ["Sin embargo"]}))
    assert [row["name"] for row in envelope["analysis"]] == ["profile_patterns"]
    assert [item["excerpt"] for item in envelope["diagnostics"]] == ["Sin embargo"]
    assert envelope["lexical_rules_enabled"] is False
    assert envelope["language"] == "es"


def test_live_pin_selected_analyzers_subset_shape() -> None:
    """An explicit selection deduplicates, preserves request order, and the
    preset is reported as live."""
    text = CORPUS[2][1]
    envelope = _sidecar_envelope(
        text,
        dict(_BASE_CASE, analyzers=["possible_adverbs", "binary_contrast", "possible_adverbs"]),
    )
    assert [row["name"] for row in envelope["analysis"][:-1]] == [
        "possible_adverbs",
        "binary_contrast",
    ]
    assert envelope["analysis"][-1]["name"] == "profile_patterns"


def test_live_empty_text_envelope_shape() -> None:
    envelope = _sidecar_envelope("", dict(_BASE_CASE))
    assert set(envelope) >= {
        "mode",
        "profile",
        "preset",
        "score",
        "analysis",
        "dialogue",
        "diagnostics",
        "text_hash",
        "duration_ms",
        "stage_timings_ms",
        "truncated",
        "persisted",
        "document_revision",
        "language",
        "lexical_rules_enabled",
        "excluded_count",
    }
    assert envelope["preset"] == "live"
    assert envelope["score"] == 0.0
    assert envelope["dialogue"]["span_count"] == 0
    assert envelope["text_hash"] == hashlib.sha256(b"").hexdigest()


def test_live_unknown_analyzer_names_raise_identically() -> None:
    """Both paths reject unknown analyzer names with the same error."""
    text = "Mara noticed it."
    case = dict(_BASE_CASE, analyzers=["not_a_real_analyzer"])
    with pytest.raises(ValueError, match="unknown analyzers: not_a_real_analyzer"):
        _sidecar_envelope(text, case)
    with pytest.raises(ValueError, match="unknown analyzers: not_a_real_analyzer"):
        _reference_envelope(text, **case)


def test_live_cancellation_still_wired_through_the_shared_path(monkeypatch) -> None:
    """The cancellation checkpoint must fire inside the shared orchestration.

    Mutating base._analyzers with a checkpoint-polling analyzer proves the
    sidecar live path still routes the registry loop through base.py and the
    cancellation ContextVar reaches it: a set event aborts cooperatively.
    """
    from backend.analyzers import base

    started = threading.Event()
    stopped = threading.Event()

    class SlowAnalyzer:
        name = "binary_contrast"

        def analyze(self, _text: str, _profile: dict[str, Any] | None = None) -> AnalyzerResult:
            started.set()
            try:
                while True:
                    base.cancellation_checkpoint()
                    time.sleep(0.005)
            finally:
                stopped.set()

    monkeypatch.setattr(base, "_analyzers", lambda: {"binary_contrast": SlowAnalyzer()})
    cancelled = threading.Event()
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            _analyze_live_cancellable(
                CORPUS[0][1],
                cancelled=cancelled,
                profile_name="creative-default",
                overrides=None,
                base_offset_utf16=0,
                exclusion_ranges=None,
                confirm_adverbs=False,
                document_revision=None,
                grammar=None,
                language=None,
                analyzers=["binary_contrast"],
            )
        except Exception as exc:  # noqa: BLE001 - record the failure mode
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(1)
    cancelled.set()
    thread.join(timeout=5)
    assert stopped.is_set()
    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), AnalysisCancelled)
