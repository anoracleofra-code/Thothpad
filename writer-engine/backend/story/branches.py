from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from backend.story.authority import BranchStatus
from backend.story.store import StoryStore

_REVISION_TABLES: tuple[tuple[str, str], ...] = (
    (
        "sources",
        "SELECT source_id,content_hash,authority_default,tombstoned FROM sources ORDER BY source_id",
    ),
    (
        "story_units",
        "SELECT story_unit_id,parent_id,source_id,content_hash,status FROM story_units "
        "WHERE branch_id='mainline' ORDER BY story_unit_id",
    ),
    (
        "claims",
        "SELECT claim_id,subject_entity_id,predicate,object_entity_id,literal_value_json,status,created_by "
        "FROM claims WHERE branch_id='mainline' ORDER BY claim_id",
    ),
    (
        "timeline_events",
        "SELECT event_id,story_unit_id,time_start,time_end,precision,status FROM timeline_events "
        "WHERE branch_id='mainline' ORDER BY event_id",
    ),
    (
        "world_state",
        "SELECT state_id,entity_id,state_type,value_json,valid_from,valid_until,status FROM world_state "
        "WHERE branch_id='mainline' ORDER BY state_id",
    ),
    (
        "knowledge_state",
        "SELECT knowledge_id,character_id,claim_id,state,acquired_at,confidence FROM knowledge_state "
        "WHERE branch_id='mainline' ORDER BY knowledge_id",
    ),
    (
        "reader_state",
        "SELECT reader_state_id,claim_id,state,story_unit_id,confidence FROM reader_state "
        "WHERE branch_id='mainline' ORDER BY reader_state_id",
    ),
    (
        "relationships",
        "SELECT relationship_id,entity_a,entity_b,relationship_type,state_json,valid_from,valid_until "
        "FROM relationships WHERE branch_id='mainline' ORDER BY relationship_id",
    ),
    (
        "threads",
        "SELECT thread_id,title,state,opened_at,last_advanced_at,resolved_at,metadata_json FROM threads "
        "WHERE branch_id='mainline' ORDER BY thread_id",
    ),
    (
        "promise_items",
        "SELECT item_id,item_type,title,state,opened_at,resolved_at,evidence_claim_id,metadata_json "
        "FROM promise_items WHERE branch_id='mainline' ORDER BY item_id",
    ),
    (
        "decisions",
        "SELECT decision_id,agent_entity_id,story_unit_id,description,status,metadata_json FROM decisions "
        "WHERE branch_id='mainline' ORDER BY decision_id",
    ),
    (
        "causal_edges",
        "SELECT edge_id,cause_kind,cause_id,effect_kind,effect_id,relation,confidence,evidence_claim_id "
        "FROM causal_edges WHERE branch_id='mainline' ORDER BY edge_id",
    ),
    (
        "opposition_state",
        "SELECT opposition_id,objective_id,source_entity_id,description,story_unit_id,metadata_json "
        "FROM opposition_state WHERE branch_id='mainline' ORDER BY opposition_id",
    ),
    (
        "scene_contracts",
        "SELECT story_unit_id,contract_json,status,updated_by FROM scene_contracts ORDER BY story_unit_id",
    ),
    (
        "author_decisions",
        "SELECT author_decision_id,title,decision,rationale,revisit_trigger,story_unit_id,status "
        "FROM author_decisions WHERE branch_id='mainline' ORDER BY author_decision_id",
    ),
)


