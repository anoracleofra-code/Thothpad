from __future__ import annotations

import json
import uuid
from typing import Any

from backend.story.authority import ThreadStatus
from backend.story.store import StoryStore


def upsert_thread(
    store: StoryStore,
    *,
    title: str,
    state: ThreadStatus | str = ThreadStatus.OPEN,
    opened_at: str | None = None,
    last_advanced_at: str | None = None,
    resolved_at: str | None = None,
    branch_id: str = "mainline",
    metadata: dict[str, Any] | None = None,
    thread_id: str | None = None,
) -> str:
    thread_id = thread_id or str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO threads(thread_id,title,state,opened_at,last_advanced_at,resolved_at,branch_id,metadata_json)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(thread_id) DO UPDATE SET
            title=excluded.title,state=excluded.state,opened_at=excluded.opened_at,
            last_advanced_at=excluded.last_advanced_at,resolved_at=excluded.resolved_at,
            branch_id=excluded.branch_id,metadata_json=excluded.metadata_json
        """,
        (
            thread_id,
            title,
            str(ThreadStatus(state)),
            opened_at,
            last_advanced_at,
            resolved_at,
            branch_id,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    return thread_id


def list_threads(store: StoryStore, *, branch_id: str = "mainline") -> list[dict[str, Any]]:
    result = []
    for row in store.rows("SELECT * FROM threads WHERE branch_id=? ORDER BY opened_at,thread_id", (branch_id,)):
        item = dict(row)
        item["metadata"] = StoryStore.decode_json(item.pop("metadata_json"), {})
        result.append(item)
    return result
