from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.analyzers import parts_of_speech, run_all_analyzers
from backend.analyzers.binary_contrast import BinaryContrastAnalyzer
from backend.analyzers.body_cliches import BodyClicheAnalyzer
from backend.analyzers.cinematic_fog import CinematicFogAnalyzer
from backend.analyzers.cliches import CATEGORIES, ClicheAnalyzer, _load_rule_pack
from backend.analyzers.filter_words import FilterWordsAnalyzer
from backend.analyzers.possible_adverbs import PossibleAdverbAnalyzer, spacy_model_status
from backend.analyzers.triad_cadence import TriadCadenceAnalyzer
from backend.analyzers.vague_abstracts import VagueAbstractAnalyzer
from backend.desktop_engine import analyze_text

FIXTURES = Path(__file__).parent / "fixtures"

requires_spacy_model_skipif = pytest.mark.skipif(
    not spacy_model_status()["available"],
    reason="production POS model is installed by the desktop extra",
)


def test_live_preset_includes_triad_cadence():
    report = analyze_text("The room was cold, narrow, and empty.", preset="live")
    assert any(item["analyzer"] == "triad_cadence" for item in report["diagnostics"])


def test_possible_adverb_fixture_precision_is_at_least_ninety_percent():
    rows = json.loads((FIXTURES / "adverb_precision.json").read_text(encoding="utf-8"))
    true_positive = false_positive = false_negative = 0
    analyzer = PossibleAdverbAnalyzer()
    for row in rows:
        found = any(flag.excerpt.casefold() == row["word"] for flag in analyzer.analyze(row["text"]).flags)
        if found and row["adverb"]:
            true_positive += 1
        elif found:
            false_positive += 1
        elif row["adverb"]:
            false_negative += 1
    precision = true_positive / max(true_positive + false_positive, 1)
    recall = true_positive / max(true_positive + false_negative, 1)
    assert precision >= 0.90
    assert recall >= 0.90


def test_possible_adverb_held_out_adjectives_and_names_are_not_flagged():
    non_adverbs = (
        "chilly crinkly dastardly frilly gravelly grisly measly pebbly spindly "
        "steely wily wobbly wrinkly Kimberly Sicily"
    )
    text = f"The {non_adverbs}. She moved quickly and answered calmly."
    found = {
        flag.excerpt.casefold()
        for flag in PossibleAdverbAnalyzer().analyze(text).flags
    }
    assert found == {"quickly", "calmly"}


@pytest.mark.requires_spacy_model
@requires_spacy_model_skipif
def test_bundled_pos_model_finds_non_ly_adverbs_when_available():
    text = "She often arrived late and never stayed long. Perhaps she left soon and worked hard."
    result = PossibleAdverbAnalyzer().analyze(
        text, {"possible_adverbs": {"confirm_pos": True}}
    )
    found = {flag.excerpt.casefold() for flag in result.flags}
    assert {"often", "never", "perhaps", "soon", "hard"} <= found
    assert all(flag.source == "pos+wordnet" for flag in result.flags)


def test_large_document_keeps_contextual_adverb_analysis_without_nonsense():
    text = "asdfly. " + ("She moved quickly and often. " * 5000)
    result = PossibleAdverbAnalyzer().analyze(
        text, {"possible_adverbs": {"confirm_pos": True}}
    )
    assert result.metrics["tier"] == "pos+wordnet"
    assert result.metrics["contextual_pos_chunked"] is True
    assert result.metrics["contextual_pos_skipped_for_size"] is False
    assert {flag.excerpt.casefold() for flag in result.flags} >= {"quickly", "often"}
    assert not any(flag.excerpt.casefold() == "asdfly" for flag in result.flags)
    assert all(flag.source == "pos+wordnet" for flag in result.flags)


def test_large_document_adverb_tier_disambiguates_common_homographs():
    text = ("The fast horse ran fast. The hard road made her work hard. " * 1000) + "The friendly horse waited."
    result = PossibleAdverbAnalyzer().analyze(text, {"possible_adverbs": {"confirm_pos": True}})
    found = [flag.excerpt.casefold() for flag in result.flags]
    assert "friendly" not in found
    assert found.count("fast") == 1000
    assert all(text[max(0, flag.start - 4):flag.start].casefold() == "ran " for flag in result.flags if flag.excerpt.casefold() == "fast")


