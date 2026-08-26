import os
import stat

import pytest

from backend.llm_clients import LLMResponse
from backend.models import RunRequest
from backend.pipeline import compare_texts, run_pipeline


def test_diagnose_is_non_persistent_by_default():
    report = run_pipeline(RunRequest(text="It wasn't fear. It was memory.", mode="diagnose", profile="creative-default"))
    assert "run_id" not in report
    assert report["persisted"] is False
    assert report["score_before"] >= 0
    assert report["output_text"] == "It wasn't fear. It was memory."


def test_diagnose_can_explicitly_save_run(tmp_path, monkeypatch):
    import backend.storage as storage
    from backend import config

    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "runs.sqlite3")
    report = run_pipeline(RunRequest(text="A clean sentence.", mode="diagnose", persist=True))
    assert report["run_id"]
    assert report["persisted"] is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_explicit_run_artifacts_are_owner_only_on_unix(tmp_path, monkeypatch):
    import backend.storage as storage
    from backend import config

    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "runs.sqlite3")
    report = run_pipeline(RunRequest(text="A private draft.", mode="diagnose", persist=True))
    run_dir = tmp_path / report["run_id"]

    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(storage.DB_PATH.stat().st_mode) == 0o600
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in run_dir.iterdir())


def test_compare_reports_delta():
    report = compare_texts("It wasn't fear. It was memory.", "Mara remembered the red cup on the counter.")
    assert report["mode"] == "compare"
    assert report["score_before"] >= report["score_after"]


def test_first_rewrite_pass_receives_filter_word_findings(monkeypatch):
    captured = {}

    def fake_complete(messages, provider):
        captured["messages"] = messages
        return LLMResponse("The clock struck noon.", "test", "test-model")

    monkeypatch.setattr("backend.pipeline.complete_chat", fake_complete)
    report = run_pipeline(
        RunRequest(text="Mara noticed the clock.", mode="rewrite", profile="creative-default")
    )

    assert "filter_words:filter_perception" in captured["messages"][1]["content"]
    assert report["output_text"] == "The clock struck noon."


def test_profile_snapshot_is_used_without_reloading_disk(monkeypatch):
    monkeypatch.setattr(
        "backend.pipeline.load_profile",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("disk reload")),
    )
    snapshot = {
        "name": "approved",
        "register_target": "approved direct voice",
        "hard_bans": ["Sin embargo"],
        "analyzer_weights": {},
    }
    report = run_pipeline(RunRequest(
        text="Sin embargo, she stayed.",
        profile="approved",
        profile_snapshot=snapshot,
        mode="diagnose",
    ))
    assert report["profile"] == "approved"
    assert any(
        flag["excerpt"] == "Sin embargo"
        for analyzer in report["analysis_before"]
        for flag in analyzer["flags"]
    )


def test_diagnose_and_first_rewrite_pass_reuse_initial_analysis(monkeypatch):
    from backend import pipeline

    original = pipeline.run_all_analyzers
    calls = []

    def counted(text, profile):
        calls.append(text)
        return original(text, profile)

    monkeypatch.setattr(pipeline, "run_all_analyzers", counted)
    run_pipeline(RunRequest(text="Mara noticed it.", mode="diagnose"))
    assert calls == ["Mara noticed it."]

    calls.clear()
    monkeypatch.setattr(
        pipeline,
        "complete_chat",
        lambda *_args, **_kwargs: LLMResponse(
            "Mara counted twelve coins.", "test", "test"
        ),
    )
    run_pipeline(RunRequest(text="Mara noticed it.", mode="rewrite"))
    assert calls == ["Mara noticed it.", "Mara counted twelve coins."]
