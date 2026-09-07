from __future__ import annotations

import json

from backend.llm_clients import LLMResponse
from backend.models import RunRequest
from backend.story.authority import AuthorityStatus, KnowledgeStatus
from backend.story.claims import create_claim
from backend.story.ingest import ProjectIngestor
from backend.story.knowledge import set_character_knowledge
from backend.story.project import StoryProject
from backend.story.query import StoryQueryEngine
from backend.story.store import StoryStore
from backend.story.writer_state import add_writer_branch_overlay, create_writer_branch
from backend.story_intelligence import try_parse_story_payload
from backend.story_runtime import (
    build_guarded_story_messages,
    run_story_intelligence,
    sanitize_model_context,
)


def _payload() -> dict:
    value = {
        "kind": "story_intelligence_v1",
        "prompt": "Correct the objective grammar mistakes and explain what changed.",
        "document": "A cold night.",
        "document_path": r"C:\Users\writer\Novel\chapters\01.md",
        "document_revision": 3,
        "project_root": r"C:\Users\writer\Novel",
        "scene_context": {},
        "characters": [],
        "active_character": {},
        "history": [],
        "tool_round": 1,
        "tool_manifest": [
            {
                "id": "run_prose_scan",
                "risk": "R2",
                "description": "Run a fresh prose scan.",
            },
            {
                "id": "hydrate_prose_category",
                "risk": "R2",
                "description": "Hydrate a prose lens.",
                "arguments": "category: lens id",
            },
            {
                "id": "apply_objective_grammar_fixes",
                "risk": "R4",
                "description": "Apply deterministic grammar fixes.",
            },
        ],
        "app_state": {
            "editor": {
                "document_path": r"C:\Users\writer\Novel\chapters\01.md",
                "modified": True,
            },
            "internal": {
                "analysis_id": "analysis-secret",
                "project_root": r"C:\Users\writer\Novel",
            },
        },
        "activity_events": [
            {
                "type": "AGENT_TRANSACTION_APPLIED",
                "checkpoint_path": r"C:\Users\writer\Novel\.thothpad\recovery\private.md",
            }
        ],
        "tool_results": [
            {
                "call_id": "scan-1",
                "tool": "run_prose_scan",
                "ok": True,
                "result": {
                    "ok": True,
                    "pending": True,
                    "analysis_id": "analysis-secret",
                    "baseline_analysis_id": "older-secret",
                    "checkpoint_path": "/home/writer/.thothpad/recovery/snapshot.md",
                    "target_generation": 42,
                    "document_path": "/home/writer/Novel/chapters/01.md",
                },
            }
        ],
    }
    parsed = try_parse_story_payload(json.dumps(value))
    assert parsed is not None
    return parsed


def test_model_boundary_strips_machine_local_identity_without_mutating_payload():
    payload = _payload()
    original_document_path = payload["document_path"]
    original_project_root = payload["project_root"]

    messages = build_guarded_story_messages(payload, [])
    visible = "\n".join(message["content"] for message in messages)

    assert r"C:\Users\writer" not in visible
    assert "/home/writer" not in visible
    assert "analysis-secret" not in visible
    assert "older-secret" not in visible
    assert "01.md" in visible
    assert "checkpoint_created" in visible
    assert payload["document_path"] == original_document_path
    assert payload["project_root"] == original_project_root


def test_async_tool_protocol_is_explicit_in_system_message():
    messages = build_guarded_story_messages(_payload(), [])
    system = messages[0]["content"]

    assert "ASYNCHRONOUS NATIVE TOOL RULES" in system
    assert "pending=true means the operation has started, not finished" in system
    assert "Request an asynchronous tool by itself" in system
    assert "hydrate_prose_category" in system
    assert "apply_objective_grammar_fixes" in system
    assert "authorized and checkpointed by native ThothPad" in system