def test_large_document_keeps_contextual_adjective_and_verb_analysis():
    from backend.analyzers.parts_of_speech import PossibleAdjectiveAnalyzer, PossibleVerbAnalyzer

    text = "asdf. " + ("The bright lamp stood beside an ancient door. " * 1400)
    profile = {
        "possible_adjectives": {"enabled": True},
        "possible_verbs": {"enabled": True},
    }
    adjective_result = PossibleAdjectiveAnalyzer().analyze(text, profile)
    verb_result = PossibleVerbAnalyzer().analyze(text, profile)
    assert {flag.excerpt.casefold() for flag in adjective_result.flags} == {"bright", "ancient"}
    assert {flag.excerpt.casefold() for flag in verb_result.flags} == {"stood"}
    for result in (adjective_result, verb_result):
        assert result.metrics["tier"] == "pos+wordnet"
        assert result.metrics["contextual_pos_chunked"] is True
        assert result.metrics["contextual_pos_skipped_for_size"] is False
        assert not any(flag.excerpt.casefold() == "asdf" for flag in result.flags)


def test_large_document_does_not_treat_wordnet_homographs_as_contextual_pos():
    from backend.analyzers.parts_of_speech import PossibleAdjectiveAnalyzer, PossibleVerbAnalyzer

    text = "I read the book beside the watch and light. " * 1400
    profile = {
        "possible_adjectives": {"enabled": True},
        "possible_verbs": {"enabled": True},
    }
    adjectives = {flag.excerpt.casefold() for flag in PossibleAdjectiveAnalyzer().analyze(text, profile).flags}
    verbs = {flag.excerpt.casefold() for flag in PossibleVerbAnalyzer().analyze(text, profile).flags}
    assert adjectives == set()
    assert verbs == {"read"}


def test_context_overlap_preserves_pos_inside_one_extremely_long_sentence():
    from backend.analyzers.parts_of_speech import PossibleAdjectiveAnalyzer, PossibleVerbAnalyzer

    text = ("word " * 9999) + "They light torches while the bright metal cools."
    profile = {
        "possible_adjectives": {"enabled": True},
        "possible_verbs": {"enabled": True},
    }
    adjectives = PossibleAdjectiveAnalyzer().analyze(text, profile).flags
    verbs = PossibleVerbAnalyzer().analyze(text, profile).flags
    light_start = text.index("light")
    assert any(flag.excerpt.casefold() == "light" and flag.start == light_start for flag in verbs)
    assert not any(flag.excerpt.casefold() == "light" for flag in adjectives)
    assert len({(flag.start, flag.end) for flag in verbs}) == len(verbs)


@pytest.mark.parametrize(
    ("text", "adjectives", "verbs"),
    [
        ("The bright lamp stood beside an ancient door.", {"bright", "ancient"}, {"stood"}),
        ("She was running and had written the final page.", {"final"}, {"running", "written"}),
        ("The light pack rested nearby. They light torches at dusk.", {"light"}, {"rested", "light"}),
        ("Lazan entered Umbar.", set(), {"entered"}),
    ],
)
def test_adjective_and_verb_context_fixture(text, adjectives, verbs):
    from backend.analyzers.parts_of_speech import PossibleAdjectiveAnalyzer, PossibleVerbAnalyzer

    profile = {
        "possible_adjectives": {"enabled": True},
        "possible_verbs": {"enabled": True},
    }
    found_adjectives = {flag.excerpt.casefold() for flag in PossibleAdjectiveAnalyzer().analyze(text, profile).flags}
    found_verbs = {flag.excerpt.casefold() for flag in PossibleVerbAnalyzer().analyze(text, profile).flags}
    assert adjectives <= found_adjectives
    assert verbs <= found_verbs
    assert not ({"lazan", "umbar"} & (found_adjectives | found_verbs))


@pytest.mark.parametrize(
    "quoted",
    [
        '"I noticed it and moved quickly into a wild goose chase."',
        "'I noticed it and moved quickly into a wild goose chase.'",
        "\u201cI noticed it and moved quickly into a wild goose chase.\u201d",
        "\u2018I noticed it and moved quickly into a wild goose chase.\u2019",
        "\u201cI noticed it and moved quickly\ninto a wild goose chase.\u201d",
    ],
)
@pytest.mark.parametrize(
    ("analyzer", "profile"),
    [
        (FilterWordsAnalyzer(), {"filter_words": {"ignore_dialogue": True}}),
        (PossibleAdverbAnalyzer(), {"possible_adverbs": {"ignore_dialogue": True}}),
        (ClicheAnalyzer(), {"cliches": {"ignore_dialogue": True}}),
    ],
)
def test_dialogue_exclusion_matrix(quoted, analyzer, profile):
    profile = {name: dict(settings) for name, settings in profile.items()}
    assert analyzer.analyze(quoted, profile).flags == []
    category = analyzer.name
    profile[category]["ignore_dialogue"] = False
    assert analyzer.analyze(quoted, profile).flags


