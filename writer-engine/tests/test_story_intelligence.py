from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.story_intelligence import (
    MAX_TOOL_CALLS,
    build_story_messages,
    retrieve_project_context,
    try_parse_story_payload,
    validate_story_response,
)


def _payload(document: str = "The lamp burned low.") -> dict:
    return {
        "kind": "story_intelligence_v1",
        "prompt": "Inspect this scene.",
        "document": document,
        "document_path": "",
        "document_revision": 7,
        "project_root": "",
        "scene_context": {},
        "characters": [],
        "active_character": {},
        "history": [],
        "app_state": {},
        "activity_events": [],
        "tool_results": [],
        "tool_round": 0,
        "tool_manifest": [
            {
                "id": "get_prose_summary",
                "risk": "R0",
                "description": "Read prose state.",
            },
            {
                "id": "set_theme",
                "risk": "R1",
                "description": "Set light/dark theme.",
                "arguments": "theme: light|dark",
            },
        ],
    }


def _validated(document: str = "The lamp burned low.") -> dict:
    parsed = try_parse_story_payload(json.dumps(_payload(document)))
    assert parsed is not None
    return parsed


def test_story_payload_bounds_and_sanitizes_native_context():
    value = _payload()
    value["tool_manifest"] = [
        {"id": "get_prose_summary", "risk": "R0", "description": "ok"},
        {"id": "../shell", "risk": "R0", "description": "bad id"},
        {"id": "set_theme", "risk": "R99", "description": "bad risk"},
        {"id": "set_theme", "risk": "R1", "description": "ok"},
    ]
    value["activity_events"] = [{"type": "USER_EDITED_AGENT_TARGET", "before": "x"}] * 100
    value["tool_results"] = [{"tool": "get_prose_summary", "ok": True}] * 100
    value["tool_round"] = True

    parsed = try_parse_story_payload(json.dumps(value))
    assert parsed is not None
    assert [item["id"] for item in parsed["tool_manifest"]] == [
        "get_prose_summary",
        "set_theme",
    ]
    assert len(parsed["activity_events"]) == 24
    assert len(parsed["tool_results"]) == 32
    # bool is deliberately not accepted as an integer round number.
    assert parsed["tool_round"] == 0


def test_duplicate_annotation_requires_occurrence():
    document = "Cold rain. Cold rain."
    payload = _validated(document)
    response = {
        "message": "The echo is visible.",
        "annotations": [
            {
                "quote": "Cold rain.",
                "category": "pacing",
                "comment": "Repeated beat.",
            }
        ],
    }
    story = validate_story_response(json.dumps(response), payload)
    assert story["annotations"] == []

    response["annotations"][0]["occurrence"] = 2
    story = validate_story_response(json.dumps(response), payload)
    assert len(story["annotations"]) == 1
    assert story["annotations"][0]["occurrence"] == 2


def test_annotation_offsets_are_utf16_safe_after_astral_character():
    document = "A 📝 cold night."
    payload = _validated(document)
    response = {
        "message": "Marked it.",
        "annotations": [
            {
                "quote": "cold",
                "category": "voice",
                "comment": "Generic adjective here.",
            }
        ],
    }
    annotation = validate_story_response(json.dumps(response), payload)["annotations"][0]
    # Python codepoint offset is 4, but the astral emoji consumes two UTF-16 units.
    assert annotation["start_utf16"] == 5
    assert annotation["end_utf16"] == 9


def test_tool_calls_are_typed_bounded_and_get_stable_call_ids():
    payload = _validated()
    calls = [
        {"call_id": "read-prose", "tool": "get_prose_summary", "arguments": {"limit": 20}},
        {"tool": "set_theme", "arguments": {"theme": "dark"}},
    ]
    calls.extend(
        {"tool": "get_prose_summary", "arguments": {}}
        for _ in range(MAX_TOOL_CALLS + 4)
    )
    story = validate_story_response(
        json.dumps({"message": "", "tool_calls": calls}), payload
    )

    assert len(story["tool_calls"]) == MAX_TOOL_CALLS
    assert story["tool_calls"][0] == {
        "call_id": "read-prose",
        "tool": "get_prose_summary",
        "arguments": {"limit": 20},
    }
    assert story["tool_calls"][1]["call_id"] == "r0-c2"


def test_oversized_or_malformed_tool_arguments_get_no_authority():
    payload = _validated()
    huge = "x" * 30_000
    response = {
        "message": "I need a tool.",
        "tool_calls": [
            {"tool": "get_prose_summary", "arguments": huge},
            {"tool": "set_theme", "arguments": {"theme": huge}},
            {"tool": "../shell", "arguments": {}},
        ],
    }
    story = validate_story_response(json.dumps(response), payload)
    assert story["tool_calls"] == []


def test_unstructured_provider_output_is_plain_chat_only():
    payload = _validated()
    story = validate_story_response(
        "I changed the document and ran a shell command.", payload
    )
    assert story["structured"] is False
    assert story["tool_calls"] == []
    assert story["annotations"] == []
    assert story["scene_context_proposal"] == {}
    assert story["character_proposals"] == []


def test_tool_results_are_labeled_as_trusted_execution_facts():
    payload = _validated()
    payload["tool_results"] = [
        {
            "call_id": "theme-1",
            "tool": "set_theme",
            "ok": True,
            "result": {"checked": True},
        }
    ]
    messages = build_story_messages(payload, [])
    text = "\n".join(message["content"] for message in messages)
    assert "THOTHPAD TOOL RESULTS (trusted execution facts" in text
    assert "Do not claim a tool succeeded" in messages[0]["content"]


def test_project_retrieval_stays_in_root_and_skips_private_state(tmp_path: Path):
    root = tmp_path / "novel"
    root.mkdir()
    (root / "chapter.md").write_text("Mara crosses the glass desert.", encoding="utf-8")
    private = root / ".thothpad"
    private.mkdir()
    (private / "story-intelligence.json").write_text(
        "Mara secret recovery content", encoding="utf-8"
    )
    outside = tmp_path / "outside.md"
    outside.write_text("Mara outside secret", encoding="utf-8")

    link = root / "outside-link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        # Windows test workers may not grant symlink creation permission.
        pass

    payload = _validated()
    payload["project_root"] = str(root)
    payload["prompt"] = "What is Mara doing?"
    retrieved = retrieve_project_context(payload)
    paths = {item["path"] for item in retrieved}

    assert "chapter.md" in paths
    assert all(not path.startswith(".thothpad/") for path in paths)
    assert "outside-link.md" not in paths
    assert all("outside secret" not in item["text"] for item in retrieved)


def test_project_retrieval_rejects_missing_or_non_directory_root(tmp_path: Path):
    payload = _validated()
    payload["project_root"] = str(tmp_path / "missing")
    assert retrieve_project_context(payload) == []

    file_root = tmp_path / "not-a-directory.md"
    file_root.write_text("text", encoding="utf-8")
    payload["project_root"] = str(file_root)
    assert retrieve_project_context(payload) == []


def test_payload_must_have_nonempty_prompt():
    value = _payload()
    value["prompt"] = "   "
    with pytest.raises(ValueError, match="non-empty"):
        try_parse_story_payload(json.dumps(value))
