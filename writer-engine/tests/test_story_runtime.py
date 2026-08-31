from __future__ import annotations

import json

from backend.story_intelligence import try_parse_story_payload
from backend.story_runtime import build_guarded_story_messages, sanitize_model_context


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
