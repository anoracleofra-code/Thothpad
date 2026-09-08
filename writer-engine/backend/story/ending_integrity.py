from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.causality import trace_causality
from backend.story.project import StoryProject
from backend.story.promises import list_promise_items
from backend.story.store import StoryStore
from backend.story.threads import list_threads


@dataclass(slots=True)
class EndingIntegrity:
    project: StoryProject
    store: StoryStore

    def audit(self, ending_story_unit_id: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        unit = next(
            iter(self.store.rows("SELECT * FROM story_units WHERE story_unit_id=?", (ending_story_unit_id,))),
            None,
        )
        if unit is None:
            raise KeyError("ending story unit not found")
        open_threads = [
            item
            for item in list_threads(self.store, branch_id=branch_id)
            if item["state"] not in {"CLOSED", "RESOLVED", "ABANDONED"}
        ]
        open_promises = [
            item
            for item in list_promise_items(self.store, branch_id=branch_id)
            if item["state"] not in {"PAID", "ABANDONED", "INTENTIONALLY_UNRESOLVED"}
        ]
        ending_decisions = [
            dict(row)
            for row in self.store.rows(
                "SELECT * FROM decisions WHERE story_unit_id=? AND branch_id=? ORDER BY rowid",
                (ending_story_unit_id, branch_id),
            )
        ]
        prerequisites = []
        for decision in ending_decisions[:50]:
            traced = trace_causality(
                self.store,
                record_kind="decision",
                record_id=decision["decision_id"],
                branch_id=branch_id,
                direction="upstream",
                maximum_depth=8,
            )
            for edge in traced.get("edges", []):
                prerequisites.append(edge)
        contract = next(
            iter(self.store.rows("SELECT * FROM scene_contracts WHERE story_unit_id=?", (ending_story_unit_id,))),
            None,
        )
        findings = []
        for item in open_threads[:100]:
            findings.append(
                {
                    "kind": "open_thread",
                    "record_id": item["thread_id"],
                    "title": item["title"],
                    "status": "DIAGNOSTIC",
                    "observation": "This tracked narrative thread remains open at the selected ending position.",
                }
            )
        for item in open_promises[:100]:
            findings.append(
                {
                    "kind": "unpaid_forward_state",
                    "record_id": item["item_id"],
                    "title": item["title"],
                    "item_type": item["item_type"],
                    "status": "DIAGNOSTIC",
                    "observation": (
                        "This tracked promise/question is not marked paid, abandoned, or intentionally unresolved."
                    ),
                }
            )
        return {
            "ending_story_unit": {
                "story_unit_id": ending_story_unit_id,
                "kind": unit["kind"],
                "display_title": unit["display_title"],
            },
            "open_threads": open_threads[:200],
            "open_promises": open_promises[:200],
            "ending_decisions": ending_decisions,
            "causal_prerequisites": prerequisites[:500],
            "scene_contract": (StoryStore.decode_json(contract["contract_json"], {}) if contract is not None else {}),
            "findings": findings,
            "backpropagation_policy": (
                "Backward prerequisites are diagnostics/SUGGESTIONS only. They do not become canon, setup, "
                "foreshadowing, or authorial intent unless the writer explicitly approves them."
            ),
        }
