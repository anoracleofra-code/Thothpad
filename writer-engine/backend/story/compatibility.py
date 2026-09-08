from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.atomic_io import atomic_write_text
from backend.story.ingest import ProjectIngestor
from backend.story.persistence import hydrate_writer_state, persist_legacy_cache_writer_state_if_needed
from backend.story.project import PROJECT_SCHEMA_VERSION, STORY_STATE_SCHEMA_VERSION, StoryProject
from backend.story.store import StoryStore

BACKUP_FORMAT = "thothpad-story-state-backup"
BACKUP_VERSION = 1
_BACKUP_NAME = re.compile(r"^story-state-[0-9a-f]{16}\.json$")


def _backups_dir(project: StoryProject) -> Path:
    path = project.metadata_dir / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_story_state_backup(root: str | Path) -> dict[str, Any]:
    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    try:
        persist_legacy_cache_writer_state_if_needed(project, store)
    finally:
        store.close()
    payload = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "project_id": project.project_id,
        "story_state_schema": STORY_STATE_SCHEMA_VERSION,
        "state": project.state,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    name = f"story-state-{digest[:16]}.json"
    path = _backups_dir(project) / name
    if not path.exists():
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))
    return {"created": True, "backup_name": name, "sha256": digest, "project_id": project.project_id}


def restore_story_state_backup(
    root: str | Path,
    backup_name: str,
    *,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("restoring writer-owned Story State requires explicit writer confirmation")
    if not _BACKUP_NAME.fullmatch(backup_name):
        raise ValueError("invalid Story State backup name")
    project = StoryProject.open(root)
    path = _backups_dir(project) / backup_name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Story State backup is unreadable or corrupt") from exc
    if not isinstance(value, dict) or value.get("format") != BACKUP_FORMAT or value.get("version") != BACKUP_VERSION:
        raise ValueError("invalid Story State backup")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    expected_name = f"story-state-{digest[:16]}.json"
    if backup_name != expected_name:
        raise ValueError("Story State backup content does not match its content-addressed name")
    if value.get("project_id") != project.project_id:
        raise ValueError("Story State backup belongs to a different project")
    if value.get("story_state_schema") != STORY_STATE_SCHEMA_VERSION:
        raise ValueError("Story State backup schema metadata does not match this engine")
    state = value.get("state")
    if not isinstance(state, dict):
        raise ValueError("Story State backup has no state object")
    version = state.get("version", STORY_STATE_SCHEMA_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= STORY_STATE_SCHEMA_VERSION:
        raise ValueError("Story State backup uses an unsupported schema version")
    if state.get("project_id") != project.project_id:
        raise ValueError("Story State backup state belongs to a different project")
    create_story_state_backup(root)
    project.state = dict(state)
    project.state["version"] = STORY_STATE_SCHEMA_VERSION
    project.state["project_id"] = project.project_id
    project.save_state()
    cache = project.cache_path.resolve()
    metadata = project.metadata_dir.resolve()
    try:
        cache.relative_to(metadata)
    except ValueError as exc:
        raise RuntimeError("Story State restore cache escaped metadata directory") from exc
    if cache.exists():
        # Rollback is authoritative: discard the compiled projection instead of
        # hydrating over it, otherwise writer records created after the backup
        # could survive even though they are absent from restored durable state.
        cache.unlink()
    store = StoryStore(cache)
    try:
        ProjectIngestor(project, store).ingest()
        hydration = hydrate_writer_state(project, store)
        store.commit()
    finally:
        store.close()
    return {"restored": True, "project_id": project.project_id, "backup_name": backup_name, "hydration": hydration}


def compatibility_status(project: StoryProject) -> dict[str, Any]:
    backups = [
        path.name
        for path in _backups_dir(project).glob("story-state-*.json")
        if _BACKUP_NAME.fullmatch(path.name)
    ]
    return {
        "project_id": project.project_id,
        "project_schema": PROJECT_SCHEMA_VERSION,
        "story_state_schema": STORY_STATE_SCHEMA_VERSION,
        "future_schema_fails_closed": True,
        "older_state_is_migratable": True,
        "backup_count": len(backups),
        "backups": sorted(backups)[-20:],
    }
