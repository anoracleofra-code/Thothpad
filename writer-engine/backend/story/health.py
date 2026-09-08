from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.context import ContextCompiler, EpistemicMode
from backend.story.project import StoryProject
from backend.story.store import StoryStore


@dataclass(slots=True)
class ProjectHealth:
    project: StoryProject
    store: StoryStore

    def report(self) -> dict[str, Any]:
        def count(sql: str, params: tuple[Any, ...] = ()) -> int:
            return int(next(iter(self.store.rows(sql, params)))["count"])

        sources = count("SELECT COUNT(*) AS count FROM sources WHERE tombstoned=0")
        unknown_sources = count(
            """
            SELECT COUNT(DISTINCT s.source_id) AS count FROM sources s
            LEFT JOIN source_roles r ON r.source_id=s.source_id AND r.confidence>=0.5
            WHERE s.tombstoned=0 AND (r.role IS NULL OR r.role='unknown')
            """
        )
        claims = count("SELECT COUNT(*) AS count FROM claims WHERE status NOT IN ('SUPERSEDED','ARCHIVED')")
        material_claims = count(
            "SELECT COUNT(*) AS count FROM claims WHERE status NOT IN ('SUPERSEDED','ARCHIVED','OPEN')"
        )
        grounded_claims = count(
            """
            SELECT COUNT(DISTINCT c.claim_id) AS count FROM claims c
            JOIN claim_evidence e ON e.claim_id=c.claim_id AND e.stale=0
            WHERE c.status NOT IN ('SUPERSEDED','ARCHIVED','OPEN')
            """
        )
        writer_claims_without_source = count(
            """
            SELECT COUNT(*) AS count FROM claims c
            WHERE c.created_by='writer' AND c.status NOT IN ('SUPERSEDED','ARCHIVED')
              AND NOT EXISTS (SELECT 1 FROM claim_evidence e WHERE e.claim_id=c.claim_id AND e.stale=0)
            """
        )
        stale_evidence = count("SELECT COUNT(*) AS count FROM claim_evidence WHERE stale=1")
        open_conflicts = count("SELECT COUNT(*) AS count FROM story_conflicts WHERE status='OPEN'")
        branches = count("SELECT COUNT(*) AS count FROM branches WHERE branch_id<>'mainline'")
        overlays = count("SELECT COUNT(*) AS count FROM branch_overlays")
        mainline_overlay_leaks = count(
            """
            SELECT COUNT(*) AS count FROM branch_overlays o
            JOIN claims c ON c.claim_id=o.record_id
            WHERE o.record_kind='claim' AND o.operation='ADD' AND c.branch_id='mainline'
              AND NOT EXISTS (
                  SELECT 1 FROM branch_merge_history h WHERE h.overlay_id=o.overlay_id
              )
            """
        )
        aliases = count("SELECT COUNT(*) AS count FROM entity_aliases")
        ambiguous_aliases = count(
            """
            SELECT COUNT(*) AS count FROM (
                SELECT lower(alias) FROM entity_aliases GROUP BY lower(alias)
                HAVING COUNT(DISTINCT entity_id)>1
            )
            """
        )
        foreign_key_issues = len(list(self.store.rows("PRAGMA foreign_key_check")))
        orphan_dependencies = count(
            """
            SELECT COUNT(*) AS count FROM dependencies d
            WHERE d.source_kind='' OR d.source_id='' OR d.dependent_kind='' OR d.dependent_id=''
            """
        )
        context_probe = ContextCompiler(self.project, self.store).compile(
            prompt="project continuity characters threads promises",
            mode=EpistemicMode.AUTHOR_OMNISCIENT,
            maximum_chars=12_000,
        )
        metrics = {
            "source_count": sources,
            "unknown_source_count": unknown_sources,
            "claim_count": claims,
            "material_claim_count": material_claims,
            "grounded_material_claim_count": grounded_claims,
            "writer_claims_without_source_evidence": writer_claims_without_source,
            "stale_evidence_count": stale_evidence,
            "open_conflict_count": open_conflicts,
            "branch_count": branches,
            "branch_overlay_count": overlays,
            "branch_contamination_count": mainline_overlay_leaks,
            "entity_alias_count": aliases,
            "ambiguous_alias_count": ambiguous_aliases,
            "foreign_key_issue_count": foreign_key_issues,
            "orphan_dependency_count": orphan_dependencies,
            "context_probe_used_chars": context_probe.used_chars,
            "context_probe_items": len(context_probe.items),
            "context_probe_budget_chars": context_probe.maximum_chars,
        }
        gates = {
            "branch_contamination_zero": mainline_overlay_leaks == 0,
            "foreign_keys_clean": foreign_key_issues == 0,
            "dependencies_well_formed": orphan_dependencies == 0,
            "context_budget_respected": context_probe.used_chars <= context_probe.maximum_chars,
            "project_has_sources": sources > 0,
        }
        return {
            "project_id": self.project.project_id,
            "metrics": metrics,
            "gates": gates,
            "all_hard_gates_pass": all(gates.values()),
            "interpretation_limit": (
                "These are engineering/coverage metrics, not a Story Engine quality score and not judgments "
                "about prose quality."
            ),
        }
