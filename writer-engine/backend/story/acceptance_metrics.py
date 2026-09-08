from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.context import ContextCompiler, EpistemicMode
from backend.story.performance import StoryPerformanceProbe
from backend.story.project import StoryProject
from backend.story.store import StoryStore


@dataclass(slots=True)
class AcceptanceMetrics:
    project: StoryProject
    store: StoryStore

    def report(self) -> dict[str, Any]:
        def count(sql: str, params: tuple[Any, ...] = ()) -> int:
            return int(next(iter(self.store.rows(sql, params)))["count"])

        material_claims = count(
            "SELECT COUNT(*) AS count FROM claims WHERE status NOT IN ('OPEN','SUPERSEDED','ARCHIVED')"
        )
        grounded_claims = count(
            "SELECT COUNT(DISTINCT c.claim_id) AS count FROM claims c JOIN claim_evidence e ON e.claim_id=c.claim_id "
            "WHERE e.stale=0 AND c.status NOT IN ('OPEN','SUPERSEDED','ARCHIVED')"
        )
        entities = count("SELECT COUNT(*) AS count FROM entities WHERE status<>'ARCHIVED'")
        ambiguous_aliases = count(
            "SELECT COUNT(*) AS count FROM (SELECT lower(alias) FROM entity_aliases GROUP BY lower(alias) "
            "HAVING COUNT(DISTINCT entity_id)>1)"
        )
        units = count("SELECT COUNT(*) AS count FROM story_units WHERE branch_id='mainline'")
        anchored_units = count(
            "SELECT COUNT(*) AS count FROM story_units WHERE branch_id='mainline' AND anchor_signature<>''"
        )
        branch_contamination = count(
            "SELECT COUNT(*) AS count FROM branch_overlays o JOIN claims c ON c.claim_id=o.record_id "
            "WHERE o.record_kind='claim' AND o.operation='ADD' AND c.branch_id='mainline' "
            "AND NOT EXISTS (SELECT 1 FROM branch_merge_history h WHERE h.overlay_id=o.overlay_id)"
        )
        context = ContextCompiler(self.project, self.store).compile(
            prompt="characters continuity threads promises current story",
            mode=EpistemicMode.AUTHOR_OMNISCIENT,
            maximum_chars=12_000,
        )
        performance = StoryPerformanceProbe(self.project, self.store).report()
        provenance = 1.0 if material_claims == 0 else grounded_claims / material_claims
        unit_reconciliation = 1.0 if units == 0 else anchored_units / units
        context_efficiency = context.used_chars / context.maximum_chars if context.maximum_chars else 0.0
        return {
            "project_id": self.project.project_id,
            "metrics": {
                "entity_count": entities,
                "ambiguous_alias_count": ambiguous_aliases,
                "claim_provenance_completeness": round(provenance, 6),
                "story_unit_anchor_coverage": round(unit_reconciliation, 6),
                "branch_contamination_count": branch_contamination,
                "context_budget_utilization": round(context_efficiency, 6),
                "query_latency_observation_ms_100": performance["source_lookup_100_ms"],
                "core_queries_indexed": performance["all_core_queries_indexed"],
                "writer_source_override_count": len(self.project.manifest.get("source_overrides", {}))
                if isinstance(self.project.manifest.get("source_overrides"), dict)
                else 0,
                "writer_source_rule_count": len(self.project.manifest.get("source_rules", []))
                if isinstance(self.project.manifest.get("source_rules"), list)
                else 0,
            },
            "hard_gates": {
                "branch_contamination_zero": branch_contamination == 0,
                "core_queries_indexed": bool(performance["all_core_queries_indexed"]),
                "context_budget_respected": context.used_chars <= context.maximum_chars,
            },
            "interpretation_limit": (
                "These are engineering acceptance metrics. They are not a prose score, creativity score, or universal "
                "measure of story quality."
            ),
        }
