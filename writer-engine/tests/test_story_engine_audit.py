from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.story.adapters.base import SourceCandidate
from backend.story.adapters.html import HtmlAdapter
from backend.story.authority import AuthorityStatus
from backend.story.claims import create_claim
from backend.story.compatibility import create_story_state_backup, restore_story_state_backup
from backend.story.exchange import export_story_bundle, import_story_bundle
from backend.story.ingest import ProjectIngestor
from backend.story.project import StoryProject
from backend.story.query import StoryQueryEngine
from backend.story.recovery import recover_story_project, recovery_status
from backend.story.service import (
    add_story_branch_overlay,
    apply_story_branch_merge,
    apply_story_writer_mutation,
    call_story_tool,
    create_story_branch,
    export_story_project,
    prepare_story_branch_merge,
    rebuild_story_project_index,
    review_story_project_proposal,
    set_source_override,
    story_runtime,
    submit_story_project_proposal,
)
from backend.story.soak import run_soak_replay
from backend.story.store import StoryStore
from backend.story.tools import story_tool_manifest
from backend.story.validation_matrix import project_model_fingerprint
from backend.story.writer_state import put_writer_claim


def _sidecar_request(operation: str, **params: object) -> dict[str, object]:
    return {
        "protocol_major": 1,
        "protocol_minor": 9,
        "request_id": "audit-request",
        "operation": operation,
        "params": params,
    }


def _project(tmp_path: Path, name: str = "audit-book") -> tuple[Path, StoryProject, StoryStore]:
    root = tmp_path / name
    root.mkdir()
    manuscript = root / "chapter.md"
    manuscript.write_text("# Chapter One\n\nMara rings the bell.\n", encoding="utf-8")
    project = StoryProject.open(root)
    project.set_source_override(
        "chapter.md",
        {"roles": ["manuscript"], "authority": "MANUSCRIPT_OBSERVED"},
    )
    store = StoryStore(project.cache_path)
    ProjectIngestor(project, store).ingest()
    return root, project, store