@pytest.mark.parametrize(
    ("analyzer", "quoted"),
    [
        (BodyClicheAnalyzer(), '"Her jaw clenched."'),
        (CinematicFogAnalyzer(), '"The silence stretched."'),
        (BinaryContrastAnalyzer(), '"It was not fear but memory."'),
        (TriadCadenceAnalyzer(), '"The room was cold, narrow, and empty."'),
        (VagueAbstractAnalyzer(), '"The stakes are high."'),
    ],
)
def test_dialogue_exclusion_is_honored_by_every_analyzer(analyzer, quoted):
    profile = {analyzer.name: {"ignore_dialogue": True}}
    result = next(
        item for item in run_all_analyzers(quoted, profile)
        if item.name == analyzer.name
    )
    assert result.flags == []
    profile[analyzer.name]["ignore_dialogue"] = False
    result = next(
        item for item in run_all_analyzers(quoted, profile)
        if item.name == analyzer.name
    )
    assert result.flags


def test_apostrophe_is_not_treated_as_dialogue():
    result = PossibleAdverbAnalyzer().analyze("Mara's hand moved quickly.")
    assert [flag.excerpt for flag in result.flags] == ["quickly"]


def test_every_bundled_cliche_matches_including_terminal_punctuation():
    pack = _load_rule_pack()
    for category, phrases in pack.items():
        profile = {
            "cliche_categories": {name: name == category for name in CATEGORIES},
            "cliches": {"ignore_dialogue": False},
        }
        for phrase in phrases:
            flags = ClicheAnalyzer().analyze(phrase, profile).flags
            assert any(flag.start == 0 and flag.end == len(phrase) for flag in flags), (category, phrase)


def test_clean_corpus_has_no_hard_fail_or_strong_builtin_findings():
    text = (FIXTURES / "clean_corpus.txt").read_text(encoding="utf-8")
    results = run_all_analyzers(text, {"name": "clean"})
    broad = [
        flag
        for result in results
        if result.name not in {"profile_patterns", "possible_adverbs", "possible_adjectives", "possible_verbs"}
        for flag in result.flags
        if flag.severity in {"hard_fail", "strong_flag"}
    ]
    assert broad == []


def test_clean_corpus_advisory_false_positive_budget_is_bounded():
    text = (FIXTURES / "clean_corpus.txt").read_text(encoding="utf-8")
    findings = [
        (result.name, flag.type)
        for result in run_all_analyzers(text, {"name": "clean"})
        if result.name not in {"profile_patterns", "possible_adverbs", "possible_adjectives", "possible_verbs"}
        for flag in result.flags
    ]
    # One known repetition finding: "pantry door" -> "The door closed" nine
    # content words apart. Cohesive anaphora, but a true within-window
    # surface repeat; the budget records it rather than hiding the lens.
    assert findings == [
        ("triad_cadence", "three_item_list"),
        ("repetition", "repetition"),
    ]


def test_general_profile_rules_alone_use_hard_fail_and_provenance_is_explicit():
    results = run_all_analyzers(
        "Let that sink in. She moved quickly. It was not fear but memory.",
        {"hard_bans": ["Let that sink in."]},
    )
    hard = [flag for result in results for flag in result.flags if flag.severity == "hard_fail"]
    assert hard and all(flag.source == "profile" for flag in hard)
    adverb = next(flag for result in results if result.name == "possible_adverbs" for flag in result.flags)
    contrast = next(flag for result in results if result.name == "binary_contrast" for flag in result.flags)
    assert adverb.source == "pos+wordnet"
    assert contrast.source == "deterministic"


@pytest.mark.requires_spacy_model
@requires_spacy_model_skipif
def test_spacy_capability_checks_model_load_and_pos_tier():
    from backend import sidecar

    status = sidecar.dispatch({"operation": "capabilities"})["optional_features"]["spacy_pos_confirmation"]
    assert status == spacy_model_status()
    result = PossibleAdverbAnalyzer().analyze("Often", {"possible_adverbs": {"confirm_pos": True}})
    assert result.metrics["tier"] == "pos+wordnet"
    assert result.flags[0].source == "pos+wordnet"


