from __future__ import annotations

import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from backend.story.authority import AuthorityStatus, KnowledgeStatus, SourceRole
from backend.story.branches import (
    add_branch_overlay,
    branch_comparison,
    branch_freshness,
    create_branch,
    prepare_branch_merge,
    rebase_branch,
    record_completed_branch_merge,
)
from backend.story.causality import add_causal_edge, add_decision, add_opposition, trace_causality
from backend.story.claims import ClaimEvidenceInput, create_claim, detect_claim_conflicts
from backend.story.context import ContextCompiler, EpistemicMode
from backend.story.ingest import ProjectIngestor
from backend.story.knowledge import set_character_knowledge
from backend.story.lenses import put_story_lens, run_story_lens, story_lens
from backend.story.model_routing import classify_story_task, route_story_model
from backend.story.persistence import persist_writer_state
from backend.story.project import StoryProject
from backend.story.promises import list_promise_items, upsert_promise_item
from backend.story.query import StoryQueryEngine
from backend.story.reader import set_reader_state
from backend.story.reader_experience import reader_experience_timeline
from backend.story.relationships import set_relationship_state
from backend.story.retcons import retcon_impact
from backend.story.service import (
    apply_story_writer_mutation,
    project_sources,
    project_understanding,
    set_manuscript_order,
    set_source_override,
)
from backend.story.store import StoryStore
from backend.story.threads import list_threads, upsert_thread
from backend.story.timeline import add_timeline_event, set_world_state, where_is_entity
from backend.story.writer_model import observe_writer_activity, writer_model
from backend.story.writer_state import (
    add_writer_branch_overlay,
    create_writer_branch,
    put_author_decision,
    put_writer_preference,
    set_scene_contract,
)


def _open(root: Path) -> tuple[StoryProject, StoryStore]:
    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    return project, store


def _ingest(root: Path) -> tuple[StoryProject, StoryStore]:
    project, store = _open(root)
    ProjectIngestor(project, store).ingest()
    return project, store