@pytest.mark.parametrize(
    ("target", "contents", "message"),
    [
        ("project.json", "{", "corrupt JSON"),
        ("project.json", "{}", "empty or incomplete"),
        ("story-state.json", "{", "corrupt JSON"),
        ("story-state.json", "{}", "empty or incomplete"),
    ],
)
def test_existing_corrupt_or_empty_story_metadata_fails_closed_without_rewrite(
    tmp_path: Path,
    target: str,
    contents: str,
    message: str,
) -> None:
    root, project, store = _project(tmp_path)
    store.close()
    path = project.metadata_dir / target
    path.write_text(contents, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        StoryProject.open(root)
    assert path.read_bytes() == before


def test_existing_story_state_with_wrong_project_identity_fails_closed(tmp_path: Path) -> None:
    root, project, store = _project(tmp_path)
    store.close()
    state = json.loads(project.state_path.read_text(encoding="utf-8"))
    state["project_id"] = "different-project"
    project.state_path.write_text(json.dumps(state), encoding="utf-8")
    before = project.state_path.read_bytes()
    with pytest.raises(ValueError, match="project_id does not match"):
        StoryProject.open(root)
    assert project.state_path.read_bytes() == before


def test_writer_mutation_rolls_back_cache_if_durable_state_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, project, store = _project(tmp_path)
    state_before = project.state_path.read_bytes()

    def fail_save(_self: StoryProject) -> None:
        raise OSError("injected durable-state write failure")

    monkeypatch.setattr(StoryProject, "save_state", fail_save)
    with pytest.raises(OSError, match="injected durable-state write failure"):
        put_writer_claim(
            project,
            store,
            predicate="must_not_commit",
            literal_value=True,
            stable_key="audit-failed-persist",
        )

    assert not store.connection.in_transaction
    assert next(iter(store.rows("SELECT 1 FROM claims WHERE predicate='must_not_commit'")), None) is None
    assert project.state_path.read_bytes() == state_before
    assert not any(item.get("predicate") == "must_not_commit" for item in project.state.get("writer_claims", []))
    store.close()


def test_writer_mutation_restores_durable_state_if_cache_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, project, store = _project(tmp_path, "cache-commit-failure")
    state_before = project.state_path.read_bytes()
    original_commit = StoryStore.commit

    def fail_commit(self: StoryStore) -> None:
        if self is store:
            raise OSError("injected SQLite commit failure")
        original_commit(self)

    monkeypatch.setattr(StoryStore, "commit", fail_commit)
    with pytest.raises(OSError, match="injected SQLite commit failure"):
        put_writer_claim(
            project,
            store,
            predicate="must_rollback_after_cache_commit_failure",
            literal_value=True,
            stable_key="audit-cache-commit-failure",
        )

    assert project.state_path.read_bytes() == state_before
    assert not store.connection.in_transaction
    assert next(
        iter(
            store.rows(
                "SELECT 1 FROM claims WHERE predicate='must_rollback_after_cache_commit_failure'"
            )
        ),
        None,
    ) is None
    assert not any(
        isinstance(item, dict) and item.get("predicate") == "must_rollback_after_cache_commit_failure"
        for item in project.state.get("writer_claims", [])
    )
    store.close()


def test_branch_merge_restores_durable_state_if_final_cache_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, project, store = _project(tmp_path, "branch-commit-failure")
    store.close()
    branch = create_story_branch(root, writer_confirmed=True)
    branch_id = str(branch["branch"]["branch_id"])
    overlay = add_story_branch_overlay(
        root,
        branch_id=branch_id,
        record_kind="thread",
        record_id="audit-branch-thread",
        operation="ADD",
        payload={"title": "Alternate thread", "state": "OPEN"},
        writer_confirmed=True,
    )
    overlay_id = str(overlay["created_overlay_id"])
    prepared = prepare_story_branch_merge(root, branch_id, [overlay_id])
    revision = str(prepared["expected_parent_revision"])
    state_before = StoryProject.open(root).state_path.read_bytes()
    original_commit = StoryStore.commit
    calls = 0

    def fail_final_commit(self: StoryStore) -> None:
        nonlocal calls
        calls += 1
        # story_runtime ingestion commits once; the second commit is the
        # branch merge's durable/cache atomic commit.
        if calls == 2:
            raise OSError("injected branch merge SQLite commit failure")
        original_commit(self)

    monkeypatch.setattr(StoryStore, "commit", fail_final_commit)
    with pytest.raises(OSError, match="injected branch merge SQLite commit failure"):
        apply_story_branch_merge(
            root,
            branch_id,
            [overlay_id],
            expected_parent_revision=revision,
            writer_confirmed=True,
        )

    reopened = StoryProject.open(root)
    assert reopened.state_path.read_bytes() == state_before
    rebuilt_store = StoryStore(reopened.cache_path)
    assert next(
        iter(rebuilt_store.rows("SELECT 1 FROM threads WHERE thread_id='audit-branch-thread'")),
        None,
    ) is None
    assert next(
        iter(rebuilt_store.rows("SELECT 1 FROM branch_merge_history WHERE branch_id=?", (branch_id,))),
        None,
    ) is None
    assert next(
        iter(rebuilt_store.rows("SELECT 1 FROM branch_overlays WHERE overlay_id=?", (overlay_id,))),
        None,
    ) is not None
    rebuilt_store.close()


def test_branch_and_proposal_payloads_are_bounded_before_durable_storage(tmp_path: Path) -> None:
    root, _project_obj, store = _project(tmp_path)
    store.close()
    branch = create_story_branch(root, writer_confirmed=True)
    branch_id = str(branch["branch"]["branch_id"])
    too_deep = {"a": {"b": {"c": {"d": {"e": {"f": "blocked"}}}}}}

    with pytest.raises(ValueError, match="nested too deeply"):
        add_story_branch_overlay(
            root,
            branch_id=branch_id,
            record_kind="thread",
            record_id="oversized-thread",
            operation="ADD",
            payload=too_deep,
            writer_confirmed=True,
        )

    with pytest.raises(ValueError, match="nested too deeply"):
        submit_story_project_proposal(
            root,
            proposal_kind="audit",
            target_mutation="claim",
            payload={"predicate": "safe", "literal_value": True},
            evidence=[too_deep],
        )

    with pytest.raises(ValueError, match="integer is too large"):
        apply_story_writer_mutation(
            root,
            "claim",
            {"predicate": "oversized_integer", "literal_value": 1 << 5000},
            writer_confirmed=True,
        )

    reopened = StoryProject.open(root)
    assert reopened.state.get("branch_overlays") == []
    assert reopened.state.get("story_proposals") == []
    assert not any(item.get("predicate") == "oversized_integer" for item in reopened.state.get("writer_claims", []))


def test_r0_soak_may_rebuild_disposable_cache_but_never_authoritative_bytes(tmp_path: Path) -> None:
    root, project, store = _project(tmp_path)
    put_writer_claim(project, store, predicate="durable_fact", literal_value=True, stable_key="audit-r0-fact")
    store.close()
    source = root / "chapter.md"
    source_before = source.read_bytes()
    state_before = project.state_path.read_bytes()
    cache = project.cache_path
    cache.unlink()

    tool = next(item for item in story_tool_manifest() if item["id"] == "run_soak_replay")
    assert tool["risk"] == "R0"
    report = run_soak_replay(root, cycles=2)
    assert report["all_cycles_green"] is True
    assert report["derived_cache_reconciliation_allowed"] is True
    assert report["authoritative_state_mutation_allowed"] is False
    assert cache.exists()
    assert source.read_bytes() == source_before
    assert project.state_path.read_bytes() == state_before


def test_mixed_writer_state_survives_cache_rebuild_with_sqlite_integrity(tmp_path: Path) -> None:
    root, project, store = _project(tmp_path)
    source = root / "chapter.md"
    source_before = source.read_bytes()
    story_unit_id = StoryQueryEngine(project, store).list_story_units()[0]["story_unit_id"]
    store.close()

    mara = apply_story_writer_mutation(
        root,
        "entity",
        {"canonical_name": "Mara", "entity_type": "character"},
        writer_confirmed=True,
    )["record_id"]
    claim = apply_story_writer_mutation(
        root,
        "claim",
        {"subject_entity_id": mara, "predicate": "holds_bell", "literal_value": True},
        writer_confirmed=True,
    )["record_id"]
    apply_story_writer_mutation(
        root,
        "world_state",
        {"entity_id": mara, "state_type": "location", "value": "tower"},
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "reader_state",
        {"claim_id": claim, "state": "KNOWS", "story_unit_id": story_unit_id},
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "scene_contract",
        {"story_unit_id": story_unit_id, "contract": {"purpose": "force a choice"}},
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "writer_preference",
        {"statement": "Prefer concrete causal beats", "scope_kind": "project"},
        writer_confirmed=True,
    )
    branch = create_story_branch(root, fork_story_unit=story_unit_id, writer_confirmed=True)
    branch_id = str(branch["branch"]["branch_id"])
    add_story_branch_overlay(
        root,
        branch_id=branch_id,
        record_kind="thread",
        record_id="alternate-thread",
        operation="ADD",
        payload={"title": "Mara refuses the bell", "state": "OPEN"},
        writer_confirmed=True,
    )

    state_before = json.loads(StoryProject.open(root).state_path.read_text(encoding="utf-8"))
    rebuilt = rebuild_story_project_index(root, writer_confirmed=True)
    assert rebuilt["rebuilt"] is True
    reopened = StoryProject.open(root)
    state_after = json.loads(reopened.state_path.read_text(encoding="utf-8"))
    assert state_after == state_before
    assert source.read_bytes() == source_before

    rebuilt_store = StoryStore(reopened.cache_path)
    assert rebuilt_store.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert list(rebuilt_store.rows("PRAGMA foreign_key_check")) == []
    assert next(iter(rebuilt_store.rows("SELECT 1 FROM claims WHERE claim_id=?", (claim,))), None) is not None
    assert next(iter(rebuilt_store.rows("SELECT 1 FROM branches WHERE branch_id=?", (branch_id,))), None) is not None
    assert next(
        iter(rebuilt_store.rows("SELECT 1 FROM branch_overlays WHERE branch_id=?", (branch_id,))),
        None,
    ) is not None
    rebuilt_store.close()


def test_html_adapter_excludes_executable_style_content_and_preserves_offsets(tmp_path: Path) -> None:
    path = tmp_path / "notes.html"
    source = (
        "\n<script>FUTURE_SECRET = 'do not index';</script>"
        "<style>.spoiler{display:none}</style>"
        "<h1>Bell Lore</h1><p><a href='bell.md'>Mara rings the bell.</a></p>"
        "<template>HIDDEN TEMPLATE TEXT</template>\n"
    )
    path.write_text(source, encoding="utf-8")
    stat = path.stat()
    candidate = SourceCandidate(
        path=path,
        relative_path="notes.html",
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
    extracted = HtmlAdapter().extract(candidate)
    assert "FUTURE_SECRET" not in extracted.text
    assert "spoiler" not in extracted.text
    assert "HIDDEN TEMPLATE TEXT" not in extracted.text
    assert "Bell Lore" in extracted.text
    assert len(extracted.links) == 1
    link = extracted.links[0]
    assert extracted.text[link.start_offset : link.end_offset] == "Mara rings the bell."
    assert extracted.structures
    heading = extracted.structures[0]
    assert extracted.text[heading.start_offset : heading.end_offset] == "Bell Lore"


def test_html_adapter_handles_heavily_tagged_bounded_input_without_quadratic_joining(tmp_path: Path) -> None:
    path = tmp_path / "dense.html"
    path.write_text("<div>x</div>" * 20_000, encoding="utf-8")
    stat = path.stat()
    candidate = SourceCandidate(
        path=path,
        relative_path="dense.html",
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
    extracted = HtmlAdapter().extract(candidate)
    assert extracted.text.count("x") == 20_000


def test_content_addressed_story_state_backup_rejects_tampering_before_restore(tmp_path: Path) -> None:
    root, project, store = _project(tmp_path)
    put_writer_claim(project, store, predicate="backup_fact", literal_value=True, stable_key="audit-backup-fact")
    store.close()
    backup = create_story_state_backup(root)
    backup_path = project.metadata_dir / "backups" / backup["backup_name"]
    document = json.loads(backup_path.read_text(encoding="utf-8"))
    document["state"]["writer_claims"][0]["literal_value"] = "tampered"
    backup_path.write_text(json.dumps(document), encoding="utf-8")
    state_before = project.state_path.read_bytes()

    with pytest.raises(ValueError, match="content-addressed name"):
        restore_story_state_backup(root, backup["backup_name"], writer_confirmed=True)
    assert project.state_path.read_bytes() == state_before


def test_portable_import_prevalidation_rejects_malformed_state_without_any_project_write(tmp_path: Path) -> None:
    root, project, store = _project(tmp_path)
    bundle = export_story_bundle(project, store)
    store.close()
    manifest_before = project.manifest_path.read_bytes()
    state_before = project.state_path.read_bytes()
    bundle["state"]["writer_claims"] = [{"nested": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}}]

    with pytest.raises(ValueError, match="nested too deeply"):
        import_story_bundle(root, bundle, writer_confirmed=True)
    reopened = StoryProject.open(root)
    assert reopened.manifest_path.read_bytes() == manifest_before
    assert reopened.state_path.read_bytes() == state_before
    assert recovery_status(reopened)["recovery_required"] is False


def test_story_tool_runtime_enforces_argument_budget_even_without_client_schema_validation(tmp_path: Path) -> None:
    root, project, store = _project(tmp_path)
    store.close()
    state_before = project.state_path.read_bytes()
    with pytest.raises(ValueError, match="array argument exceeds"):
        call_story_tool(
            root,
            "get_story_context",
            {"prompt": "Context", "user_pins": [f"pin-{index}" for index in range(1_001)]},
        )
    assert StoryProject.open(root).state_path.read_bytes() == state_before


def test_existing_durable_state_outranks_rogue_cache_during_backup_export_and_rebuild(tmp_path: Path) -> None:
    root, project, store = _project(tmp_path)
    legitimate = put_writer_claim(
        project,
        store,
        predicate="legitimate_durable_fact",
        literal_value=True,
        stable_key="audit-legitimate-durable",
    )
    rogue = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=None,
        predicate="rogue_cache_only_fact",
        literal_value=True,
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="audit-rogue-cache",
    )
    store.commit()
    assert next(iter(store.rows("SELECT 1 FROM claims WHERE claim_id=?", (rogue,))), None) is not None
    state_before = project.state_path.read_bytes()

    backup = create_story_state_backup(root)
    backup_path = project.metadata_dir / "backups" / backup["backup_name"]
    backup_state = json.loads(backup_path.read_text(encoding="utf-8"))["state"]
    assert {item["claim_id"] for item in backup_state["writer_claims"]} == {legitimate}

    bundle = export_story_bundle(project, store)
    assert {item["claim_id"] for item in bundle["state"]["writer_claims"]} == {legitimate}
    assert project.state_path.read_bytes() == state_before
    store.close()

    rebuilt = rebuild_story_project_index(root, writer_confirmed=True)
    assert rebuilt["rebuilt"] is True
    reopened = StoryProject.open(root)
    assert reopened.state_path.read_bytes() == state_before
    rebuilt_store = StoryStore(reopened.cache_path)
    assert next(iter(rebuilt_store.rows("SELECT 1 FROM claims WHERE claim_id=?", (legitimate,))), None) is not None
    assert next(iter(rebuilt_store.rows("SELECT 1 FROM claims WHERE claim_id=?", (rogue,))), None) is None
    rebuilt_store.close()


def test_concurrent_story_operations_serialize_without_lost_updates(tmp_path: Path) -> None:
    root = tmp_path / "concurrent-story"
    root.mkdir()
    for index in range(12):
        (root / f"chapter-{index:02d}.md").write_text(
            f"# Chapter {index}\n\nText {index}.\n",
            encoding="utf-8",
        )
    StoryProject.open(root)

    def create_claim(index: int) -> None:
        apply_story_writer_mutation(
            root,
            "claim",
            {"predicate": f"parallel_{index:02d}", "literal_value": index},
            writer_confirmed=True,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(create_claim, range(24)))

    durable = StoryProject.open(root)
    parallel_claims = {
        str(item.get("predicate"))
        for item in durable.state.get("writer_claims", [])
        if isinstance(item, dict) and str(item.get("predicate", "")).startswith("parallel_")
    }
    assert parallel_claims == {f"parallel_{index:02d}" for index in range(24)}

    def override_source(index: int) -> None:
        set_source_override(
            root,
            f"chapter-{index:02d}.md",
            roles=["manuscript"],
            authority="MANUSCRIPT_OBSERVED",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(override_source, range(12)))

    reopened = StoryProject.open(root)
    overrides = reopened.manifest.get("source_overrides", {})
    assert isinstance(overrides, dict)
    assert set(overrides) == {f"chapter-{index:02d}.md" for index in range(12)}


def test_project_lock_serializes_separate_local_processes(tmp_path: Path) -> None:
    root = tmp_path / "cross-process-story"
    root.mkdir()
    for index in range(6):
        (root / f"part-{index}.md").write_text(f"# Part {index}\nText.\n", encoding="utf-8")
    StoryProject.open(root)

    code = (
        "from backend.story.service import set_source_override; import sys; "
        "set_source_override(sys.argv[1], sys.argv[2], roles=['manuscript'], "
        "authority='MANUSCRIPT_OBSERVED')"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(root), f"part-{index}.md"],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(6)
    ]
    failures: list[str] = []
    for process in processes:
        _stdout, stderr = process.communicate(timeout=30)
        if process.returncode:
            failures.append(stderr)
    assert failures == []

    reopened = StoryProject.open(root)
    overrides = reopened.manifest.get("source_overrides", {})
    assert isinstance(overrides, dict)
    assert set(overrides) == {f"part-{index}.md" for index in range(6)}


