from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from backend.story.acceptance_metrics import AcceptanceMetrics
from backend.story.context import ContextCompiler
from backend.story.explain import StoryExplainer
from backend.story.indexing import indexing_status, run_index_batch
from backend.story.ingest import ProjectIngestor
from backend.story.migrations import bind_legacy_workspace, migration_status
from backend.story.operational_acceptance import OperationalAcceptance
from backend.story.performance import StoryPerformanceProbe
from backend.story.privacy import EgressInspector
from backend.story.project import StoryProject
from backend.story.proposals import list_story_proposals
from backend.story.retrieval import StaticRetrievalSignal, retrieval_capabilities
from backend.story.security import ProjectSecurityAudit
from backend.story.service import review_story_project_proposal, submit_story_project_proposal
from backend.story.store import StoryStore
from backend.story.validation_matrix import compare_model_fingerprints, project_model_fingerprint
from backend.story.writer_state import put_writer_claim, put_writer_entity


def _project(tmp_path: Path, name: str = "book") -> tuple[Path, StoryProject, StoryStore]:
    root = tmp_path / name
    root.mkdir()
    (root / "chapter.md").write_text(
        "# Chapter One\n\nMara keeps the bronze bell key.\n\n# Chapter Two\n\nMara confronts Iven.\n",
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


def test_phase26_legacy_workspace_binding_is_additive_idempotent_and_read_only(tmp_path: Path):
    root, project, store = _project(tmp_path)
    workspace = root / ".thothpad" / "chapter.md.story.json"
    workspace.parent.mkdir(exist_ok=True)
    payload = {
        "version": 2,
        "id": "legacy-workspace",
        "agents": [],
        "sessions": [],
        "memories": [],
        "markers": [],
        "scopes": [
            {"id": "manuscript", "title": "Whole manuscript", "level": 0, "start": 0},
            {"id": "chapter-one", "title": "Chapter One", "level": 1, "start": 0},
            {"id": "chapter-two", "title": "Chapter Two", "level": 1, "start": 48},
        ],
    }
    raw = json.dumps(payload, indent=2).encode()
    workspace.write_bytes(raw)
    result = bind_legacy_workspace(
        project,
        store,
        workspace_path=workspace,
        manuscript_path="chapter.md",
        writer_confirmed=True,
    )
    assert result["legacy_workspace_preserved"] is True
    assert workspace.read_bytes() == raw
    assert result["linked_scope_count"] >= 2
    again = bind_legacy_workspace(
        project,
        store,
        workspace_path=workspace,
        manuscript_path="chapter.md",
        writer_confirmed=True,
    )
    assert again["binding"]["binding_id"] == result["binding"]["binding_id"]
    assert migration_status(project)["binding_count"] == 1
    store.close()


def test_phase27_resumable_index_batches_finish_without_tombstoning_unvisited_files(tmp_path: Path):
    root = tmp_path / "many"
    root.mkdir()
    for index in range(7):
        (root / f"chapter-{index}.md").write_text(f"# Chapter {index}\nBell {index}.\n", encoding="utf-8")
    project = StoryProject.open(root)
    first = run_index_batch(root, maximum_documents=2)
    assert first["complete"] is False
    assert first["processed"] == 2
    assert indexing_status(project)["remaining"] == 5
    second = run_index_batch(root, maximum_documents=2)
    assert second["processed"] == 4
    final = second
    while not final["complete"]:
        final = run_index_batch(root, maximum_documents=2)
    assert final["total"] == 7
    assert indexing_status(StoryProject.open(root))["active"] is False
    store = StoryStore(StoryProject.open(root).cache_path)
    assert next(iter(store.rows("SELECT COUNT(*) AS count FROM sources WHERE tombstoned=0")))["count"] == 7
    store.close()


def test_phase28_large_project_core_queries_use_indexes_not_filesystem_scans(tmp_path: Path):
    root = tmp_path / "large"
    root.mkdir()
    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    rows = [
        (
            f"source-{index}",
            project.project_id,
            f"vault/{index:05d}.md",
            f"{index:05d}.md",
            "md",
            f"hash-{index}",
            10,
            index,
            "readable",
            "PROVISIONAL",
            "mainline",
            "",
            "{}",
            "markdown",
        )
        for index in range(10_000)
    ]
    store.connection.executemany(
        "INSERT INTO sources(source_id,project_id,relative_path,display_name,format,content_hash,size,mtime_ns,"
        "readability_status,authority_default,branch_scope,story_scope,metadata_json,adapter_origin,tombstoned) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
        rows,
    )
    store.commit()
    report = StoryPerformanceProbe(project, store).report()
    assert report["source_count"] == 10_000
    assert report["all_core_queries_indexed"] is True
    assert report["filesystem_scans_during_query"] == 0
    store.close()


def test_phase29_security_audit_ignores_archives_executables_and_rejects_docx_bomb(tmp_path: Path):
    root = tmp_path / "unsafe"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nSafe text.", encoding="utf-8")
    (root / "payload.exe").write_bytes(b"MZ")
    (root / "archive.zip").write_bytes(b"not actually opened")
    bomb = root / "bomb.docx"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w:document>" + ("A" * (33 * 1024 * 1024)) + "</w:document>")
    project = StoryProject.open(root)
    report = ProjectSecurityAudit(project).report()
    assert report["counts"]["archives_ignored"] == 1
    assert report["counts"]["executables_ignored"] == 1
    assert report["archives_extracted"] is False
    store = StoryStore(project.cache_path)
    summary = ProjectIngestor(project, store).ingest()
    assert summary.unreadable_documents == 1
    assert summary.readable_documents == 1
    store.close()


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation policy varies by host")
def test_phase29_security_audit_never_follows_symlink_escape(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (root / "escape.md").symlink_to(outside)
    report = ProjectSecurityAudit(StoryProject.open(root)).report()
    assert report["counts"]["symlink_entries_ignored"] == 1
    assert report["symlinks_followed"] is False


def test_phase30_egress_preview_contains_only_relative_story_context_and_no_credentials(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    preview = EgressInspector(project, store).preview(
        prompt="Mara bell key",
        selected_is_remote=True,
        include_text_preview=True,
        maximum_chars=4_000,
    )
    assert preview["would_leave_machine"] is True
    assert preview["credentials_included"] is False
    assert preview["absolute_paths_included"] is False
    assert all(not Path(item["path"]).is_absolute() for item in preview["sources"])
    store.close()


def _layout_fingerprint(tmp_path: Path, name: str, relative: str) -> dict:
    root = tmp_path / name
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_text("# Chapter One\n\nMara keeps the bronze bell key.\n", encoding="utf-8")
    project = StoryProject.open(root)
    project.set_source_override(relative.replace("\\", "/"), {"roles": ["manuscript"]})
    store = StoryStore(project.cache_path)
    ProjectIngestor(project, store).ingest()
    fingerprint = project_model_fingerprint(project, store)
    store.close()
    return fingerprint


def test_phase31_three_radically_different_layouts_compile_to_equivalent_model_fingerprint(tmp_path: Path):
    fingerprints = [
        _layout_fingerprint(tmp_path, "minimal", "novel.md"),
        _layout_fingerprint(tmp_path, "chaotic", "random/deep/final-copy.md"),
        _layout_fingerprint(tmp_path, "flat", "anything.md"),
    ]
    comparison = compare_model_fingerprints(fingerprints)
    assert comparison["equivalent"] is True
    assert all(item["filesystem_paths_included"] is False for item in fingerprints)


def test_phase32_acceptance_metrics_are_engineering_metrics_not_story_score(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    report = AcceptanceMetrics(project, store).report()
    assert all(report["hard_gates"].values())
    assert "score" not in report
    assert "not a prose score" in report["interpretation_limit"]
    store.close()


def test_phase33_optional_retrieval_signal_can_rerank_but_never_changes_authority(tmp_path: Path):
    root = tmp_path / "retrieval"
    root.mkdir()
    (root / "a.md").write_text("Bell tower bell tower bell tower.", encoding="utf-8")
    (root / "b.md").write_text("Bell tower.", encoding="utf-8")
    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    ProjectIngestor(project, store).ingest()
    b_source = store.source_by_path("b.md")
    assert b_source is not None
    b_chunk = next(iter(store.rows("SELECT chunk_id FROM source_chunks WHERE source_id=?", (b_source["source_id"],))))
    signal = StaticRetrievalSignal({str(b_chunk["chunk_id"]): 4.0}, name="local-test-semantic")
    compiled = ContextCompiler(project, store, retrieval_signals=[signal]).compile(prompt="bell tower", maximum_chars=4_000)
    assert compiled.items[0].path == "b.md"
    assert compiled.inspector()["retrieval_signals"] == ["local-test-semantic"]
    assert retrieval_capabilities()["semantic_similarity_is_authority"] is False
    store.close()


def test_phase34_proposal_queue_does_not_mutate_canon_until_writer_accepts(tmp_path: Path):
    root, _project_obj, store = _project(tmp_path)
    store.close()
    proposal = submit_story_project_proposal(
        root,
        proposal_kind="canon_fact",
        target_mutation="claim",
        payload={"predicate": "bell_is_cursed", "literal_value": True},
    )
    reopened = StoryStore(StoryProject.open(root).cache_path)
    assert list(reopened.rows("SELECT * FROM claims WHERE predicate='bell_is_cursed'")) == []
    reopened.close()
    reviewed = review_story_project_proposal(
        root,
        proposal_id=proposal["proposal_id"],
        decision="ACCEPTED",
        writer_confirmed=True,
    )
    assert reviewed["reviewed"]["status"] == "ACCEPTED"
    assert reviewed["applied"]["mutation"] == "claim"
    final_store = StoryStore(StoryProject.open(root).cache_path)
    assert len(list(final_store.rows("SELECT * FROM claims WHERE predicate='bell_is_cursed'"))) == 1
    final_store.close()


def test_phase34_rejected_proposal_stays_noncanon(tmp_path: Path):
    root, _project_obj, store = _project(tmp_path)
    store.close()
    proposal = submit_story_project_proposal(
        root,
        proposal_kind="canon_fact",
        target_mutation="claim",
        payload={"predicate": "iven_is_king", "literal_value": True},
    )
    review_story_project_proposal(
        root,
        proposal_id=proposal["proposal_id"],
        decision="REJECTED",
        writer_confirmed=True,
    )
    project = StoryProject.open(root)
    assert list_story_proposals(project, status="REJECTED")[0]["proposal_id"] == proposal["proposal_id"]
    final_store = StoryStore(project.cache_path)
    assert list(final_store.rows("SELECT * FROM claims WHERE predicate='iven_is_king'")) == []
    final_store.close()


def test_phase34_alternate_branch_proposal_cannot_bypass_branch_workflow(tmp_path: Path):
    root, _project_obj, store = _project(tmp_path)
    store.close()
    proposal = submit_story_project_proposal(
        root,
        proposal_kind="branch_fact",
        target_mutation="claim",
        payload={"predicate": "branch_only_fact", "literal_value": True},
        branch_id="ALT-001",
    )
    with pytest.raises(ValueError, match="branch workflow"):
        review_story_project_proposal(
            root,
            proposal_id=proposal["proposal_id"],
            decision="ACCEPTED",
            writer_confirmed=True,
        )
    project = StoryProject.open(root)
    final_store = StoryStore(project.cache_path)
    assert list(final_store.rows("SELECT * FROM claims WHERE predicate='branch_only_fact'")) == []
    final_store.close()


def test_phase35_ask_why_returns_grounding_and_dependencies_and_operational_harness_green(tmp_path: Path):
    _root, project, store = _project(tmp_path)
    mara = put_writer_entity(project, store, canonical_name="Mara", entity_type="character")
    claim_id = put_writer_claim(
        project,
        store,
        subject_entity_id=mara["entity_id"],
        predicate="guards_bell",
        literal_value=True,
        stable_key="phase35-why",
    )
    store.add_dependency("claim", claim_id, "thread", "bell-thread", "supports")
    store.commit()
    explanation = StoryExplainer(project, store).explain("claim", claim_id)
    assert explanation["record"]["claim_id"] == claim_id
    assert explanation["dependencies"]["downstream"][0]["dependent_id"] == "bell-thread"
    acceptance = OperationalAcceptance(project, store).run(prompt="Mara bell")
    assert acceptance["total_steps"] == 10
    assert acceptance["passed_steps"] == 10
    assert acceptance["all_green"] is True
    store.close()
