from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.acceptance_metrics import AcceptanceMetrics
from backend.story.path_resilience import path_resilience_report
from backend.story.project import StoryProject
from backend.story.store import StoryStore


@dataclass(slots=True)
class ReleaseProjectValidator:
    project: StoryProject
    store: StoryStore

    def report(self) -> dict[str, Any]:
        sources = [dict(row) for row in self.store.rows("SELECT format,relative_path,tombstoned FROM sources")]
        formats = sorted({str(item["format"]) for item in sources if not item["tombstoned"]})
        nested = sum(1 for item in sources if "/" in str(item["relative_path"]) and not item["tombstoned"])
        units = int(
            next(
                iter(self.store.rows("SELECT COUNT(*) AS count FROM story_units WHERE branch_id='mainline'"))
            )["count"]
        )
        anchored = int(
            next(
                iter(
                    self.store.rows(
                        "SELECT COUNT(*) AS count FROM story_units WHERE branch_id='mainline' AND anchor_signature<>''"
                    )
                )
            )["count"]
        )
        metrics = AcceptanceMetrics(self.project, self.store).report()
        paths = path_resilience_report(self.project, self.store)
        hard_gates = {
            "normalized_sources_present": any(not item["tombstoned"] for item in sources),
            "story_units_anchored": units == anchored,
            "branch_contamination_zero": metrics["hard_gates"]["branch_contamination_zero"],
            "context_budget_respected": metrics["hard_gates"]["context_budget_respected"],
            "project_paths_portable": bool(paths["portable"]),
        }
        return {
            "project_id": self.project.project_id,
            "source_count": sum(1 for item in sources if not item["tombstoned"]),
            "formats": formats,
            "nested_source_count": nested,
            "story_unit_count": units,
            "hard_gates": hard_gates,
            "all_green": all(bool(value) for value in hard_gates.values()),
            "project_specific_folder_semantics": False,
        }