def test_proposal_acceptance_rolls_back_if_review_status_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _project_obj, store = _project(tmp_path, "proposal-review-atomic")
    store.close()
    submitted = submit_story_project_proposal(
        root,
        proposal_kind="canon_fact",
        target_mutation="claim",
        payload={"predicate": "must_be_atomic", "literal_value": True},
    )
    proposal_id = str(submitted["proposal_id"])
    original_save = StoryProject.save_state
    calls = 0

    def fail_review_status(self: StoryProject) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected proposal review persistence failure")
        original_save(self)

    monkeypatch.setattr(StoryProject, "save_state", fail_review_status)
    with pytest.raises(OSError, match="injected proposal review persistence failure"):
        review_story_project_proposal(
            root,
            proposal_id=proposal_id,
            decision="ACCEPTED",
            writer_confirmed=True,
        )

    reopened = StoryProject.open(root)
    assert not any(
        isinstance(item, dict) and item.get("predicate") == "must_be_atomic"
        for item in reopened.state.get("writer_claims", [])
    )
    proposal_record = next(
        item
        for item in reopened.state.get("story_proposals", [])
        if isinstance(item, dict) and item.get("proposal_id") == proposal_id
    )
    assert proposal_record["status"] == "PROPOSED"
    assert recovery_status(reopened)["recovery_required"] is False