def _write_docx(path: Path, paragraphs: list[tuple[str, str | None]]) -> None:
    body = []
    for text, style in paragraphs:
        style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body.append(f"<w:p>{style_xml}<w:r><w:t>{text}</w:t></w:r></w:p>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def test_generic_project_is_folder_agnostic_and_persistent(tmp_path: Path):
    root = tmp_path / "utterly-weird-layout"
    root.mkdir()
    (root / "x7.md").write_text(
        "# Chapter One\n\nMara crossed the glass desert. \"Keep walking,\" Tomas said.\n",
        encoding="utf-8",
    )
    odd = root / "banana"
    odd.mkdir()
    (odd / "Mara profile.md").write_text(
        "# Mara\n\nAppearance: scarred left hand\nGoal: reach the western gate\nRelationships: trusts Tomas\n",
        encoding="utf-8",
    )

    project, store = _ingest(root)
    first_id = project.project_id
    understanding = StoryQueryEngine(project, store).get_project_understanding()
    assert understanding["source_count"] == 2
    assert understanding["role_counts"][SourceRole.MANUSCRIPT] == 1
    assert understanding["role_counts"][SourceRole.CHARACTER_REFERENCE] == 1
    store.close()

    reopened, store = _ingest(root)
    assert reopened.project_id == first_id
    assert reopened.manifest_path.exists()
    assert reopened.cache_path.exists()
    store.close()


def test_folder_name_is_never_semantic_authority(tmp_path: Path):
    root = tmp_path / "novel"
    misleading = root / "Characters"
    misleading.mkdir(parents=True)
    (misleading / "train-schedule.txt").write_text(
        "Research source. Timetable and platform reference for a real railway.", encoding="utf-8"
    )
    project, store = _ingest(root)
    row = store.source_by_path("Characters/train-schedule.txt")
    assert row is not None
    roles = {item["role"] for item in store.source_roles(row["source_id"])}
    assert SourceRole.CHARACTER_REFERENCE not in roles
    assert row["authority_default"] != AuthorityStatus.CONFIRMED_CANON
    store.close()


def test_writer_source_override_beats_classifier(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    source = root / "mystery.txt"
    source.write_text("Loose scraps and unresolved possibilities.", encoding="utf-8")
    project, store = _ingest(root)
    project.set_source_override(
        "mystery.txt",
        {"roles": [SourceRole.CHARACTER_REFERENCE], "authority": AuthorityStatus.CONFIRMED_CANON},
    )
    ProjectIngestor(project, store).ingest()
    row = store.source_by_path("mystery.txt")
    assert row is not None
    assert row["authority_default"] == AuthorityStatus.CONFIRMED_CANON
    assert store.source_roles(row["source_id"])[0]["role"] == SourceRole.CHARACTER_REFERENCE
    assert store.source_roles(row["source_id"])[0]["user_confirmed"] == 1
    store.close()


def test_story_service_source_override_recompiles_unchanged_source(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    source = root / "anything.txt"
    source.write_text("Loose unresolved ideas.", encoding="utf-8")
    project, store = _ingest(root)
    before = store.source_by_path("anything.txt")
    assert before is not None
    before_hash = before["content_hash"]
    store.close()

    result = set_source_override(
        root,
        "anything.txt",
        roles=[SourceRole.WORLD_REFERENCE],
        authority=AuthorityStatus.CONFIRMED_CANON,
    )
    assert result["authority"] == AuthorityStatus.CONFIRMED_CANON
    assert result["roles"][0]["role"] == SourceRole.WORLD_REFERENCE

    project = StoryProject.open(root)
    with StoryStore(project.cache_path) as reopened:
        after = reopened.source_by_path("anything.txt")
        assert after is not None
        assert after["content_hash"] == before_hash
        assert after["authority_default"] == AuthorityStatus.CONFIRMED_CANON
        assert reopened.source_roles(after["source_id"])[0]["user_confirmed"] == 1


def test_story_service_rejects_source_override_traversal(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "notes.txt").write_text("Notes.", encoding="utf-8")
    _project, store = _ingest(root)
    store.close()
    with pytest.raises(ValueError, match="project-relative"):
        set_source_override(root, "../outside.txt", roles=[SourceRole.RESEARCH])


def test_story_service_sources_are_bounded_and_project_relative(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    for index in range(4):
        (root / f"note-{index}.txt").write_text(f"Research source {index}.", encoding="utf-8")
    _project, store = _ingest(root)
    store.close()
    page = project_sources(root, offset=1, limit=2)
    assert len(page["sources"]) == 2
    assert page["offset"] == 1
    assert page["next_offset"] == 3
    assert all(not Path(item["path"]).is_absolute() for item in page["sources"])


def test_story_service_persists_writer_owned_manuscript_order(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "second.md").write_text("# Chapter Two\nMara returns.\n", encoding="utf-8")
    (root / "first.md").write_text("# Chapter One\nMara leaves.\n", encoding="utf-8")
    (root / "research.txt").write_text("Research source about roads.", encoding="utf-8")
    _project, store = _ingest(root)
    store.close()

    result = set_manuscript_order(root, ["first.md", "second.md"])
    assert result["writer_owned"] is True
    assert result["active_manuscripts"] == ["first.md", "second.md"]
    assert StoryProject.open(root).active_manuscripts() == ["first.md", "second.md"]
    assert project_understanding(root)["active_manuscripts"] == ["first.md", "second.md"]
    assert project_sources(root, role="manuscript")["active_manuscripts"] == ["first.md", "second.md"]

    with pytest.raises(ValueError, match="not classified as manuscript"):
        set_manuscript_order(root, ["research.txt"])
    with pytest.raises(ValueError, match="duplicate"):
        set_manuscript_order(root, ["first.md", "first.md"])
    with pytest.raises(ValueError, match="project-relative"):
        set_manuscript_order(root, ["../outside.md"])


def test_symlink_escape_and_private_metadata_are_not_ingested(tmp_path: Path):
    root = tmp_path / "novel"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nMara waits.", encoding="utf-8")
    private = root / ".thothpad"
    private.mkdir()
    (private / "secret.md").write_text("private machine state", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside secret", encoding="utf-8")
    link = root / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pass

    _project, store = _ingest(root)
    paths = {row["relative_path"] for row in store.list_sources()}
    assert "chapter.md" in paths
    assert all(not path.startswith(".thothpad/") for path in paths)
    assert "linked.md" not in paths
    store.close()


def test_story_unit_identity_survives_file_move(tmp_path: Path):
    root = tmp_path / "novel"
    root.mkdir()
    original = root / "draft.md"
    original.write_text(
        "# Chapter One\n\nMara crosses the glass desert and hears a bell beyond the ridge.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    first_units = StoryQueryEngine(project, store).list_story_units()
    assert len(first_units) == 1
    unit_id = first_units[0]["story_unit_id"]

    moved_dir = root / "whatever"
    moved_dir.mkdir()
    moved = moved_dir / "renamed-anything.md"
    original.rename(moved)
    ProjectIngestor(project, store).ingest()
    second_units = StoryQueryEngine(project, store).list_story_units()
    assert len(second_units) == 1
    assert second_units[0]["story_unit_id"] == unit_id
    assert second_units[0]["source_id"] != first_units[0]["source_id"]
    store.close()


def test_story_unit_identity_survives_file_move_and_cache_rebuild(tmp_path: Path):
    root = tmp_path / "novel"
    root.mkdir()
    original = root / "draft.md"
    original.write_text(
        "# Chapter One\n\nMara crosses the glass desert and hears a bell beyond the ridge.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    first_unit = StoryQueryEngine(project, store).list_story_units()[0]
    unit_id = first_unit["story_unit_id"]
    store.close()

    moved_dir = root / "whatever"
    moved_dir.mkdir()
    original.rename(moved_dir / "renamed-anything.md")
    project.cache_path.unlink()

    rebuilt_project, rebuilt_store = _ingest(root)
    rebuilt_unit = StoryQueryEngine(rebuilt_project, rebuilt_store).list_story_units()[0]
    assert rebuilt_unit["story_unit_id"] == unit_id
    assert rebuilt_unit["source_id"] != first_unit["source_id"]
    rebuilt_store.close()


def test_character_profile_compiles_claims_with_exact_evidence(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    path = root / "Alice profile.md"
    path.write_text(
        "# Alice\n\nAppearance: silver braid\nGoal: protect the archive\nRelationships: wary of Tomas\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    resolved = query.resolve_entity("Alice")
    assert resolved["resolution"] == "exact"
    claims = query.query_claims(entity="Alice")
    predicates = {claim["predicate"] for claim in claims}
    assert {"appearance", "goal", "relationships"} <= predicates
    goal = next(claim for claim in claims if claim["predicate"] == "goal")
    assert goal["literal_value"] == "protect the archive"
    assert goal["evidence"]
    evidence = goal["evidence"][0]
    assert evidence["source_hash"] == evidence["current_source_hash"]
    assert evidence["stale"] == 0
    assert evidence["relative_path"] == "Alice profile.md"
    store.close()


def test_explicit_character_state_compiles_without_inference(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    profile = root / "Alice profile.md"
    profile.write_text(
        "# Alice\n"
        "Appearance: tired\n"
        "Goal: survive\n"
        "Relationships: trusts Mara\n"
        "Knows: the bell was moved\n"
        "Believes: Mara moved the bell\n"
        "Location: East Gate\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)

    knowledge = query.character_knowledge("Alice")
    states = {(row["state"], row["literal_value"]) for row in knowledge}
    assert (KnowledgeStatus.KNOWS, "the bell was moved") in states
    assert (KnowledgeStatus.BELIEVES, "Mara moved the bell") in states
    beliefs = query.character_beliefs("Alice")
    assert {(row["state"], row["literal_value"]) for row in beliefs} == {
        (KnowledgeStatus.BELIEVES, "Mara moved the bell")
    }
    assert query.world_state("Alice", state_type="location")[0]["value"] == "East Gate"

    belief_claim = next(
        claim for claim in query.query_claims(entity="Alice")
        if claim["predicate"] == "believes"
    )
    assert belief_claim["literal_value"] == "Mara moved the bell"
    assert belief_claim["evidence"][0]["stale"] == 0
    # The compiler records the explicit statement "Alice believes X". It does
    # not fabricate a separate objective claim that X itself is true.
    assert not any(
        claim["predicate"] == "mara_moved_the_bell"
        for claim in query.query_claims(entity="Alice")
    )
    store.close()


def test_free_prose_does_not_create_character_knowledge_state(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "Alice profile.md").write_text(
        "# Alice\n"
        "Appearance: tired\n"
        "Goal: survive\n"
        "Relationships: trusts Mara\n\n"
        "Alice knows the bell was moved and believes Mara did it.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    assert StoryQueryEngine(project, store).character_knowledge("Alice") == []
    store.close()


def test_removed_explicit_state_retires_compiler_derivative_but_preserves_promoted_claim(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    profile = root / "Alice profile.md"
    profile.write_text(
        "# Alice\nAppearance: tired\nGoal: survive\nRelationships: trusts Mara\n"
        "Knows: the bell was moved\nLocation: East Gate\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    knows_claim = next(
        claim for claim in query.query_claims(entity="Alice")
        if claim["predicate"] == "knows"
    )
    store.promote_claim(knows_claim["claim_id"], AuthorityStatus.CONFIRMED_CANON, approved_by="writer")
    store.commit()

    profile.write_text(
        "# Alice\nAppearance: tired\nGoal: survive\nRelationships: trusts Mara\n",
        encoding="utf-8",
    )
    ProjectIngestor(project, store).ingest()

    assert query.character_knowledge("Alice") == []
    assert query.world_state("Alice", state_type="location") == []
    retained = next(
        claim for claim in query.query_claims(entity="Alice")
        if claim["claim_id"] == knows_claim["claim_id"]
    )
    assert retained["status"] == AuthorityStatus.CONFIRMED_CANON
    assert retained["created_by"] == "writer"
    assert retained["evidence"] and all(item["stale"] == 1 for item in retained["evidence"])
    store.close()


def test_explicit_outline_architecture_compiles_promises_and_threads_with_provenance(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    outline = root / "outline.md"
    outline.write_text(
        "# Outline\n"
        "Dramatic Promise: the bell will ring before dawn\n"
        "Reader Question: who moved the bell?\n"
        "Plot Thread: Mara investigates the bell\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)

    promises = list_promise_items(store)
    assert {(item["item_type"], item["title"]) for item in promises} == {
        ("DRAMATIC_PROMISE", "the bell will ring before dawn"),
        ("READER_QUESTION", "who moved the bell?"),
    }
    assert all(item["evidence_claim_id"] for item in promises)
    assert all(item["metadata"]["authority_status"] == AuthorityStatus.AUTHOR_INTENT for item in promises)
    threads = list_threads(store)
    assert [(item["state"], item["title"]) for item in threads] == [
        ("OPEN", "Mara investigates the bell")
    ]
    evidence_claim = threads[0]["metadata"]["evidence_claim_id"]
    grounded = store.claim_with_evidence(evidence_claim)
    assert grounded is not None
    assert grounded["claim"]["status"] == AuthorityStatus.AUTHOR_INTENT
    assert grounded["evidence"][0]["stale"] == 0

    outline.write_text("# Outline\nNo active promises are recorded here.\n", encoding="utf-8")
    ProjectIngestor(project, store).ingest()
    assert list_promise_items(store) == []
    assert list_threads(store) == []
    store.close()


def test_edited_profile_fact_supersedes_obsolete_compiler_claim(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    profile = root / "Alice profile.md"
    profile.write_text(
        "# Alice\nAppearance: tall\nGoal: leave the city\nRelationships: trusts Mara\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    old_goal = next(claim for claim in query.query_claims(entity="Alice") if claim["predicate"] == "goal")

    profile.write_text(
        "# Alice\nAppearance: tall\nGoal: remain in the city\nRelationships: trusts Mara\n",
        encoding="utf-8",
    )
    ProjectIngestor(project, store).ingest()
    goals = [claim for claim in query.query_claims(entity="Alice") if claim["predicate"] == "goal"]
    active = [claim for claim in goals if claim["status"] != AuthorityStatus.SUPERSEDED]
    obsolete = next(claim for claim in goals if claim["claim_id"] == old_goal["claim_id"])

    assert len(active) == 1
    assert active[0]["literal_value"] == "remain in the city"
    assert active[0]["claim_id"] != old_goal["claim_id"]
    assert obsolete["status"] == AuthorityStatus.SUPERSEDED
    assert obsolete["evidence"] and all(item["stale"] == 1 for item in obsolete["evidence"])
    assert not list(store.rows("SELECT * FROM story_conflicts WHERE type='claim_value' AND status='OPEN'"))
    store.close()


def test_moved_profile_fact_keeps_stable_claim_identity_and_refreshes_evidence(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    profile = root / "Alice profile.md"
    profile.write_text(
        "# Alice\nAppearance: tall\nGoal: protect the archive\nRelationships: trusts Mara\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    before = next(claim for claim in query.query_claims(entity="Alice") if claim["predicate"] == "goal")

    profile.write_text(
        "# Alice\nAppearance: tall\nVoice: quiet\nGoal: protect the archive\nRelationships: trusts Mara\n",
        encoding="utf-8",
    )
    ProjectIngestor(project, store).ingest()
    after = next(
        claim
        for claim in query.query_claims(entity="Alice")
        if claim["predicate"] == "goal" and claim["status"] != AuthorityStatus.SUPERSEDED
    )

    assert after["claim_id"] == before["claim_id"]
    assert any(item["stale"] == 0 for item in after["evidence"])
    assert any(item["stale"] == 1 for item in after["evidence"])
    store.close()


def test_writer_promoted_claim_survives_source_change_and_becomes_explicit_conflict(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    profile = root / "Alice profile.md"
    profile.write_text(
        "# Alice\nAppearance: tall\nGoal: leave the city\nRelationships: trusts Mara\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    original = next(claim for claim in query.query_claims(entity="Alice") if claim["predicate"] == "goal")
    store.promote_claim(original["claim_id"], AuthorityStatus.CONFIRMED_CANON, approved_by="writer")
    store.commit()

    profile.write_text(
        "# Alice\nAppearance: tall\nGoal: remain in the city\nRelationships: trusts Mara\n",
        encoding="utf-8",
    )
    ProjectIngestor(project, store).ingest()
    goals = [claim for claim in query.query_claims(entity="Alice") if claim["predicate"] == "goal"]
    promoted = next(claim for claim in goals if claim["claim_id"] == original["claim_id"])
    current_compiled = next(claim for claim in goals if claim["literal_value"] == "remain in the city")

    assert promoted["status"] == AuthorityStatus.CONFIRMED_CANON
    assert promoted["created_by"] == "writer"
    assert promoted["evidence"] and all(item["stale"] == 1 for item in promoted["evidence"])
    assert current_compiled["created_by"] == "compiler"
    assert list(store.rows("SELECT * FROM story_conflicts WHERE type='claim_value' AND status='OPEN'"))
    store.close()


def test_authority_override_recompiles_same_explicit_fact_without_new_identity(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    profile = root / "Alice profile.md"
    profile.write_text(
        "# Alice\nAppearance: tall\nGoal: protect the archive\nRelationships: trusts Mara\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    before = next(claim for claim in query.query_claims(entity="Alice") if claim["predicate"] == "goal")
    assert before["status"] == AuthorityStatus.PROVISIONAL

    project.set_source_override(
        "Alice profile.md",
        {"roles": [SourceRole.CHARACTER_REFERENCE], "authority": AuthorityStatus.CONFIRMED_CANON},
    )
    ProjectIngestor(project, store).ingest()
    after = next(claim for claim in query.query_claims(entity="Alice") if claim["predicate"] == "goal")

    assert after["claim_id"] == before["claim_id"]
    assert after["status"] == AuthorityStatus.COMPILED_CANON
    assert after["evidence"] and all(item["stale"] == 0 for item in after["evidence"])
    store.close()


def test_model_cannot_promote_its_own_claim(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "notes.txt").write_text("notes", encoding="utf-8")
    project, store = _ingest(root)
    with pytest.raises(PermissionError):
        store.add_claim(
            {
                "claim_id": "bad",
                "project_id": project.project_id,
                "predicate": "secret",
                "literal_value": True,
                "status": AuthorityStatus.CONFIRMED_CANON,
                "created_by": "model",
            }
        )
    create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="hypothesis",
        literal_value="maybe",
        status=AuthorityStatus.INFERENCE,
        created_by="model",
    )
    store.commit()
    assert store.query_claims()[0]["status"] == AuthorityStatus.INFERENCE
    store.close()


def test_conflicting_claims_become_explicit_conflict(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "Alice profile.md").write_text(
        "# Alice\nAppearance: tall\nGoal: leave the city\nRelationships: none\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    entity_id = StoryQueryEngine(project, store).resolve_entity("Alice")["matches"][0]["entity_id"]
    create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=entity_id,
        predicate="goal",
        literal_value="remain in the city",
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="writer-conflicting-goal",
    )
    conflicts = detect_claim_conflicts(store, subject_entity_id=entity_id)
    assert conflicts
    row = next(iter(store.rows("SELECT * FROM story_conflicts WHERE conflict_id=?", (conflicts[0],))))
    assert row["status"] == "OPEN"
    store.close()


def test_context_compiler_is_inspectable_and_epistemically_masked(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text(
        "# Chapter One\nAlice enters Harbor City and hides the brass key.", encoding="utf-8"
    )
    (root / "world.md").write_text(
        "# Geography\n## Government\nHarbor City is ruled by the Salt Council.\n## History\nThe council predates Alice.",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    compiler = ContextCompiler(project, store)

    author = compiler.compile(prompt="What does Alice know about Harbor City?", mode=EpistemicMode.AUTHOR_OMNISCIENT)
    assert {item.path for item in author.items} >= {"chapter.md", "world.md"}
    inspector = author.inspector()
    assert inspector["included"]
    assert inspector["budget"]["used_chars"] <= inspector["budget"]["maximum_chars"]

    cold = compiler.compile(prompt="What does Alice know about Harbor City?", mode=EpistemicMode.COLD_READER)
    assert all(SourceRole.MANUSCRIPT in item.roles for item in cold.items)
    assert "world.md" not in {item.path for item in cold.items}
    assert any(item["path"] == "world.md" for item in cold.excluded)
    store.close()


def test_knowledge_reader_and_world_state_remain_distinct(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "Alice profile.md").write_text(
        "# Alice\nAppearance: tired\nGoal: survive\nRelationships: trusts nobody\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    entity_id = query.resolve_entity("Alice")["matches"][0]["entity_id"]
    claim_id = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=entity_id,
        predicate="door_code",
        literal_value="7319",
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="door-code",
    )
    set_character_knowledge(
        store,
        character_id=entity_id,
        claim_id=claim_id,
        state=KnowledgeStatus.SUSPECTS,
        acquired_at="SC-1",
    )
    set_reader_state(store, claim_id=claim_id, state="KNOWS", story_unit_id=None)
    set_world_state(store, entity_id=entity_id, state_type="location", value="Harbor City")
    store.commit()

    assert query.character_knowledge("Alice")[0]["state"] == KnowledgeStatus.SUSPECTS
    assert query.reader_state()[0]["state"] == "KNOWS"
    assert where_is_entity(store, entity_id)[0]["value"] == "Harbor City"
    store.close()


def test_context_model_state_keeps_objective_fact_and_character_belief_distinct(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "Alice profile.md").write_text(
        "# Alice\nAppearance: tired\nGoal: survive\nRelationships: trusts nobody\n",
        encoding="utf-8",
    )
    (root / "chapter.md").write_text("# Chapter One\nAlice waits at the locked door.\n", encoding="utf-8")
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    alice_id = query.resolve_entity("Alice")["matches"][0]["entity_id"]
    claim_id = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=alice_id,
        predicate="door_code",
        literal_value="7319",
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="alice-door-code",
    )
    set_character_knowledge(
        store,
        character_id=alice_id,
        claim_id=claim_id,
        state=KnowledgeStatus.SUSPECTS,
        acquired_at=None,
    )
    store.commit()

    compiled = ContextCompiler(project, store).compile(
        prompt="What does Alice know about the door code?",
        mode=EpistemicMode.AUTHOR_OMNISCIENT,
        active_character="Alice",
    )
    state = compiled.model_state()
    objective = next(item for item in state["objective_claims"] if item["claim_id"] == claim_id)
    belief = next(item for item in state["character_knowledge"] if item["claim"]["claim_id"] == claim_id)
    assert objective["value"] == "7319"
    assert objective["status"] == AuthorityStatus.CONFIRMED_CANON
    assert belief["state"] == KnowledgeStatus.SUSPECTS
    assert belief["claim"]["value"] == "7319"
    store.close()


def test_character_context_excludes_knowledge_acquired_after_active_story_position(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "Alice profile.md").write_text(
        "# Alice\nAppearance: tired\nGoal: survive\nRelationships: trusts nobody\n",
        encoding="utf-8",
    )
    (root / "book.md").write_text(
        "# Chapter One\nAlice reaches the locked door.\n\n# Chapter Two\nAlice learns the vault code.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    alice_id = query.resolve_entity("Alice")["matches"][0]["entity_id"]
    manuscript = store.source_by_path("book.md")
    assert manuscript is not None
    units = query.list_story_units(source_id=manuscript["source_id"])
    assert len(units) == 2
    early_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=alice_id,
        predicate="has_key",
        literal_value=True,
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="alice-has-key",
    )
    future_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=alice_id,
        predicate="vault_code",
        literal_value="7319",
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="alice-vault-code",
    )
    set_character_knowledge(
        store,
        character_id=alice_id,
        claim_id=early_claim,
        state=KnowledgeStatus.KNOWS,
        acquired_at=None,
    )
    set_character_knowledge(
        store,
        character_id=alice_id,
        claim_id=future_claim,
        state=KnowledgeStatus.KNOWS,
        acquired_at=units[1]["story_unit_id"],
    )
    store.commit()

    compiled = ContextCompiler(project, store).compile(
        prompt="What does Alice know?",
        mode=EpistemicMode.CHARACTER,
        current_document="book.md",
        active_story_unit=units[0]["story_unit_id"],
        active_source_path="book.md",
        active_document_end=units[0]["end_offset"],
        active_character="Alice",
    )
    state = compiled.model_state()
    assert state["objective_claims"] == []
    visible_claim_ids = {item["claim"]["claim_id"] for item in state["character_knowledge"]}
    assert early_claim in visible_claim_ids
    assert future_claim not in visible_claim_ids
    store.close()


def test_reader_context_exposes_reader_state_not_objective_omniscience(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "book.md").write_text(
        "# Chapter One\nA bell is found.\n\n# Chapter Two\nThe bell names the killer.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    manuscript = store.source_by_path("book.md")
    assert manuscript is not None
    units = query.list_story_units(source_id=manuscript["source_id"])
    early_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="bell_found",
        literal_value=True,
        status=AuthorityStatus.MANUSCRIPT_OBSERVED,
        created_by="writer",
        stable_key="bell-found",
    )
    future_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="killer_identity",
        literal_value="the archivist",
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="killer-identity",
    )
    set_reader_state(store, claim_id=early_claim, state="KNOWS", story_unit_id=units[0]["story_unit_id"])
    set_reader_state(store, claim_id=future_claim, state="KNOWS", story_unit_id=units[1]["story_unit_id"])
    store.commit()

    compiled = ContextCompiler(project, store).compile(
        prompt="What can the reader know?",
        mode=EpistemicMode.READER,
        current_document="book.md",
        active_story_unit=units[0]["story_unit_id"],
        active_source_path="book.md",
        active_document_end=units[0]["end_offset"],
    )
    state = compiled.model_state()
    assert state["objective_claims"] == []
    visible_claim_ids = {item["claim"]["claim_id"] for item in state["reader_state"] if item["claim"]}
    assert visible_claim_ids == {early_claim}
    store.close()


def test_reader_state_cutoff_respects_writer_owned_cross_file_order(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "opening.md").write_text("# Chapter One\nAlice finds the bell.\n", encoding="utf-8")
    (root / "ending.md").write_text("# Chapter Two\nAlice rings the bell.\n", encoding="utf-8")
    project, store = _ingest(root)
    project.set_active_manuscripts(["opening.md", "ending.md"])
    query = StoryQueryEngine(project, store)
    opening_source = store.source_by_path("opening.md")
    ending_source = store.source_by_path("ending.md")
    assert opening_source is not None and ending_source is not None
    opening_unit = query.list_story_units(source_id=opening_source["source_id"])[0]["story_unit_id"]
    ending_unit = query.list_story_units(source_id=ending_source["source_id"])[0]["story_unit_id"]

    opening_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="reader_opening",
        literal_value="bell found",
        status=AuthorityStatus.MANUSCRIPT_OBSERVED,
        created_by="writer",
        stable_key="reader-opening",
    )
    ending_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="reader_ending",
        literal_value="bell rung",
        status=AuthorityStatus.MANUSCRIPT_OBSERVED,
        created_by="writer",
        stable_key="reader-ending",
    )
    premise_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="reader_premise",
        literal_value="Alice exists",
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="reader-premise",
    )
    set_reader_state(store, claim_id=opening_claim, state="KNOWS", story_unit_id=opening_unit)
    set_reader_state(store, claim_id=ending_claim, state="KNOWS", story_unit_id=ending_unit)
    set_reader_state(store, claim_id=premise_claim, state="KNOWS", story_unit_id=None)
    store.commit()

    through_opening = query.reader_state(through_story_unit=opening_unit)
    predicates = {row["predicate"] for row in through_opening}
    assert predicates == {"reader_opening", "reader_premise"}
    through_ending = query.reader_state(through_story_unit=ending_unit)
    assert {row["predicate"] for row in through_ending} == {
        "reader_opening",
        "reader_ending",
        "reader_premise",
    }
    store.close()


def test_restricted_context_fails_closed_when_cross_file_order_is_unknown(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "alpha.md").write_text("# Chapter One\nThe silver bell is found.\n", encoding="utf-8")
    (root / "omega.md").write_text("# Chapter Two\nThe silver bell reveals the murderer.\n", encoding="utf-8")
    project, store = _ingest(root)
    alpha = store.source_by_path("alpha.md")
    assert alpha is not None
    alpha_unit = StoryQueryEngine(project, store).list_story_units(source_id=alpha["source_id"])[0]

    compiled = ContextCompiler(project, store).compile(
        prompt="What does the silver bell reveal?",
        mode=EpistemicMode.COLD_READER,
        current_document="alpha.md",
        active_story_unit=alpha_unit["story_unit_id"],
        active_source_path="alpha.md",
        active_document_end=alpha_unit["end_offset"],
    )
    assert "omega.md" not in {item.path for item in compiled.items}
    assert any(
        item["path"] == "omega.md" and "order unresolved" in item["reason"]
        for item in compiled.excluded
    )
    assert compiled.inspector()["epistemic_boundary"]["cross_file_order"] == "unresolved"
    store.close()


def test_restricted_context_uses_explicit_manuscript_order_without_future_leakage(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    for filename, heading, body in (
        ("a.md", "Chapter One", "The copper key is introduced."),
        ("b.md", "Chapter Two", "The copper key opens the archive."),
        ("c.md", "Chapter Three", "The copper key is destroyed."),
    ):
        (root / filename).write_text(f"# {heading}\n{body}\n", encoding="utf-8")
    project, store = _ingest(root)
    project.set_active_manuscripts(["a.md", "b.md", "c.md"])
    b_source = store.source_by_path("b.md")
    assert b_source is not None
    b_unit = StoryQueryEngine(project, store).list_story_units(source_id=b_source["source_id"])[0]

    compiled = ContextCompiler(project, store).compile(
        prompt="What happens to the copper key?",
        mode=EpistemicMode.READER,
        current_document="b.md",
        active_story_unit=b_unit["story_unit_id"],
        active_source_path="b.md",
        active_document_end=b_unit["end_offset"],
    )
    included = {item.path for item in compiled.items}
    assert "a.md" in included
    assert "c.md" not in included
    assert any(item["path"] == "c.md" and "later" in item["reason"] for item in compiled.excluded)
    assert compiled.inspector()["epistemic_boundary"]["cross_file_order"] == "writer_owned"
    store.close()


def test_branch_overlay_never_mutates_mainline(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice refuses.", encoding="utf-8")
    project, store = _ingest(root)
    branch = create_branch(store, assumptions=["Alice confesses instead"])
    overlay = add_branch_overlay(
        store,
        branch_id=branch,
        record_kind="claim",
        record_id="confession",
        operation="ADD",
        payload={"predicate": "confesses", "value": True},
    )
    store.commit()
    comparison = branch_comparison(store, branch)
    assert comparison["overlays"][0]["overlay_id"] == overlay
    assert store.query_claims(branch_id="mainline") == []
    assert next(iter(store.rows("SELECT COUNT(*) AS count FROM branch_overlays WHERE branch_id='mainline'")))["count"] == 0
    store.close()


def test_context_branch_overlay_is_visible_only_when_branch_is_selected(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice refuses.\n", encoding="utf-8")
    project, store = _ingest(root)
    unit = StoryQueryEngine(project, store).list_story_units()[0]
    branch = create_branch(
        store,
        fork_story_unit=unit["story_unit_id"],
        assumptions=["Alice confesses instead"],
        branch_id="ALT-CONTEXT",
    )
    overlay = add_branch_overlay(
        store,
        branch_id=branch,
        record_kind="claim",
        record_id="alice-confesses",
        operation="ADD",
        payload={
            "predicate": "confesses",
            "value": True,
            "story_unit_id": unit["story_unit_id"],
        },
    )
    store.commit()

    compiler = ContextCompiler(project, store)
    mainline = compiler.compile(prompt="What does Alice do?", branch_id="mainline")
    assert mainline.model_state()["branch_overlays"] == []
    assert mainline.inspector()["branch_id"] == "mainline"

    alternate = compiler.compile(prompt="What does Alice do?", branch_id=branch)
    assert alternate.inspector()["branch_id"] == branch
    assert alternate.model_state()["branch_overlays"] == [
        {
            "overlay_id": overlay,
            "branch_id": branch,
            "record_kind": "claim",
            "record_id": "alice-confesses",
            "operation": "ADD",
            "payload": {
                "predicate": "confesses",
                "value": True,
                "story_unit_id": unit["story_unit_id"],
            },
            "fork_story_unit": unit["story_unit_id"],
            "branch_status": "ACTIVE",
            "authority": "BRANCH_ONLY",
        }
    ]
    store.close()


def test_nested_branch_context_inherits_parent_overlay_without_mainline_contamination(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice waits.\n", encoding="utf-8")
    project, store = _ingest(root)
    unit = StoryQueryEngine(project, store).list_story_units()[0]["story_unit_id"]
    parent = create_branch(store, fork_story_unit=unit, branch_id="ALT-PARENT")
    parent_overlay = add_branch_overlay(
        store,
        branch_id=parent,
        record_kind="claim",
        record_id="alice-leaves",
        operation="ADD",
        payload={"predicate": "leaves", "value": True},
    )
    child = create_branch(store, parent_branch=parent, fork_story_unit=unit, branch_id="ALT-CHILD")
    child_overlay = add_branch_overlay(
        store,
        branch_id=child,
        record_kind="claim",
        record_id="alice-returns",
        operation="ADD",
        payload={"predicate": "returns", "value": True},
    )
    store.commit()

    compiled = ContextCompiler(project, store).compile(prompt="Trace Alice's alternate path", branch_id=child)
    assert [item["overlay_id"] for item in compiled.model_state()["branch_overlays"]] == [
        parent_overlay,
        child_overlay,
    ]
    assert ContextCompiler(project, store).compile(
        prompt="Trace Alice's path",
        branch_id="mainline",
    ).model_state()["branch_overlays"] == []
    store.close()


def test_restricted_branch_context_does_not_reveal_overlay_before_fork(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "book.md").write_text(
        "# Chapter One\nAlice has not chosen yet.\n\n"
        "# Chapter Two\nAlice reaches the fork.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    source = store.source_by_path("book.md")
    assert source is not None
    units = StoryQueryEngine(project, store).list_story_units(source_id=source["source_id"])
    assert len(units) == 2
    branch = create_branch(store, fork_story_unit=units[1]["story_unit_id"], branch_id="ALT-FUTURE-FORK")
    add_branch_overlay(
        store,
        branch_id=branch,
        record_kind="claim",
        record_id="future-choice",
        operation="ADD",
        payload={"predicate": "chooses_gate", "value": "red"},
    )
    store.commit()

    compiler = ContextCompiler(project, store)
    before = compiler.compile(
        prompt="What can I infer here?",
        mode=EpistemicMode.COLD_READER,
        current_document="book.md",
        active_source_path="book.md",
        active_story_unit=units[0]["story_unit_id"],
        active_document_end=units[0]["end_offset"],
        branch_id=branch,
    )
    assert before.model_state()["branch_overlays"] == []

    at_fork = compiler.compile(
        prompt="What changed in this alternate?",
        mode=EpistemicMode.COLD_READER,
        current_document="book.md",
        active_source_path="book.md",
        active_story_unit=units[1]["story_unit_id"],
        active_document_end=units[1]["end_offset"],
        branch_id=branch,
    )
    assert [item["record_id"] for item in at_fork.model_state()["branch_overlays"]] == ["future-choice"]
    store.close()


def test_branch_detects_mainline_drift_and_requires_writer_rebase_before_merge(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    chapter = root / "chapter.md"
    chapter.write_text("# Chapter One\nAlice refuses.\n", encoding="utf-8")
    project, store = _ingest(root)
    branch = create_branch(store, assumptions=["Alice confesses instead"], branch_id="ALT-DRIFT")
    overlay = add_branch_overlay(
        store,
        branch_id=branch,
        record_kind="claim",
        record_id="confession",
        operation="ADD",
        payload={"predicate": "confesses", "value": True},
    )
    store.commit()
    assert branch_freshness(store, branch)["stale"] is False

    chapter.write_text("# Chapter One\nAlice refuses and leaves the room.\n", encoding="utf-8")
    ProjectIngestor(project, store).ingest()
    comparison = branch_comparison(store, branch)
    assert comparison["freshness"]["stale"] is True
    with pytest.raises(ValueError, match="stale"):
        prepare_branch_merge(store, branch, [overlay])
    with pytest.raises(PermissionError, match="writer confirmation"):
        rebase_branch(store, branch)

    freshness = rebase_branch(store, branch, writer_confirmed=True)
    assert freshness["stale"] is False
    prepared = prepare_branch_merge(store, branch, [overlay])
    assert prepared["operations"][0]["overlay_id"] == overlay
    assert prepared["expected_parent_revision"] == freshness["current_parent_revision"]
    store.close()


def test_selective_branch_merge_history_records_only_writer_applied_overlays(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice refuses.\n", encoding="utf-8")
    project, store = _ingest(root)
    branch = create_branch(store, assumptions=["Alice chooses differently"], branch_id="ALT-MERGE")
    first = add_branch_overlay(
        store,
        branch_id=branch,
        record_kind="claim",
        record_id="confession",
        operation="ADD",
        payload={"predicate": "confesses", "value": True},
    )
    second = add_branch_overlay(
        store,
        branch_id=branch,
        record_kind="claim",
        record_id="arrest",
        operation="ADD",
        payload={"predicate": "arrested", "value": True},
    )
    store.commit()

    prepared = prepare_branch_merge(store, branch, [first])
    # Simulate the native/domain transaction applying only the writer-selected
    # confession to mainline. Merge-history recording happens after that change,
    # but is tied to the exact pre-merge revision the writer reviewed.
    create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="confesses",
        literal_value=True,
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="merged-confession",
    )
    with pytest.raises(PermissionError, match="writer confirmation"):
        record_completed_branch_merge(
            store,
            branch,
            [{"overlay_id": first, "target_record_kind": "claim", "target_record_id": "merged-confession"}],
            expected_parent_revision=prepared["expected_parent_revision"],
        )
    result = record_completed_branch_merge(
        store,
        branch,
        [{"overlay_id": first, "target_record_kind": "claim", "target_record_id": "merged-confession"}],
        writer_confirmed=True,
        expected_parent_revision=prepared["expected_parent_revision"],
    )
    assert result["merged_count"] == 1
    assert result["overlay_count"] == 2
    assert result["branch_status"] == "ACTIVE"
    comparison = branch_comparison(store, branch)
    by_id = {item["overlay_id"]: item for item in comparison["overlays"]}
    assert by_id[first]["merged"] is True
    assert by_id[second]["merged"] is False
    with pytest.raises(ValueError, match="already recorded as merged"):
        record_completed_branch_merge(
            store,
            branch,
            [{"overlay_id": first, "target_record_kind": "claim", "target_record_id": "merged-confession"}],
            writer_confirmed=True,
            expected_parent_revision=prepared["expected_parent_revision"],
        )
    store.close()


def test_story_store_migrates_v1_branch_history_schema(tmp_path: Path):
    db = tmp_path / "story.sqlite"
    store = StoryStore(db)
    store.connection.execute("DROP TABLE branch_merge_history")
    store.connection.execute("PRAGMA user_version=1")
    store.connection.commit()
    store.close()

    migrated = StoryStore(db)
    version = next(iter(migrated.rows("PRAGMA user_version")))[0]
    assert version == 2
    table = next(
        iter(
            migrated.rows(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='branch_merge_history'"
            )
        ),
        None,
    )
    assert table is not None
    migrated.close()


def test_retcon_impact_walks_only_registered_dependencies(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice acts.", encoding="utf-8")
    _project, store = _ingest(root)
    store.add_dependency("claim", "A", "knowledge", "K1", "supports")
    store.add_dependency("knowledge", "K1", "scene", "S2", "assumed_by")
    store.add_dependency("claim", "UNRELATED", "scene", "S9", "supports")
    impact = retcon_impact(store, source_kind="claim", source_id="A")
    assert impact["counts"] == {"knowledge": 1, "scene": 1}
    assert all(item["dependent_id"] != "S9" for item in impact["affected"])
    store.close()


def test_cold_reader_excludes_future_state_and_preserves_writer_owned_order(tmp_path: Path):
    root = tmp_path / "reader-book"
    root.mkdir()
    first_text = "# Chapter One\nMara sees a hairline crack in the bell.\n"
    second_text = "# Chapter Two\nThe bell shatters when Mara rings it.\n"
    (root / "strange-a.md").write_text(first_text, encoding="utf-8")
    (root / "strange-z.md").write_text(second_text, encoding="utf-8")
    project, store = _ingest(root)
    project.set_active_manuscripts(["strange-a.md", "strange-z.md"])
    query = StoryQueryEngine(project, store)
    source_one = store.source_by_path("strange-a.md")
    source_two = store.source_by_path("strange-z.md")
    assert source_one is not None and source_two is not None
    unit_one = query.list_story_units(source_id=source_one["source_id"])[0]["story_unit_id"]
    unit_two = query.list_story_units(source_id=source_two["source_id"])[0]["story_unit_id"]

    early_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="bell_is_cracked",
        literal_value=True,
        status=AuthorityStatus.MANUSCRIPT_OBSERVED,
        evidence=ClaimEvidenceInput(
            source_id=source_one["source_id"],
            start_offset=first_text.index("hairline crack"),
            end_offset=first_text.index("hairline crack") + len("hairline crack"),
            quote="hairline crack",
            source_hash=source_one["content_hash"],
            story_unit_id=unit_one,
        ),
    )
    future_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="bell_shatters",
        literal_value=True,
        status=AuthorityStatus.MANUSCRIPT_OBSERVED,
        evidence=ClaimEvidenceInput(
            source_id=source_two["source_id"],
            start_offset=second_text.index("shatters"),
            end_offset=second_text.index("shatters") + len("shatters"),
            quote="shatters",
            source_hash=source_two["content_hash"],
            story_unit_id=unit_two,
        ),
    )
    set_reader_state(store, claim_id=early_claim, state="KNOWS", story_unit_id=unit_one)
    set_reader_state(store, claim_id=future_claim, state="KNOWS", story_unit_id=unit_two)
    upsert_promise_item(
        store,
        item_type="READER_QUESTION",
        title="Why is the bell cracked?",
        state="OPEN",
        opened_at=unit_one,
    )
    upsert_promise_item(
        store,
        item_type="DRAMATIC_PROMISE",
        title="The bell will break",
        state="OPEN",
        opened_at=unit_two,
    )
    store.commit()

    cold = query.cold_reader_at(unit_one, prompt="What does the reader know about the bell?")
    reader_claim_ids = {
        str(item.get("claim", {}).get("claim_id") or item.get("claim_id") or "")
        for item in cold["reader_state"]
    }
    assert early_claim in reader_claim_ids
    assert future_claim not in reader_claim_ids
    assert [item["title"] for item in cold["reader_questions"]] == ["Why is the bell cracked?"]
    assert cold["dramatic_promises"] == []
    assert cold["hard_boundary"]["writer_owned_cross_file_order"] is True
    assert all(item["path"] != "strange-z.md" for item in cold["evidence"])
    store.close()


def test_reveal_fairness_reports_tracked_setup_without_claiming_literary_failure(tmp_path: Path):
    root = tmp_path / "reveal-book"
    root.mkdir()
    text = "# Chapter One\nA thin crack crosses the bell.\n\n## Reveal\nThe bell is already broken inside.\n"
    (root / "novel.md").write_text(text, encoding="utf-8")
    project, store = _ingest(root)
    project.set_active_manuscripts(["novel.md"])
    query = StoryQueryEngine(project, store)
    source = store.source_by_path("novel.md")
    assert source is not None
    units = query.list_story_units(source_id=source["source_id"])
    reveal_unit = next(item for item in units if item["display_title"] == "Reveal")
    first_unit = next(item for item in units if item["ordinal"] < reveal_unit["ordinal"])
    claim_id = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="bell_broken_inside",
        literal_value=True,
        status=AuthorityStatus.MANUSCRIPT_OBSERVED,
        evidence=ClaimEvidenceInput(
            source_id=source["source_id"],
            start_offset=text.index("thin crack"),
            end_offset=text.index("thin crack") + len("thin crack"),
            quote="thin crack",
            source_hash=source["content_hash"],
            story_unit_id=first_unit["story_unit_id"],
        ),
    )
    set_reader_state(
        store,
        claim_id=claim_id,
        state="KNOWS",
        story_unit_id=reveal_unit["story_unit_id"],
    )
    store.commit()

    audit = query.reveal_fairness(reveal_unit["story_unit_id"])
    assert audit["absence_means_untracked"] is True
    assert audit["findings"][0]["status"] == "TRACKED_PRIOR_SETUP"
    assert audit["findings"][0]["prior_setup_evidence"]
    assert "not proof" in audit["findings"][0]["interpretation_limit"]
    store.close()


def test_dramatic_irony_is_cutoff_bounded_by_character_knowledge(tmp_path: Path):
    root = tmp_path / "irony-book"
    root.mkdir()
    (root / "chapter-one.md").write_text(
        "# Chapter One\nThe reader sees the hidden key beneath the old stair and watches Mara walk past it.\n",
        encoding="utf-8",
    )
    (root / "chapter-two.md").write_text(
        "# Chapter Two\nMara returns after dark, searches the landing, and finally finds the hidden key.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    project.set_active_manuscripts(["chapter-one.md", "chapter-two.md"])
    query = StoryQueryEngine(project, store)
    source_a = store.source_by_path("chapter-one.md")
    source_b = store.source_by_path("chapter-two.md")
    assert source_a is not None and source_b is not None
    unit_a = query.list_story_units(source_id=source_a["source_id"])[0]["story_unit_id"]
    unit_b = query.list_story_units(source_id=source_b["source_id"])[0]["story_unit_id"]
    mara = "mara-irony"
    store.upsert_entity(mara, "Mara", "character", status=AuthorityStatus.CONFIRMED_CANON)
    claim_id = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="key_is_hidden",
        literal_value=True,
        status=AuthorityStatus.CONFIRMED_CANON,
    )
    set_reader_state(store, claim_id=claim_id, state="KNOWS", story_unit_id=unit_a)
    set_character_knowledge(
        store,
        character_id=mara,
        claim_id=claim_id,
        state=KnowledgeStatus.KNOWS,
        acquired_at=unit_b,
    )
    store.commit()

    before = query.dramatic_irony(unit_a, character="Mara")
    assert [item["claim_id"] for item in before["dramatic_irony"]] == [claim_id]
    after = query.dramatic_irony(unit_b, character="Mara")
    assert after["dramatic_irony"] == []
    store.close()


def test_writer_model_inference_is_provisional_evidence_backed_and_writer_review_wins(tmp_path: Path):
    root = tmp_path / "writer-model-book"
    root.mkdir()
    (root / "chapter.md").write_text(
        "# Chapter One\nMara crosses the courtyard and refuses to explain herself.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    events = [
        {
            "type": "USER_EDITED_AGENT_TARGET",
            "timestamp_utc": "2026-09-07T10:00:00Z",
            "related_operation_id": "op-1",
            "before": "Mara was very angry.",
            "after": "Mara shut the door hard enough to shake the latch.",
        },
        {
            "type": "USER_EDITED_AGENT_TARGET",
            "timestamp_utc": "2026-09-07T10:05:00Z",
            "related_operation_id": "op-2",
            "before": "She felt afraid.",
            "after": "Her hand stayed on the bolt.",
        },
    ]
    observed = observe_writer_activity(project, store, events)
    assert len(observed["updated_preferences"]) == 1
    preference_id = observed["updated_preferences"][0]
    model = writer_model(store)
    assert model["behavioral_inference_is_canon"] is False
    assert model["provisional_count"] == 1
    preference = model["preferences"][0]
    assert preference["preference_id"] == preference_id
    assert preference["evidence_count"] == 2
    assert preference["status"] == "PROVISIONAL"
    assert 0.0 < preference["confidence"] < 1.0

    put_writer_preference(
        project,
        store,
        preference_id=preference_id,
        statement="Prefer concrete physical behavior over abstract emotional labels.",
        status="CONFIRMED",
        confidence=1.0,
        evidence=preference["evidence"],
    )
    observe_writer_activity(
        project,
        store,
        [
            {
                "type": "USER_EDITED_AGENT_TARGET",
                "timestamp_utc": "2026-09-07T10:10:00Z",
                "related_operation_id": "op-3",
                "before": "She was sad.",
                "after": "She folded the letter twice and put it away.",
            }
        ],
    )
    reviewed = writer_model(store)["preferences"][0]
    assert reviewed["status"] == "CONFIRMED"
    assert reviewed["statement"] == "Prefer concrete physical behavior over abstract emotional labels."
    assert reviewed["evidence_count"] == 2

    put_writer_preference(
        project,
        store,
        preference_id=preference_id,
        statement=reviewed["statement"],
        status="IGNORED",
        confidence=reviewed["confidence"],
        evidence=reviewed["evidence"],
    )
    assert writer_model(store)["preferences"] == []
    assert writer_model(store, include_ignored=True)["preferences"][0]["status"] == "IGNORED"
    store.close()


def test_editorial_council_runs_independent_read_only_reviewers_and_synthesizes_agreement(tmp_path: Path):
    root = tmp_path / "council-book"
    root.mkdir()
    (root / "chapter.md").write_text(
        "# Chapter One\n"
        "Mara takes the stair. Mara takes the key. Mara takes the blame.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    project.set_active_manuscripts(["chapter.md"])
    query = StoryQueryEngine(project, store)
    source = store.source_by_path("chapter.md")
    assert source is not None
    unit = query.list_story_units(source_id=source["source_id"])[0]["story_unit_id"]
    mara = "mara-council"
    store.upsert_entity(mara, "Mara", "character", status=AuthorityStatus.CONFIRMED_CANON)
    add_decision(
        store,
        description="Mara takes the blame",
        agent_entity_id=mara,
        story_unit_id=unit,
        status=AuthorityStatus.CONFIRMED_CANON,
    )
    set_scene_contract(
        project,
        store,
        story_unit_id=unit,
        contract={"visible_goal": "Reach the roof", "conflict": "The stair is watched"},
    )
    reveal_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=mara,
        predicate="betrayed_guard",
        literal_value=True,
        status=AuthorityStatus.CONFIRMED_CANON,
    )
    set_reader_state(store, claim_id=reveal_claim, state="KNOWS", story_unit_id=unit)
    store.commit()

    council = query.editorial_council(unit)
    assert council["parallel_read_only"] is True
    assert council["agent_chatter"] is False
    assert council["reviewer_count"] == 7
    assert [review["reviewer"] for review in council["reviews"]] == [
        "continuity",
        "character",
        "scene_architecture",
        "cold_reader",
        "line_editor",
        "suspense",
        "theme_motif",
    ]
    assert council["agreement"]
    assert council["agreement"][0]["story_unit_id"] == unit
    assert council["agreement"][0]["reviewer_count"] >= 2
    store.close()


def test_story_lens_is_writer_owned_durable_and_returns_exact_evidence_without_semantic_overclaim(tmp_path: Path):
    root = tmp_path / "lens-book"
    root.mkdir()
    text = (
        "# Chapter One\n"
        "Barry shut the office door and kept the witness's phone in his own hand. "
        "He told Benny which exit to use and refused to let the witness leave alone.\n"
    )
    (root / "odd-name.md").write_text(text, encoding="utf-8")
    project, store = _ingest(root)
    lens = put_story_lens(
        project,
        store,
        name="Control of choices",
        definition="Find places where Barry tries to control someone else's choices.",
    )
    lens_id = lens["lens_id"]
    result = run_story_lens(store, lens_id)
    assert result["retrieval_only"] is True
    assert result["semantic_conclusion"] is False
    assert result["findings"]
    finding = result["findings"][0]
    assert finding["path"] == "odd-name.md"
    assert finding["excerpt"]
    assert finding["semantic_conclusion"] is False
    source_text = (root / "odd-name.md").read_bytes().decode("utf-8")
    assert source_text[finding["start_offset"] : finding["end_offset"]] == finding["excerpt"]
    assert story_lens(store, lens_id)["findings"][0]["evidence_current"] is True
    store.close()

    project = StoryProject.open(root)
    project.cache_path.unlink()
    rebuilt_project, rebuilt_store = _ingest(root)
    rebuilt = StoryQueryEngine(rebuilt_project, rebuilt_store)
    assert rebuilt.story_lenses()[0]["lens_id"] == lens_id
    # Findings are intentionally compiled/cache-only and must be rerun after a
    # cache rebuild rather than masquerading as persistent story truth.
    assert rebuilt.story_lens(lens_id)["findings"] == []
    rerun = rebuilt.run_story_lens(lens_id)
    assert rerun["findings"]
    assert rebuilt.story_lens(lens_id)["findings"][0]["evidence_current"] is True
    rebuilt_store.close()


def test_reader_experience_timeline_is_qualitative_ordered_and_exposes_no_fake_scores(tmp_path: Path):
    root = tmp_path / "experience-book"
    root.mkdir()
    (root / "later.md").write_text(
        "# Chapter Two\nMara ran up the stair, grabbed the rail, and shouted, \"Move!\" The guard blocked her.\n",
        encoding="utf-8",
    )
    (root / "earlier.md").write_text(
        "# Chapter One\nI remembered the old letter and wondered why the hidden name had been crossed out.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    project.set_active_manuscripts(["earlier.md", "later.md"])
    timeline = reader_experience_timeline(project, store)
    assert timeline["qualitative_only"] is True
    assert timeline["numeric_scores_exposed"] is False
    assert timeline["dimensions"] == [
        "tension",
        "curiosity",
        "intimacy",
        "action",
        "reflection",
        "mystery",
        "wonder",
        "relief",
        "conflict",
        "narrative_distance",
    ]
    assert [item["display_title"] for item in timeline["timeline"]] == ["Chapter One", "Chapter Two"]
    first = timeline["timeline"][0]
    second = timeline["timeline"][1]
    assert all(set(item) == {"dimension", "label", "rationale"} for item in first["dimensions"])
    assert not any("score" in key or "percent" in key for item in first["dimensions"] for key in item)
    first_by_dimension = {item["dimension"]: item for item in first["dimensions"]}
    second_by_dimension = {item["dimension"]: item for item in second["dimensions"]}
    assert first_by_dimension["reflection"]["label"] in {"present", "elevated"}
    assert first_by_dimension["narrative_distance"]["label"] == "close"
    assert second_by_dimension["action"]["label"] in {"present", "elevated"}
    assert second_by_dimension["conflict"]["label"] in {"present", "elevated"}
    store.close()


def test_task_aware_model_routing_is_provider_agnostic_and_privacy_first():
    local = {
        "provider": "llama_cpp",
        "model": "local-27b",
        "base_url": "http://127.0.0.1:8080/v1",
        "roles": ["creative", "chat", "line_edit"],
        "quality": "balanced",
        "priority": 20,
        "api_key": "must-never-survive-routing",
    }
    remote = {
        "provider": "anthropic",
        "model": "remote-reasoner",
        "base_url": "https://api.anthropic.com/v1",
        "roles": ["continuity", "structural"],
        "quality": "quality",
        "priority": 1,
        "api_key": "must-never-survive-routing",
    }
    assert classify_story_task("Check the timeline and continuity for contradictions") == "continuity"
    quality = route_story_model(
        prompt="Check the timeline and continuity for contradictions",
        candidates=[local, remote],
        quality="quality",
        privacy="allow_remote",
    )
    assert quality["task"] == "continuity"
    assert quality["selected"]["provider"] == "anthropic"
    assert quality["provider_agnostic"] is True
    assert "api_key" not in quality["selected"]

    local_only = route_story_model(
        prompt="Check the timeline and continuity for contradictions",
        candidates=[local, remote],
        quality="quality",
        privacy="local_only",
    )
    assert local_only["selected"]["provider"] == "llama_cpp"
    assert local_only["selected_is_remote"] is False

    prefer_local = route_story_model(
        prompt="Write three alternative ways Mara could refuse the offer",
        candidates=[remote, local],
        quality="balanced",
        privacy="prefer_local",
    )
    assert prefer_local["task"] == "creative"
    assert prefer_local["selected"]["provider"] == "llama_cpp"
    assert prefer_local["reason"]["local_preference_applied"] is True

    with pytest.raises(PermissionError, match="no eligible local"):
        route_story_model(
            prompt="Check continuity",
            candidates=[remote],
            privacy="local_only",
        )


def test_causal_graph_is_queryable_without_llm(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nCause and consequence.", encoding="utf-8")
    _project, store = _ingest(root)
    add_causal_edge(store, cause_kind="decision", cause_id="D1", effect_kind="event", effect_id="E1")
    add_causal_edge(store, cause_kind="event", cause_id="E1", effect_kind="scene", effect_id="S2")
    graph = trace_causality(store, record_kind="decision", record_id="D1", direction="downstream")
    assert {node["id"] for node in graph["nodes"]} == {"D1", "E1", "S2"}
    store.close()


def test_narrative_architecture_queries_are_bounded_and_evidence_enriched(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nMara chooses to ring the bell.\n", encoding="utf-8")
    (root / "outline.md").write_text(
        "# Outline\n"
        "Dramatic Promise: the bell will ring before dawn\n"
        "Reader Question: who moved the bell?\n"
        "Plot Thread: Mara investigates the bell\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    query = StoryQueryEngine(project, store)
    chapter_source = store.source_by_path("chapter.md")
    assert chapter_source is not None
    chapter_unit = query.list_story_units(source_id=chapter_source["source_id"])[0]["story_unit_id"]

    mara_id = "mara-query-test"
    store.upsert_entity(mara_id, "Mara", "character", status=AuthorityStatus.CONFIRMED_CANON)
    decision_id = add_decision(
        store,
        description="Mara rings the bell",
        agent_entity_id=mara_id,
        story_unit_id=chapter_unit,
        status=AuthorityStatus.MANUSCRIPT_OBSERVED,
        metadata={"source": "writer-reviewed"},
    )
    add_causal_edge(
        store,
        cause_kind="decision",
        cause_id=decision_id,
        effect_kind="event",
        effect_id="bell-rings",
    )
    add_opposition(
        store,
        objective_id="ring-bell",
        description="The tower guard blocks the stairs",
        source_entity_id=mara_id,
        story_unit_id=chapter_unit,
        metadata={"kind": "external"},
    )
    store.connection.execute(
        "INSERT INTO scene_contracts(story_unit_id,contract_json,status,updated_by) VALUES(?,?,?,?)",
        (chapter_unit, '{"purpose":"force Mara to choose","turn":"she rings the bell"}', "AUTHOR_LOCKED", "writer"),
    )
    store.connection.execute(
        """
        INSERT INTO author_decisions(
            author_decision_id,title,decision,rationale,revisit_trigger,story_unit_id,branch_id,status
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            "author-decision-bell",
            "Bell choice belongs to Mara",
            "Mara must choose to ring it herself",
            "Preserve her agency",
            "Revisit if another character rings the bell first",
            chapter_unit,
            "mainline",
            "AUTHOR_LOCKED",
        ),
    )
    store.commit()

    threads = query.threads(limit=1)
    assert len(threads) == 1
    assert threads[0]["title"] == "Mara investigates the bell"
    assert threads[0]["evidence_claim"]["evidence"][0]["stale"] == 0
    questions = query.reader_questions(limit=1)
    assert questions[0]["title"] == "who moved the bell?"
    assert questions[0]["evidence_claim"]["status"] == AuthorityStatus.AUTHOR_INTENT
    promises = query.dramatic_promises(limit=1)
    assert promises[0]["title"] == "the bell will ring before dawn"

    causal = query.causality(record_kind="decision", record_id=decision_id, direction="downstream")
    assert {node["id"] for node in causal["nodes"]} == {decision_id, "bell-rings"}
    decisions = query.decision_history(character="Mara", limit=1)
    assert decisions[0]["decision_id"] == decision_id
    assert decisions[0]["agent_name"] == "Mara"
    opposition = query.opposition_state(objective_id="ring-bell", limit=1)
    assert opposition[0]["description"] == "The tower guard blocks the stairs"
    contract = query.scene_contract(chapter_unit)
    assert contract["contract"]["purpose"] == "force Mara to choose"
    author = query.author_decisions(story_unit_id=chapter_unit, limit=1)
    assert author[0]["author_decision_id"] == "author-decision-bell"
    audit = query.audit_chapter(chapter_unit)
    assert audit["audit_kind"] == "chapter"
    assert audit["diagnostic_only"] is True
    assert audit["absence_means_untracked"] is True
    assert audit["coverage"]["decisions"] == 1
    assert audit["coverage"]["causal_edges"] == 1
    assert audit["coverage"]["opposition_records"] == 1
    assert audit["coverage"]["scene_contracts"] == 1
    assert audit["coverage"]["author_decisions"] == 1
    assert not any(item["code"] == "no_tracked_decisions" for item in audit["findings"])
    store.close()


def test_narrative_audit_treats_missing_state_as_coverage_not_prose_failure(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text(
        "# Chapter One\nMara climbs the tower and makes a difficult choice.\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    source = store.source_by_path("chapter.md")
    assert source is not None
    query = StoryQueryEngine(project, store)
    chapter_unit = query.list_story_units(source_id=source["source_id"])[0]["story_unit_id"]

    audit = query.audit_chapter(chapter_unit)
    finding = next(item for item in audit["findings"] if item["code"] == "no_tracked_decisions")
    assert finding["level"] == "coverage"
    assert "not evidence" in finding["interpretation_limit"]
    assert "lacks agency" in finding["interpretation_limit"]
    store.close()


def test_writer_state_survives_complete_cache_deletion_and_rebuild(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nMara makes a choice.\n", encoding="utf-8")
    project, store = _ingest(root)
    source = store.source_by_path("chapter.md")
    assert source is not None
    unit_id = StoryQueryEngine(project, store).list_story_units(source_id=source["source_id"])[0]["story_unit_id"]

    branch_id = create_writer_branch(
        project,
        store,
        assumptions=["Mara refuses instead"],
        fork_story_unit=unit_id,
        branch_id="ALT-DURABLE",
    )
    overlay_id = add_writer_branch_overlay(
        project,
        store,
        branch_id=branch_id,
        record_kind="claim",
        record_id="mara-refuses",
        operation="ADD",
        payload={"predicate": "refuses", "value": True},
    )
    set_scene_contract(
        project,
        store,
        story_unit_id=unit_id,
        contract={"purpose": "force Mara to choose"},
    )
    put_author_decision(
        project,
        store,
        author_decision_id="durable-author-decision",
        title="Mara owns the choice",
        decision="Mara decides without coercion",
        rationale="Preserve agency",
        revisit_trigger="Revisit if another character chooses for her",
        story_unit_id=unit_id,
    )
    put_writer_preference(
        project,
        store,
        preference_id="durable-pref",
        scope_kind="project",
        scope_id=project.project_id,
        statement="Prefer concrete action beats in confrontations.",
        status="CONFIRMED",
        confidence=0.9,
        evidence=[{"kind": "writer_review"}],
    )
    persisted = persist_writer_state(project, store)
    assert persisted["branches"] == 1
    assert persisted["branch_overlays"] == 1
    assert persisted["branch_merge_history"] == 0
    assert persisted["scene_contracts"] == 1
    assert persisted["author_decisions"] == 1
    assert persisted["writer_preferences"] == 1
    original_state_text = project.state_path.read_text(encoding="utf-8")
    original_manuscript = (root / "chapter.md").read_text(encoding="utf-8")
    cache_path = project.cache_path
    store.close()

    cache_path.unlink()
    rebuilt_project, rebuilt_store = _ingest(root)
    rebuilt_query = StoryQueryEngine(rebuilt_project, rebuilt_store)
    comparison = branch_comparison(rebuilt_store, branch_id)
    assert comparison["overlays"][0]["overlay_id"] == overlay_id
    assert comparison["overlays"][0]["merged"] is False
    assert rebuilt_query.scene_contract(unit_id)["contract"]["purpose"] == "force Mara to choose"
    assert rebuilt_query.author_decisions(story_unit_id=unit_id)[0]["author_decision_id"] == "durable-author-decision"
    preference = next(iter(rebuilt_store.rows("SELECT * FROM writer_preferences WHERE preference_id='durable-pref'")))
    assert preference["statement"] == "Prefer concrete action beats in confrontations."
    assert rebuilt_project.state_path.read_text(encoding="utf-8") == original_state_text
    assert (root / "chapter.md").read_text(encoding="utf-8") == original_manuscript
    rebuilt_store.close()


def test_writer_owned_truth_survives_cache_rebuild_with_provenance_and_state(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    chapter = root / "chapter.md"
    chapter.write_text("# Chapter One\nMara finds the silver bell.\n", encoding="utf-8")
    project, store = _ingest(root)
    source = store.source_by_path("chapter.md")
    assert source is not None
    unit_id = StoryQueryEngine(project, store).list_story_units(source_id=source["source_id"])[0]["story_unit_id"]

    mara_id = "durable-mara"
    iven_id = "durable-iven"
    store.upsert_entity(mara_id, "Mara", "character", status=AuthorityStatus.CONFIRMED_CANON)
    store.upsert_entity(iven_id, "Iven", "character", status=AuthorityStatus.CONFIRMED_CANON)
    store.add_entity_alias(mara_id, "Captain Mara", confirmed=True)
    claim_id = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=mara_id,
        predicate="bell_is_cursed",
        literal_value=True,
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="durable-bell-curse",
        evidence=ClaimEvidenceInput(
            source_id=source["source_id"],
            story_unit_id=unit_id,
            start_offset=0,
            end_offset=len("# Chapter One"),
            quote="# Chapter One",
            source_hash=source["content_hash"],
            evidence_type="writer_confirmed_source_span",
        ),
    )
    set_character_knowledge(
        store,
        character_id=mara_id,
        claim_id=claim_id,
        state=KnowledgeStatus.SUSPECTS,
        acquired_at=unit_id,
    )
    set_reader_state(
        store,
        claim_id=claim_id,
        state="HAS_SEEN",
        story_unit_id=unit_id,
    )
    set_world_state(
        store,
        entity_id=mara_id,
        state_type="location",
        value="Bell Tower",
        status=AuthorityStatus.CONFIRMED_CANON,
        evidence_claim_id=claim_id,
        state_id="durable-world-location",
    )
    set_relationship_state(
        store,
        entity_a=mara_id,
        entity_b=iven_id,
        relationship_type="trust",
        state={"label": "strained"},
        evidence_claim_id=claim_id,
        relationship_id="durable-relationship",
    )
    add_timeline_event(
        store,
        title="Mara finds the silver bell",
        story_unit_id=unit_id,
        time_start="DAY-01",
        precision="EXACT",
        status=AuthorityStatus.CONFIRMED_CANON,
        event_id="durable-event",
    )
    upsert_thread(store, title="Who cursed the bell?", thread_id="durable-thread")
    upsert_promise_item(
        store,
        item_type="READER_QUESTION",
        title="Who cursed the bell?",
        evidence_claim_id=claim_id,
        item_id="durable-promise",
    )
    decision_id = add_decision(
        store,
        description="Mara keeps the bell",
        agent_entity_id=mara_id,
        story_unit_id=unit_id,
        status=AuthorityStatus.CONFIRMED_CANON,
        decision_id="durable-decision",
    )
    add_causal_edge(
        store,
        cause_kind="decision",
        cause_id=decision_id,
        effect_kind="event",
        effect_id="bell-kept",
        evidence_claim_id=claim_id,
        edge_id="durable-edge",
    )
    add_opposition(
        store,
        objective_id="keep-bell",
        description="Iven demands that Mara surrender it",
        source_entity_id=iven_id,
        story_unit_id=unit_id,
        opposition_id="durable-opposition",
    )
    store.commit()
    counts = persist_writer_state(project, store)
    assert counts["writer_claims"] == 1
    assert counts["knowledge_state"] == 1
    assert counts["reader_state"] == 1
    assert counts["relationships"] == 1
    assert counts["decisions"] == 1
    cache_path = project.cache_path
    store.close()

    cache_path.unlink()
    rebuilt_project, rebuilt_store = _ingest(root)
    query = StoryQueryEngine(rebuilt_project, rebuilt_store)
    resolved = query.resolve_entity("Captain Mara")
    assert resolved["matches"][0]["entity_id"] == mara_id
    claim = next(item for item in query.query_claims(entity="Mara") if item["claim_id"] == claim_id)
    assert claim["status"] == AuthorityStatus.CONFIRMED_CANON
    assert claim["created_by"] == "writer"
    assert claim["evidence"][0]["stale"] == 0
    assert claim["evidence"][0]["relative_path"] == "chapter.md"
    assert query.character_beliefs("Mara")[0]["claim_id"] == claim_id
    assert query.reader_state(through_story_unit=unit_id)[0]["claim_id"] == claim_id
    assert query.world_state("Mara", state_type="location")[0]["value"] == "Bell Tower"
    relationship = next(iter(rebuilt_store.rows("SELECT * FROM relationships WHERE relationship_id='durable-relationship'")))
    assert StoryStore.decode_json(relationship["state_json"], {})["label"] == "strained"
    assert query.timeline()[0]["event_id"] == "durable-event"
    assert query.threads()[0]["thread_id"] == "durable-thread"
    assert query.reader_questions()[0]["item_id"] == "durable-promise"
    assert query.decision_history(character="Mara")[0]["decision_id"] == decision_id
    assert query.causality(record_kind="decision", record_id=decision_id)["edges"][0]["edge_id"] == "durable-edge"
    assert query.opposition_state(objective_id="keep-bell")[0]["opposition_id"] == "durable-opposition"
    rebuilt_store.close()


def test_writer_claim_provenance_survives_deleted_source_as_stale_tombstone(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    source_path = root / "fact.md"
    source_path.write_text("The bell is cursed.\n", encoding="utf-8")
    project, store = _ingest(root)
    source = store.source_by_path("fact.md")
    assert source is not None
    claim_id = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="bell_is_cursed",
        literal_value=True,
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="deleted-source-proof",
        evidence=ClaimEvidenceInput(
            source_id=source["source_id"],
            start_offset=0,
            end_offset=len("The bell is cursed."),
            quote="The bell is cursed.",
            source_hash=source["content_hash"],
        ),
    )
    store.commit()
    persist_writer_state(project, store)
    cache_path = project.cache_path
    store.close()

    source_path.unlink()
    cache_path.unlink()
    rebuilt_project, rebuilt_store = _ingest(root)
    grounded = rebuilt_store.claim_with_evidence(claim_id)
    assert grounded is not None
    assert grounded["claim"]["created_by"] == "writer"
    assert grounded["evidence"][0]["stale"] == 1
    tombstone = next(iter(rebuilt_store.rows("SELECT * FROM sources WHERE source_id=?", (source["source_id"],))))
    assert tombstone["tombstoned"] == 1
    assert tombstone["relative_path"] == "fact.md"
    assert rebuilt_project.state_path.exists()
    rebuilt_store.close()


def test_first_upgrade_captures_legacy_sqlite_writer_state_before_hydration(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice waits.\n", encoding="utf-8")
    project, store = _ingest(root)
    branch_id = create_branch(store, assumptions=["Alice leaves"], branch_id="ALT-LEGACY")
    add_branch_overlay(
        store,
        branch_id=branch_id,
        record_kind="claim",
        record_id="alice-leaves",
        operation="ADD",
        payload={"predicate": "leaves", "value": True},
    )
    store.commit()
    state_path = project.state_path
    store.close()

    # Simulate a project created before durable story-state.json existed.
    state_path.unlink()
    upgraded_project = StoryProject.open(root)
    assert upgraded_project.state_was_existing is False
    upgraded_store = StoryStore(upgraded_project.cache_path)
    ProjectIngestor(upgraded_project, upgraded_store).ingest()
    assert upgraded_project.state_was_existing is True
    assert [item["branch_id"] for item in upgraded_project.state["branches"]] == ["ALT-LEGACY"]
    assert upgraded_project.state["branch_overlays"][0]["record_id"] == "alice-leaves"
    assert branch_comparison(upgraded_store, "ALT-LEGACY")["overlays"]
    upgraded_store.close()


def test_story_store_migrates_schema_v1_to_v2_without_losing_branch_state(tmp_path: Path):
    path = tmp_path / "story.sqlite"
    store = StoryStore(path)
    create_branch(store, assumptions=["legacy branch"], branch_id="ALT-V1")
    store.commit()
    store.close()

    connection = sqlite3.connect(path)
    connection.execute("DROP TABLE branch_merge_history")
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()

    migrated = StoryStore(path)
    version = migrated.connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == 2
    assert next(iter(migrated.rows("SELECT branch_id FROM branches WHERE branch_id='ALT-V1'")))["branch_id"] == "ALT-V1"
    assert next(iter(migrated.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='branch_merge_history'")))
    migrated.close()


def test_docx_adapter_extracts_text_and_heading_structure(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    _write_docx(
        root / "novel.docx",
        [
            ("Chapter One", "Heading1"),
            ("Alice entered the room and said, \"No.\"", None),
        ],
    )
    project, store = _ingest(root)
    source = store.source_by_path("novel.docx")
    assert source is not None
    assert source["format"] == "docx"
    units = StoryQueryEngine(project, store).list_story_units(source_id=source["source_id"])
    assert units and units[0]["display_title"] == "Chapter One"
    evidence = StoryQueryEngine(project, store).find_story_evidence("Alice room")
    assert evidence and evidence[0]["path"] == "novel.docx"
    store.close()


def test_same_story_in_different_layouts_compiles_equivalent_core_roles(tmp_path: Path):
    story = "# Chapter One\n\nMara entered the tower. \"Open it,\" she said.\n"
    character = "# Mara\nAppearance: red coat\nGoal: find the bell\nRelationships: trusts Iven\n"
    roots = []
    for index in range(2):
        root = tmp_path / f"layout-{index}"
        root.mkdir()
        if index == 0:
            (root / "book.md").write_text(story, encoding="utf-8")
            (root / "Mara profile.md").write_text(character, encoding="utf-8")
        else:
            nested = root / "strange" / "nested" / "things"
            nested.mkdir(parents=True)
            (nested / "x.md").write_text(story, encoding="utf-8")
            (root / "totally-random-name.md").write_text(character, encoding="utf-8")
        roots.append(root)

    signatures = []
    for root in roots:
        project, store = _ingest(root)
        query = StoryQueryEngine(project, store)
        role_counts = query.get_project_understanding()["role_counts"]
        mara = query.resolve_entity("Mara")
        claims = query.query_claims(entity="Mara")
        signatures.append(
            (
                role_counts.get(SourceRole.MANUSCRIPT, 0),
                role_counts.get(SourceRole.CHARACTER_REFERENCE, 0),
                bool(mara["matches"]),
                {claim["predicate"] for claim in claims},
            )
        )
        store.close()
    assert signatures[0] == signatures[1]



def test_project_understanding_can_learn_writer_confirmed_source_rule(tmp_path: Path):
    root = tmp_path / "book"
    notes = root / "profiles"
    notes.mkdir(parents=True)
    (notes / "a.md").write_text("# A\nScratch note.\n", encoding="utf-8")
    (notes / "b.md").write_text("# B\nAnother scratch note.\n", encoding="utf-8")
    (root / "outside.md").write_text("# Outside\nScratch note.\n", encoding="utf-8")
    project, store = _ingest(root)
    store.close()

    result = set_source_override(
        root,
        "profiles/a.md",
        roles=[SourceRole.CHARACTER_REFERENCE.value],
        authority=AuthorityStatus.AUTHOR_INTENT.value,
        pattern="profiles/*.md",
    )
    assert result["learned_rule"] == "profiles/*.md"

    project = StoryProject.open(root)
    assert project.manifest["source_rules"] == [
        {
            "pattern": "profiles/*.md",
            "user_confirmed": True,
            "roles": [SourceRole.CHARACTER_REFERENCE.value],
            "authority": AuthorityStatus.AUTHOR_INTENT.value,
        }
    ]
    store = StoryStore(project.cache_path)
    second = store.source_by_path("profiles/b.md")
    outside = store.source_by_path("outside.md")
    assert second is not None and outside is not None
    assert second["authority_default"] == AuthorityStatus.AUTHOR_INTENT.value
    assert [row["role"] for row in store.source_roles(second["source_id"])] == [
        SourceRole.CHARACTER_REFERENCE.value
    ]
    assert [row["role"] for row in store.source_roles(outside["source_id"])] != [
        SourceRole.CHARACTER_REFERENCE.value
    ]
    store.close()

    before = StoryProject.open(root).source_override("outside.md")
    with pytest.raises(ValueError, match="project-relative"):
        set_source_override(
            root,
            "outside.md",
            roles=[SourceRole.RESEARCH.value],
            pattern="../*.md",
        )
    assert StoryProject.open(root).source_override("outside.md") == before


def test_writer_reviewed_entity_alias_survives_cache_rebuild(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "mara.md").write_text(
        "# Mara\nAppearance: scarred hand\nGoal: guard the bell\nRelationships: trusts Iven\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    mara_id = StoryQueryEngine(project, store).resolve_entity("Mara")["matches"][0]["entity_id"]
    store.close()

    mutation = apply_story_writer_mutation(
        root,
        "entity_alias",
        {"entity_id": mara_id, "alias": "Captain Mara"},
        writer_confirmed=True,
    )
    assert mutation["writer_owned"] is True
    assert mutation["record"]["user_confirmed"] is True

    project = StoryProject.open(root)
    cache = project.cache_path
    if cache.exists():
        cache.unlink()
    rebuilt_project, rebuilt_store = _ingest(root)
    resolved = StoryQueryEngine(rebuilt_project, rebuilt_store).resolve_entity("Captain Mara")
    assert resolved["matches"][0]["entity_id"] == mara_id
    assert resolved["matches"][0]["user_confirmed"] == 1
    rebuilt_store.close()


def test_grounding_conflict_inspector_returns_exact_provenance(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    (root / "mara-a.md").write_text(
        "# Mara\nAppearance: scarred hand\nGoal: leave the city\nRelationships: trusts Iven\n",
        encoding="utf-8",
    )
    project, store = _ingest(root)
    mara_id = StoryQueryEngine(project, store).resolve_entity("Mara")["matches"][0]["entity_id"]
    writer_claim = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=mara_id,
        predicate="goal",
        literal_value="remain in the city",
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="grounding-inspector-conflict",
    )
    detect_claim_conflicts(store, subject_entity_id=mara_id)
    store.commit()

    conflicts = StoryQueryEngine(project, store).list_conflicts()
    assert len(conflicts) == 1
    claims = conflicts[0]["claims"]
    assert {claim["claim_id"] for claim in claims} >= {writer_claim}
    source_claim = next(claim for claim in claims if claim["created_by"] == "compiler")
    assert source_claim["evidence"]
    evidence = source_claim["evidence"][0]
    source_text = (root / "mara-a.md").read_bytes().decode("utf-8")
    assert evidence["quote"] == source_text[evidence["start_offset"] : evidence["end_offset"]]
    assert evidence["quote"].rstrip("\r") == "Goal: leave the city"
    assert evidence["relative_path"] == "mara-a.md"
    assert evidence["source_hash"]
    assert evidence["start_offset"] < evidence["end_offset"]
    store.close()


def test_context_compiler_scales_to_ten_thousand_source_universe(tmp_path: Path):
    root = tmp_path / "universe"
    root.mkdir()
    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    source_rows = []
    role_rows = []
    chunk_rows = []
    fts_rows = []
    for index in range(10_000):
        source_id = f"SRC-{index:05d}"
        chunk_id = f"CHK-{index:05d}"
        path = f"reference/{index:05d}.md"
        text = (
            "The obsidian needle is hidden beneath the west stair."
            if index == 9_999
            else f"Reference dossier {index} contains ordinary archival material."
        )
        source_rows.append(
            (
                source_id,
                project.project_id,
                path,
                f"{index:05d}.md",
                "md",
                f"hash-{index:05d}",
                len(text),
                index,
                "readable",
                AuthorityStatus.CONFIRMED_CANON.value,
                "mainline",
                "",
                "{}",
                "scale_fixture",
                0,
            )
        )
        role_rows.append(
            (source_id, SourceRole.WORLD_REFERENCE.value, 1.0, "scale fixture", 1)
        )
        chunk_rows.append(
            (chunk_id, source_id, 0, "", 0, len(text), text, f"hash-{index:05d}")
        )
        fts_rows.append((chunk_id, source_id, "", text))
    store.connection.executemany(
        """
        INSERT INTO sources(
            source_id,project_id,relative_path,display_name,format,content_hash,size,mtime_ns,
            readability_status,authority_default,branch_scope,story_scope,metadata_json,adapter_origin,tombstoned
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        source_rows,
    )
    store.connection.executemany(
        "INSERT INTO source_roles(source_id,role,confidence,reason,user_confirmed) VALUES(?,?,?,?,?)",
        role_rows,
    )
    store.connection.executemany(
        """
        INSERT INTO source_chunks(chunk_id,source_id,ordinal,heading,start_offset,end_offset,text,content_hash)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        chunk_rows,
    )
    if store._has_fts():
        store.connection.executemany(
            "INSERT INTO source_chunks_fts(chunk_id,source_id,heading,text) VALUES(?,?,?,?)",
            fts_rows,
        )
    store.commit()

    compiled = ContextCompiler(project, store).compile(
        prompt="Where is the obsidian needle?",
        mode=EpistemicMode.AUTHOR_OMNISCIENT,
        maximum_chars=4_000,
    )
    assert compiled.items
    assert compiled.items[0].path == "reference/09999.md"
    assert "obsidian needle" in compiled.items[0].text
    assert compiled.used_chars <= 4_000
    assert len(compiled.items) <= 24
    assert StoryQueryEngine(project, store).get_project_understanding()["source_count"] == 10_000
    store.close()


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink behavior is covered by permission-tolerant test above")
def test_directory_symlink_is_never_followed(tmp_path: Path):
    root = tmp_path / "book"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# Chapter One\noutside", encoding="utf-8")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    _project, store = _ingest(root)
    assert store.list_sources() == []
    store.close()

