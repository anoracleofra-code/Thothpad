from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.project import StoryProject
from backend.story.store import StoryStore


@dataclass(slots=True)
class SceneSemantics:
    project: StoryProject
    store: StoryStore

    def inspect(self, story_unit_id: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        unit = next(
            iter(
                self.store.rows(
                    """
                    SELECT u.*,s.relative_path FROM story_units u
                    LEFT JOIN sources s ON s.source_id=u.source_id
                    WHERE u.story_unit_id=? AND u.branch_id='mainline'
                    """,
                    (story_unit_id,),
                )
            ),
            None,
        )
        if unit is None:
            raise KeyError("story unit not found")
        if unit["kind"] not in {"scene", "chapter", "manuscript"}:
            raise ValueError("scene semantics requires a scene/chapter/manuscript story unit")
        start, end, source_id = int(unit["start_offset"]), int(unit["end_offset"]), unit["source_id"]
        present = []
        for row in self.store.rows(
            """
            SELECT e.entity_id,e.canonical_name,e.entity_type,COUNT(*) AS mention_count,
                   MIN(m.start_offset) AS first_offset
            FROM entity_mentions m JOIN entities e ON e.entity_id=m.entity_id
            WHERE m.source_id=? AND m.end_offset>? AND m.start_offset<?
            GROUP BY e.entity_id,e.canonical_name,e.entity_type
            ORDER BY first_offset,e.canonical_name COLLATE NOCASE
            """,
            (source_id, start, end),
        ):
            if row["entity_type"] == "character":
                present.append(dict(row))
        entity_ids = [item["entity_id"] for item in present]
        locations: list[dict[str, Any]] = []
        for entity_id in entity_ids:
            location_row = next(
                iter(
                    self.store.rows(
                        """
                        SELECT w.*,e.canonical_name FROM world_state w
                        LEFT JOIN entities e ON e.entity_id=w.entity_id
                        WHERE w.entity_id=? AND w.branch_id=? AND w.state_type='location'
                        ORDER BY CASE WHEN w.valid_from=? THEN 0 WHEN w.valid_from IS NULL THEN 1 ELSE 2 END,
                                 w.rowid DESC LIMIT 1
                        """,
                        (entity_id, branch_id, story_unit_id),
                    )
                ),
                None,
            )
            if location_row is not None:
                item = dict(location_row)
                item["value"] = StoryStore.decode_json(item.pop("value_json"), None)
                locations.append(item)
        contract = next(
            iter(self.store.rows("SELECT * FROM scene_contracts WHERE story_unit_id=?", (story_unit_id,))),
            None,
        )
        decisions = [
            dict(row)
            for row in self.store.rows(
                "SELECT * FROM decisions WHERE story_unit_id=? AND branch_id=? ORDER BY rowid",
                (story_unit_id, branch_id),
            )
        ]
        opposition = []
        for row in self.store.rows(
            "SELECT * FROM opposition_state WHERE story_unit_id=? AND branch_id=? ORDER BY rowid",
            (story_unit_id, branch_id),
        ):
            item = dict(row)
            item["metadata"] = StoryStore.decode_json(item.pop("metadata_json"), {})
            opposition.append(item)
        claims = []
        for row in self.store.rows(
            """
            SELECT DISTINCT c.claim_id FROM claims c JOIN claim_evidence e ON e.claim_id=c.claim_id
            WHERE e.story_unit_id=? AND c.branch_id=? AND e.stale=0 ORDER BY c.claim_id LIMIT 200
            """,
            (story_unit_id, branch_id),
        ):
            grounded = self.store.claim_with_evidence(row["claim_id"])
            if grounded:
                claim = dict(grounded["claim"])
                claim["literal_value"] = StoryStore.decode_json(claim.pop("literal_value_json", None), None)
                claim["qualifiers"] = StoryStore.decode_json(claim.pop("qualifiers_json", None), {})
                claim["evidence"] = grounded["evidence"]
                claims.append(claim)
        return {
            "story_unit": {
                "story_unit_id": story_unit_id,
                "kind": unit["kind"],
                "display_title": unit["display_title"],
                "path": unit["relative_path"],
                "start_offset": start,
                "end_offset": end,
            },
            "characters_present": present,
            "tracked_locations": locations,
            "scene_contract": (
                {
                    **dict(contract),
                    "contract": StoryStore.decode_json(contract["contract_json"], {}),
                }
                if contract is not None
                else {}
            ),
            "decisions": decisions,
            "opposition": opposition,
            "grounded_claims": claims,
            "interpretation_limit": (
                "Presence is exact mention overlap; locations and scene intent are included only when "
                "explicitly tracked. "
                "Absence means untracked, not absent from the prose."
            ),
        }
