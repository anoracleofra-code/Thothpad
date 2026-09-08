from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from backend.atomic_io import atomic_write_text
from backend.story.ingest import ProjectIngestor
from backend.story.observability import record_story_event
from backend.story.project import StoryProject
from backend.story.store import StoryStore

INDEX_PROGRESS_VERSION = 1


def _checkpoint_path(project: StoryProject) -> Path:
    return project.metadata_dir / "index-progress.json"


def _source_inventory(project: StoryProject, ingestor: ProjectIngestor) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for candidate in ingestor.generic.enumerate_sources(project.root):
        if ingestor._adapter(candidate) is None:
            continue
        inventory.append(
            {
                "path": candidate.relative_path,
                "size": candidate.size,
                "mtime_ns": candidate.mtime_ns,
            }
        )
    inventory.sort(key=lambda item: str(item["path"]).casefold())
    return inventory


def _fingerprint(inventory: list[dict[str, Any]]) -> str:
    raw = json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_checkpoint(project: StoryProject) -> dict[str, Any]:
    path = _checkpoint_path(project)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def indexing_status(project: StoryProject) -> dict[str, Any]:
    checkpoint = _load_checkpoint(project)
    pending = checkpoint.get("pending_paths", [])
    if not isinstance(pending, list):
        pending = []
    cursor = checkpoint.get("cursor", 0)
    cursor = cursor if isinstance(cursor, int) and not isinstance(cursor, bool) else 0
    total = len(pending)
    return {
        "project_id": project.project_id,
        "active": bool(checkpoint),
        "inventory_fingerprint": str(checkpoint.get("inventory_fingerprint") or ""),
        "processed": min(max(cursor, 0), total),
        "total": total,
        "remaining": max(0, total - max(cursor, 0)),
        "checkpoint_path_kind": "project" if not project.metadata_is_external else "application_data",
        "resumable": True,
    }


def run_index_batch(
    root: str | Path,
    *,
    maximum_documents: int = 100,
    reset: bool = False,
) -> dict[str, Any]:
    """Run one resumable local indexing batch.

    The checkpoint contains only project-relative paths and file metadata. It is
    invalidated automatically if the source inventory changes while indexing.
    Global reconciliation/tombstoning occurs only when the complete inventory has
    been visited, so an interrupted batch cannot make unvisited files disappear.
    """

    if isinstance(maximum_documents, bool):
        raise ValueError("maximum_documents must be an integer")
    maximum_documents = max(1, min(int(maximum_documents), 1_000))
    started = time.monotonic()
    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    try:
        ingestor = ProjectIngestor(project, store)
        inventory = _source_inventory(project, ingestor)
        inventory_fingerprint = _fingerprint(inventory)
        checkpoint = {} if reset else _load_checkpoint(project)
        if (
            checkpoint.get("version") != INDEX_PROGRESS_VERSION
            or checkpoint.get("project_id") != project.project_id
            or checkpoint.get("inventory_fingerprint") != inventory_fingerprint
            or not isinstance(checkpoint.get("pending_paths"), list)
        ):
            checkpoint = {
                "version": INDEX_PROGRESS_VERSION,
                "project_id": project.project_id,
                "inventory_fingerprint": inventory_fingerprint,
                "pending_paths": [str(item["path"]) for item in inventory],
                "cursor": 0,
            }

        pending_paths = [str(item) for item in checkpoint["pending_paths"] if isinstance(item, str)]
        cursor = checkpoint.get("cursor", 0)
        cursor = cursor if isinstance(cursor, int) and not isinstance(cursor, bool) else 0
        cursor = min(max(cursor, 0), len(pending_paths))
        batch = pending_paths[cursor : cursor + maximum_documents]
        summary = ingestor.ingest(selected_paths=set(batch), finalize=False)
        cursor += len(batch)
        complete = cursor >= len(pending_paths)
        if complete:
            final_summary = ingestor.ingest(selected_paths=set(), finalize=True)
            summary.removed_documents = final_summary.removed_documents
            summary.likely_manuscripts = final_summary.likely_manuscripts
            progress_path = _checkpoint_path(project)
            if progress_path.exists():
                progress_path.unlink()
        else:
            checkpoint["cursor"] = cursor
            atomic_write_text(
                _checkpoint_path(project),
                json.dumps(checkpoint, indent=2, ensure_ascii=False),
            )
        result = {
            "project_id": project.project_id,
            "complete": complete,
            "processed_this_batch": len(batch),
            "processed": cursor,
            "total": len(pending_paths),
            "remaining": max(0, len(pending_paths) - cursor),
            "inventory_fingerprint": inventory_fingerprint,
            "understanding": summary.to_dict(),
            "resumable": True,
        }
        record_story_event(
            project,
            "index_batch",
            outcome="COMPLETED",
            duration_ms=(time.monotonic() - started) * 1000.0,
            counts={"processed": len(batch), "remaining": max(0, len(pending_paths) - cursor)},
        )
        return result
    finally:
        store.close()
