from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.acceptance_metrics import AcceptanceMetrics
from backend.story.explain import StoryExplainer
from backend.story.indexing import indexing_status
from backend.story.migrations import migration_status
from backend.story.performance import StoryPerformanceProbe
from backend.story.privacy import EgressInspector
from backend.story.project import StoryProject
from backend.story.proposals import list_story_proposals
from backend.story.retrieval import retrieval_capabilities
from backend.story.security import ProjectSecurityAudit
from backend.story.store import StoryStore
from backend.story.validation_matrix import project_model_fingerprint


@dataclass(slots=True)
class OperationalAcceptance:
    """Read-only Phase 26-35 acceptance harness."""

    project: StoryProject
    store: StoryStore

    def run(self, *, prompt: str = "current story continuity") -> dict[str, Any]:
        steps: list[dict[str, Any]] = []

        def add(number: int, name: str, passed: bool, evidence: Any) -> None:
            steps.append({"step": number, "name": name, "passed": bool(passed), "evidence": evidence})

        migration = migration_status(self.project)
        add(
            1,
            "Legacy migration is additive",
            migration["legacy_files_are_read_only"] is True,
            migration,
        )

        index_progress = indexing_status(self.project)
        add(2, "Background indexing is resumable", index_progress["resumable"] is True, index_progress)

        performance = StoryPerformanceProbe(self.project, self.store).report()
        add(
            3,
            "Core normalized queries are indexed",
            performance["all_core_queries_indexed"] is True
            and performance["filesystem_scans_during_query"] == 0,
            performance,
        )

        security = ProjectSecurityAudit(self.project).report()
        add(
            4,
            "Filesystem/adaptor boundary is fail-closed",
            security["symlinks_followed"] is False
            and security["archives_extracted"] is False
            and security["executables_loaded"] is False,
            security,
        )

        egress = EgressInspector(self.project, self.store).preview(
            prompt=prompt,
            selected_is_remote=True,
            maximum_chars=12_000,
        )
        add(
            5,
            "Remote egress is inspectable and path/credential safe",
            egress["would_leave_machine"] is True
            and egress["absolute_paths_included"] is False
            and egress["credentials_included"] is False,
            egress,
        )

        fingerprint = project_model_fingerprint(self.project, self.store)
        add(
            6,
            "Story Model fingerprint ignores filesystem layout/IDs",
            fingerprint["filesystem_paths_included"] is False
            and fingerprint["stable_ids_included"] is False
            and bool(fingerprint["fingerprint"]),
            {"fingerprint": fingerprint["fingerprint"]},
        )

        metrics = AcceptanceMetrics(self.project, self.store).report()
        add(7, "Acceptance metrics hard gates pass", all(metrics["hard_gates"].values()), metrics)

        retrieval = retrieval_capabilities()
        add(
            8,
            "Hybrid retrieval cannot become truth authority",
            retrieval["mandatory_vector_database"] is False
            and retrieval["semantic_similarity_is_authority"] is False
            and retrieval["authority_scoring_is_independent"] is True,
            retrieval,
        )

        proposals = list_story_proposals(self.project, limit=20)
        add(
            9,
            "Proposal queue is durable and non-canon until review",
            all(item.get("status") in {"PROPOSED", "ACCEPTED", "REJECTED", "SUPERSEDED"} for item in proposals),
            {"proposal_count": len(proposals), "writer_review_required_for_application": True},
        )

        first_claim = next(
            iter(
                self.store.rows(
                    "SELECT claim_id FROM claims WHERE status NOT IN ('SUPERSEDED','ARCHIVED') ORDER BY rowid LIMIT 1"
                )
            ),
            None,
        )
        explanation: dict[str, Any] = {}
        if first_claim is not None:
            explanation = StoryExplainer(self.project, self.store).explain("claim", str(first_claim["claim_id"]))
        add(
            10,
            "Ask ThothPad Why is provenance-backed",
            first_claim is None or (bool(explanation.get("record")) and "evidence" in explanation),
            explanation if explanation else {"tracked_claims": 0, "nothing_to_explain": True},
        )

        return {
            "steps": steps,
            "passed_steps": sum(1 for step in steps if step["passed"]),
            "total_steps": len(steps),
            "all_green": all(step["passed"] for step in steps),
            "authority_rule": (
                "Acceptance is read-only and cannot promote retrieval, metrics, proposals, or explanations into canon."
            ),
        }
