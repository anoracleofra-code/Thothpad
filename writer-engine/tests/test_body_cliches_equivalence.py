"""Equivalence harness: required-literal prefilter vs the original regex_flags loop.

Guarantees byte-identical output on adversarial fixtures: overlapping patterns
(where greedy per-pattern claiming is order-dependent), case variants,
whitespace/newline variants inside patterns, and 4-tuple callers.
"""

import re

from backend.analyzers.body_cliches import PATTERNS, BodyClicheAnalyzer
from backend.analyzers.pattern_helpers import regex_flags
from backend.models import AnalyzerResult, Flag
from backend.text_utils import excerpt


def _reference_regex_flags(
    *,
    name: str,
    text: str,
    patterns: list[tuple],
    flags: int = re.I | re.S,
) -> AnalyzerResult:
    """Verbatim copy of the pre-optimization implementation (4-tuple view)."""
    found: list[Flag] = []
    occupied = bytearray(len(text))
    total = 0
    for flag_type, pattern, severity, suggestion in [(p[0], p[1], p[2], p[3]) for p in patterns]:
        for match in re.finditer(pattern, text, flags):
            start, end = match.span()
            if occupied.find(1, start, end) != -1:
                continue
            occupied[start:end] = b"\x01" * (end - start)
            total += 1
            found.append(
                Flag(
                    type=flag_type,
                    severity=severity,
                    start=start,
                    end=end,
                    excerpt=excerpt(text, start, end),
                    suggestion=suggestion,
                )
            )
    return AnalyzerResult(
        name=name,
        score=float(total),
        flags=found,
        metrics={"total_findings": total, "findings_truncated": False},
    )


def _assert_identical(text: str, patterns: list[tuple]) -> None:
    old = _reference_regex_flags(name="t", text=text, patterns=patterns)
    new = regex_flags(name="t", text=text, patterns=patterns)
    assert [(f.type, f.start, f.end, f.excerpt, f.severity, f.suggestion) for f in new.flags] == [
        (f.type, f.start, f.end, f.excerpt, f.severity, f.suggestion) for f in old.flags
    ], f"flag mismatch on {text!r}"
    assert new.score == old.score
    assert new.metrics == old.metrics


# Overlap fixture: greedy claiming means pattern ORDER decides winners when
# spans overlap. p1 claims [0,3); p2's [1,4) must be dropped. Reversing the
# order flips the winner — both implementations must agree either way.
_OVERLAP_A = [
    ("p1", r"abc", "context_flag", "s", "abc"),
    ("p2", r"bcd", "context_flag", "s", "bcd"),
]
_OVERLAP_B = list(reversed(_OVERLAP_A))

# Case variants (lower/UPPER/Mixed), whitespace variants where \s+ spans
# newlines and multiple spaces, literal-present-but-no-match near misses.
_FIXTURES = [
    "",
    "plain text with nothing here",
    "HER BREATH CAUGHT in her throat.",
    "her breath caught",
    "Breath  caught\nin his throat",
    "His JAW clenched; her jaw tightened, my jaw worked.",
    "heart POUNDED! He raced... wait",
    "Eyes\twidened and eyes flashed",
    "fists balled, fist clenched, FISTS CLENCHED",
    "A chill traveled along her spine. Shiver ran down spine.",
    "blood ran cold; BLOOD RAN COLD",
    "took a deep breath, drew one long breath, let out a slow breath",
    "leaned back in her chair / LEANED BACK IN HIS CHAIR",
    "shook his head slowly ... shook her head slowly",
    "crossed his arms, CROSSED THEIR ARMS",
    "leaned against the wall",
    "took a step back, took a step forward, TOOK A STEP TOWARD",
    "looked at each other, stared at one another, LOOKED AT ONE ANOTHER",
    "head to one side, head on one side, HEAD\nTO ONE SIDE",
    "went down on one knee, dropped on one knee, DROPPED DOWN ON ONE KNEE",
    "said in a low voice; SAID IN A LOW VOICE",
    "after a moment, after a long moment, AFTER A FEW MOMENTS",
    "turned and walked away. TURNED AND WALKED AWAY",
    # Every literal present, no pattern matches (prefilter passes, zero flags):
    "breath jaw heart eyes fist spine blood chair shook crossed wall step side knee voice moment walked",
    # Near misses that must stay non-flags:
    "she took one step backwards",
    "the breath was caught by wind",
    "she crossed the street and looked at the wall",
]


def test_overlap_order_equivalence() -> None:
    _assert_identical("abcd", _OVERLAP_A)
    _assert_identical("abcd", _OVERLAP_B)
    _assert_identical("xxabcdbcd", _OVERLAP_A)
    _assert_identical("xxabcdbcd", _OVERLAP_B)


def test_body_cliches_patterns_equivalence() -> None:
    for text in _FIXTURES:
        _assert_identical(text, PATTERNS)


def test_body_cliches_analyzer_unchanged_on_fixtures() -> None:
    reference = _reference_regex_flags(name="body_cliches", text="", patterns=PATTERNS)
    for text in _FIXTURES:
        result = BodyClicheAnalyzer().analyze(text)
        expected = _reference_regex_flags(name="body_cliches", text=text, patterns=PATTERNS)
        assert result.name == expected.name == "body_cliches"
        assert result.score == expected.score
        assert [(f.type, f.start, f.end, f.excerpt) for f in result.flags] == [
            (f.type, f.start, f.end, f.excerpt) for f in expected.flags
        ]
    assert reference.score == 0.0


def test_backward_compatible_four_tuples() -> None:
    patterns: list[tuple] = [("a", r"foo\s+bar", "context_flag", "s"), ("b", r"baz", "hard_fail", "s2")]
    for text in ["foo bar baz", "FOO\nBAR and BAZ", "nope"]:
        _assert_identical(text, patterns)
