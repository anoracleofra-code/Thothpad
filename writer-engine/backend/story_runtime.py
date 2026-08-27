from __future__ import annotations

import ntpath
import posixpath
import re
from copy import deepcopy
from typing import Any

from backend.llm_clients import complete_chat, scrub_provider
from backend.models import RunRequest
from backend.story_intelligence import (
    build_story_messages,
    retrieve_project_context,
    validate_story_response,
)

# The native desktop already minimizes the context it sends. This module is a
# second, independent model-boundary guard so a future native regression cannot
# casually expose machine-local identity to a remote provider.
_PRIVATE_KEYS = {
    "analysis_id",
    "api_key",
    "baseline_analysis_id",
    "checkpoint_dir",
    "checkpoint_directory",
    "checkpoint_path",
    "credential_id",
    "project_root",
    "target_generation",
}
_PATH_KEYS = {
    "document_path",
    "file_path",
    "filepath",
    "folder_path",
    "local_path",
    "path",
}
_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

_ASYNC_TOOL_GUIDANCE = "\n".join(
    [
        "",
        "ASYNCHRONOUS NATIVE TOOL RULES",
        "- run_prose_scan and hydrate_prose_category can be asynchronous.",
        "- Request an asynchronous tool by itself in a tool-call round.",
        "- A result with pending=true means the operation has started, not finished.",
        "- Do not claim completion or request a dependent mutation while a tool is pending.",
        "- Wait for a later trusted THOTHPAD TOOL RESULT with completed=true or an error.",
        "- After a fresh scan, hydrate the lens you need before assuming its findings are complete.",
        "",
        "OBJECTIVE GRAMMAR WORKFLOW",
        "1. Request run_prose_scan with scope=document.",
        "2. Wait for its completed native result.",
        "3. Request hydrate_prose_category with category=grammar_mechanics.",
        "4. Wait for the hydration result before reasoning over all grammar findings.",
        "5. Use get_prose_summary/get_prose_findings when you need to explain the evidence.",
        "6. If the user asked to correct objective grammar, request apply_objective_grammar_fixes.",
        "7. That final R4 mutation is authorized and checkpointed by native ThothPad, not by you.",
        "",
    ]
)


def _looks_absolute_path(value: str) -> bool:
    return value.startswith("/") or bool(_WINDOWS_ABSOLUTE.match(value))


def _path_leaf(value: str) -> str:
    if _WINDOWS_ABSOLUTE.match(value):
        leaf = ntpath.basename(value.rstrip("\\/"))
    else:
        leaf = posixpath.basename(value.rstrip("/"))
    return leaf or "<local-path>"


def _is_path_key(key: str) -> bool:
    folded = key.casefold()
    return (
        folded in _PATH_KEYS
        or folded.endswith("_path")
        or folded.endswith("_paths")
        or folded.endswith("_directory")
        or folded.endswith("_root")
    )


def sanitize_model_context(value: Any, *, key_hint: str = "") -> Any:
    """Return a copy safe to serialize into a model-visible context block.

    This guard intentionally operates only on app/tool/activity metadata. It is
    not used on manuscript text or retrieved project excerpts, where paths are
    already represented by project-relative names.
    """

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            folded = key.casefold()
            if folded in _PRIVATE_KEYS:
                if folded.startswith("checkpoint_") and item:
                    result["checkpoint_created"] = True
                continue
            result[key] = sanitize_model_context(item, key_hint=key)
        return result

    if isinstance(value, list):
        return [sanitize_model_context(item, key_hint=key_hint) for item in value]

    if isinstance(value, str) and _is_path_key(key_hint) and _looks_absolute_path(value):
        return _path_leaf(value)

    return value


def build_guarded_story_messages(
    payload: dict[str, Any],
    retrieved: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Build the model prompt after stripping native-only machine metadata."""

    safe_payload = deepcopy(payload)
    for key in ("app_state", "activity_events", "tool_results"):
        safe_payload[key] = sanitize_model_context(safe_payload.get(key, {}))

    messages = build_story_messages(safe_payload, retrieved)
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = messages[0]["content"].rstrip() + _ASYNC_TOOL_GUIDANCE
    return messages


def run_story_intelligence(
    request: RunRequest,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute one Story Intelligence model turn through the guarded boundary."""

    retrieved = retrieve_project_context(payload)
    messages = build_guarded_story_messages(payload, retrieved)
    response = complete_chat(messages, request.provider)
    if response.error:
        story = {
            "message": "",
            "tool_calls": [],
            "annotations": [],
            "scene_context_proposal": {},
            "character_proposals": [],
            "structured": False,
        }
        errors = [response.error]
    else:
        # Response validation uses the original payload so exact manuscript
        # quote resolution and document revisions remain authoritative.
        story = validate_story_response(response.text, payload)
        errors = []

    return {
        "mode": "story_chat",
        "profile": request.profile,
        "output_text": story["message"],
        "llm_errors": errors,
        "story_intelligence": story,
        "provider": scrub_provider(request.provider),
        "retrieved_project_files": [item["path"] for item in retrieved],
        "persisted": False,
    }
