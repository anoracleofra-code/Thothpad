from backend.analyzers.base import LIVE_ANALYZERS, _analyzers
from backend.analyzers.repetition import RepetitionAnalyzer
from backend.models import Flag
from backend.text_utils import Utf16Index


def _flags(result):
    return [(flag.excerpt, flag.start, flag.end) for flag in result.flags]


def test_repeat_inside_window_flagged_once_at_later_occurrence():
    # distance_words between the two "granite" tokens is exactly 5.
    text = "granite bravo charlie delta echo granite"
    result = RepetitionAnalyzer().analyze(text, {"repetition": {"window_words": 5}})
    assert len(result.flags) == 1
    flag = result.flags[0]
    assert flag.excerpt == "granite"
    assert flag.type == "repetition"
    assert flag.severity == "taste_flag"
    assert text[flag.start : flag.end] == "granite"
    assert result.metrics["repeats"] == [
        {"word": "granite", "distance_words": 5, "start": flag.start}
    ]


def test_repeat_pair_carries_both_spans():
    text = "granite bravo charlie delta echo granite"
    result = RepetitionAnalyzer().analyze(text, {"repetition": {"window_words": 5}})
    assert len(result.flags) == 1
    flag = result.flags[0]
    # Primary span stays the LATER occurrence; the earlier half rides along
    # so both occurrences highlight.
    assert (flag.start, flag.end) == (33, 40)
    assert flag.extra_spans == [(0, 7)]
    serialized = flag.to_dict(text)
    assert serialized["extra_spans_utf16"] == [[0, 7]]
    assert (serialized["start_utf16"], serialized["end_utf16"]) == (33, 40)
    # Flags without related occurrences keep the old envelope shape.
    plain = RepetitionAnalyzer().analyze("alpha bravo", {"repetition": {"window_words": 5}})
    assert not plain.flags
    single = Flag(
        type="t", severity="taste_flag", start=0, end=3, excerpt="abc", suggestion="s"
    ).to_dict("abcdef")
    assert "extra_spans_utf16" not in single


def test_repeat_outside_window_not_flagged():
    text = "granite bravo charlie delta echo granite"
    result = RepetitionAnalyzer().analyze(text, {"repetition": {"window_words": 4}})
    assert result.flags == []
    assert result.metrics["repeats"] == []


def test_case_insensitive_matching():
    text = "Shadow fell across the courtyard. Later, shadow returned."
    result = RepetitionAnalyzer().analyze(text)
    assert _flags(result)[0][0] == "shadow"


def test_stopwords_are_skipped():
    text = "That thought arrived early. That evening changed everything."
    result = RepetitionAnalyzer().analyze(text)
    assert not any(flag.excerpt.casefold() == "that" for flag in result.flags)


def test_new_default_stopwords_are_skipped():
    text = (
        "Either way he stepped around the desk onto the mat, moved forward, "
        "seemed calm, looked away."
    )
    result = RepetitionAnalyzer().analyze(text)
    flagged = {flag.excerpt.casefold() for flag in result.flags}
    assert not flagged & {"either", "onto", "around", "forward", "seemed", "looked"}


def test_extra_stopwords_respected():
    text = "granite bravo charlie delta echo granite"
    profile = {"repetition": {"window_words": 5, "extra_stopwords": ["granite"]}}
    result = RepetitionAnalyzer().analyze(text, profile)
    assert result.flags == []


def test_short_words_are_skipped():
    text = "The cat saw another cat."
    result = RepetitionAnalyzer().analyze(text)
    assert not any(flag.excerpt.casefold() == "cat" for flag in result.flags)