def test_local_only_story_routing_blocks_remote_provider_before_model_call(monkeypatch):
    payload = _payload()
    payload["model_routing"] = {"privacy": "local_only", "task": "chat", "quality": "balanced"}
    called = False

    def fake_complete(messages, provider):
        nonlocal called
        del messages, provider
        called = True
        return LLMResponse(text="should not run", provider="test", model="test")

    monkeypatch.setattr("backend.story_runtime.complete_chat", fake_complete)
    result = run_story_intelligence(
        RunRequest(
            provider={
                "provider": "anthropic",
                "model": "remote-model",
                "base_url": "https://api.anthropic.com/v1",
            }
        ),
        payload,
    )
    assert called is False
    assert result["model_routing"]["blocked"] is True
    assert "local_only" in result["llm_errors"][0]


def test_sanitizer_handles_windows_and_posix_paths_recursively():
    value = {
        "document_path": r"D:\Drafts\Book\chapter-7.md",
        "nested": {
            "path": "/Users/writer/Book/outline.md",
            "checkpoint_path": r"D:\Drafts\Book\.thothpad\recovery\secret.md",
            "analysis_id": "opaque-analysis-id",
        },
        "paths": [
            "/Users/writer/Book/a.md",
            r"D:\Drafts\Book\b.md",
        ],
        "message": "A normal sentence / with slashes is not a path field.",
    }

    safe = sanitize_model_context(value)

    assert safe["document_path"] == "chapter-7.md"
    assert safe["nested"]["path"] == "outline.md"
    assert safe["nested"]["checkpoint_created"] is True
    assert "checkpoint_path" not in safe["nested"]
    assert "analysis_id" not in safe["nested"]
    assert safe["paths"] == ["a.md", "b.md"]
    assert safe["message"] == value["message"]


def test_restricted_story_turn_masks_future_current_document_text(tmp_path, monkeypatch):
    root = tmp_path / "novel"
    root.mkdir()
    first = "# Chapter One\nA 📝 clue is visible now.\n"
    future = "# Chapter Two\nFUTURE SECRET: the archivist is the killer.\n"
    document = first + future
    path = root / "book.md"
    path.write_text(document, encoding="utf-8")

    raw = {
        "kind": "story_intelligence_v1",
        "prompt": "What can the reader know here?",
        "document": document,
        "document_path": str(path),
        "document_revision": 1,
        "project_root": str(root),
        "scene_context": {},
        "characters": [],
        "active_character": {},
        "history": [],
        "app_state": {},
        "activity_events": [],
        "tool_results": [],
        "tool_round": 0,
        "tool_manifest": [],
        "epistemic_mode": "cold_reader",
        "scope": {
            "id": "native-chapter-one",
            "title": "Chapter One",
            "start": 0,
            # Native Qt offsets are UTF-16, including the astral notebook emoji.
            "end": len(first.encode("utf-16-le")) // 2,
            "level": 1,
        },
    }
    payload = try_parse_story_payload(json.dumps(raw))
    assert payload is not None
    captured: dict[str, object] = {}

    def fake_complete(messages, provider):
        captured["messages"] = messages
        captured["provider"] = provider
        return LLMResponse(
            text=json.dumps(
                {
                    "message": "The reader can see the clue.",
                    "tool_calls": [],
                    "annotations": [],
                    "scene_context_proposal": {},
                    "character_proposals": [],
                    "memory_proposals": [],
                }
            ),
            provider="test",
            model="test",
        )

    monkeypatch.setattr("backend.story_runtime.complete_chat", fake_complete)
    result = run_story_intelligence(RunRequest(provider={"provider": "ollama", "model": "test"}), payload)
    visible = "\n".join(message["content"] for message in captured["messages"])

    assert "A 📝 clue is visible now." in visible
    assert "FUTURE SECRET" not in visible
    assert "truncated by ThothPad at the active epistemic boundary" in visible
    assert result["story_context_inspector"]["current_document_masked"] is True
    assert result["story_context_inspector"]["active_story_unit"]


