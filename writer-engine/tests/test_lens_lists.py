import pytest

from backend.analyzers.base import run_analyzers, run_live_analyzers
from backend.lens_lists import LENS_ANALYZERS, apply_lens_lists
from backend.models import AnalyzerResult, Flag
from backend.profiles import export_profile, import_profile, load_profile, save_profile
from backend.validation import validate_profile


@pytest.mark.parametrize("lens,names", LENS_ANALYZERS.items())
def test_every_lens_accepts_literal_phrases(lens, names):
    results = [AnalyzerResult(name=name, score=0) for name in names]
    text = "😀 moon dust and MOON   DUST; notmoon dust. a+b."
    apply_lens_lists(results, text, {lens: {"include": ["moon dust", "a+b"]}})
    flags = [flag for result in results for flag in result.flags]
    assert [text[f.start:f.end] for f in flags] == ["moon dust", "MOON   DUST", "a+b"]
    assert all(flag.rule_id.startswith(f"lens_list.{lens}.") for flag in flags)
    assert all(flag.source == "profile" for flag in flags)
    assert len({f.rule_id for f in flags}) == 2


def test_filter_list_live_flow_excludes_builtins_and_avoids_duplicate():
    profile = {"filter_words": {"ignore_dialogue": False}, "lens_lists": {
        "filter_words": {"include": ["saw", "moon dust"], "exclude": ["felt"]}}}
    results = run_live_analyzers("I saw moon dust and felt fine.", profile)
    result = next(r for r in results if r.name == "filter_words")
    assert len(result.flags) == 2
    assert result.score == 2
    assert result.metrics["total_findings"] == 2


def test_custom_only_and_dialogue_and_ignored_phrase():
    results = run_analyzers('I saw moon dust. "moon dust". clouds.', {"lens_lists": {
        "filter_words": {"use_builtin": False, "ignore_dialogue": True,
                         "include": ["moon dust", "clouds"], "exclude": ["clouds"]}}}, ["filter_words"])
    result = next(r for r in results if r.name == "filter_words")
    assert len(result.flags) == 1
    assert result.flags[0].start == 6
    assert result.score == 1


def test_unicode_literals_keep_original_spelling_and_utf16_positions():
    text = "😀 Straße STRAẞE"
    results = run_analyzers(text, {"lens_lists": {"filter_words": {"include": ["Straße"]}}}, ["filter_words"])
    flags = next(r for r in results if r.name == "filter_words").flags
    assert len(flags) == 2
    assert flags[0].to_dict(text)["start_utf16"] == 3


def test_exclusions_preserve_noncount_score_and_respect_scope():
    result = AnalyzerResult("metaphor_density", 42, [Flag("x", "taste_flag", 0, 4, "moon", "")],
                            score_semantics="per_1000")
    apply_lens_lists([result], "moon dust", {
        "metaphor_texture": {"exclude": ["moon"], "include": ["dust"]},
        "filter_words": {"include": ["moon"]}})
    assert result.score == 42
    assert len(result.flags) == 1
    assert result.flags[0].start == 5


@pytest.mark.parametrize("lists", [[], {"grammar_mechanics": {}}, {"filter_words": []},
    {"filter_words": {"include": "saw"}}, {"filter_words": {"include": ["x"] * 501}},
    {"filter_words": {"include": ["x" * 257]}}, {"filter_words": {"include": ["a\nb"]}},
    {"filter_words": {"use_builtin": "false"}}, {"filter_words": {"regex": "(a+)+"}}])
def test_invalid_shared_lists_rejected(lists):
    with pytest.raises(ValueError):
        validate_profile({"lens_lists": lists})


def test_named_profile_save_load_export_import(tmp_path, monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    lists = {"filter_words": {"name": "My filters", "include": ["moon dust"], "exclude": ["saw"]}}
    save_profile("my-list", {"lens_lists": lists})
    exported = export_profile("my-list")
    import_profile(profile=exported["profile"], name="shared-copy")
    assert load_profile("shared-copy")["lens_lists"] == lists
    assert load_profile("my-list")["lens_lists"] == lists


def test_saved_list_reaches_snapshot_counts_findings_and_overlay_spans(tmp_path, monkeypatch):
    from backend import config
    from backend.sidecar import PROTOCOL_MAJOR, dispatch
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(config, "ANALYSIS_CACHE_DB", tmp_path / "cache.sqlite3")

    def call(operation, **params):
        return dispatch({"protocol_major": PROTOCOL_MAJOR, "protocol_minor": 0,
                         "request_id": "lists-test", "document_id": "lists-doc",
                         "document_revision": 1, "operation": operation, "params": params})

    for phrase, expected_start in [("moon dust", 3), ("cloud", 14)]:
        call("save_profile", name="shared-filters", profile={"lens_lists": {
            "filter_words": {"use_builtin": False, "include": [phrase]}}})
        snapshot = call("analyze_document", text="😀 moon dust; cloud.", profile="shared-filters",
                        analyzers=["filter_words"], grammar={"enabled": False})
        assert snapshot["counts_by_analyzer"]["filter_words"] == 1
        findings = call("query_findings", analysis_id=snapshot["analysis_id"], analyzers=["filter_words"])
        assert findings["diagnostics"][0]["start_utf16"] == expected_start
        overlays = call("query_overlay_spans", analysis_id=snapshot["analysis_id"], categories=["filter_words"])
        assert len(overlays["spans"]) == 1
        assert overlays["spans"][0][0] == expected_start
        call("dispose_analysis", analysis_id=snapshot["analysis_id"])
