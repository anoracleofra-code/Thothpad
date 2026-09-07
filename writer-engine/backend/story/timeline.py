from __future__ import annotations

import json
import uuid
from typing import Any

from backend.story.authority import AuthorityStatus
from backend.story.store import StoryStore


def add_timeline_event(
    store: StoryStore,
    *,
    title: str,
    story_unit_id: str | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    precision: str = "OPEN",
    branch_id: str = "mainline",
    status: AuthorityStatus | str = AuthorityStatus.PROVISIONAL,
    metadata: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> str:
    event_id = event_id or str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO timeline_events(
            event_id,title,story_unit_id,time_start,time_end,precision,branch_id,status,metadata_json
        )
        VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_id) DO UPDATE SET
            title=excluded.title,story_unit_id=excluded.story_unit_id,time_start=excluded.time_start,
            time_end=excluded.time_end,precision=excluded.precision,branch_id=excluded.branch_id,
            status=excluded.status,metadata_json=excluded.metadata_json
        """,
        (
            event_id,
            title,
            story_unit_id,
            time_start,
            time_end,
            precision,
            branch_id,
            str(AuthorityStatus(status)),
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    return event_id


def query_timeline(store: StoryStore, *, branch_id: str = "mainline") -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in store.rows(
            """
            SELECT * FROM timeline_events
            WHERE branch_id=?
            ORDER BY CASE WHEN time_start IS NULL THEN 1 ELSE 0 END,time_start,event_id
            """,
            (branch_id,),
        )
    ]


def set_world_state(
    store: StoryStore,
    *,
    entity_id: str | None,
    state_type: str,
    value: Any,
    valid_from: str | None = None,
    valid_until: str | None = None,
    branch_id: str = "mainline",
    status: AuthorityStatus | str = AuthorityStatus.PROVISIONAL,
    evidence_claim_id: str | None = None,
    state_id: str | None = None,
) -> str:
    state_id = state_id or str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO world_state(
            state_id,entity_id,state_type,value_json,valid_from,valid_until,branch_id,status,evidence_claim_id
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(state_id) DO UPDATE SET
            entity_id=excluded.entity_id,state_type=excluded.state_type,value_json=excluded.value_json,
            valid_from=excluded.valid_from,valid_until=excluded.valid_until,branch_id=excluded.branch_id,
            status=excluded.status,evidence_claim_id=excluded.evidence_claim_id
        """,
        (
            state_id,
            entity_id,
            state_type,
            json.dumps(value, ensure_ascii=False),
            valid_from,
            valid_until,
            branch_id,
            str(AuthorityStatus(status)),
            evidence_claim_id,
        ),
    )
    return state_id


def world_state_for(
    store: StoryStore,
    entity_id: str,
    *,
    state_type: str | None = None,
    branch_id: str = "mainline",
) -> list[dict[str, Any]]:
    params: list[Any] = [entity_id, branch_id]
    extra = ""
    if state_type:
        extra = " AND state_type=?"
        params.append(state_type)
    result = []
    for row in store.rows(
        f"""
        SELECT * FROM world_state
        WHERE entity_id=? AND branch_id=?{extra}
        ORDER BY valid_from,state_id
        """,  # noqa: S608 - optional condition is fixed SQL
        params,
    ):
        item = dict(row)
        item["value"] = StoryStore.decode_json(item.pop("value_json"), None)
        result.append(item)
    return result


def where_is_entity(store: StoryStore, entity_id: str, *, branch_id: str = "mainline") -> list[dict[str, Any]]:
    return world_state_for(store, entity_id, state_type="location", branch_id=branch_id)


def who_has_object(store: StoryStore, object_entity_id: str, *, branch_id: str = "mainline") -> list[dict[str, Any]]:
    result = []
    for row in store.rows(
        """
        SELECT w.*,e.canonical_name AS holder_name
        FROM world_state w LEFT JOIN entities e ON e.entity_id=w.entity_id
        WHERE w.state_type='possession' AND w.branch_id=?
        ORDER BY w.valid_from,w.state_id
        """,
        (branch_id,),
    ):
        value = StoryStore.decode_json(row["value_json"], None)
        if value == object_entity_id or (isinstance(value, dict) and value.get("object_entity_id") == object_entity_id):
            item = dict(row)
            item["value"] = value
            item.pop("value_json", None)
            result.append(item)
    return result
