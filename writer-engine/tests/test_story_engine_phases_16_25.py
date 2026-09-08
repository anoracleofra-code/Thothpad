from __future__ import annotations

from pathlib import Path

import pytest

from backend.sidecar import dispatch
from backend.story.acceptance import WowAcceptance
from backend.story.arcs import ArcIntelligence
from backend.story.authority import KnowledgeStatus
from backend.story.branches import add_branch_overlay, create_branch
from backend.story.claims import ClaimEvidenceInput
from backend.story.continuity import ContinuityAuditor
from backend.story.ending_integrity import EndingIntegrity
from backend.story.exchange import export_story_bundle, import_story_bundle
from backend.story.health import ProjectHealth
from backend.story.ingest import ProjectIngestor
from backend.story.knowledge import set_character_knowledge
from backend.story.maintenance import index_status, rebuild_story_index
from backend.story.project import StoryProject
from backend.story.promises import upsert_promise_item
from backend.story.relationships import set_relationship_state
from backend.story.scene_semantics import SceneSemantics
from backend.story.store import StoryStore
from backend.story.story_explorer import StoryExplorer
from backend.story.threads import upsert_thread
from backend.story.writer_state import put_writer_claim, put_writer_entity, put_writer_preference


def _project(root: Path) -> tuple[StoryProject, StoryStore]:
    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    return project, store


def _rich_project(tmp_path: Path) -> tuple[Path, StoryProject, StoryStore, str, str, str]:
    root = tmp_path / "messy-story"
    root.mkdir()
    (root / "00-mara.md").write_text(
        "# Mara\nAppearance: scarred hand\nGoal: protect the bell\nRelationships: distrusts Iven\n",
        encoding="utf-8",
    )
    (root / "chapter.md").write_text(
        "# Chapter One\n\n## Scene One\nMara entered the bell tower. Iven watched her hide the brass key.\n\n"
        "## Scene Two\nMara returned at dawn and confronted Iven.\n",
        encoding="utf-8",
    )
    project = StoryProject.open(root)
    project.set_source_override(
        "00-mara.md",
        {"roles": ["character_reference"], "authority": "CONFIRMED_CANON"},
    )
    project.set_source_override(
        "chapter.md",
        {"roles": ["manuscript"], "authority": "MANUSCRIPT_OBSERVED"},
    )
    project.set_active_manuscripts(["chapter.md"])
    store = StoryStore(project.cache_path)
    ProjectIngestor(project, store).ingest()
    query_units = [
        dict(row)
        for row in store.rows(
            "SELECT * FROM story_units WHERE source_id=(SELECT source_id FROM sources WHERE relative_path='chapter.md') ORDER BY ordinal"
        )
    ]
    scene_one = next(row for row in query_units if row["display_title"] == "Scene One")
    scene_two = next(row for row in query_units if row["display_title"] == "Scene Two")
    mara = store.resolve_entities("Mara")[0]
    return (
        root,
        project,
        store,
        str(mara["entity_id"]),
        str(scene_one["story_unit_id"]),
        str(scene_two["story_unit_id"]),
    )


def test_phase16_story_explorer_returns_cross_state_without_semantic_overclaim(tmp_path: Path):
    _root, project, store, mara_id, scene_one, _scene_two = _rich_project(tmp_path)
    set_relationship_state(
        store,
        entity_a=mara_id,
        entity_b=mara_id,
        relationship_type="self_concept",
        state="guarded",
        valid_from=scene_one,
    )
    upsert_thread(store, title="Who wants the bell?", state="OPEN", opened_at=scene_one, thread_id="bell-thread")
    store.commit()
    result = StoryExplorer(project, store).explore("Mara bell", limit=50)
    assert any(node["kind"] == "entity" and node["label"] == "Mara" for node in result["nodes"])
    assert any(node["kind"] in {"claim", "thread", "evidence"} for node in result["nodes"])
    assert result["coverage"]["semantic_conclusion"] is False
    assert result["coverage"]["bounded"] is True
    store.close()


def test_phase17_scene_semantics_uses_exact_mentions_and_only_tracked_location(tmp_path: Path):
    _root, project, store, mara_id, scene_one, _scene_two = _rich_project(tmp_path)
    store.connection.execute(
        "INSERT INTO world_state(state_id,entity_id,state_type,value_json,valid_from,branch_id,status) VALUES(?,?,?,?,?,'mainline','CONFIRMED_CANON')",
        ("mara-location", mara_id, "location", '"Bell Tower"', scene_one),
    )
    store.commit()
    result = SceneSemantics(project, store).inspect(scene_one)
    assert [item["canonical_name"] for item in result["characters_present"]] == ["Mara"]
    assert result["tracked_locations"][0]["value"] == "Bell Tower"
    assert "Absence means untracked" in result["interpretation_limit"]
    store.close()