def test_repeated_index_rebuild_is_semantically_and_portably_idempotent(tmp_path: Path) -> None:
    root, _project_obj, store = _project(tmp_path, "rebuild-idempotence")
    store.close()
    entity = apply_story_writer_mutation(
        root,
        "entity",
        {"canonical_name": "Mara", "entity_type": "character", "aliases": ["The Bellkeeper"]},
        writer_confirmed=True,
    )["record_id"]
    apply_story_writer_mutation(
        root,
        "claim",
        {"subject_entity_id": entity, "predicate": "keeps_bell", "literal_value": True},
        writer_confirmed=True,
    )

    with story_runtime(root, initialize=False) as (project, current_store):
        fingerprint_before = project_model_fingerprint(project, current_store)["fingerprint"]
    bundle_before = export_story_project(root)
    state_before = StoryProject.open(root).state_path.read_bytes()

    for _ in range(2):
        result = rebuild_story_project_index(root, writer_confirmed=True)
        assert result["rebuilt"] is True
        with story_runtime(root, initialize=False) as (project, current_store):
            assert project_model_fingerprint(project, current_store)["fingerprint"] == fingerprint_before
        assert export_story_project(root) == bundle_before
        assert StoryProject.open(root).state_path.read_bytes() == state_before


def test_interrupted_portable_import_rolls_back_both_durable_metadata_files(tmp_path: Path, monkeypatch) -> None:
    root, project, store = _project(tmp_path)
    bundle = export_story_bundle(project, store)
    store.close()
    bundle["manifest"]["active_manuscripts"] = ["chapter.md"]
    bundle["state"]["writer_claims"] = [
        {
            "claim_id": "incoming-claim",
            "project_id": "portable-source",
            "predicate": "incoming_fact",
            "literal_value": True,
            "qualifiers": {},
            "status": "CONFIRMED_CANON",
            "branch_id": "mainline",
            "confidence": 1.0,
            "created_by": "writer",
            "evidence": [],
        }
    ]
    manifest_before = project.manifest_path.read_bytes()
    state_before = project.state_path.read_bytes()
    real_save_state = StoryProject.save_state

    def fail_import_state_save(self: StoryProject) -> None:
        if self.state.get("writer_claims"):
            raise OSError("injected interruption between durable metadata writes")
        real_save_state(self)

    monkeypatch.setattr(StoryProject, "save_state", fail_import_state_save)
    with pytest.raises(OSError, match="injected interruption"):
        import_story_bundle(root, bundle, writer_confirmed=True)
    monkeypatch.setattr(StoryProject, "save_state", real_save_state)

    interrupted = StoryProject.open(root)
    assert interrupted.manifest_path.read_bytes() != manifest_before
    assert interrupted.state_path.read_bytes() == state_before
    assert recovery_status(interrupted)["recovery_required"] is True

    recovered = recover_story_project(root, writer_confirmed=True)
    assert recovered["durable_metadata_rolled_back"] is True
    reopened = StoryProject.open(root)
    assert reopened.manifest_path.read_bytes() == manifest_before
    assert reopened.state_path.read_bytes() == state_before
    assert recovery_status(reopened)["recovery_required"] is False
    rebuilt_store = StoryStore(reopened.cache_path)
    assert rebuilt_store.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert list(rebuilt_store.rows("PRAGMA foreign_key_check")) == []
    rebuilt_store.close()


