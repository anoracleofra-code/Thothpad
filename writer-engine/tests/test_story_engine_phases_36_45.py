from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from backend.story.authority import AuthorityStatus
from backend.story.claims import create_claim
from backend.story.compatibility import create_story_state_backup, restore_story_state_backup
from backend.story.context import ContextCompiler
from backend.story.ingest import ProjectIngestor
from backend.story.observability import observability_report, record_story_event
from backend.story.offline import certify_provider_offline, offline_readiness
from backend.story.path_resilience import analyze_relative_paths, path_resilience_report
from backend.story.project import STORY_STATE_SCHEMA_VERSION, StoryProject
from backend.story.query import StoryQueryEngine
from backend.story.recovery import begin_recovery_operation, recover_story_project, recovery_status
from backend.story.release_candidate import ReleaseCandidateAcceptance
from backend.story.release_validation import ReleaseProjectValidator
from backend.story.resource_budget import StoryBudgetExceeded, StoryResourceBudget
from backend.story.store import StoryStore
from backend.story.story_explorer import StoryExplorer
from backend.story.tools import invoke_story_tool
from backend.story.writer_state import put_writer_claim


def _project(tmp_path: Path, name: str = "release-book") -> tuple[Path, StoryProject, StoryStore]:
    root = tmp_path / name
    nested = root / "Act Ω" / "Nested"
    nested.mkdir(parents=True)
    manuscript = nested / "Chapter α.md"
    manuscript.write_text("# Chapter One\n\nMara carries the bronze bell key.\n", encoding="utf-8")
    project = StoryProject.open(root)
    project.set_source_override(
        manuscript.relative_to(root).as_posix(),
        {"roles": ["manuscript"], "authority": "MANUSCRIPT_OBSERVED"},
    )
    store = StoryStore(project.cache_path)
    ProjectIngestor(project, store).ingest()
    return root, project, store


def test_phase36_release_validation_accepts_nested_unicode_real_project_shape(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    report = ReleaseProjectValidator(project, store).report()
    assert report["all_green"] is True
    assert report["nested_source_count"] == 1
    assert report["project_specific_folder_semantics"] is False
    assert report["story_unit_count"] >= 1
    store.close()


def test_phase37_recovery_discards_partial_cache_and_rehydrates_durable_writer_state(tmp_path: Path):
    root, project, store = _project(tmp_path)
    durable_id = put_writer_claim(
        project,
        store,
        predicate="bell_is_guarded",
        literal_value=True,
        stable_key="phase37-durable",
    )
    token = begin_recovery_operation(project, "writer_mutation")
    assert token
    rogue_id = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="partial_cache_only",
        literal_value=True,
        status=AuthorityStatus.CONFIRMED_CANON,
        branch_id="mainline",
        created_by="writer",
        stable_key="phase37-rogue",
    )
    store.commit()
    store.close()
    assert recovery_status(StoryProject.open(root))["recovery_required"] is True
    recovered = recover_story_project(root, writer_confirmed=True)
    assert recovered["recovered"] is True
    reopened = StoryStore(StoryProject.open(root).cache_path)
    assert next(iter(reopened.rows("SELECT claim_id FROM claims WHERE claim_id=?", (durable_id,))), None) is not None
    assert next(iter(reopened.rows("SELECT claim_id FROM claims WHERE claim_id=?", (rogue_id,))), None) is None
    reopened.close()
    assert recovery_status(StoryProject.open(root))["recovery_required"] is False


def test_phase38_observability_is_local_bounded_and_content_free(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    record_story_event(project, "context_compile", outcome="PASS", duration_ms=12.4, counts={"sources": 7})
    report = observability_report(project)
    assert report["event_count"] == 1
    assert report["content_logged"] is False
    assert report["source_paths_logged"] is False
    assert report["telemetry_uploaded"] is False
    raw = (project.metadata_dir / "observability.json").read_text(encoding="utf-8")
    assert "Mara carries the bronze bell key" not in raw
    assert "Chapter α.md" not in raw
    store.close()


def test_phase40_resource_budget_is_cancellation_and_deadline_aware(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    now = [0.0]
    budget = StoryResourceBudget(maximum_records=2, maximum_characters=10, timeout_ms=10, clock=lambda: now[0])
    budget.checkpoint(records=1, characters=4)
    now[0] = 0.020
    with pytest.raises(StoryBudgetExceeded):
        budget.checkpoint()
    explored = StoryExplorer(project, store).explore("Mara bell", limit=20)
    assert explored["coverage"]["resource_budget"]["bounded"] is True
    assert explored["coverage"]["resource_budget"]["cancellation_aware"] is True
    store.close()


def test_phase41_unicode_and_cross_platform_path_resilience_is_explicit(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    report = path_resilience_report(project, store)
    assert report["portable"] is True
    collision = analyze_relative_paths(["Cafe\u0301/chapter.md", "Café/chapter.md"])
    assert collision["portable"] is False
    assert any(item["kind"] == "unicode_or_case_collision" for item in collision["problems"])
    reserved = analyze_relative_paths(["notes/CON.txt"])
    assert any(item["kind"] == "windows_reserved_name" for item in reserved["problems"])
    store.close()


def test_phase42_deterministic_story_queries_run_with_network_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _root, project, store = _project(tmp_path)

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("deterministic Story Engine attempted network access")

    monkeypatch.setattr(socket, "create_connection", blocked)
    query = StoryQueryEngine(project, store)
    result = invoke_story_tool(
        "get_project_understanding",
        {},
        query=query,
        context=ContextCompiler(project, store),
    )
    assert result["project_id"] == project.project_id
    assert offline_readiness(project)["deterministic_story_engine_requires_network"] is False
    local = certify_provider_offline(
        {"provider": "openai_compatible", "model": "local", "base_url": "http://127.0.0.1:8080/v1"}
    )
    assert local["offline_certified"] is True
    with pytest.raises(PermissionError):
        certify_provider_offline(
            {"provider": "openai", "model": "remote", "base_url": "https://api.openai.com/v1"}
        )
    store.close()


def test_phase44_future_state_schema_fails_closed(tmp_path: Path):
    root, project, store = _project(tmp_path)
    store.close()
    document = json.loads(project.state_path.read_text(encoding="utf-8"))
    document["version"] = STORY_STATE_SCHEMA_VERSION + 100
    project.state_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="newer unsupported"):
        StoryProject.open(root)


def test_phase44_writer_state_backup_and_restore_rolls_back_durable_truth(tmp_path: Path):
    root, project, store = _project(tmp_path)
    first = put_writer_claim(project, store, predicate="first_fact", literal_value=True, stable_key="phase44-first")
    backup = create_story_state_backup(root)
    second = put_writer_claim(project, store, predicate="second_fact", literal_value=True, stable_key="phase44-second")
    store.close()
    restored = restore_story_state_backup(root, backup["backup_name"], writer_confirmed=True)
    assert restored["restored"] is True
    reopened = StoryStore(StoryProject.open(root).cache_path)
    assert next(iter(reopened.rows("SELECT claim_id FROM claims WHERE claim_id=?", (first,))), None) is not None
    assert next(iter(reopened.rows("SELECT claim_id FROM claims WHERE claim_id=?", (second,))), None) is None
    reopened.close()


def test_phase45_release_candidate_engine_harness_is_green(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    report = ReleaseCandidateAcceptance(project, store).run()
    assert report["total_steps"] == 10
    assert report["passed_steps"] == 10
    assert report["all_green"] is True
    store.close()
