from __future__ import annotations

from typing import Any

from backend.story.performance import StoryPerformanceProbe
from backend.story.project import StoryProject
from backend.story.store import StoryStore

DEFAULT_LOOKUP_100_BUDGET_MS = 1000.0


def performance_budget_report(
    project: StoryProject,
    store: StoryStore,
    *,
    lookup_100_budget_ms: float = DEFAULT_LOOKUP_100_BUDGET_MS,
) -> dict[str, Any]:
    if lookup_100_budget_ms <= 0:
        raise ValueError("performance budget must be positive")
    report = StoryPerformanceProbe(project, store).report()
    observed = float(report["source_lookup_100_ms"])
    gates = {
        "core_queries_indexed": bool(report["all_core_queries_indexed"]),
        "filesystem_scans_zero": int(report["filesystem_scans_during_query"]) == 0,
        "reference_lookup_budget": observed <= float(lookup_100_budget_ms),
    }
    return {
        "project_id": project.project_id,
        "gates": gates,
        "all_green": all(gates.values()),
        "source_lookup_100_ms": observed,
        "source_lookup_100_budget_ms": float(lookup_100_budget_ms),
        "budget_is_machine_reference_not_story_quality": True,
    }
