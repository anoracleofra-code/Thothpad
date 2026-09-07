from __future__ import annotations

import uuid
from typing import Any

from backend.story.store import StoryStore

READER_STATES = frozenset({"KNOWS", "SUSPECTS", "HAS_SEEN", "EXPECTS"})


def set_reader_state(
    store: StoryStore,
    *,
    claim_id: str | None,
    state: str,
    story_unit_id: str | None,
    branch_id: str = "mainline",
    confidence: float = 1.0,
    reader_state_id: str | None = None,
) -> str:
    normalized = state.strip().upper()
    if normalized not in READER_STATES:
        raise ValueError(f"unsupported reader state: {state}")
    reader_state_id = reader_state_id or str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"thothpad-reader:{claim_id}:{normalized}:{story_unit_id}:{branch_id}",
        )
    )
    store.connection.execute(
        """
        INSERT INTO reader_state(reader_state_id,claim_id,state,story_unit_id,branch_id,confidence)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(reader_state_id) DO UPDATE SET
            claim_id=excluded.claim_id,state=excluded.state,story_unit_id=excluded.story_unit_id,
            branch_id=excluded.branch_id,confidence=excluded.confidence
        """,
        (reader_state_id, claim_id, normalized, story_unit_id, branch_id, float(confidence)),
    )
    return reader_state_id


def reader_state_records(store: StoryStore, *, branch_id: str = "mainline") -> list[dict[str, Any]]:
    return [dict(row) for row in store.rows("SELECT * FROM reader_state WHERE branch_id=?", (branch_id,))]
