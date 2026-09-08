from __future__ import annotations

from pathlib import Path

from backend.story.ingest import ProjectIngestor
from backend.story.interface_fingerprint import interface_fingerprint
from backend.story.performance_budget import performance_budget_report
from backend.story.project import StoryProject
from backend.story.quality_gates import aggregate_quality_gates
from backend.story.release_readiness import ReleaseReadiness
from backend.story.relocation import relocation_readiness
from backend.story.soak import run_soak_replay
from backend.story.store import StoryStore
from backend.story.support_bundle import build_support_bundle
from backend.story.tools import story_tool_manifest


def _project(tmp_path: Path, name: str = "field-book") -> tuple[Path, StoryProject, StoryStore]:
    root = tmp_path / name
    root.mkdir()
    (root / "chapter.md").write_text(
        "# Chapter One\n\nMara carries the bronze bell.\n\n# Chapter Two\n\nIven hears it ring.\n",
        encoding="utf-8",
    )
    project = StoryProject.open(root)
    project.set_source_override(
        "chapter.md",
        {"roles": ["manuscript"], "authority": "MANUSCRIPT_OBSERVED"},
    )
    store = StoryStore(project.cache_path)
    ProjectIngestor(project, store).ingest()
    return root, project, store


def test_phase46_soak_replay_keeps_semantic_and_durable_state_stable(tmp_path: Path):
    root, _project_obj, store = _project(tmp_path)
    store.close()
    result = run_soak_replay(root, cycles=3)
    assert result["semantic_fingerprint_stable"] is True
    assert result["durable_writer_state_unchanged"] is True
    assert result["all_cycles_green"] is True
    assert result["source_files_rewritten"] is False


def test_phase47_support_bundle_is_content_and_path_free(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    bundle = build_support_bundle(project, store)
    assert bundle["privacy"] == {
        "contains_manuscript_text": False,
        "contains_prompts": False,
        "contains_source_paths": False,
        "contains_absolute_paths": False,
        "contains_credentials": False,
        "telemetry_upload_required": False,
    }
    raw = str(bundle)
    assert "Mara carries the bronze bell" not in raw
    assert "chapter.md" not in raw
    assert str(project.root) not in raw
    store.close()


def test_phase48_interface_fingerprint_changes_when_contract_changes():
    manifest = story_tool_manifest()
    original = interface_fingerprint(manifest)
    changed = interface_fingerprint([*manifest, {"id": "future_tool", "risk": "R0"}])
    assert len(original["fingerprint"]) == 64
    assert original["fingerprint"] != changed["fingerprint"]
    assert original["protocol"]["major"] == 1


def test_phase50_performance_budget_is_explicit_and_index_backed(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    result = performance_budget_report(project, store, lookup_100_budget_ms=10_000)
    assert result["all_green"] is True
    assert result["gates"]["core_queries_indexed"] is True
    assert result["gates"]["filesystem_scans_zero"] is True
    assert result["budget_is_machine_reference_not_story_quality"] is True
    store.close()


def test_phase51_relocation_bundle_contains_no_machine_paths(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    result = relocation_readiness(project, store)
    assert result["all_green"] is True
    assert all(result["gates"].values())
    assert result["relocation_requires_source_files_to_exist_at_destination"] is True
    store.close()


def test_phase52_quality_gate_aggregator_never_hides_required_failure():
    result = aggregate_quality_gates(
        {
            "architecture": True,
            "privacy": {"passed": False, "required": True, "evidence": "review-17"},
            "optional_field_trial": {"passed": False, "required": False},
        }
    )
    assert result["all_required_green"] is False
    assert result["failed_required"] == ["privacy"]
    assert result["required_passed"] == 1


def test_phase55_release_readiness_keeps_external_platform_actions_explicit(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    report = ReleaseReadiness(project, store, story_tool_manifest()).report()
    assert report["story_engine_release_hardening_green"] is True
    assert report["production_release_ready"] is False
    assert "platform_release_actions" in report["external_pending"]

    fully_attested = ReleaseReadiness(project, store, story_tool_manifest()).report(
        external_evidence={
            "network_capture": True,
            "quality_gate_reviews": True,
            "clean_install_contract": True,
            "update_manifest": True,
            "platform_release_actions": True,
        }
    )
    assert fully_attested["production_release_ready"] is True
    assert fully_attested["external_pending"] == []
    store.close()