def test_far_band_repeat_counted_but_not_surfaced_by_default():
    import itertools

    # 31 distinct filler tokens -> the second "granite" sits inside the
    # default 50-token window but beyond the 12-token close-echo band.
    fillers = " ".join(
        "".join(letters) for letters in itertools.islice(itertools.product("abcdef", repeat=4), 30)
    )
    text = f"granite {fillers} granite"
    default_result = RepetitionAnalyzer().analyze(text)
    assert default_result.flags == []
    assert default_result.metrics["window_words"] == 50
    assert default_result.metrics["max_flag_distance"] == 12
    assert default_result.metrics["far_repeat_count"] == 1

    # Disabling banding surfaces it without changing any other semantics.
    unbanded = RepetitionAnalyzer().analyze(
        text, {"repetition": {"max_flag_distance": None}}
    )
    assert [excerpt for excerpt, _, _ in _flags(unbanded)] == ["granite"]
    assert unbanded.metrics["repeats"][0]["distance_words"] == 31


def test_wide_net_750_window_remains_available_as_opt_in():
    import itertools

    # 61 distinct filler tokens: far beyond every default, inside 750.
    fillers = " ".join(
        "".join(letters) for letters in itertools.islice(itertools.product("abcdef", repeat=4), 60)
    )
    text = f"granite {fillers} granite"
    assert RepetitionAnalyzer().analyze(text).flags == []

    wide = RepetitionAnalyzer().analyze(
        text, {"repetition": {"window_words": 750, "max_flag_distance": None}}
    )
    assert [excerpt for excerpt, _, _ in _flags(wide)] == ["granite"]
    assert wide.metrics["window_words"] == 750
    assert wide.metrics["repeats"][0]["distance_words"] == 61


def test_high_frequency_name_suppressed_by_default_and_recoverable():
    name = "Kelsier"
    text = " ".join(
        f"The plan worked. {name} smiled and left quickly through the door."
        for _ in range(30)
    )
    default_result = RepetitionAnalyzer().analyze(text)
    assert not any(
        flag.excerpt.casefold() == "kelsier" for flag in default_result.flags
    )
    assert default_result.metrics["suppressed_names"] == ["kelsier"]

    off = RepetitionAnalyzer().analyze(text, {"repetition": {"suppress_names": False}})
    assert sum(1 for flag in off.flags if flag.excerpt.casefold() == "kelsier") >= 25


def test_lowercase_common_nouns_are_not_suppressed():
    # 30 mentions but lowercase mid-sentence -> not name evidence.
    text = " ".join("He walked toward the harbor while the tide rose slowly." for _ in range(30))
    result = RepetitionAnalyzer().analyze(text)
    assert "harbor" not in result.metrics["suppressed_names"]
    assert any(flag.excerpt.casefold() == "harbor" for flag in result.flags)


def test_rare_capitalized_name_suppressed_by_default_recoverable_via_thresholds():
    # Two capitalized MID-SENTENCE mentions, 100% capitalized: suppressed
    # like the manuscript machinery's _likely_proper_nouns would classify
    # it (sentence-initial capitals carry no name evidence).
    rare = "The herald watched as Zarine spoke about the harbor. Much later, Zarine spoke again."
    default_result = RepetitionAnalyzer().analyze(rare)
    assert "zarine" in default_result.metrics["suppressed_names"]
    assert not any(flag.excerpt == "Zarine" for flag in default_result.flags)

    # Raising the frequency threshold to the old Kelsier-class gate
    # restores flagging for rare names.
    strict = RepetitionAnalyzer().analyze(
        rare, {"repetition": {"name_min_frequency": 25, "name_capitalized_ratio": 0.9}}
    )
    assert any(flag.excerpt == "Zarine" for flag in strict.flags)


def test_dialogue_excluded_by_default_and_flagged_when_disabled():
    text = 'He said, "Mara whispered softly," and left. Mara listened instead.'
    default_result = RepetitionAnalyzer().analyze(text)
    assert default_result.metrics["ignored_dialogue"] is True
    assert not any(flag.excerpt == "Mara" for flag in default_result.flags)

    enabled = RepetitionAnalyzer().analyze(
        text,
        {"repetition": {"ignore_dialogue": False, "suppress_names": False}},
    )
    mara_flags = [flag for flag in enabled.flags if flag.excerpt == "Mara"]
    assert len(mara_flags) == 1
    assert text[mara_flags[0].start : mara_flags[0].end] == "Mara"


