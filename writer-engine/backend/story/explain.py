from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.project import StoryProject
from backend.story.store import StoryStore


@dataclass(slots=True)
class StoryExplainer:
    project: StoryProject
    store: StoryStore

    def explain(self, record_kind: str, record_id: str) -> dict[str, Any]:
        kind = record_kind.strip().casefold()
        identifier = record_id.strip()
        if not kind or not identifier:
            raise ValueError("record_kind and record_id are required")
        if kind == "claim":
            grounded = self.store.claim_with_evidence(identifier)
            if grounded is None:
                raise KeyError("claim not found")
            return {
                "record_kind": "claim",
                "record_id": identifier,
                "record": grounded["claim"],
                "evidence": grounded["evidence"],
                "dependencies": self._dependencies("claim", identifier),
                "why": "Claim authority and exact supporting evidence are shown separately; evidence may be stale.",
            }
        table_map = {
            "knowledge": ("knowledge_state", "knowledge_id"),
            "reader_state": ("reader_state", "reader_state_id"),
            "world_state": ("world_state", "state_id"),
            "relationship": ("relationships", "relationship_id"),
            "thread": ("threads", "thread_id"),
            "promise": ("promise_items", "item_id"),
            "decision": ("decisions", "decision_id"),
            "causal_edge": ("causal_edges", "edge_id"),
            "opposition": ("opposition_state", "opposition_id"),
            "author_decision": ("author_decisions", "author_decision_id"),
        }
        if kind not in table_map:
            raise ValueError("unsupported explainable Story Model record kind")
        table, column = table_map[kind]
        row = next(iter(self.store.rows(f"SELECT * FROM {table} WHERE {column}=?", (identifier,))), None)  # noqa: S608
        if row is None:
            raise KeyError(f"{kind} record not found")
        record = dict(row)
        evidence_claim_id = str(record.get("evidence_claim_id") or record.get("claim_id") or "")
        evidence = None
        if evidence_claim_id:
            evidence = self.store.claim_with_evidence(evidence_claim_id)
        return {
            "record_kind": kind,
            "record_id": identifier,
            "record": record,
            "supporting_claim": evidence,
            "dependencies": self._dependencies(kind, identifier),
            "why": (
                "This explanation reports tracked provenance/dependencies only. Absence of a dependency or revocation "
                "record is not proof that no such event exists in the prose."
            ),
        }

    def _dependencies(self, kind: str, identifier: str) -> dict[str, list[dict[str, Any]]]:
        downstream = [
            dict(row)
            for row in self.store.rows(
                "SELECT * FROM dependencies WHERE source_kind=? AND source_id=? "
                "ORDER BY dependent_kind,dependent_id LIMIT 500",
                (kind, identifier),
            )
        ]
        upstream = [
            dict(row)
            for row in self.store.rows(
                "SELECT * FROM dependencies WHERE dependent_kind=? AND dependent_id=? "
                "ORDER BY source_kind,source_id LIMIT 500",
                (kind, identifier),
            )
        ]
        return {"upstream": upstream, "downstream": downstream}