def test_direct_sidecar_rejects_oversized_branch_writer_model_and_story_tool_inputs(tmp_path: Path) -> None:
    from backend.sidecar import dispatch

    root, project, store = _project(tmp_path)
    store.close()
    state_before = project.state_path.read_bytes()
    with pytest.raises(ValueError, match="1-500 overlay IDs"):
        dispatch(
            _sidecar_request(
                "story_branch_prepare_merge",
                project_root=str(root),
                branch_id="ALT-NOT-USED",
                overlay_ids=[f"overlay-{index}" for index in range(501)],
            )
        )
    with pytest.raises(ValueError, match="nested too deeply"):
        dispatch(
            _sidecar_request(
                "story_writer_model_observe",
                project_root=str(root),
                events=[{"type": "USER_ACCEPTED_SUGGESTION", "nested": {"a": {"b": {"c": {"d": {"e": 1}}}}}}],
            )
        )
    with pytest.raises(ValueError, match="array argument exceeds"):
        dispatch(
            _sidecar_request(
                "story_tool",
                project_root=str(root),
                tool_id="get_story_context",
                arguments={"prompt": "Context", "user_pins": [f"pin-{index}" for index in range(1_001)]},
            )
        )
    assert StoryProject.open(root).state_path.read_bytes() == state_before


