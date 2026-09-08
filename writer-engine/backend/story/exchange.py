from __future__ import annotations

import copy
from pathlib import Path, PurePosixPath
from typing import Any

from backend.story.compatibility import create_story_state_backup
from backend.story.ingest import ProjectIngestor
from backend.story.persistence import (
    hydrate_writer_state,
    persist_legacy_cache_writer_state_if_needed,
    persist_writer_state,
)
from backend.story.project import STORY_STATE_SCHEMA_VERSION, StoryProject
from backend.story.recovery import begin_recovery_operation, complete_recovery_operation
from backend.story.store import StoryStore

BUNDLE_VERSION = 1
MAX_BUNDLE_DEPTH = 8
MAX_BUNDLE_NODES = 250_000
MAX_BUNDLE_STRING_CHARS = 50_000
_MANIFEST_KEYS = ("source_rules", "authority_rules", "source_overrides", "entity_overrides", "active_manuscripts")
_STATE_KEYS = (
    "writer_entities",
    "writer_entity_aliases",
    "writer_claims",
    "writer_claim_relations",
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
    "branches",
    "branch_overlays",
    "branch_merge_history",
    "scene_contracts",
    "author_decisions",
    "writer_preferences",
    "story_lenses",
    "story_proposals",
)


def _safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    return not path.is_absolute() and ".." not in path.parts


def _bounded_bundle_value(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> Any:
    if budget is None:
        budget = [MAX_BUNDLE_NODES]
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("Story Project bundle contains too many values")
    if depth > MAX_BUNDLE_DEPTH:
        raise ValueError("Story Project bundle is nested too deeply")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > MAX_BUNDLE_STRING_CHARS:
            raise ValueError("Story Project bundle string exceeds the safe limit")
        return value
    if isinstance(value, int):
        if value.bit_length() > 4096:
            raise ValueError("Story Project bundle integer exceeds the safe limit")
        return value
    if isinstance(value, float):
        if not -1e12 <= value <= 1e12:
            raise ValueError("Story Project bundle number exceeds the safe range")
        return value
    if isinstance(value, list):
        if len(value) > 20_000:
            raise ValueError("Story Project bundle array exceeds the safe item limit")
        return [_bounded_bundle_value(item, depth=depth + 1, budget=budget) for item in value]
    if isinstance(value, dict):
        if len(value) > 500:
            raise ValueError("Story Project bundle object has too many fields")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 240:
                raise ValueError("Story Project bundle object keys must be bounded strings")
            result[key] = _bounded_bundle_value(item, depth=depth + 1, budget=budget)
        return result
    raise ValueError("Story Project bundle contains an unsupported JSON value")


def _sanitize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    bounded = _bounded_bundle_value(manifest)
    if not isinstance(bounded, dict):
        raise ValueError("Story Project bundle manifest must be an object")
    result: dict[str, Any] = {}
    for key in _MANIFEST_KEYS:
        default: list[Any] | dict[str, Any] = [] if key.endswith("rules") or key == "active_manuscripts" else {}
        value = copy.deepcopy(bounded.get(key, default))
        result[key] = value
    if not isinstance(result["source_rules"], list) or not isinstance(result["authority_rules"], list):
        raise ValueError("portable bundle rules must be arrays")
    if not isinstance(result["active_manuscripts"], list):
        raise ValueError("portable bundle active_manuscripts must be an array")
    if not isinstance(result["source_overrides"], dict) or not isinstance(result["entity_overrides"], dict):
        raise ValueError("portable bundle overrides must be objects")
    for path in result["source_overrides"]:
        if not _safe_relative(path):
            raise ValueError("portable bundle contains an unsafe source override path")
    for path in result["active_manuscripts"]:
        if not _safe_relative(path):
            raise ValueError("portable bundle contains an unsafe manuscript path")
    for rule in result["source_rules"]:
        if isinstance(rule, dict):
            pattern = rule.get("pattern")
            if not isinstance(pattern, str) or pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:
                raise ValueError("portable bundle contains an unsafe source rule")
        else:
            raise ValueError("portable bundle source rules must be objects")
    return result


def _sanitize_state(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    bounded = _bounded_bundle_value(state)
    if not isinstance(bounded, dict):
        raise ValueError("Story Project bundle state must be an object")
    result: dict[str, list[dict[str, Any]]] = {}
    for key in _STATE_KEYS:
        value = bounded.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"Story Project state field {key} must be an array")
        if any(not isinstance(item, dict) for item in value):
            raise ValueError(f"Story Project state field {key} must contain objects")
        result[key] = [dict(item) for item in value]
    return result


def export_story_bundle(project: StoryProject, store: StoryStore) -> dict[str, Any]:
    persist_legacy_cache_writer_state_if_needed(project, store)
    state = {key: copy.deepcopy(project.state.get(key, [])) for key in _STATE_KEYS}
    return {
        "format": "thothpad-story-project",
        "bundle_version": BUNDLE_VERSION,
        "source_project_id": project.project_id,
        "manifest": _sanitize_manifest(project.manifest),
        "state": state,
        "contains_source_text": False,
        "contains_absolute_paths": False,
    }


def import_story_bundle(
    root: str | Path,
    bundle: dict[str, Any],
    *,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("importing Story Project metadata requires explicit writer confirmation")
    if not isinstance(bundle, dict) or bundle.get("format") != "thothpad-story-project":
        raise ValueError("invalid Story Project bundle")
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError("unsupported Story Project bundle version")
    manifest = bundle.get("manifest")
    state = bundle.get("state")
    if not isinstance(manifest, dict) or not isinstance(state, dict):
        raise ValueError("Story Project bundle must contain manifest and state objects")
    safe_manifest = _sanitize_manifest(manifest)
    safe_state = _sanitize_state(state)
    project = StoryProject.open(root)
    backup = create_story_state_backup(root)
    recovery_token = begin_recovery_operation(project, "portable_import", snapshot_durable=True)
    try:
        for key in _MANIFEST_KEYS:
            project.manifest[key] = copy.deepcopy(safe_manifest.get(key, project.manifest.get(key)))
        project.save_manifest()
        project.state["version"] = STORY_STATE_SCHEMA_VERSION
        project.state["project_id"] = project.project_id
        for key in _STATE_KEYS:
            project.state[key] = copy.deepcopy(safe_state[key])
        for claim in project.state.get("writer_claims", []):
            if isinstance(claim, dict):
                claim["project_id"] = project.project_id
        project.save_state()
        store = StoryStore(project.cache_path)
        try:
            ProjectIngestor(project, store).ingest()
            hydration = hydrate_writer_state(project, store)
            store.commit()
            persist_writer_state(project, store)
        finally:
            store.close()
    except BaseException:
        # Leave the journal pending. The next writer-confirmed recovery discards
        # compiled cache state and rebuilds from durable files instead of guessing
        # which half of an interrupted import should win.
        raise
    complete_recovery_operation(project, recovery_token)
    return {
        "imported": True,
        "project_id": project.project_id,
        "source_project_id": str(bundle.get("source_project_id", "")),
        "hydration": hydration,
        "pre_import_backup": backup,
    }
