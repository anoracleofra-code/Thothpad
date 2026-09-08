from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.story.ingest import ProjectIngestor
from backend.story.persistence import persist_legacy_cache_writer_state_if_needed
from backend.story.project import StoryProject
from backend.story.store import StoryStore


def index_status(project: StoryProject, store: StoryStore) -> dict[str, Any]:
    def count(table: str) -> int:
        return int(next(iter(store.rows(f"SELECT COUNT(*) AS count FROM {table}")))["count"])  # noqa: S608

    foreign_key_issues = [dict(row) for row in store.rows("PRAGMA foreign_key_check")]
    return {
        "project_id": project.project_id,
        "cache_path_kind": "project" if not project.metadata_is_external else "application_data",
        "cache_exists": project.cache_path.exists(),
        "cache_bytes": project.cache_path.stat().st_size if project.cache_path.exists() else 0,
        "sqlite_user_version": int(store.connection.execute("PRAGMA user_version").fetchone()[0]),
        "fts_available": bool(store._has_fts()),
        "source_count": count("sources"),
        "chunk_count": count("source_chunks"),
        "story_unit_count": count("story_units"),
        "claim_count": count("claims"),
        "tombstoned_source_count": int(
            next(iter(store.rows("SELECT COUNT(*) AS count FROM sources WHERE tombstoned=1")))["count"]
        ),
        "stale_evidence_count": int(
            next(iter(store.rows("SELECT COUNT(*) AS count FROM claim_evidence WHERE stale=1")))["count"]
        ),
        "foreign_key_issues": foreign_key_issues[:100],
        "writer_state_exists": project.state_path.exists(),
        "cache_disposable": project.state_path.exists(),
    }


def rebuild_story_index(root: str | Path, *, writer_confirmed: bool = False) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("rebuilding the Story Engine index requires explicit writer confirmation")
    project = StoryProject.open(root)
    cache_path = project.cache_path.resolve()
    metadata_root = project.metadata_dir.resolve()
    try:
        cache_path.relative_to(metadata_root)
    except ValueError as exc:
        raise RuntimeError("Story Engine cache path escaped its metadata directory") from exc
    if cache_path.exists():
        store = StoryStore(cache_path)
        try:
            persist_legacy_cache_writer_state_if_needed(project, store)
        finally:
            store.close()
        cache_path.unlink()
    rebuilt = StoryStore(cache_path)
    try:
        understanding = ProjectIngestor(project, rebuilt).ingest().to_dict()
        status = index_status(project, rebuilt)
    finally:
        rebuilt.close()
    return {
        "rebuilt": True,
        "project_id": project.project_id,
        "understanding": understanding,
        "index_status": status,
    }
