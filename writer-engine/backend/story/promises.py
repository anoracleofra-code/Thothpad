from __future__ import annotations

import json
import uuid
from typing import Any

from backend.story.store import StoryStore

PROMISE_TYPES = frozenset({"SETUP", "READER_QUESTION", "DRAMATIC_PROMISE", "FORESHADOWING", "PAYOFF"})
PROMISE_STATES = frozenset({"OPEN", "DEVELOPED", "PARTIALLY_PAID", "PAID", "ABANDONED", "INTENTIONALLY_UNRESOLVED"})


def upsert_promise_item(
    store: StoryStore,
    *,
    item_type: str,
    title: str,
    state: str = "OPEN",
    opened_at: str | None = None,
    resolved_at: str | None = None,
    branch_id: str = "mainline",
    evidence_claim_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    item_id: str | None = None,
) -> str:
    normalized_type = item_type.strip().upper()
    normalized_state = state.strip().upper()
    if normalized_type not in PROMISE_TYPES:
        raise ValueError(f"unsupported promise item type: {item_type}")
    if normalized_state not in PROMISE_STATES:
        raise ValueError(f"unsupported promise state: {state}")
    item_id = item_id or str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO promise_items(
            item_id,item_type,title,state,opened_at,resolved_at,branch_id,evidence_claim_id,metadata_json
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(item_id) DO UPDATE SET
            item_type=excluded.item_type,title=excluded.title,state=excluded.state,opened_at=excluded.opened_at,
            resolved_at=excluded.resolved_at,branch_id=excluded.branch_id,
            evidence_claim_id=excluded.evidence_claim_id,metadata_json=excluded.metadata_json
        """,
        (
            item_id,
            normalized_type,
            title,
            normalized_state,
            opened_at,
            resolved_at,
            branch_id,
            evidence_claim_id,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    return item_id


def list_promise_items(
    store: StoryStore,
    *,
    item_type: str | None = None,
    branch_id: str = "mainline",
) -> list[dict[str, Any]]:
    params: list[Any] = [branch_id]
    extra = ""
    if item_type:
        extra = " AND item_type=?"
        params.append(item_type.strip().upper())
    result = []
    for row in store.rows(
        f"SELECT * FROM promise_items WHERE branch_id=?{extra} ORDER BY opened_at,item_id",  # noqa: S608
        params,
    ):
        item = dict(row)
        item["metadata"] = StoryStore.decode_json(item.pop("metadata_json"), {})
        result.append(item)
    return result
