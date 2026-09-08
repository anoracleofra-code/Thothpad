from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.scene_semantics import SceneSemantics
from backend.story.store import StoryStore

_CANONICAL = {"AUTHOR_LOCKED", "CONFIRMED_CANON", "MANUSCRIPT_OBSERVED", "COMPILED_CANON"}


@dataclass(slots=True)
class ContinuityAuditor:
    project: Any
    store: StoryStore

    def audit(self, story_unit_id: str, *, character: str = "", branch_id: str = "mainline") -> dict[str, Any]:
        semantics = SceneSemantics(self.project, self.store).inspect(story_unit_id, branch_id=branch_id)
        findings: list[dict[str, Any]] = []
        conflicts = []
        for row in self.store.rows(
            "SELECT * FROM story_conflicts WHERE status='OPEN' ORDER BY severity DESC,conflict_id"
        ):
            claim_ids = [
                item["claim_id"]
                for item in self.store.rows(
                    "SELECT claim_id FROM conflict_claims WHERE conflict_id=? ORDER BY claim_id", (row["conflict_id"],)
                )
            ]
            relevant = False
            grounded_claims = []
            for claim_id in claim_ids:
                grounded = self.store.claim_with_evidence(claim_id)
                if not grounded:
                    continue
                grounded_claims.append(grounded)
                if any(ev.get("story_unit_id") == story_unit_id for ev in grounded["evidence"]):
                    relevant = True
            if relevant:
                conflicts.append({"conflict": dict(row), "claims": grounded_claims})
                findings.append(
                    {
                        "kind": "explicit_conflict",
                        "severity": row["severity"],
                        "status": "TRACKED_CONFLICT",
                        "observation": (
                            "This story unit contains evidence participating in an unresolved Story Model conflict."
                        ),
                        "conflict_id": row["conflict_id"],
                    }
                )

        character_id = ""
        if character:
            matches = self.store.resolve_entities(character)
            if matches:
                character_id = str(matches[0]["entity_id"])
        knowledge = (
            {
                row["claim_id"]: row["state"]
                for row in self.store.rows(
                    "SELECT claim_id,state FROM knowledge_state WHERE character_id=? AND branch_id=?",
                    (character_id, branch_id),
                )
            }
            if character_id
            else {}
        )
        if character_id:
            for claim in semantics["grounded_claims"]:
                if claim["status"] not in _CANONICAL:
                    continue
                if claim["claim_id"] in knowledge:
                    continue
                findings.append(
                    {
                        "kind": "knowledge_access_gap",
                        "severity": "review",
                        "status": "UNTRACKED_ACCESS_CANDIDATE",
                        "claim_id": claim["claim_id"],
                        "observation": (
                            f"{character} is present in this unit, which contains evidence for a tracked fact, "
                            "but the Story Model has no knowledge-state record granting that fact "
                            "to the character here."
                        ),
                        "interpretation_limit": "This is a review candidate, not proof that the prose leaks knowledge.",
                    }
                )

        world_duplicates = []
        for row in self.store.rows(
            """
            SELECT entity_id,state_type,COUNT(DISTINCT value_json) AS variants
            FROM world_state WHERE branch_id=? AND (valid_from=? OR valid_from IS NULL)
            GROUP BY entity_id,state_type HAVING variants>1 ORDER BY entity_id,state_type
            """,
            (branch_id, story_unit_id),
        ):
            world_duplicates.append(dict(row))
        for duplicate in world_duplicates:
            findings.append(
                {
                    "kind": "world_state_ambiguity",
                    "severity": "review",
                    "status": "MULTIPLE_TRACKED_VALUES",
                    "entity_id": duplicate["entity_id"],
                    "state_type": duplicate["state_type"],
                    "observation": (
                        "Multiple distinct tracked values are simultaneously applicable at this story position."
                    ),
                }
            )
        return {
            "story_unit": semantics["story_unit"],
            "character": character,
            "findings": findings,
            "explicit_conflicts": conflicts,
            "coverage": {
                "characters_present": len(semantics["characters_present"]),
                "grounded_claims": len(semantics["grounded_claims"]),
                "knowledge_records_for_character": len(knowledge),
                "missing_tracking_is_not_failure": True,
            },
        }