def test_wordnet_rejects_nonsense_even_when_statistical_tagger_calls_it_an_adverb():
    from backend.analyzers.parts_of_speech import PossibleAdjectiveAnalyzer, PossibleVerbAnalyzer

    profile = {
        "possible_adjectives": {"enabled": True},
        "possible_verbs": {"enabled": True},
    }
    assert PossibleAdverbAnalyzer().analyze("asdf").flags == []
    assert PossibleAdjectiveAnalyzer().analyze("asdf", profile).flags == []
    assert PossibleVerbAnalyzer().analyze("asdf", profile).flags == []


def test_context_distinguishes_adjective_adverb_and_verb_lenses():
    from backend.analyzers.parts_of_speech import PossibleAdjectiveAnalyzer, PossibleVerbAnalyzer

    text = "The fast horse ran fast."
    profile = {
        "possible_adjectives": {"enabled": True},
        "possible_verbs": {"enabled": True},
    }
    adjective = PossibleAdjectiveAnalyzer().analyze(text, profile)
    adverb = PossibleAdverbAnalyzer().analyze(text)
    verb = PossibleVerbAnalyzer().analyze(text, profile)
    assert [flag.excerpt for flag in adjective.flags] == ["fast"]
    assert [flag.excerpt for flag in adverb.flags] == ["fast"]
    assert [flag.excerpt for flag in verb.flags] == ["ran"]


@pytest.mark.requires_spacy_model
@pytest.mark.skipif(
    parts_of_speech._spacy_model()[0] is None,
    reason="production POS model is installed by the desktop extra",
)
def test_stripped_pos_pipeline_keeps_contextual_disambiguation():
    from backend.analyzers.parts_of_speech import PossibleAdjectiveAnalyzer, PossibleVerbAnalyzer

    model, _ = parts_of_speech._spacy_model()
    assert model.pipe_names == ["tok2vec", "tagger"]

    text = "When did the Islamic State respond? I have an answer. The waiting room is quiet. Best,"
    profile = {
        "possible_adverbs": {"ignore_dialogue": False},
        "possible_adjectives": {"enabled": True},
        "possible_verbs": {"enabled": True},
    }
    adverbs = {flag.excerpt.casefold() for flag in PossibleAdverbAnalyzer().analyze(text, profile).flags}
    adjectives = {flag.excerpt.casefold() for flag in PossibleAdjectiveAnalyzer().analyze(text, profile).flags}
    verbs = {flag.excerpt.casefold() for flag in PossibleVerbAnalyzer().analyze(text, profile).flags}

    assert "when" in adverbs
    assert "best" not in adverbs
    assert "islamic" in adjectives
    assert "have" in verbs
    assert "waiting" not in verbs


def test_selected_pos_lenses_share_one_contextual_traversal(monkeypatch):
    from backend.analyzers import parts_of_speech
    from backend.desktop_engine import analyze_text

    calls = 0
    original = parts_of_speech._build_tagged_tokens

    def counted(text):
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(parts_of_speech, "_build_tagged_tokens", counted)
    report = analyze_text(
        "She quickly opened the heavy door.",
        preset="full",
        confirm_adverbs=True,
        analyzers=["possible_adverbs", "possible_adjectives", "possible_verbs"],
        overrides={
            "possible_adjectives": {"enabled": True},
            "possible_verbs": {"enabled": True},
        },
    )
    assert calls == 1
    assert {row["name"] for row in report["analysis"]} == {
        "possible_adverbs",
        "possible_adjectives",
        "possible_verbs",
        "profile_patterns",
    }


def test_analyzers_do_not_stop_at_five_hundred_occurrences():
    text = " ".join("I just moved quickly." for _ in range(650))
    adverbs = PossibleAdverbAnalyzer().analyze(
        text, {"possible_adverbs": {"ignore_dialogue": False}}
    )
    filters = FilterWordsAnalyzer().analyze(
        text, {"filter_words": {"ignore_dialogue": False}}
    )
    assert len(adverbs.flags) == 1300
    assert len(filters.flags) == 650
    assert adverbs.metrics["findings_truncated"] is False
    assert filters.metrics["findings_truncated"] is False
