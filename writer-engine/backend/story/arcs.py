from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.project import StoryProject
from backend.story.store import StoryStore


def _unit_order(project: StoryProject, store: StoryStore) -> dict[str, tuple[int, int, str]]:
    manuscripts = {path.casefold(): index for index, path in enumerate(project.active_manuscripts())}
    result: dict[str, tuple[int, int, str]] = {}
    for row in store.rows(
        """
        SELECT u.story_unit_id,u.ordinal,s.relative_path FROM story_units u
        LEFT JOIN sources s ON s.source_id=u.source_id WHERE u.branch_id='mainline'
        """
    ):
        path = str(row["relative_path"] or "")
        file_order = manuscripts.get(path.casefold(), 1_000_000)
        result[row["story_unit_id"]] = (file_order, int(row["ordinal"]), path.casefold())
    return result


@dataclass(slots=True)
class ArcIntelligence:
    project: StoryProject
    store: StoryStore

    def character_arc(self, character: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        matches = self.store.resolve_entities(character)
        if not matches:
            raise KeyError("character not found")
        entity_id = str(matches[0]["entity_id"])
        order = _unit_order(self.project, self.store)
        moments: list[dict[str, Any]] = []
        for row in self.store.rows(
            """
            SELECT d.*,u.display_title FROM decisions d LEFT JOIN story_units u ON u.story_unit_id=d.story_unit_id
            WHERE d.agent_entity_id=? AND d.branch_id=? ORDER BY d.rowid
            """,
            (entity_id, branch_id),
        ):
            moments.append(
                {
                    "kind": "decision",
                    "story_unit_id": row["story_unit_id"],
                    "story_unit_title": row["display_title"],
                    "label": row["description"],
                    "status": row["status"],
                }
            )
        for row in self.store.rows(
            """
            SELECT k.*,c.predicate,c.literal_value_json,u.display_title
            FROM knowledge_state k JOIN claims c ON c.claim_id=k.claim_id
            LEFT JOIN story_units u ON u.story_unit_id=k.acquired_at
            WHERE k.character_id=? AND k.branch_id=? ORDER BY k.rowid
            """,
            (entity_id, branch_id),
        ):
            moments.append(
                {
                    "kind": "knowledge_change",
                    "story_unit_id": row["acquired_at"],
                    "story_unit_title": row["display_title"],
                    "label": (
                        f"{row['state']} ? {row['predicate']} = "
                        f"{StoryStore.decode_json(row['literal_value_json'], None)}"
                    ),
                    "claim_id": row["claim_id"],
                }
            )
        for row in self.store.rows(
            """
            SELECT r.*,ea.canonical_name AS name_a,eb.canonical_name AS name_b
            FROM relationships r JOIN entities ea ON ea.entity_id=r.entity_a JOIN entities eb ON eb.entity_id=r.entity_b
            WHERE r.branch_id=? AND (r.entity_a=? OR r.entity_b=?) ORDER BY r.rowid
            """,
            (branch_id, entity_id, entity_id),
        ):
            state = StoryStore.decode_json(row["state_json"], {})
            other = row["name_b"] if row["entity_a"] == entity_id else row["name_a"]
            moments.append(
                {
                    "kind": "relationship_state",
                    "story_unit_id": row["valid_from"],
                    "label": f"{other} · {row['relationship_type']} · {state.get('label', state)}",
                    "relationship_id": row["relationship_id"],
                }
            )
        moments.sort(key=lambda item: order.get(str(item.get("story_unit_id") or ""), (999_999, 999_999, "")))
        return {
            "character": {"entity_id": entity_id, "name": matches[0]["canonical_name"]},
            "moments": moments[:500],
            "transition_count": max(0, len(moments) - 1),
            "interpretation_limit": (
                "This is a chronology of tracked decisions, knowledge and relationship-state changes. "
                "It does not infer psychology, growth, regression, or thematic meaning."
            ),
        }

    def relationship_arc(self, entity_a: str, entity_b: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        a = self.store.resolve_entities(entity_a)
        b = self.store.resolve_entities(entity_b)
        if not a or not b:
            raise KeyError("relationship entity not found")
        aid, bid = str(a[0]["entity_id"]), str(b[0]["entity_id"])
        order = _unit_order(self.project, self.store)
        states = []
        for row in self.store.rows(
            """
            SELECT * FROM relationships
            WHERE branch_id=? AND ((entity_a=? AND entity_b=?) OR (entity_a=? AND entity_b=?))
            ORDER BY rowid
            """,
            (branch_id, aid, bid, bid, aid),
        ):
            state = StoryStore.decode_json(row["state_json"], {})
            states.append(
                {
                    "relationship_id": row["relationship_id"],
                    "type": row["relationship_type"],
                    "state": state,
                    "story_unit_id": row["valid_from"],
                    "valid_until": row["valid_until"],
                    "evidence_claim_id": row["evidence_claim_id"],
                }
            )
        states.sort(key=lambda item: order.get(str(item.get("story_unit_id") or ""), (999_999, 999_999, "")))
        transitions = []
        for previous, current in zip(states, states[1:], strict=False):
            if previous["state"] != current["state"]:
                transitions.append({"from": previous, "to": current, "status": "TRACKED_STATE_CHANGE"})
        return {
            "entities": [a[0]["canonical_name"], b[0]["canonical_name"]],
            "states": states,
            "transitions": transitions,
            "interpretation_limit": "Only explicit tracked relationship-state changes are reported.",
        }
