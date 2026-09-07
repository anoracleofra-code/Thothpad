from __future__ import annotations

import uuid
from typing import Any

from backend.story.authority import KnowledgeStatus
from backend.story.store import StoryStore


def set_character_knowledge(
    store: StoryStore,
    *,
    character_id: str,
    claim_id: str,
    state: KnowledgeStatus | str,
    acquired_at: str | None = None,
    branch_id: str = "mainline",
    confidence: float = 1.0,
    source_claim_id: str | None = None,
    knowledge_id: str | None = None,
) -> str:
    normalized = KnowledgeStatus(state)
    knowledge_id = knowledge_id or str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"thothpad-knowledge:{character_id}:{claim_id}:{branch_id}:{acquired_at}:{normalized}",
        )
    )
    store.connection.execute(
        """
        INSERT INTO knowledge_state(
            knowledge_id,character_id,claim_id,state,acquired_at,branch_id,confidence,source_claim_id
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(knowledge_id) DO UPDATE SET
            state=excluded.state,acquired_at=excluded.acquired_at,branch_id=excluded.branch_id,
            confidence=excluded.confidence,source_claim_id=excluded.source_claim_id
        """,
        (
            knowledge_id,
            character_id,
            claim_id,
            str(normalized),
            acquired_at,
            branch_id,
            float(confidence),
            source_claim_id,
        ),
    )
    return knowledge_id


def knowledge_for_character(
    store: StoryStore,
    character_id: str,
    *,
    branch_id: str = "mainline",
    states: set[KnowledgeStatus | str] | None = None,
) -> list[dict[str, Any]]:
    allowed = {str(KnowledgeStatus(value)) for value in states} if states else None
    result = []
    for row in store.rows(
        """
        SELECT k.*,c.predicate,c.literal_value_json,c.status AS claim_status
        FROM knowledge_state k JOIN claims c ON c.claim_id=k.claim_id
        WHERE k.character_id=? AND k.branch_id=?
        ORDER BY k.acquired_at,k.knowledge_id
        """,
        (character_id, branch_id),
    ):
        if allowed is not None and row["state"] not in allowed:
            continue
        item = dict(row)
        item["literal_value"] = StoryStore.decode_json(item.pop("literal_value_json"), None)
        result.append(item)
    return result


def beliefs_for_character(store: StoryStore, character_id: str, *, branch_id: str = "mainline") -> list[dict[str, Any]]:
    return knowledge_for_character(
        store,
        character_id,
        branch_id=branch_id,
        states={KnowledgeStatus.BELIEVES, KnowledgeStatus.SUSPECTS, KnowledgeStatus.DISBELIEVES},
    )
