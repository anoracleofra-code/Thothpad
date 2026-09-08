from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from backend.story.project import StoryProject
from backend.story.store import StoryStore

MAX_LEGACY_WORKSPACE_BYTES = 32 * 1024 * 1024


def _relative_path(project: StoryProject, value: str | Path) -> tuple[Path, str]:
    raw = Path(value)
    path = raw if raw.is_absolute() else project.root / raw
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(project.root).as_posix()
    except ValueError as exc:
        raise ValueError("legacy workspace must remain inside the Story Project root") from exc
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("legacy workspace path must be project-relative")
    return resolved, pure.as_posix()


def _workspace(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.stat().st_size > MAX_LEGACY_WORKSPACE_BYTES:
        raise ValueError("legacy Story Workspace exceeds 32 MB")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("legacy Story Workspace is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or value.get("version") != 2:
        raise ValueError("only Story Workspace schema version 2 can be bound")
    if not isinstance(value.get("scopes"), list):
        raise ValueError("legacy Story Workspace has no valid scopes array")
    return value, raw


def _normalized_title(value: Any) -> str:
    title = str(value or "").strip().strip("*# ")
    return " ".join(title.casefold().split())


def bind_legacy_workspace(
    project: StoryProject,
    store: StoryStore,
    *,
    workspace_path: str | Path,
    manuscript_path: str,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    """Bind a schema-2 Story Workspace to normalized Story Units without rewriting it.

    Migration is additive. The legacy sidecar remains byte-for-byte untouched;
    only the Story Project manifest gains a binding and best-effort stable-unit
    links. Repeating the operation with the same workspace/manuscript replaces
    the existing binding rather than duplicating it.
    """

    if not writer_confirmed:
        raise PermissionError("binding a legacy Story Workspace requires explicit writer confirmation")
    resolved_workspace, relative_workspace = _relative_path(project, workspace_path)
    normalized_manuscript = project._relative_manifest_path(manuscript_path)
    if normalized_manuscript is None:
        raise ValueError("manuscript_path must be project-relative")
    source = store.source_by_path(normalized_manuscript)
    if source is None or bool(source["tombstoned"]):
        raise ValueError("manuscript_path is not an indexed project source")

    workspace, raw = _workspace(resolved_workspace)
    before_hash = hashlib.sha256(raw).hexdigest()
    units = [
        dict(row)
        for row in store.rows(
            "SELECT story_unit_id,kind,display_title,start_offset,end_offset,ordinal "
            "FROM story_units WHERE source_id=? AND branch_id='mainline' ORDER BY ordinal",
            (source["source_id"],),
        )
    ]
    by_title: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        normalized = _normalized_title(unit.get("display_title"))
        if normalized:
            by_title.setdefault(normalized, []).append(unit)

    links: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for raw_scope in workspace.get("scopes", [])[:20_000]:
        if not isinstance(raw_scope, dict):
            continue
        scope_id = str(raw_scope.get("id") or "").strip()
        if not scope_id:
            continue
        title = str(raw_scope.get("title") or "").strip()
        level = raw_scope.get("level") if isinstance(raw_scope.get("level"), int) else None
        candidates = by_title.get(_normalized_title(title), [])
        matched: dict[str, Any] | None = candidates[0] if len(candidates) == 1 else None
        if matched is None and scope_id == "manuscript":
            matched = next((unit for unit in units if unit["kind"] == "manuscript"), None)
        if matched is None and isinstance(raw_scope.get("start"), int):
            start = int(raw_scope["start"])
            containing = [
                unit
                for unit in units
                if int(unit["start_offset"]) <= start < int(unit["end_offset"])
            ]
            if containing:
                matched = min(
                    containing,
                    key=lambda item: int(item["end_offset"]) - int(item["start_offset"]),
                )
        record = {"scope_id": scope_id, "title": title, "level": level}
        if matched is None:
            unmatched.append(record)
            continue
        record.update(
            {
                "story_unit_id": matched["story_unit_id"],
                "story_unit_kind": matched["kind"],
                "story_unit_title": matched["display_title"],
            }
        )
        links.append(record)

    binding_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"thothpad-legacy-binding:{project.project_id}:{relative_workspace.casefold()}:{normalized_manuscript.casefold()}",
        )
    )
    binding = {
        "binding_id": binding_id,
        "workspace_path": relative_workspace,
        "workspace_id": str(workspace.get("id") or ""),
        "workspace_version": 2,
        "workspace_sha256": before_hash,
        "manuscript_path": normalized_manuscript,
        "story_unit_links": links,
        "unmatched_scopes": unmatched,
        "writer_confirmed": True,
    }
    bindings = project.manifest.get("legacy_workspace_bindings")
    if not isinstance(bindings, list):
        bindings = []
    replaced = False
    for index, existing in enumerate(bindings):
        if isinstance(existing, dict) and existing.get("binding_id") == binding_id:
            bindings[index] = binding
            replaced = True
            break
    if not replaced:
        bindings.append(binding)
    project.manifest["legacy_workspace_bindings"] = bindings[:2_000]
    project.save_manifest()

    if hashlib.sha256(resolved_workspace.read_bytes()).hexdigest() != before_hash:
        raise RuntimeError("legacy Story Workspace changed while binding")
    return {
        "bound": True,
        "binding": binding,
        "linked_scope_count": len(links),
        "unmatched_scope_count": len(unmatched),
        "legacy_workspace_preserved": True,
    }


def migration_status(project: StoryProject) -> dict[str, Any]:
    bindings = project.manifest.get("legacy_workspace_bindings", [])
    if not isinstance(bindings, list):
        bindings = []
    safe = [dict(item) for item in bindings[:2_000] if isinstance(item, dict)]
    return {
        "project_id": project.project_id,
        "legacy_workspace_bindings": safe,
        "binding_count": len(safe),
        "legacy_files_are_read_only": True,
    }
