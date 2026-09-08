from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from backend.atomic_io import atomic_write_text
from backend.story.project import StoryProject

OBSERVABILITY_VERSION = 1
_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")


def _path(project: StoryProject) -> Path:
    return project.metadata_dir / "observability.json"


def _load(project: StoryProject) -> list[dict[str, Any]]:
    path = _path(project)
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(value, dict) or value.get("version") != OBSERVABILITY_VERSION:
        return []
    events = value.get("events", [])
    return [dict(item) for item in events if isinstance(item, dict)][-200:]


def record_story_event(
    project: StoryProject,
    operation: str,
    *,
    outcome: str,
    duration_ms: float = 0.0,
    counts: dict[str, int] | None = None,
) -> None:
    name = operation.strip().casefold()
    if not _NAME.fullmatch(name):
        raise ValueError("observability operation name is invalid")
    safe_counts: dict[str, int] = {}
    for key, value in dict(counts or {}).items():
        folded = str(key).strip().casefold()
        if _NAME.fullmatch(folded) and isinstance(value, int) and not isinstance(value, bool):
            safe_counts[folded] = max(-1_000_000_000, min(value, 1_000_000_000))
    events = _load(project)
    events.append(
        {
            "operation": name,
            "outcome": str(outcome).strip().upper()[:40],
            "duration_ms": round(max(0.0, min(float(duration_ms), 86_400_000.0)), 3),
            "counts": safe_counts,
            "recorded_unix": int(time.time()),
        }
    )
    atomic_write_text(
        _path(project),
        json.dumps({"version": OBSERVABILITY_VERSION, "events": events[-200:]}, indent=2),
    )


def observability_report(project: StoryProject) -> dict[str, Any]:
    events = _load(project)
    by_operation: dict[str, dict[str, Any]] = {}
    for event in events:
        operation = str(event.get("operation", "unknown"))
        bucket = by_operation.setdefault(operation, {"count": 0, "failure_count": 0, "max_duration_ms": 0.0})
        bucket["count"] += 1
        if str(event.get("outcome", "")).upper() not in {"OK", "PASS", "COMPLETED", "RECOVERED"}:
            bucket["failure_count"] += 1
        bucket["max_duration_ms"] = max(float(bucket["max_duration_ms"]), float(event.get("duration_ms", 0.0)))
    return {
        "project_id": project.project_id,
        "event_count": len(events),
        "operations": by_operation,
        "content_logged": False,
        "prompts_logged": False,
        "source_paths_logged": False,
        "credentials_logged": False,
        "telemetry_uploaded": False,
        "local_only": True,
    }
