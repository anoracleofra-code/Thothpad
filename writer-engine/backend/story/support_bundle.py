from __future__ import annotations

from typing import Any

from backend.story.compatibility import compatibility_status
from backend.story.health import ProjectHealth
from backend.story.observability import observability_report
from backend.story.offline import offline_readiness
from backend.story.performance import StoryPerformanceProbe
from backend.story.project import StoryProject
from backend.story.recovery import recovery_status
from backend.story.store import StoryStore

SUPPORT_BUNDLE_FORMAT = "thothpad-story-support"
SUPPORT_BUNDLE_VERSION = 1


def build_support_bundle(project: StoryProject, store: StoryStore) -> dict[str, Any]:
    """Return diagnostics intentionally safe to paste into a support ticket."""

    health = ProjectHealth(project, store).report()
    performance = StoryPerformanceProbe(project, store).report()
    recovery = recovery_status(project)
    compatibility = compatibility_status(project)
    observability = observability_report(project)
    offline = offline_readiness(project)
    return {
        "format": SUPPORT_BUNDLE_FORMAT,
        "version": SUPPORT_BUNDLE_VERSION,
        "project_id": project.project_id,
        "metadata_is_external": bool(project.metadata_is_external),
        "health": {
            "gates": dict(health["gates"]),
            "metrics": dict(health["metrics"]),
        },
        "performance": {
            "source_count": performance["source_count"],
            "indexed_queries": dict(performance["indexed_queries"]),
            "source_lookup_100_ms": performance["source_lookup_100_ms"],
            "filesystem_scans_during_query": performance["filesystem_scans_during_query"],
        },
        "recovery": {
            "recovery_required": recovery["recovery_required"],
            "journal_corrupt": recovery["journal_corrupt"],
            "history_count": recovery["history_count"],
        },
        "compatibility": {
            "project_schema": compatibility["project_schema"],
            "story_state_schema": compatibility["story_state_schema"],
            "future_schema_fails_closed": compatibility["future_schema_fails_closed"],
            "backup_count": compatibility["backup_count"],
        },
        "observability": observability,
        "offline": offline,
        "privacy": {
            "contains_manuscript_text": False,
            "contains_prompts": False,
            "contains_source_paths": False,
            "contains_absolute_paths": False,
            "contains_credentials": False,
            "telemetry_upload_required": False,
        },
    }
