from backend.desktop_engine import analyze_text


def test_live_analysis_uses_utf16_offsets_and_revision():
    report = analyze_text("Mara 😀 noticed the clock.", preset="live", document_revision=17)
    finding = next(item for item in report["diagnostics"] if item["rule_id"] == "filter_words.filter_perception")
    assert finding["start_utf16"] == 8
    assert finding["end_utf16"] == 15
    assert finding["revision"] == 17
    assert finding["id"]


def test_live_analysis_filters_global_exclusion_ranges():
    report = analyze_text(
        "Mara noticed the clock.",
        preset="live",
        base_offset_utf16=100,
        exclusion_ranges=[{"start_utf16": 105, "end_utf16": 112, "kind": "inline_code"}],
    )
    assert not any(item["rule_id"] == "filter_words.filter_perception" for item in report["diagnostics"])
    assert report["excluded_count"] >= 1


def test_possible_adverbs_use_live_context_and_lexicon():
    report = analyze_text("She moved quickly past the friendly clerk.", preset="live")
    findings = [item for item in report["diagnostics"] if item["analyzer"] == "possible_adverbs"]
    assert [item["excerpt"] for item in findings] == ["quickly"]
    assert findings[0]["confidence"] == 0.95
    assert findings[0]["source"] == "heuristic+wordnet"


def test_cliche_live_preset_detects_phrase():
    report = analyze_text("The errand became a wild goose chase.", preset="live")
    assert any(item["analyzer"] == "cliches" for item in report["diagnostics"])


def test_pos_tier_reports_confirmed_non_ly_adverbs():
    report = analyze_text("Often friendly", preset="full", confirm_adverbs=True)
    findings = [item for item in report["diagnostics"] if item["analyzer"] == "possible_adverbs"]
    assert [item["excerpt"] for item in findings] == ["Often"]
    assert findings[0]["source"] == "pos+wordnet"


def test_non_english_disables_lexical_rules_but_keeps_exact_profile_phrases():
    report = analyze_text(
        "Ella realmente corrio. Sin embargo, regreso.",
        preset="full",
        language="es",
        overrides={"hard_bans": ["Sin embargo"]},
    )
    assert report["lexical_rules_enabled"] is False
    assert [item["name"] for item in report["analysis"]] == ["profile_patterns"]
    assert [item["excerpt"] for item in report["diagnostics"]] == ["Sin embargo"]


def test_unknown_language_keeps_lexical_rules_for_short_regions():
    report = analyze_text("She moved quickly.", preset="live", language="und")
    assert report["lexical_rules_enabled"] is True
    assert any(item["analyzer"] == "possible_adverbs" for item in report["diagnostics"])


def test_profile_thresholds_control_analyzer_and_rule_visibility():
    report = analyze_text(
        "She moved quickly. It was not fear but memory.",
        preset="full",
        overrides={
            "thresholds": {
                "possible_adverbs": {"enabled": False},
                "binary_contrast.not_x_but_y": {"minimum_level": "hard_fail"},
            }
        },
    )
    assert not any(
        item["analyzer"] in {"possible_adverbs", "binary_contrast"}
        for item in report["diagnostics"]
    )
    removed = {
        item["name"]: item["metrics"].get("threshold_findings_removed", 0)
        for item in report["analysis"]
    }
    assert removed["possible_adverbs"] == 1
    assert removed["binary_contrast"] == 1