def test_selected_branch_overlay_crosses_provider_boundary_without_contaminating_mainline(tmp_path, monkeypatch):
    root = tmp_path / "novel"
    root.mkdir()
    document = "# Chapter One\nAlice refuses.\n"
    path = root / "book.md"
    path.write_text(document, encoding="utf-8")

    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    ProjectIngestor(project, store).ingest()
    unit_id = StoryQueryEngine(project, store).list_story_units()[0]["story_unit_id"]
    branch = create_writer_branch(
        project,
        store,
        fork_story_unit=unit_id,
        branch_id="ALT-PROVIDER",
    )
    add_writer_branch_overlay(
        project,
        store,
        branch_id=branch,
        record_kind="claim",
        record_id="alice-confesses",
        operation="ADD",
        payload={"predicate": "confesses", "value": True, "story_unit_id": unit_id},
    )
    store.commit()
    store.close()

    captured: list[list[dict[str, str]]] = []

    def fake_complete(messages, provider):
        del provider
        captured.append(messages)
        return LLMResponse(
            text=json.dumps({"message": "Branch inspected.", "tool_calls": [], "annotations": []}),
            provider="test",
            model="test",
        )

    monkeypatch.setattr("backend.story_runtime.complete_chat", fake_complete)
    base = {
        "kind": "story_intelligence_v1",
        "prompt": "What does Alice do in this version?",
        "document": document,
        "document_path": str(path),
        "document_revision": 1,
        "project_root": str(root),
        "scene_context": {},
        "characters": [],
        "active_character": {},
        "history": [],
        "app_state": {},
        "activity_events": [],
        "tool_results": [],
        "tool_round": 0,
        "tool_manifest": [],
        "epistemic_mode": "author_omniscient",
        "scope": {"id": "manuscript"},
    }

    alternate_payload = try_parse_story_payload(json.dumps({**base, "active_branch": branch}))
    assert alternate_payload is not None
    alternate = run_story_intelligence(
        RunRequest(provider={"provider": "ollama", "model": "test"}),
        alternate_payload,
    )
    alternate_visible = "\n".join(message["content"] for message in captured[-1])
    assert '"active_branch":"ALT-PROVIDER"' in alternate_visible
    assert '"record_id":"alice-confesses"' in alternate_visible
    assert '"authority":"BRANCH_ONLY"' in alternate_visible
    assert alternate["story_context_inspector"]["active_branch"] == branch

    mainline_payload = try_parse_story_payload(json.dumps({**base, "active_branch": "mainline"}))
    assert mainline_payload is not None
    run_story_intelligence(
        RunRequest(provider={"provider": "ollama", "model": "test"}),
        mainline_payload,
    )
    mainline_visible = "\n".join(message["content"] for message in captured[-1])
    assert '"active_branch":"mainline"' in mainline_visible
    assert "alice-confesses" not in mainline_visible
    assert "BRANCH_ONLY" not in mainline_visible


def test_character_story_state_is_epistemically_filtered_before_provider_boundary(tmp_path, monkeypatch):
    root = tmp_path / "novel"
    root.mkdir()
    (root / "Alice profile.md").write_text(
        "# Alice\nAppearance: tired\nGoal: protect the archive\nRelationships: trusts nobody\n",
        encoding="utf-8",
    )
    first = "# Chapter One\nAlice reaches the locked archive with a brass key.\n"
    second = "# Chapter Two\nAlice learns a private number.\n"
    document = first + second
    path = root / "book.md"
    path.write_text(document, encoding="utf-8")

    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    ProjectIngestor(project, store).ingest()
    query = StoryQueryEngine(project, store)
    alice_id = query.resolve_entity("Alice")["matches"][0]["entity_id"]
    source = store.source_by_path("book.md")
    assert source is not None
    units = query.list_story_units(source_id=source["source_id"])
    has_key = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=alice_id,
        predicate="has_key",
        literal_value=True,
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="runtime-has-key",
    )
    future_code = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=alice_id,
        predicate="vault_code",
        literal_value="7319",
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="runtime-vault-code",
    )
    set_character_knowledge(
        store,
        character_id=alice_id,
        claim_id=has_key,
        state=KnowledgeStatus.KNOWS,
        acquired_at=None,
    )
    set_character_knowledge(
        store,
        character_id=alice_id,
        claim_id=future_code,
        state=KnowledgeStatus.KNOWS,
        acquired_at=units[1]["story_unit_id"],
    )
    store.commit()
    store.close()

    raw = {
        "kind": "story_intelligence_v1",
        "prompt": "What does Alice know here?",
        "document": document,
        "document_path": str(path),
        "document_revision": 1,
        "project_root": str(root),
        "scene_context": {"pov": "Alice"},
        "characters": [],
        "active_character": {"name": "Alice"},
        "history": [],
        "app_state": {},
        "activity_events": [],
        "tool_results": [],
        "tool_round": 0,
        "tool_manifest": [],
        "epistemic_mode": "character",
        "scope": {
            "id": "native-chapter-one",
            "title": "Chapter One",
            "start": 0,
            "end": len(first.encode("utf-16-le")) // 2,
            "level": 1,
        },
    }
    payload = try_parse_story_payload(json.dumps(raw))
    assert payload is not None
    captured: dict[str, object] = {}

    def fake_complete(messages, provider):
        captured["messages"] = messages
        captured["provider"] = provider
        return LLMResponse(
            text=json.dumps(
                {
                    "message": "Alice knows she has the key.",
                    "tool_calls": [],
                    "annotations": [],
                    "scene_context_proposal": {},
                    "character_proposals": [],
                    "memory_proposals": [],
                }
            ),
            provider="test",
            model="test",
        )

    monkeypatch.setattr("backend.story_runtime.complete_chat", fake_complete)
    run_story_intelligence(RunRequest(provider={"provider": "ollama", "model": "test"}), payload)
    visible = "\n".join(message["content"] for message in captured["messages"])

    assert '"story_state"' in visible
    assert '"objective_claims":[]' in visible
    assert '"predicate":"has_key"' in visible
    assert '"state":"KNOWS"' in visible
    assert "vault_code" not in visible
    assert "7319" not in visible


