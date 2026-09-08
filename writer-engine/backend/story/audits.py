from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.project import StoryProject
from backend.story.store import StoryStore


def _decode_metadata(store: StoryStore, item: dict[str, Any]) -> dict[str, Any]:
    item["metadata"] = StoryStore.decode_json(item.pop("metadata_json", None), {})
    return item


@dataclass(slots=True)
class NarrativeAuditEngine:
    """Compose deterministic narrative-state evidence without literary invention.

    An audit reports what ThothPad has tracked and where that tracked state is
    structurally incomplete. Absence is always described as *untracked* rather
    than as proof that the manuscript itself lacks a narrative property.
    """

    project: StoryProject
    store: StoryStore

    def _scope_units(self, story_unit_id: str, *, branch_id: str) -> list[dict[str, Any]]:
        root = next(
            iter(
                self.store.rows(
                    "SELECT * FROM story_units WHERE story_unit_id=? AND branch_id=?",
                    (story_unit_id, branch_id),
                )
            ),
            None,
        )
        if root is None:
            raise KeyError("story unit not found")
        rows = list(
            self.store.rows(
                """
                WITH RECURSIVE scoped(story_unit_id) AS (
                    SELECT story_unit_id FROM story_units
                    WHERE story_unit_id=? AND branch_id=?
                    UNION ALL
                    SELECT u.story_unit_id
                    FROM story_units u JOIN scoped s ON u.parent_id=s.story_unit_id
                    WHERE u.branch_id=?
                )
                SELECT u.* FROM story_units u JOIN scoped s ON s.story_unit_id=u.story_unit_id
                ORDER BY u.ordinal,u.story_unit_id LIMIT 256
                """,
                (story_unit_id, branch_id, branch_id),
            )
        )
        return [dict(row) for row in rows]

    def audit_story_unit(self, story_unit_id: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        units = self._scope_units(story_unit_id, branch_id=branch_id)
        unit_ids = [row["story_unit_id"] for row in units]
        placeholders = ",".join("?" for _ in unit_ids)

        decisions = [
            _decode_metadata(self.store, dict(row))
            for row in self.store.rows(
                f"""
                SELECT d.*,e.canonical_name AS agent_name
                FROM decisions d LEFT JOIN entities e ON e.entity_id=d.agent_entity_id
                WHERE d.branch_id=? AND d.story_unit_id IN ({placeholders})
                ORDER BY d.rowid LIMIT 100
                """,  # noqa: S608 - placeholders contain only bound-parameter markers
                (branch_id, *unit_ids),
            )
        ]
        opposition = [
            _decode_metadata(self.store, dict(row))
            for row in self.store.rows(
                f"""
                SELECT o.*,e.canonical_name AS source_entity_name
                FROM opposition_state o LEFT JOIN entities e ON e.entity_id=o.source_entity_id
                WHERE o.branch_id=? AND o.story_unit_id IN ({placeholders})
                ORDER BY o.rowid LIMIT 100
                """,  # noqa: S608 - placeholders contain only bound-parameter markers
                (branch_id, *unit_ids),
            )
        ]
        contracts = []
        for row in self.store.rows(
            f"""
            SELECT c.*,u.display_title,u.kind
            FROM scene_contracts c JOIN story_units u ON u.story_unit_id=c.story_unit_id
            WHERE c.story_unit_id IN ({placeholders}) AND u.branch_id=?
            ORDER BY u.ordinal LIMIT 100
            """,  # noqa: S608 - placeholders contain only bound-parameter markers
            (*unit_ids, branch_id),
        ):
            item = dict(row)
            item["contract"] = StoryStore.decode_json(item.pop("contract_json"), {})
            contracts.append(item)
        author_decisions = [
            dict(row)
            for row in self.store.rows(
                f"""
                SELECT a.*,u.display_title AS story_unit_title
                FROM author_decisions a LEFT JOIN story_units u ON u.story_unit_id=a.story_unit_id
                WHERE a.branch_id=? AND a.story_unit_id IN ({placeholders})
                ORDER BY a.rowid LIMIT 100
                """,  # noqa: S608 - placeholders contain only bound-parameter markers
                (branch_id, *unit_ids),
            )
        ]

        causal_edges: list[dict[str, Any]] = []
        unconnected_decisions: list[dict[str, Any]] = []
        for decision in decisions:
            edges = [
                dict(row)
                for row in self.store.rows(
                    """
                    SELECT * FROM causal_edges
                    WHERE branch_id=? AND (cause_kind='decision' AND cause_id=?)
                    ORDER BY edge_id LIMIT 50
                    """,
                    (branch_id, decision["decision_id"]),
                )
            ]
            causal_edges.extend(edges)
            if not edges:
                unconnected_decisions.append(decision)

        open_threads = [
            _decode_metadata(self.store, dict(row))
            for row in self.store.rows(
                """
                SELECT * FROM threads
                WHERE branch_id=? AND state IN ('OPEN','ACTIVE','DORMANT')
                ORDER BY rowid LIMIT 100
                """,
                (branch_id,),
            )
        ]
        forward_items = [
            _decode_metadata(self.store, dict(row))
            for row in self.store.rows(
                """
                SELECT * FROM promise_items
                WHERE branch_id=? AND state IN ('OPEN','DEVELOPED','PARTIALLY_PAID')
                ORDER BY rowid LIMIT 100
                """,
                (branch_id,),
            )
        ]

        findings: list[dict[str, Any]] = []
        if not decisions:
            findings.append(
                {
                    "code": "no_tracked_decisions",
                    "level": "coverage",
                    "observation": "No consequential decisions are currently tracked in this story scope.",
                    "interpretation_limit": (
                        "This is a state-coverage observation, not evidence that the prose lacks agency."
                    ),
                }
            )
        for decision in unconnected_decisions[:20]:
            findings.append(
                {
                    "code": "decision_without_tracked_consequence",
                    "level": "coverage",
                    "story_unit_id": decision.get("story_unit_id"),
                    "record_id": decision["decision_id"],
                    "observation": f"Tracked decision has no downstream causal edge: {decision['description']}",
                    "interpretation_limit": (
                        "The consequence may exist in prose but has not been linked in Story State."
                    ),
                }
            )
        if contracts and not opposition:
            conflict_contracts = [
                item
                for item in contracts
                if any(item["contract"].get(key) for key in ("conflict", "visible_goal", "stakes"))
            ]
            if conflict_contracts:
                findings.append(
                    {
                        "code": "contract_without_tracked_opposition",
                        "level": "coverage",
                        "observation": (
                            "A reviewed scene contract names conflict/goal/stakes, but no opposition record "
                            "is attached to this scope."
                        ),
                        "interpretation_limit": (
                            "This may indicate unmodeled opposition rather than a manuscript problem."
                        ),
                    }
                )
        if forward_items:
            question_count = sum(item["item_type"] == "READER_QUESTION" for item in forward_items)
            promise_count = sum(item["item_type"] == "DRAMATIC_PROMISE" for item in forward_items)
            findings.append(
                {
                    "code": "open_forward_motion_state",
                    "level": "state",
                    "observation": (
                        f"Project forward state includes {question_count} open reader question(s) and "
                        f"{promise_count} open dramatic promise(s)."
                    ),
                    "interpretation_limit": (
                        "These are project-level tracked obligations, not automatically obligations of this chapter."
                    ),
                }
            )

        root = units[0]
        return {
            "audit_kind": "story_unit",
            "diagnostic_only": True,
            "absence_means_untracked": True,
            "story_unit": {
                "story_unit_id": root["story_unit_id"],
                "kind": root["kind"],
                "display_title": root["display_title"],
                "status": root["status"],
            },
            "scope_unit_ids": unit_ids,
            "coverage": {
                "story_units": len(units),
                "decisions": len(decisions),
                "causal_edges": len({edge["edge_id"] for edge in causal_edges}),
                "opposition_records": len(opposition),
                "scene_contracts": len(contracts),
                "author_decisions": len(author_decisions),
                "open_threads_projectwide": len(open_threads),
                "open_forward_items_projectwide": len(forward_items),
            },
            "decisions": decisions,
            "causal_edges": list({edge["edge_id"]: edge for edge in causal_edges}.values()),
            "opposition": opposition,
            "scene_contracts": contracts,
            "author_decisions": author_decisions,
            "open_threads": open_threads,
            "open_forward_items": forward_items,
            "findings": findings,
        }
