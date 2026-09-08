from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from backend.atomic_io import atomic_write_text
from backend.story.ingest import ProjectIngestor
from backend.story.project import StoryProject
from backend.story.store import StoryStore

RECOVERY_JOURNAL_VERSION = 1
_OPERATION = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")


def _journal_path(project: StoryProject) -> Path:
    return project.metadata_dir / "recovery-journal.json"


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(project: StoryProject) -> dict[str, Any]:
    path = _journal_path(project)
    if not path.exists():
        return {"version": RECOVERY_JOURNAL_VERSION, "pending": None, "history": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "version": RECOVERY_JOURNAL_VERSION,
            "pending": {"operation": "unknown", "corrupt_journal": True},
            "history": [],
        }
    if not isinstance(value, dict):
        return {"version": RECOVERY_JOURNAL_VERSION, "pending": {"corrupt_journal": True}, "history": []}
    return value


def _save(project: StoryProject, document: dict[str, Any]) -> None:
    atomic_write_text(_journal_path(project), json.dumps(document, indent=2, ensure_ascii=False))


def begin_recovery_operation(project: StoryProject, operation: str) -> str:
    normalized = operation.strip().casefold()
    if not _OPERATION.fullmatch(normalized):
        raise ValueError("recovery operation name is invalid")
    document = _load(project)
    if isinstance(document.get("pending"), dict):
        raise RuntimeError("another Story Engine recovery-sensitive operation is already pending")
    token = str(uuid.uuid4())
    document["version"] = RECOVERY_JOURNAL_VERSION
    document["pending"] = {
        "token": token,
        "operation": normalized,
        "started_unix": int(time.time()),
        "manifest_sha256_before": _sha256(project.manifest_path),
        "state_sha256_before": _sha256(project.state_path),
    }
    _save(project, document)
    return token


def complete_recovery_operation(project: StoryProject, token: str, *, outcome: str = "COMPLETED") -> None:
    document = _load(project)
    pending = document.get("pending")
    if not isinstance(pending, dict) or pending.get("token") != token:
        raise ValueError("recovery operation token is not current")
    record = dict(pending)
    record.update(
        {
            "outcome": outcome[:40],
            "finished_unix": int(time.time()),
            "manifest_sha256_after": _sha256(project.manifest_path),
            "state_sha256_after": _sha256(project.state_path),
        }
    )
    history = document.get("history", [])
    if not isinstance(history, list):
        history = []
    document["history"] = [*history[-49:], record]
    document["pending"] = None
    _save(project, document)


def recovery_status(project: StoryProject) -> dict[str, Any]:
    document = _load(project)
    pending = document.get("pending")
    return {
        "project_id": project.project_id,
        "pending": dict(pending) if isinstance(pending, dict) else None,
        "recovery_required": isinstance(pending, dict),
        "journal_corrupt": bool(isinstance(pending, dict) and pending.get("corrupt_journal")),
        "history_count": len(document.get("history", [])) if isinstance(document.get("history"), list) else 0,
        "source_files_are_never_rewritten": True,
        "recovery_strategy": "discard_compiled_cache_and_rehydrate_from_sources_plus_durable_writer_state",
    }


def recover_story_project(root: str | Path, *, writer_confirmed: bool = False) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("recovering Story Engine state requires explicit writer confirmation")
    project = StoryProject.open(root)
    status = recovery_status(project)
    if not status["recovery_required"]:
        return {"recovered": False, "project_id": project.project_id, "reason": "no_pending_operation"}
    cache = project.cache_path.resolve()
    metadata = project.metadata_dir.resolve()
    try:
        cache.relative_to(metadata)
    except ValueError as exc:
        raise RuntimeError("Story Engine recovery cache escaped metadata directory") from exc
    if cache.exists():
        cache.unlink()
    store = StoryStore(cache)
    try:
        understanding = ProjectIngestor(project, store).ingest().to_dict()
        store.commit()
    finally:
        store.close()
    pending = _load(project).get("pending")
    token = str(pending.get("token", "")) if isinstance(pending, dict) else ""
    if token:
        complete_recovery_operation(project, token, outcome="RECOVERED")
    return {
        "recovered": True,
        "project_id": project.project_id,
        "understanding": understanding,
        "durable_state_authoritative": True,
    }
