from __future__ import annotations

import copy
from pathlib import Path, PurePosixPath
from typing import Any

from backend.story.ingest import ProjectIngestor
from backend.story.persistence import hydrate_writer_state, persist_writer_state
from backend.story.project import STORY_STATE_SCHEMA_VERSION, StoryProject
from backend.story.store import StoryStore

BUNDLE_VERSION = 1
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
)


def _safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    return not path.is_absolute() and ".." not in path.parts


def _sanitize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in _MANIFEST_KEYS:
        value = copy.deepcopy(manifest.get(key, [] if key.endswith("rules") or key == "active_manuscripts" else {}))
        result[key] = value
    for path in result.get("source_overrides", {}):
        if not _safe_relative(path):
            raise ValueError("portable bundle contains an unsafe source override path")
    for path in result.get("active_manuscripts", []):
        if not _safe_relative(path):
            raise ValueError("portable bundle contains an unsafe manuscript path")
    for rule in result.get("source_rules", []):
        if isinstance(rule, dict):
            pattern = rule.get("pattern")
            if not isinstance(pattern, str) or pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:
                raise ValueError("portable bundle contains an unsafe source rule")
    return result


def export_story_bundle(project: StoryProject, store: StoryStore) -> dict[str, Any]:
    persist_writer_state(project, store)
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
    project = StoryProject.open(root)
    for key in _MANIFEST_KEYS:
        project.manifest[key] = copy.deepcopy(safe_manifest.get(key, project.manifest.get(key)))
    project.save_manifest()
    project.state["version"] = STORY_STATE_SCHEMA_VERSION
    project.state["project_id"] = project.project_id
    for key in _STATE_KEYS:
        value = state.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"Story Project state field {key} must be an array")
        project.state[key] = copy.deepcopy(value[:20_000])
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
    return {
        "imported": True,
        "project_id": project.project_id,
        "source_project_id": str(bundle.get("source_project_id", "")),
        "hydration": hydration,
    }