def test_multiple_repeats_chain_within_window():
    text = "lumen brick frost lumen brick frost lumen"
    result = RepetitionAnalyzer().analyze(text, {"repetition": {"window_words": 10}})
    lumen_repeats = [entry for entry in result.metrics["repeats"] if entry["word"] == "lumen"]
    assert [entry["distance_words"] for entry in lumen_repeats] == [3, 3]
    assert [excerpt for excerpt, _, _ in _flags(result)].count("lumen") == 2


def test_offsets_survive_emoji_utf16_slicing():
    text = "\U0001F30A stone wall. Beyond it, stone steps."
    result = RepetitionAnalyzer().analyze(text)
    assert len(result.flags) == 1
    flag = result.flags[0]
    assert text[flag.start : flag.end] == "stone"
    data = flag.to_dict(text)
    index = Utf16Index(text)
    assert data["start_utf16"] == index[flag.start]
    assert data["end_utf16"] == index[flag.end]
    # The wave emoji is one Python char but two UTF-16 code units.
    assert data["end_utf16"] - data["start_utf16"] == len("stone".encode("utf-16-le")) // 2


def test_deterministic_output():
    text = "harbor lights faded. Somewhere harbor bells rang. harbor fog closed in."
    first = RepetitionAnalyzer().analyze(text)
    second = RepetitionAnalyzer().analyze(text)
    assert _flags(first) == _flags(second)
    assert first.score == second.score
    assert first.metrics["repeats"] == second.metrics["repeats"]


def test_empty_text_yields_no_flags():
    result = RepetitionAnalyzer().analyze("")
    assert result.name == "repetition"
    assert result.score == 0.0
    assert result.flags == []


def test_disabled_setting_short_circuits():
    result = RepetitionAnalyzer().analyze(
        "granite granite granite", {"repetition": {"enabled": False}}
    )
    assert result.flags == []
    assert result.score == 0.0


def test_registered_as_live_analyzer():
    assert "repetition" in LIVE_ANALYZERS
    registry = _analyzers()
    assert isinstance(registry["repetition"], RepetitionAnalyzer)


def test_interjections_are_builtin_stopwords():
    # Round 3 P3: ah, aye, hm, oh, ouch never flag and never count as
    # capitalized name evidence.
    text = "Oh no. Ouch, that hurt. He paused. Ouch, again. Aye, hm, ah."
    result = RepetitionAnalyzer().analyze(text)
    flagged = {flag.excerpt.casefold() for flag in result.flags}
    assert not flagged & {"ah", "aye", "hm", "oh", "ouch"}
    assert "oh" not in result.metrics["suppressed_names"]
    assert "ouch" not in result.metrics["suppressed_names"]


def test_sentence_initial_capitals_are_not_name_evidence():
    # Dialogue-heavy drafts capitalize ordinary content words at sentence
    # starts; aligned with _likely_proper_nouns these carry no name
    # evidence and the words must stay flaggable.
    text = " ".join("Come here now. Dream again tonight." for _ in range(20))
    result = RepetitionAnalyzer().analyze(text)
    assert "come" not in result.metrics["suppressed_names"]
    assert "dream" not in result.metrics["suppressed_names"]


def test_protagonist_guard_suppresses_high_frequency_capitalized_anywhere():
    # 30 mentions, every one capitalized but at a sentence start: the
    # mid-sentence ratio is 0, yet doc-frequency >= 25 at >= 90%
    # capitalized anywhere triggers the protagonist guard.
    text = " ".join("Lazan walked in. The room went quiet again." for _ in range(30))
    result = RepetitionAnalyzer().analyze(text)
    assert "lazan" in result.metrics["suppressed_names"]
    assert not any(flag.excerpt.casefold() == "lazan" for flag in result.flags)