def test_current_pov_uses_scene_pov_for_structured_knowledge(tmp_path, monkeypatch):
    root = tmp_path / "novel"
    root.mkdir()
    (root / "Alice profile.md").write_text(
        "# Alice\nAppearance: tired\nGoal: protect the archive\nRelationships: trusts nobody\n",
        encoding="utf-8",
    )
    document = "# Chapter One\nAlice carries the brass key.\n"
    path = root / "book.md"
    path.write_text(document, encoding="utf-8")

    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    ProjectIngestor(project, store).ingest()
    query = StoryQueryEngine(project, store)
    alice_id = query.resolve_entity("Alice")["matches"][0]["entity_id"]
    claim_id = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=alice_id,
        predicate="has_key",
        literal_value=True,
        status=AuthorityStatus.CONFIRMED_CANON,
        created_by="writer",
        stable_key="runtime-pov-has-key",
    )
    set_character_knowledge(
        store,
        character_id=alice_id,
        claim_id=claim_id,
        state=KnowledgeStatus.KNOWS,
        acquired_at=None,
    )
    store.commit()
    store.close()

    raw = {
        "kind": "story_intelligence_v1",
        "prompt": "What can the POV character act on?",
        "document": document,
        "document_path": str(path),
        "document_revision": 1,
        "project_root": str(root),
        "scene_context": {"pov": "Alice"},
        "characters": [],
        "active_character": {},
        "history": [],
        "app_state": {},
        "activity_events": [],
        "tool_results": [],
        "tool_round": 0,
        "tool_manifest": [],
        "epistemic_mode": "current_pov",
        "scope": {
            "id": "native-chapter-one",
            "title": "Chapter One",
            "start": 0,
            "end": len(document.encode("utf-16-le")) // 2,
            "level": 1,
        },
    }
    payload = try_parse_story_payload(json.dumps(raw))
    assert payload is not None
    captured: dict[str, object] = {}

    def fake_complete(messages, provider):
        captured["messages"] = messages
        return LLMResponse(
            text=json.dumps({"message": "Alice can act on the key.", "tool_calls": [], "annotations": []}),
            provider="test",
            model="test",
        )

    monkeypatch.setattr("backend.story_runtime.complete_chat", fake_complete)
    run_story_intelligence(RunRequest(provider={"provider": "ollama", "model": "test"}), payload)
    visible = "\n".join(message["content"] for message in captured["messages"])
    assert '"predicate":"has_key"' in visible
    assert '"character":"Alice"' in visible