def test_phase18_continuity_audit_calls_missing_knowledge_a_candidate_not_a_leak(tmp_path: Path):
    _root, project, store, mara_id, scene_one, _scene_two = _rich_project(tmp_path)
    source = store.source_by_path("chapter.md")
    assert source is not None
    claim_id = put_writer_claim(
        project,
        store,
        subject_entity_id=mara_id,
        predicate="key_is_brass",
        literal_value=True,
        evidence=ClaimEvidenceInput(
            source_id=source["source_id"],
            story_unit_id=scene_one,
            start_offset=0,
            end_offset=10,
            quote="# Chapter ",
            source_hash=source["content_hash"],
        ),
        stable_key="continuity-risk-fact",
    )
    result = ContinuityAuditor(project, store).audit(scene_one, character="Mara")
    candidate = next(item for item in result["findings"] if item.get("claim_id") == claim_id)
    assert candidate["status"] == "UNTRACKED_ACCESS_CANDIDATE"
    assert "not proof" in candidate["interpretation_limit"]
    store.close()


def test_phase19_character_and_relationship_arcs_report_only_tracked_changes(tmp_path: Path):
    _root, project, store, mara_id, scene_one, scene_two = _rich_project(tmp_path)
    iven = put_writer_entity(project, store, canonical_name="Iven", entity_type="character")
    set_relationship_state(
        store,
        entity_a=mara_id,
        entity_b=iven["entity_id"],
        relationship_type="trust",
        state="low",
        valid_from=scene_one,
        relationship_id="trust-1",
    )
    set_relationship_state(
        store,
        entity_a=mara_id,
        entity_b=iven["entity_id"],
        relationship_type="trust",
        state="conditional",
        valid_from=scene_two,
        relationship_id="trust-2",
    )
    store.connection.execute(
        "INSERT INTO decisions(decision_id,agent_entity_id,story_unit_id,description,branch_id,status) VALUES(?,?,?,?,?,'CONFIRMED_CANON')",
        ("mara-decides", mara_id, scene_two, "Mara confronts Iven", "mainline"),
    )
    store.commit()
    arcs = ArcIntelligence(project, store)
    character = arcs.character_arc("Mara")
    relationship = arcs.relationship_arc("Mara", "Iven")
    assert any(moment["kind"] == "decision" for moment in character["moments"])
    assert len(relationship["transitions"]) == 1
    assert "does not infer psychology" in character["interpretation_limit"]
    store.close()


def test_phase20_ending_integrity_keeps_backpropagation_diagnostic(tmp_path: Path):
    _root, project, store, _mara_id, scene_one, scene_two = _rich_project(tmp_path)
    upsert_thread(store, title="The bell's origin", state="OPEN", opened_at=scene_one, thread_id="origin-thread")
    upsert_promise_item(
        store,
        item_type="READER_QUESTION",
        title="Who forged the bell?",
        state="OPEN",
        opened_at=scene_one,
        item_id="bell-question",
    )
    store.commit()
    report = EndingIntegrity(project, store).audit(scene_two)
    assert report["open_threads"][0]["thread_id"] == "origin-thread"
    assert any(item["status"] == "DIAGNOSTIC" for item in report["findings"])
    assert "do not become canon" in report["backpropagation_policy"]
    store.close()


def test_phase21_expanded_adapters_ingest_fountain_html_and_rtf(tmp_path: Path):
    root = tmp_path / "formats"
    root.mkdir()
    (root / "script.fountain").write_text("INT. BELL TOWER - NIGHT\nMara enters.\n", encoding="utf-8")
    (root / "notes.html").write_text("<h1>Bell Lore</h1><p>The bell is bronze.</p>", encoding="utf-8")
    (root / "old.rtf").write_bytes(b"{\\rtf1\\ansi Old note\\par The bell rang.}")
    project, store = _project(root)
    summary = ProjectIngestor(project, store).ingest()
    assert summary.readable_documents == 3
    formats = {row["format"] for row in store.list_sources()}
    assert {"fountain", "html", "rtf"} <= formats
    assert any("Bell Lore" in row["text"] for row in store.rows("SELECT text FROM source_chunks"))
    store.close()


def test_phase22_project_health_exposes_metrics_not_a_quality_score(tmp_path: Path):
    _root, project, store, _mara_id, _scene_one, _scene_two = _rich_project(tmp_path)
    report = ProjectHealth(project, store).report()
    assert report["all_hard_gates_pass"] is True
    assert report["gates"]["branch_contamination_zero"] is True
    assert "score" not in report
    assert "not a Story Engine quality score" in report["interpretation_limit"]
    store.close()


