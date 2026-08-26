from backend import config
from backend.manuscript import analyze_manuscript, calibrate_corpus, load_lens_baselines


def test_manuscript_finds_cross_file_repetition():
    report = analyze_manuscript(
        [
            {"name": "one.md", "text": "Mara checked the red ledger. Mara checked the red ledger."},
            {"name": "two.md", "text": "At noon, Mara checked the red ledger again."},
        ],
        "creative-default",
    )
    phrases = report["repetition"]["repeated_phrases"]
    assert any(item["phrase"] == "mara checked the red ledger" for item in phrases)
    assert report["document_count"] == 2
    assert report["persisted"] is False
    assert "run_id" not in report


def test_repeated_words_carry_per_chapter_counts():
    # Hand-computed: "ledger" 2x in chapter one + 2x in chapter two;
    # "shadow" 3x in chapter one + 1x in chapter two. Proper-noun names
    # are deliberately excluded from crutch-word detection.
    report = analyze_manuscript(
        [
            {"name": "one.md",
             "text": "She checked the ledger twice. She read the ledger again. The shadow grew. The shadow watched. The shadow waited."},
            {"name": "two.md",
             "text": "The ledger won. The ledger waited. The shadow followed her home."},
        ],
        "creative-default",
    )
    repetition = report["repetition"]
    assert repetition["chapter_names"] == ["one.md", "two.md"]
    rows = {item["lemma"]: item for item in repetition["repeated_words"]}
    ledger = rows.get("ledger")
    assert ledger is not None
    assert ledger["count"] == 4
    assert ledger["per_chapter"] == [2, 2]
    shadow = rows.get("shadow")
    assert shadow is not None
    assert shadow["count"] == 4
    assert shadow["per_chapter"] == [3, 1]


def test_chapter_opener_audit_flags_repeated_patterns():
    # All three chapters open with "The"; two share the "the fog" bigram.
    report = analyze_manuscript(
        [
            {"name": "a.md", "text": "The fog rolled in over the harbor before dawn."},
            {"name": "b.md", "text": "The fog had never lifted since the accident."},
            {"name": "c.md", "text": "The coins were counted twice before speaking."},
        ],
        "creative-default",
    )
    audit = report["repetition"]["chapter_openers"]
    words_flagged = {item["word"] for item in audit["repeated_first_words"]}
    assert "the" in words_flagged
    bigrams_flagged = {item["bigram"] for item in audit["repeated_first_bigrams"]}
    assert "the fog" in bigrams_flagged
    assert len(audit["openers"]) == 3


def test_chapter_opener_audit_passes_distinct_openers():
    report = analyze_manuscript(
        [
            {"name": "a.md", "text": "Fog rolled in over the harbor before dawn."},
            {"name": "b.md", "text": "Marcus counted the coins twice before speaking."},
            {"name": "c.md", "text": "Nobody believed her anymore, and that was fine."},
        ],
        "creative-default",
    )
    audit = report["repetition"]["chapter_openers"]
    assert audit["repeated_first_words"] == []
    assert audit["repeated_first_bigrams"] == []


def test_calibration_builds_overrepresentation_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path)
    result = calibrate_corpus(
        ["The system will leverage synergy. We leverage synergy again."],
        "pytest-model",
        ["The mechanic tightened the bolt."],
    )
    words = {item["word"] for item in result["top_overrepresented_words"]}
    assert "leverage" in words
    assert result["path"].endswith("pytest-model.json")


def test_quality_timeline_records_and_orders_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    from backend.projects import create_project

    created = create_project("Timeline Saga")
    project_dir = tmp_path / created["name"]
    project_dir.mkdir(parents=True, exist_ok=True)
    docs = [
        {"name": "one.md", "text": "Mara checked the red ledger. Mara checked the red ledger."},
        {"name": "two.md", "text": "At noon, Mara checked the red ledger again."},
    ]
    first = analyze_manuscript(docs, "creative-default", project="Timeline Saga", persist=True)
    second = analyze_manuscript(docs, "creative-default", project="Timeline Saga", persist=True)
    from backend.manuscript import read_project_timeline

    timeline = read_project_timeline("Timeline Saga")
    assert timeline["project"] == "Timeline Saga"
    assert len(timeline["runs"]) == 2
    ordered = [run["created_at"] for run in timeline["runs"]]
    assert ordered == sorted(ordered)
    assert {first["run_id"], second["run_id"]} == {run["run_id"] for run in timeline["runs"]}
    assert all(isinstance(run.get("category_counts"), dict) for run in timeline["runs"])


def test_manuscript_report_embeds_quality_timeline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path)
    from backend.projects import create_project

    created = create_project("Embedded Saga")
    (tmp_path / created["name"]).mkdir(parents=True, exist_ok=True)
    docs = [
        {"name": "one.md", "text": "Mara checked the red ledger. Mara checked the red ledger."},
        {"name": "two.md", "text": "At noon, Mara checked the red ledger again."},
    ]
    report = analyze_manuscript(docs, "creative-default", project="Embedded Saga", persist=True)
    from backend.manuscript import manuscript_report_with_timeline

    envelope = manuscript_report_with_timeline(report, "Embedded Saga")
    assert len(envelope["quality_timeline"]["runs"]) == 1
    plain = analyze_manuscript(docs, "creative-default")
    assert manuscript_report_with_timeline(plain, None).get("quality_timeline") is None


def test_genre_comparison_embeds_when_baseline_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path)
    calibrate_corpus(
        ["She felt that it was very good. She felt that it was very nice."],
        "creative-default",
    )
    from backend.manuscript import manuscript_report_with_timeline

    docs = [
        {"name": "one.md", "text": "Mara checked the red ledger. Mara checked the red ledger."},
        {"name": "two.md", "text": "At noon, Mara checked the red ledger again."},
    ]
    report = analyze_manuscript(docs, "creative-default")
    envelope = manuscript_report_with_timeline(report, None)
    comparison = envelope.get("genre_comparison")
    assert comparison is not None
    assert comparison["calibration"] == "creative-default"
    assert isinstance(comparison["baselines"], dict) and comparison["baselines"]
    other = analyze_manuscript(docs, "creative-default")
    import os

    os.remove(tmp_path / "calibrations" / "creative-default.json")
    stripped = manuscript_report_with_timeline(other, None)
    assert stripped.get("genre_comparison") is None


def test_lens_baselines_stored_and_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path)
    samples = [
        "She felt that it was very good. She felt that it was very nice indeed.",
        "He felt that it was very bad. He felt that it was very wrong somehow.",
    ]
    result = calibrate_corpus(samples, "pytest-genre")
    baselines = result["lens_baselines"]
    assert isinstance(baselines, dict) and baselines
    loaded = load_lens_baselines("pytest-genre")
    assert loaded == baselines
    assert load_lens_baselines("missing-calibration") == {}


def test_lens_baselines_served_through_protocol(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path)
    calibrate_corpus(["She felt that it was very good indeed."], "pytest-serve")
    from backend.sidecar import dispatch

    result = dispatch({"operation": "lens_baselines", "params": {"name": "pytest-serve"}})
    assert isinstance(result["baselines"], dict)
    empty = dispatch({"operation": "lens_baselines", "params": {"name": "nope"}})
    assert empty["baselines"] == {}
    from backend.mcp_server import tool_call

    assert tool_call("prose_lens_baselines", {"name": "pytest-serve"})["baselines"] == result["baselines"]
