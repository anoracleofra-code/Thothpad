from __future__ import annotations

import json
import uuid
from typing import Any

from backend.story.authority import AuthorityStatus
from backend.story.store import StoryStore


def add_decision(
    store: StoryStore,
    *,
    description: str,
    agent_entity_id: str | None = None,
    story_unit_id: str | None = None,
    branch_id: str = "mainline",
    status: AuthorityStatus | str = AuthorityStatus.PROVISIONAL,
    metadata: dict[str, Any] | None = None,
    decision_id: str | None = None,
) -> str:
    decision_id = decision_id or str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO decisions(decision_id,agent_entity_id,story_unit_id,description,branch_id,status,metadata_json)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(decision_id) DO UPDATE SET
            agent_entity_id=excluded.agent_entity_id,story_unit_id=excluded.story_unit_id,
            description=excluded.description,branch_id=excluded.branch_id,status=excluded.status,
            metadata_json=excluded.metadata_json
        """,
        (
            decision_id,
            agent_entity_id,
            story_unit_id,
            description,
            branch_id,
            str(AuthorityStatus(status)),
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    return decision_id


def add_causal_edge(
    store: StoryStore,
    *,
    cause_kind: str,
    cause_id: str,
    effect_kind: str,
    effect_id: str,
    relation: str = "causes",
    branch_id: str = "mainline",
    confidence: float = 1.0,
    evidence_claim_id: str | None = None,
    edge_id: str | None = None,
) -> str:
    edge_id = edge_id or str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO causal_edges(
            edge_id,cause_kind,cause_id,effect_kind,effect_id,relation,branch_id,confidence,evidence_claim_id
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(edge_id) DO UPDATE SET
            cause_kind=excluded.cause_kind,cause_id=excluded.cause_id,effect_kind=excluded.effect_kind,
            effect_id=excluded.effect_id,relation=excluded.relation,branch_id=excluded.branch_id,
            confidence=excluded.confidence,evidence_claim_id=excluded.evidence_claim_id
        """,
        (
            edge_id,
            cause_kind,
            cause_id,
            effect_kind,
            effect_id,
            relation,
            branch_id,
            confidence,
            evidence_claim_id,
        ),
    )
    return edge_id


def trace_causality(
    store: StoryStore,
    *,
    record_kind: str,
    record_id: str,
    branch_id: str = "mainline",
    direction: str = "both",
    maximum_depth: int = 8,
) -> dict[str, Any]:
    if direction not in {"upstream", "downstream", "both"}:
        raise ValueError("direction must be upstream, downstream, or both")
    maximum_depth = max(1, min(maximum_depth, 32))
    frontier = {(record_kind, record_id)}
    visited = set(frontier)
    edges: list[dict[str, Any]] = []
    for _depth in range(maximum_depth):
        next_frontier: set[tuple[str, str]] = set()
        for kind, identifier in frontier:
            if direction in {"downstream", "both"}:
                rows = store.rows(
                    """
                    SELECT * FROM causal_edges
                    WHERE branch_id=? AND cause_kind=? AND cause_id=?
                    """,
                    (branch_id, kind, identifier),
                )
                for row in rows:
                    edge = dict(row)
                    edges.append(edge)
                    target = (row["effect_kind"], row["effect_id"])
                    if target not in visited:
                        visited.add(target)
                        next_frontier.add(target)
            if direction in {"upstream", "both"}:
                rows = store.rows(
                    """
                    SELECT * FROM causal_edges
                    WHERE branch_id=? AND effect_kind=? AND effect_id=?
                    """,
                    (branch_id, kind, identifier),
                )
                for row in rows:
                    edge = dict(row)
                    edges.append(edge)
                    target = (row["cause_kind"], row["cause_id"])
                    if target not in visited:
                        visited.add(target)
                        next_frontier.add(target)
        if not next_frontier:
            break
        frontier = next_frontier
    unique = {edge["edge_id"]: edge for edge in edges}
    return {
        "nodes": [{"kind": kind, "id": identifier} for kind, identifier in sorted(visited)],
        "edges": list(unique.values()),
    }


def add_opposition(
    store: StoryStore,
    *,
    objective_id: str,
    description: str,
    source_entity_id: str | None = None,
    story_unit_id: str | None = None,
    branch_id: str = "mainline",
    metadata: dict[str, Any] | None = None,
    opposition_id: str | None = None,
) -> str:
    opposition_id = opposition_id or str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO opposition_state(
            opposition_id,objective_id,source_entity_id,description,story_unit_id,branch_id,metadata_json
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(opposition_id) DO UPDATE SET
            objective_id=excluded.objective_id,source_entity_id=excluded.source_entity_id,
            description=excluded.description,story_unit_id=excluded.story_unit_id,
            branch_id=excluded.branch_id,metadata_json=excluded.metadata_json
        """,
        (
            opposition_id,
            objective_id,
            source_entity_id,
            description,
            story_unit_id,
            branch_id,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    return opposition_id
