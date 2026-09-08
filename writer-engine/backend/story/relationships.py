from __future__ import annotations

import json
import uuid
from typing import Any

from backend.story.store import StoryStore


def set_relationship_state(
    store: StoryStore,
    *,
    entity_a: str,
    entity_b: str,
    relationship_type: str,
    state: dict[str, Any] | str,
    valid_from: str | None = None,
    valid_until: str | None = None,
    branch_id: str = "mainline",
    evidence_claim_id: str | None = None,
    relationship_id: str | None = None,
) -> str:
    relationship_id = relationship_id or str(uuid.uuid4())
    state_value = state if isinstance(state, dict) else {"label": state}
    store.connection.execute(
        """
        INSERT INTO relationships(
            relationship_id,entity_a,entity_b,relationship_type,state_json,valid_from,valid_until,
            branch_id,evidence_claim_id
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(relationship_id) DO UPDATE SET
            entity_a=excluded.entity_a,entity_b=excluded.entity_b,relationship_type=excluded.relationship_type,
            state_json=excluded.state_json,valid_from=excluded.valid_from,valid_until=excluded.valid_until,
            branch_id=excluded.branch_id,evidence_claim_id=excluded.evidence_claim_id
        """,
        (
            relationship_id,
            entity_a,
            entity_b,
            relationship_type,
            json.dumps(state_value, ensure_ascii=False),
            valid_from,
            valid_until,
            branch_id,
            evidence_claim_id,
        ),
    )
    return relationship_id


def relationships_for_entity(store: StoryStore, entity_id: str, *, branch_id: str = "mainline") -> list[dict[str, Any]]:
    result = []
    for row in store.rows(
        """
        SELECT * FROM relationships
        WHERE branch_id=? AND (entity_a=? OR entity_b=?)
        ORDER BY valid_from,relationship_id
        """,
        (branch_id, entity_id, entity_id),
    ):
        item = dict(row)
        item["state"] = StoryStore.decode_json(item.pop("state_json"), {})
        result.append(item)
    return result