def mainline_revision(store: StoryStore) -> str:
    """Return a deterministic fingerprint of the mainline state a branch depends on."""

    snapshot: dict[str, list[dict[str, Any]]] = {}
    for name, query in _REVISION_TABLES:
        snapshot[name] = [dict(row) for row in store.rows(query)]
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def branch_revision(store: StoryStore, branch_id: str, *, _seen: set[str] | None = None) -> str:
    if branch_id == "mainline":
        return mainline_revision(store)
    seen = set(_seen or set())
    if branch_id in seen:
        raise ValueError("branch parent cycle detected")
    seen.add(branch_id)
    branch = next(iter(store.rows("SELECT * FROM branches WHERE branch_id=?", (branch_id,))), None)
    if branch is None:
        raise KeyError("branch not found")
    parent = str(branch["parent_branch"] or "mainline")
    overlays = [
        dict(row)
        for row in store.rows(
            """
            SELECT overlay_id,record_kind,record_id,operation,payload_json
            FROM branch_overlays WHERE branch_id=? ORDER BY overlay_id
            """,
            (branch_id,),
        )
    ]
    payload = {
        "parent_revision": branch_revision(store, parent, _seen=seen),
        "overlays": overlays,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def branch_freshness(store: StoryStore, branch_id: str) -> dict[str, Any]:
    if branch_id == "mainline":
        revision = mainline_revision(store)
        return {
            "branch_id": branch_id,
            "parent_branch": None,
            "base_revision": revision,
            "current_parent_revision": revision,
            "stale": False,
        }
    branch = next(iter(store.rows("SELECT * FROM branches WHERE branch_id=?", (branch_id,))), None)
    if branch is None:
        raise KeyError("branch not found")
    parent = str(branch["parent_branch"] or "mainline")
    current = branch_revision(store, parent)
    base = str(branch["base_revision"] or "")
    return {
        "branch_id": branch_id,
        "parent_branch": parent,
        "base_revision": base,
        "current_parent_revision": current,
        "stale": not base or base != current,
    }


def create_branch(
    store: StoryStore,
    *,
    parent_branch: str = "mainline",
    fork_story_unit: str | None = None,
    base_revision: str = "",
    assumptions: list[str] | None = None,
    branch_id: str | None = None,
) -> str:
    if next(iter(store.rows("SELECT 1 FROM branches WHERE branch_id=?", (parent_branch,))), None) is None:
        raise KeyError("parent branch not found")
    branch_id = branch_id or f"ALT-{uuid.uuid4().hex[:8].upper()}"
    if not base_revision:
        base_revision = branch_revision(store, parent_branch)
    store.connection.execute(
        """
        INSERT INTO branches(branch_id,parent_branch,fork_story_unit,base_revision,status,assumptions_json)
        VALUES(?,?,?,?,?,?)
        """,
        (
            branch_id,
            parent_branch,
            fork_story_unit,
            base_revision,
            str(BranchStatus.ACTIVE),
            json.dumps(assumptions or [], ensure_ascii=False),
        ),
    )
    return branch_id


def add_branch_overlay(
    store: StoryStore,
    *,
    branch_id: str,
    record_kind: str,
    record_id: str,
    operation: str,
    payload: dict[str, Any] | None = None,
    overlay_id: str | None = None,
) -> str:
    if branch_id == "mainline":
        raise ValueError("mainline does not use branch overlays")
    if next(iter(store.rows("SELECT 1 FROM branches WHERE branch_id=?", (branch_id,))), None) is None:
        raise KeyError("branch not found")
    normalized = operation.strip().upper()
    if normalized not in {"ADD", "REPLACE", "DELETE"}:
        raise ValueError("overlay operation must be ADD, REPLACE, or DELETE")
    overlay_id = overlay_id or str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO branch_overlays(overlay_id,branch_id,record_kind,record_id,operation,payload_json)
        VALUES(?,?,?,?,?,?)
        """,
        (overlay_id, branch_id, record_kind, record_id, normalized, json.dumps(payload or {}, ensure_ascii=False)),
    )
    return overlay_id


def branch_overlay_view(store: StoryStore, branch_id: str, record_kind: str) -> list[dict[str, Any]]:
    if branch_id == "mainline":
        return []
    result = []
    for row in store.rows(
        """
        SELECT * FROM branch_overlays WHERE branch_id=? AND record_kind=? ORDER BY rowid
        """,
        (branch_id, record_kind),
    ):
        item = dict(row)
        item["payload"] = StoryStore.decode_json(item.pop("payload_json"), {})
        result.append(item)
    return result


def branch_lineage(store: StoryStore, branch_id: str) -> list[dict[str, Any]]:
    """Return branch records from the first alternate ancestor through branch_id."""

    if branch_id == "mainline":
        return []
    lineage: list[dict[str, Any]] = []
    current = branch_id
    seen: set[str] = set()
    while current != "mainline":
        if current in seen:
            raise ValueError("branch parent cycle detected")
        if len(seen) >= 64:
            raise ValueError("branch lineage exceeds supported depth")
        seen.add(current)
        row = next(iter(store.rows("SELECT * FROM branches WHERE branch_id=?", (current,))), None)
        if row is None:
            raise KeyError("branch not found")
        item = dict(row)
        item["assumptions"] = StoryStore.decode_json(item.pop("assumptions_json"), [])
        lineage.append(item)
        current = str(row["parent_branch"] or "mainline")
    lineage.reverse()
    return lineage


def effective_branch_overlays(store: StoryStore, branch_id: str, *, maximum: int = 200) -> list[dict[str, Any]]:
    """Return inherited overlay deltas without projecting them into mainline tables."""

    if branch_id == "mainline":
        return []
    result: list[dict[str, Any]] = []
    for branch in branch_lineage(store, branch_id):
        for row in store.rows(
            "SELECT * FROM branch_overlays WHERE branch_id=? ORDER BY rowid",
            (branch["branch_id"],),
        ):
            item = dict(row)
            item["payload"] = StoryStore.decode_json(item.pop("payload_json"), {})
            item["fork_story_unit"] = branch.get("fork_story_unit")
            item["branch_status"] = branch.get("status")
            result.append(item)
            if len(result) >= max(1, min(int(maximum), 500)):
                return result
    return result


def mark_branch_stale(store: StoryStore, branch_id: str) -> None:
    if branch_id == "mainline":
        return
    store.connection.execute(
        "UPDATE branches SET status=? WHERE branch_id=? AND status=?",
        (str(BranchStatus.STALE_NEEDS_REBASE), branch_id, str(BranchStatus.ACTIVE)),
    )


def branch_comparison(store: StoryStore, branch_id: str) -> dict[str, Any]:
    branch = next(iter(store.rows("SELECT * FROM branches WHERE branch_id=?", (branch_id,))), None)
    if branch is None:
        raise KeyError("branch not found")
    overlays = [
        {**dict(row), "payload": StoryStore.decode_json(row["payload_json"], {})}
        for row in store.rows("SELECT * FROM branch_overlays WHERE branch_id=? ORDER BY rowid", (branch_id,))
    ]
    for item in overlays:
        item.pop("payload_json", None)
    merged_overlay_ids = {
        row["overlay_id"]
        for row in store.rows(
            "SELECT overlay_id FROM branch_merge_history WHERE branch_id=?",
            (branch_id,),
        )
    }
    for item in overlays:
        item["merged"] = item["overlay_id"] in merged_overlay_ids
    return {
        "branch": dict(branch),
        "freshness": branch_freshness(store, branch_id),
        "overlays": overlays,
        "merged_overlay_ids": sorted(merged_overlay_ids),
    }


def merge_branch_overlays(store: StoryStore, branch_id: str, overlay_ids: list[str]) -> list[dict[str, Any]]:
    """Return reviewed merge operations; caller must apply each target-domain mutation.

    This deliberately does not mutate canonical records. It is a transactional
    handoff boundary for native/user-controlled merge workflows.
    """

    if branch_id == "mainline":
        raise ValueError("cannot merge mainline into itself")
    if not overlay_ids:
        return []
    freshness = branch_freshness(store, branch_id)
    if freshness["stale"]:
        raise ValueError("branch is stale and must be reviewed/rebased before merge")
    if len(set(overlay_ids)) != len(overlay_ids):
        raise ValueError("overlay_ids must not contain duplicates")
    placeholders = ",".join("?" for _ in overlay_ids)
    rows = list(
        store.rows(
            f"""
            SELECT * FROM branch_overlays
            WHERE branch_id=? AND overlay_id IN ({placeholders}) ORDER BY rowid
            """,  # noqa: S608 - placeholders only
            [branch_id, *overlay_ids],
        )
    )
    if len(rows) != len(overlay_ids):
        raise KeyError("one or more overlays do not belong to this branch")
    already_merged = {
        row["overlay_id"]
        for row in store.rows(
            f"""
            SELECT overlay_id FROM branch_merge_history
            WHERE branch_id=? AND overlay_id IN ({placeholders})
            """,  # noqa: S608 - placeholders only
            [branch_id, *overlay_ids],
        )
    }
    if already_merged:
        raise ValueError("one or more selected overlays were already merged")
    return [
        {
            "overlay_id": row["overlay_id"],
            "record_kind": row["record_kind"],
            "record_id": row["record_id"],
            "operation": row["operation"],
            "payload": StoryStore.decode_json(row["payload_json"], {}),
        }
        for row in rows
    ]


def prepare_branch_merge(store: StoryStore, branch_id: str, overlay_ids: list[str]) -> dict[str, Any]:
    freshness = branch_freshness(store, branch_id)
    operations = merge_branch_overlays(store, branch_id, overlay_ids)
    return {
        "branch_id": branch_id,
        "expected_parent_revision": freshness["current_parent_revision"],
        "base_revision": freshness["base_revision"],
        "operations": operations,
    }


def rebase_branch(store: StoryStore, branch_id: str, *, writer_confirmed: bool = False) -> dict[str, Any]:
    if branch_id == "mainline":
        raise ValueError("mainline cannot be rebased")
    if not writer_confirmed:
        raise PermissionError("branch rebase requires explicit writer confirmation")
    freshness = branch_freshness(store, branch_id)
    store.connection.execute(
        "UPDATE branches SET base_revision=?,status=? WHERE branch_id=?",
        (
            freshness["current_parent_revision"],
            str(BranchStatus.ACTIVE),
            branch_id,
        ),
    )
    return branch_freshness(store, branch_id)


def record_completed_branch_merge(
    store: StoryStore,
    branch_id: str,
    applied: list[dict[str, str]],
    *,
    writer_confirmed: bool = False,
    merge_id: str | None = None,
    expected_parent_revision: str = "",
) -> dict[str, Any]:
    """Record overlays only after a writer-controlled domain transaction applied them."""

    if not writer_confirmed:
        raise PermissionError("recording a branch merge requires explicit writer confirmation")
    if not applied:
        raise ValueError("applied merge records must not be empty")
    if not expected_parent_revision:
        raise ValueError("completed merge requires the reviewed expected_parent_revision")
    overlay_ids = [str(item.get("overlay_id", "")) for item in applied]
    if len(set(overlay_ids)) != len(overlay_ids):
        raise ValueError("applied merge records must not contain duplicate overlay IDs")
    branch = next(iter(store.rows("SELECT * FROM branches WHERE branch_id=?", (branch_id,))), None)
    if branch is None:
        raise KeyError("branch not found")
    if str(branch["base_revision"] or "") != expected_parent_revision:
        raise ValueError("completed merge does not match the reviewed branch base revision")
    placeholders = ",".join("?" for _ in overlay_ids)
    overlays = list(
        store.rows(
            f"""
            SELECT overlay_id FROM branch_overlays
            WHERE branch_id=? AND overlay_id IN ({placeholders})
            """,  # noqa: S608 - placeholders only
            [branch_id, *overlay_ids],
        )
    )
    if len(overlays) != len(overlay_ids):
        raise KeyError("one or more completed overlays do not belong to this branch")
    already_merged = list(
        store.rows(
            f"""
            SELECT overlay_id FROM branch_merge_history
            WHERE branch_id=? AND overlay_id IN ({placeholders})
            """,  # noqa: S608 - placeholders only
            [branch_id, *overlay_ids],
        )
    )
    if already_merged:
        raise ValueError("one or more completed overlays were already recorded as merged")
    normalized: list[tuple[str, str, str]] = []
    for item in applied:
        overlay_id = str(item.get("overlay_id", ""))
        target_kind = str(item.get("target_record_kind", "")).strip()
        target_id = str(item.get("target_record_id", "")).strip()
        if not overlay_id or not target_kind or not target_id:
            raise ValueError("completed merge records require overlay_id, target_record_kind and target_record_id")
        normalized.append((overlay_id, target_kind, target_id))
    merge_id = merge_id or str(uuid.uuid4())
    for overlay_id, target_kind, target_id in normalized:
        store.connection.execute(
            """
            INSERT INTO branch_merge_history(
                merge_id,branch_id,overlay_id,target_record_kind,target_record_id
            ) VALUES(?,?,?,?,?)
            """,
            (merge_id, branch_id, overlay_id, target_kind, target_id),
        )
    total = next(iter(store.rows("SELECT COUNT(*) AS count FROM branch_overlays WHERE branch_id=?", (branch_id,))))[
        "count"
    ]
    merged = next(
        iter(store.rows("SELECT COUNT(*) AS count FROM branch_merge_history WHERE branch_id=?", (branch_id,)))
    )["count"]
    if total > 0 and merged == total:
        store.connection.execute(
            "UPDATE branches SET status=? WHERE branch_id=?",
            (str(BranchStatus.MERGED), branch_id),
        )
    return {
        "merge_id": merge_id,
        "branch_id": branch_id,
        "recorded_overlay_ids": overlay_ids,
        "merged_count": merged,
        "overlay_count": total,
        "branch_status": next(iter(store.rows("SELECT status FROM branches WHERE branch_id=?", (branch_id,))))[
            "status"
        ],
    }