def test_phase23_index_rebuild_preserves_writer_state_and_reports_integrity(tmp_path: Path):
    root, project, store, mara_id, _scene_one, _scene_two = _rich_project(tmp_path)
    put_writer_preference(
        project,
        store,
        statement="Prefer concrete sensory detail in action scenes",
        scope_kind="project",
        scope_id="",
        status="CONFIRMED",
        confidence=1.0,
        evidence=[],
        preference_id="durable-pref-16-25",
    )
    before = index_status(project, store)
    store.close()
    assert before["cache_disposable"] is True
    rebuilt = rebuild_story_index(root, writer_confirmed=True)
    assert rebuilt["rebuilt"] is True
    reopened = StoryStore(StoryProject.open(root).cache_path)
    assert next(
        iter(reopened.rows("SELECT preference_id FROM writer_preferences WHERE preference_id='durable-pref-16-25'"))
    )["preference_id"]
    assert reopened.resolve_entities("Mara")[0]["entity_id"] == mara_id
    reopened.close()


def test_phase24_portable_exchange_contains_no_source_text_or_absolute_paths_and_rebinds(tmp_path: Path):
    root, project, store, _mara_id, _scene_one, _scene_two = _rich_project(tmp_path)
    put_writer_preference(
        project,
        store,
        statement="Keep dialogue terse",
        scope_kind="project",
        scope_id="",
        status="CONFIRMED",
        confidence=1.0,
        evidence=[],
        preference_id="portable-pref",
    )
    bundle = export_story_bundle(project, store)
    store.close()
    assert bundle["contains_source_text"] is False
    assert bundle["contains_absolute_paths"] is False
    serialized = repr(bundle)
    assert str(root.resolve()) not in serialized

    target = tmp_path / "target"
    target.mkdir()
    (target / "00-mara.md").write_text((root / "00-mara.md").read_text(encoding="utf-8"), encoding="utf-8")
    (target / "chapter.md").write_text((root / "chapter.md").read_text(encoding="utf-8"), encoding="utf-8")
    target_project = StoryProject.open(target)
    target_store = StoryStore(target_project.cache_path)
    ProjectIngestor(target_project, target_store).ingest()
    target_store.close()
    result = import_story_bundle(target, bundle, writer_confirmed=True)
    assert result["imported"] is True
    reopened = StoryStore(StoryProject.open(target).cache_path)
    assert (
        next(iter(reopened.rows("SELECT statement FROM writer_preferences WHERE preference_id='portable-pref'")))[
            "statement"
        ]
        == "Keep dialogue terse"
    )
    reopened.close()


def test_phase25_wow_acceptance_runs_all_ten_steps_green(tmp_path: Path):
    _root, project, store, mara_id, scene_one, _scene_two = _rich_project(tmp_path)
    claim_id = put_writer_claim(
        project,
        store,
        subject_entity_id=mara_id,
        predicate="suspects_iven",
        literal_value=True,
        stable_key="wow-suspicion",
    )
    set_character_knowledge(
        store,
        character_id=mara_id,
        claim_id=claim_id,
        state=KnowledgeStatus.SUSPECTS,
        acquired_at=scene_one,
    )
    upsert_promise_item(
        store,
        item_type="READER_QUESTION",
        title="Will Mara accuse Iven?",
        state="OPEN",
        opened_at=scene_one,
        item_id="wow-question",
    )
    create_branch(store, fork_story_unit=scene_one, assumptions=["Mara accuses Iven"], branch_id="ALT-WOW")
    add_branch_overlay(
        store,
        branch_id="ALT-WOW",
        record_kind="claim",
        record_id="wow-accusation",
        operation="ADD",
        payload={"predicate": "accuses", "literal_value": True, "status": "CONFIRMED_CANON"},
    )
    store.add_dependency("claim", claim_id, "promise_item", "wow-question", "supports")
    store.commit()
    report = WowAcceptance(project, store).run(story_unit_id=scene_one, character="Mara")
    assert report["total_steps"] == 10
    assert report["passed_steps"] == 10
    assert report["all_green"] is True
    assert report["project_health"]["all_hard_gates_pass"] is True
    store.close()


def test_phase23_24_sidecar_operations_require_writer_confirmation(tmp_path: Path):
    root, _project_obj, store, _mara_id, _scene_one, _scene_two = _rich_project(tmp_path)
    store.close()
    base = {
        "protocol_major": 1,
        "protocol_minor": 7,
        "request_id": "phase23",
        "params": {"project_root": str(root)},
    }
    with pytest.raises(PermissionError):
        dispatch({**base, "operation": "story_index_rebuild"})
    exported = dispatch({**base, "request_id": "phase24-export", "operation": "story_project_export"})
    assert exported["contains_source_text"] is False
    with pytest.raises(PermissionError):
        dispatch(
            {
                **base,
                "request_id": "phase24-import",
                "operation": "story_project_import",
                "params": {"project_root": str(root), "bundle": exported},
            }
        )
