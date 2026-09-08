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
_SNAPSHOT_NAME = re.compile(r"^[0-9a-f-]{36}\.json$")


def _journal_path(project: StoryProject) -> Path:
    return project.metadata_dir / "recovery-journal.json"


def _snapshot_path(project: StoryProject, token: str) -> Path:
    return project.metadata_dir / "recovery-snapshots" / f"{token}.json"


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


def begin_recovery_operation(project: StoryProject, operation: str, *, snapshot_durable: bool = False) -> str:
    normalized = operation.strip().casefold()
    if not _OPERATION.fullmatch(normalized):
        raise ValueError("recovery operation name is invalid")
    document = _load(project)
    if isinstance(document.get("pending"), dict):
        raise RuntimeError("another Story Engine recovery-sensitive operation is already pending")
    token = str(uuid.uuid4())
    snapshot_name = ""
    snapshot_sha256 = ""
    if snapshot_durable:
        snapshot = {
            "format": "thothpad-recovery-snapshot",
            "version": RECOVERY_JOURNAL_VERSION,
            "project_id": project.project_id,
            "manifest": project.manifest,
            "state": project.state,
        }
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        snapshot_file = _snapshot_path(project, token)
        atomic_write_text(snapshot_file, json.dumps(snapshot, indent=2, ensure_ascii=False))
        snapshot_name = snapshot_file.name
        snapshot_sha256 = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    document["version"] = RECOVERY_JOURNAL_VERSION
    document["pending"] = {
        "token": token,
        "operation": normalized,
        "started_unix": int(time.time()),
        "manifest_sha256_before": _sha256(project.manifest_path),
        "state_sha256_before": _sha256(project.state_path),
        "rollback_snapshot": snapshot_name,
        "rollback_snapshot_sha256": snapshot_sha256,
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
    snapshot_name = str(record.get("rollback_snapshot", ""))
    if _SNAPSHOT_NAME.fullmatch(snapshot_name):
        try:
            (project.metadata_dir / "recovery-snapshots" / snapshot_name).unlink()
        except FileNotFoundError:
            pass


def _restore_pending_snapshot(project: StoryProject, pending: dict[str, Any]) -> bool:
    snapshot_name = str(pending.get("rollback_snapshot", ""))
    expected_hash = str(pending.get("rollback_snapshot_sha256", ""))
    if not snapshot_name:
        return False
    if not _SNAPSHOT_NAME.fullmatch(snapshot_name) or len(expected_hash) != 64:
        raise ValueError("recovery rollback snapshot metadata is invalid")
    path = project.metadata_dir / "recovery-snapshots" / snapshot_name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("recovery rollback snapshot is unreadable or corrupt") from exc
    if not isinstance(value, dict):
        raise ValueError("recovery rollback snapshot must be an object")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if digest != expected_hash:
        raise ValueError("recovery rollback snapshot integrity check failed")
    if (
        value.get("format") != "thothpad-recovery-snapshot"
        or value.get("version") != RECOVERY_JOURNAL_VERSION
        or value.get("project_id") != project.project_id
    ):
        raise ValueError("recovery rollback snapshot belongs to a different project or format")
    manifest = value.get("manifest")
    state = value.get("state")
    if not isinstance(manifest, dict) or not isinstance(state, dict):
        raise ValueError("recovery rollback snapshot is missing durable metadata")
    project.manifest = dict(manifest)
    project.state = dict(state)
    project.save_manifest()
    project.save_state()
    return True


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
    pending = _load(project).get("pending")
    if not isinstance(pending, dict):
        raise RuntimeError("Story Engine recovery journal lost its pending operation")
    restored_snapshot = _restore_pending_snapshot(project, pending)
    if restored_snapshot:
        project = StoryProject.open(root)
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
    token = str(pending.get("token", "")) if isinstance(pending, dict) else ""
    if token:
        complete_recovery_operation(project, token, outcome="RECOVERED")
    return {
        "recovered": True,
        "project_id": project.project_id,
        "understanding": understanding,
        "durable_state_authoritative": True,
        "durable_metadata_rolled_back": restored_snapshot,
    }
