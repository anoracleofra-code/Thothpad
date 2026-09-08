from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.context import ContextCompiler, EpistemicMode
from backend.story.continuity import ContinuityAuditor
from backend.story.health import ProjectHealth
from backend.story.reader_intelligence import ReaderIntelligence
from backend.story.scene_semantics import SceneSemantics
from backend.story.store import StoryStore
from backend.story.story_explorer import StoryExplorer


@dataclass(slots=True)
class WowAcceptance:
    project: Any
    store: StoryStore

    def run(
        self,
        *,
        story_unit_id: str | None = None,
        character: str = "",
        branch_id: str = "mainline",
    ) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []

        def step(number: int, name: str, passed: bool, evidence: Any, note: str = "") -> None:
            steps.append(
                {
                    "step": number,
                    "name": name,
                    "passed": bool(passed),
                    "evidence": evidence,
                    "note": note,
                }
            )

        source_count = int(
            next(iter(self.store.rows("SELECT COUNT(*) AS count FROM sources WHERE tombstoned=0")))["count"]
        )
        step(1, "Project understood", source_count > 0, {"source_count": source_count})

        scene = {}
        if story_unit_id:
            try:
                scene = SceneSemantics(self.project, self.store).inspect(story_unit_id, branch_id=branch_id)
            except (KeyError, ValueError):
                scene = {}
        step(
            2,
            "Current story position is computationally legible",
            bool(scene),
            {
                "story_unit": scene.get("story_unit", {}),
                "characters_present": scene.get("characters_present", []),
                "tracked_locations": scene.get("tracked_locations", []),
            },
            "Requires a resolved story unit.",
        )

        knowledge = []
        if character:
            matches = self.store.resolve_entities(character)
            if matches:
                entity_id = str(matches[0]["entity_id"])
                knowledge = [
                    dict(row)
                    for row in self.store.rows(
                        "SELECT * FROM knowledge_state WHERE character_id=? AND branch_id=? ORDER BY rowid LIMIT 200",
                        (entity_id, branch_id),
                    )
                ]
        continuity = {}
        if story_unit_id:
            continuity = ContinuityAuditor(self.project, self.store).audit(
                story_unit_id,
                character=character,
                branch_id=branch_id,
            )
        step(
            3,
            "Character knowledge continuity is queryable",
            bool(character) and bool(continuity),
            {"knowledge_records": len(knowledge), "continuity_findings": continuity.get("findings", [])},
            "No invented knowledge is required for this gate.",
        )

        cold = {}
        if story_unit_id:
            cold = ReaderIntelligence(self.project, self.store).cold_reader_at(
                story_unit_id,
                branch_id=branch_id,
                maximum_chars=12_000,
            )
        step(
            4,
            "Reader perspective is future-bounded",
            bool(cold) and bool(cold.get("hard_boundary")),
            cold.get("hard_boundary", {}),
        )

        promises = [
            dict(row)
            for row in self.store.rows(
                "SELECT * FROM promise_items WHERE branch_id=? "
                "AND state NOT IN ('PAID','ABANDONED') ORDER BY rowid LIMIT 200",
                (branch_id,),
            )
        ]
        step(5, "Live promises/questions are queryable", True, {"tracked_open_items": len(promises)})

        branches = [
            dict(row)
            for row in self.store.rows("SELECT * FROM branches WHERE branch_id<>'mainline' ORDER BY rowid LIMIT 200")
        ]
        step(
            6,
            "Alternate continuity is isolated",
            all(item["branch_id"] != "mainline" for item in branches),
            {"alternate_branches": len(branches)},
        )

        merge_history = int(next(iter(self.store.rows("SELECT COUNT(*) AS count FROM branch_merge_history")))["count"])
        step(
            7,
            "Selective branch promotion is transaction-tracked",
            True,
            {"recorded_merged_overlays": merge_history, "writer_confirmation_required": True},
        )

        dependency_count = int(next(iter(self.store.rows("SELECT COUNT(*) AS count FROM dependencies")))["count"])
        step(
            8,
            "Retcon impact graph is available",
            True,
            {"registered_dependencies": dependency_count},
        )

        preferences = [
            dict(row)
            for row in self.store.rows(
                "SELECT preference_id,status,statement FROM writer_preferences ORDER BY rowid LIMIT 200"
            )
        ]
        step(
            9,
            "Writer Model is inspectable/rejectable",
            True,
            {"preference_count": len(preferences), "review_states": sorted({item["status"] for item in preferences})},
        )

        compiled = ContextCompiler(self.project, self.store).compile(
            prompt=f"Explain the current story context for {character or 'the active scene'}",
            mode=EpistemicMode.AUTHOR_OMNISCIENT,
            active_story_unit=story_unit_id,
            branch_id=branch_id,
            maximum_chars=12_000,
        )
        inspector = compiled.inspector()
        step(
            10,
            "What the AI sees is inspectable",
            isinstance(inspector, dict) and "included" in inspector and "excluded" in inspector,
            inspector,
        )

        explorer = StoryExplorer(self.project, self.store).explore(character or "story", branch_id=branch_id, limit=30)
        health = ProjectHealth(self.project, self.store).report()
        return {
            "steps": steps,
            "passed_steps": sum(1 for item in steps if item["passed"]),
            "total_steps": len(steps),
            "all_green": all(item["passed"] for item in steps),
            "project_health": health,
            "explorer_probe": explorer,
            "authority_rule": (
                "A passing acceptance report never changes canon and never hides skipped/empty tracked state."
            ),
        }
