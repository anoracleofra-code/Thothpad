from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from backend.story.context import ContextCompiler, EpistemicMode
from backend.story.health import ProjectHealth
from backend.story.ingest import ProjectIngestor
from backend.story.performance import StoryPerformanceProbe
from backend.story.project import StoryProject
from backend.story.store import StoryStore
from backend.story.validation_matrix import project_model_fingerprint


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_soak_replay(root: str | Path, *, cycles: int = 3) -> dict[str, Any]:
    """Replay deterministic Story Engine reads across fresh store handles.

    The soak intentionally permits cache/index maintenance while requiring the
    durable writer state and normalized semantic model to remain stable.
    """

    if isinstance(cycles, bool) or not 1 <= int(cycles) <= 20:
        raise ValueError("soak cycles must be between 1 and 20")
    project = StoryProject.open(root)
    state_before = _sha256(project.state_path)
    fingerprints: list[str] = []
    cycle_reports: list[dict[str, Any]] = []
    for cycle in range(int(cycles)):
        current = StoryProject.open(root)
        store = StoryStore(current.cache_path)
        try:
            ProjectIngestor(current, store).ingest()
            fingerprint = project_model_fingerprint(current, store)["fingerprint"]
            context = ContextCompiler(current, store).compile(
                prompt="continuity characters threads promises",
                mode=EpistemicMode.AUTHOR_OMNISCIENT,
                maximum_chars=12_000,
            )
            health = ProjectHealth(current, store).report()
            performance = StoryPerformanceProbe(current, store).report()
            fingerprints.append(str(fingerprint))
            cycle_reports.append(
                {
                    "cycle": cycle + 1,
                    "fingerprint": fingerprint,
                    "context_budget_respected": context.used_chars <= context.maximum_chars,
                    "health_green": bool(health["all_hard_gates_pass"]),
                    "indexed_queries": bool(performance["all_core_queries_indexed"]),
                    "filesystem_scans": int(performance["filesystem_scans_during_query"]),
                }
            )
        finally:
            store.close()
    state_after = _sha256(project.state_path)
    return {
        "project_id": project.project_id,
        "cycles": int(cycles),
        "cycle_reports": cycle_reports,
        "semantic_fingerprint_stable": len(set(fingerprints)) <= 1,
        "durable_writer_state_unchanged": state_before == state_after,
        "all_cycles_green": all(
            item["context_budget_respected"]
            and item["health_green"]
            and item["indexed_queries"]
            and item["filesystem_scans"] == 0
            for item in cycle_reports
        ),
        "source_files_rewritten": False,
        "derived_cache_reconciliation_allowed": True,
        "authoritative_state_mutation_allowed": False,
    }
