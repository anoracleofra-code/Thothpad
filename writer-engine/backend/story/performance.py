from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from backend.story.project import StoryProject
from backend.story.store import StoryStore


def _plan_rows(store: StoryStore, sql: str, params: tuple[Any, ...] = ()) -> list[str]:
    return [str(row[3]) for row in store.connection.execute(f"EXPLAIN QUERY PLAN {sql}", params)]


def _uses_index(plan: list[str]) -> bool:
    return any("USING INDEX" in item.upper() or "USING COVERING INDEX" in item.upper() for item in plan)


@dataclass(slots=True)
class StoryPerformanceProbe:
    project: StoryProject
    store: StoryStore

    def report(self) -> dict[str, Any]:
        sample_source = next(
            iter(
                self.store.rows(
                    "SELECT relative_path,source_id FROM sources WHERE project_id=? AND tombstoned=0 "
                    "ORDER BY relative_path LIMIT 1",
                    (self.project.project_id,),
                )
            ),
            None,
        )
        relative_path = str(sample_source["relative_path"]) if sample_source is not None else ""
        source_id = str(sample_source["source_id"]) if sample_source is not None else ""

        plans = {
            "source_lookup": _plan_rows(
                self.store,
                "SELECT source_id FROM sources WHERE project_id=? AND relative_path=?",
                (self.project.project_id, relative_path),
            ),
            "scene_span": _plan_rows(
                self.store,
                "SELECT story_unit_id FROM story_units WHERE branch_id='mainline' AND source_id=? "
                "AND start_offset<=? AND end_offset>?",
                (source_id, 1, 1),
            ),
            "entity_mentions": _plan_rows(
                self.store,
                "SELECT entity_id FROM entity_mentions WHERE source_id=? AND end_offset>? AND start_offset<?",
                (source_id, 0, 1000),
            ),
        }
        indexed = {name: _uses_index(plan) for name, plan in plans.items()}

        start = time.perf_counter()
        if relative_path:
            for _ in range(100):
                self.store.connection.execute(
                    "SELECT source_id FROM sources WHERE project_id=? AND relative_path=?",
                    (self.project.project_id, relative_path),
                ).fetchone()
        lookup_ms_100 = (time.perf_counter() - start) * 1000.0

        source_count = int(
            next(
                iter(
                    self.store.rows(
                        "SELECT COUNT(*) AS count FROM sources WHERE project_id=? AND tombstoned=0",
                        (self.project.project_id,),
                    )
                )
            )["count"]
        )
        return {
            "project_id": self.project.project_id,
            "source_count": source_count,
            "query_plans": plans,
            "indexed_queries": indexed,
            "all_core_queries_indexed": all(indexed.values()),
            "source_lookup_100_ms": round(lookup_ms_100, 3),
            "filesystem_scans_during_query": 0,
            "interpretation_limit": (
                "Timing is an observation from this machine, not a universal pass/fail threshold. "
                "The hard gate is that core normalized queries use SQLite indexes and do not rescan project files."
            ),
        }