def test_every_writer_mutation_kind_round_trips_through_durable_state_and_cache_rebuild(tmp_path: Path) -> None:
    root, project, store = _project(tmp_path)
    story_unit_id = StoryQueryEngine(project, store).list_story_units()[0]["story_unit_id"]
    store.close()

    mara = apply_story_writer_mutation(
        root,
        "entity",
        {"canonical_name": "Mara Audit", "entity_type": "character", "entity_id": "audit-mara"},
        writer_confirmed=True,
    )["record_id"]
    iven = apply_story_writer_mutation(
        root,
        "entity",
        {"canonical_name": "Iven Audit", "entity_type": "character", "entity_id": "audit-iven"},
        writer_confirmed=True,
    )["record_id"]
    apply_story_writer_mutation(
        root,
        "entity_alias",
        {"entity_id": mara, "alias": "Captain Mara Audit"},
        writer_confirmed=True,
    )
    claim = apply_story_writer_mutation(
        root,
        "claim",
        {"subject_entity_id": mara, "predicate": "audit_fact", "literal_value": "bell"},
        writer_confirmed=True,
    )["record_id"]
    apply_story_writer_mutation(
        root,
        "promote_claim",
        {"claim_id": claim, "status": "AUTHOR_LOCKED"},
        writer_confirmed=True,
    )
    event = apply_story_writer_mutation(
        root,
        "timeline_event",
        {"title": "Mara rings the audit bell", "story_unit_id": story_unit_id, "event_id": "audit-event"},
        writer_confirmed=True,
    )["record_id"]
    apply_story_writer_mutation(
        root,
        "world_state",
        {"entity_id": mara, "state_type": "location", "value": "audit tower", "state_id": "audit-world"},
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "character_knowledge",
        {
            "character_id": mara,
            "claim_id": claim,
            "state": "KNOWS",
            "acquired_at": story_unit_id,
            "knowledge_id": "audit-knowledge",
        },
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "reader_state",
        {"claim_id": claim, "state": "KNOWS", "story_unit_id": story_unit_id, "reader_state_id": "audit-reader"},
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "relationship",
        {
            "entity_a": mara,
            "entity_b": iven,
            "relationship_type": "trusts",
            "state": {"level": "guarded"},
            "relationship_id": "audit-relationship",
        },
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "thread",
        {"title": "Audit thread", "state": "OPEN", "thread_id": "audit-thread"},
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "promise",
        {"item_type": "READER_QUESTION", "title": "Audit promise?", "state": "OPEN", "item_id": "audit-promise"},
        writer_confirmed=True,
    )
    decision = apply_story_writer_mutation(
        root,
        "decision",
        {
            "description": "Mara chooses the audit bell",
            "agent_entity_id": mara,
            "story_unit_id": story_unit_id,
            "decision_id": "audit-decision",
        },
        writer_confirmed=True,
    )["record_id"]
    apply_story_writer_mutation(
        root,
        "causal_edge",
        {
            "cause_kind": "decision",
            "cause_id": decision,
            "effect_kind": "event",
            "effect_id": event,
            "edge_id": "audit-edge",
        },
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "opposition",
        {
            "objective_id": "ring-audit-bell",
            "description": "Iven blocks the stairs",
            "source_entity_id": iven,
            "story_unit_id": story_unit_id,
            "opposition_id": "audit-opposition",
        },
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "scene_contract",
        {"story_unit_id": story_unit_id, "contract": {"purpose": "audit durable scene contract"}},
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "author_decision",
        {
            "title": "Audit author decision",
            "decision": "Mara chooses",
            "story_unit_id": story_unit_id,
            "author_decision_id": "audit-author-decision",
        },
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "writer_preference",
        {"statement": "Prefer audit clarity", "preference_id": "audit-preference"},
        writer_confirmed=True,
    )
    apply_story_writer_mutation(
        root,
        "story_lens",
        {"name": "Audit Lens", "definition": "Track audit bell imagery", "lens_id": "audit-lens"},
        writer_confirmed=True,
    )

    state_before = json.loads(StoryProject.open(root).state_path.read_text(encoding="utf-8"))
    expected_nonempty = {
        "writer_entities",
        "writer_entity_aliases",
        "writer_claims",
        "timeline_events",
        "world_state",
        "knowledge_state",
        "reader_state",
        "relationships",
        "threads",
        "promise_items",
        "decisions",
        "causal_edges",
        "opposition_state",
        "scene_contracts",
        "author_decisions",
        "writer_preferences",
        "story_lenses",
    }
    assert all(state_before[key] for key in expected_nonempty)
    assert next(item for item in state_before["writer_claims"] if item["claim_id"] == claim)["status"] == "AUTHOR_LOCKED"

    rebuild_story_project_index(root, writer_confirmed=True)
    reopened = StoryProject.open(root)
    assert json.loads(reopened.state_path.read_text(encoding="utf-8")) == state_before
    rebuilt_store = StoryStore(reopened.cache_path)
    assert rebuilt_store.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert list(rebuilt_store.rows("PRAGMA foreign_key_check")) == []
    for table in (
        "claims",
        "timeline_events",
        "world_state",
        "knowledge_state",
        "reader_state",
        "relationships",
        "threads",
        "promise_items",
        "decisions",
        "causal_edges",
        "opposition_state",
        "scene_contracts",
        "author_decisions",
        "writer_preferences",
        "story_lenses",
    ):
        assert rebuilt_store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0  # noqa: S608
    rebuilt_store.close()
