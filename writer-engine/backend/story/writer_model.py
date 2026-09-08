from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from backend.story.persistence import commit_writer_state
from backend.story.project import StoryProject
from backend.story.store import StoryStore

_SPACE = re.compile(r"\s+")
_OBSERVABLE_EVENTS = frozenset(
    {
        "USER_ACCEPTED_SUGGESTION",
        "USER_REJECTED_SUGGESTION",
        "USER_UNDID_AGENT_TRANSACTION",
        "USER_REDID_AGENT_TRANSACTION",
        "USER_EDITED_AGENT_TARGET",
    }
)


def _clean(value: Any, maximum: int = 500) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()[:maximum]


def _event_signature(event: dict[str, Any]) -> str:
    payload = {
        "type": _clean(event.get("type"), 80),
        "timestamp_utc": _clean(event.get("timestamp_utc"), 80),
        "operation_id": _clean(event.get("operation_id") or event.get("related_operation_id"), 160),
        "suggestion_id": _clean(event.get("suggestion_id"), 160),
        "tool_id": _clean(event.get("tool_id"), 120),
        "summary": _clean(event.get("summary"), 500),
        "before": _clean(event.get("before"), 300),
        "after": _clean(event.get("after"), 300),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _topic(event: dict[str, Any]) -> str:
    summary = _clean(event.get("summary"), 240)
    tool_id = _clean(event.get("tool_id"), 100)
    if summary:
        return summary
    if tool_id:
        return tool_id.replace("_", " ")
    return "AI suggestion"


def _hypothesis(event: dict[str, Any]) -> tuple[str, str] | None:
    event_type = _clean(event.get("type"), 80).upper()
    if event_type not in _OBSERVABLE_EVENTS:
        return None
    topic = _topic(event)
    if event_type == "USER_ACCEPTED_SUGGESTION":
        return f"accept:{topic.casefold()}", f"Writer tends to accept suggestions described as: {topic}"
    if event_type == "USER_REJECTED_SUGGESTION":
        return f"reject:{topic.casefold()}", f"Writer tends to reject suggestions described as: {topic}"
    if event_type == "USER_UNDID_AGENT_TRANSACTION":
        return f"undo:{topic.casefold()}", f"Writer tends to undo AI edits described as: {topic}"
    if event_type == "USER_REDID_AGENT_TRANSACTION":
        return f"redo:{topic.casefold()}", f"Writer tends to restore AI edits described as: {topic}"
    return (
        "revise-agent-output",
        "Writer tends to revise AI-applied prose rather than keep it verbatim.",
    )


def _confidence(evidence_count: int) -> float:
    if evidence_count <= 1:
        return 0.45
    if evidence_count == 2:
        return 0.60
    if evidence_count == 3:
        return 0.70
    if evidence_count <= 5:
        return 0.80
    return 0.88


def _preference_id(project: StoryProject, scope_kind: str, scope_id: str, key: str) -> str:
    try:
        namespace = uuid.UUID(project.project_id)
    except ValueError:
        namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"thothpad-project:{project.project_id}")
    return str(uuid.uuid5(namespace, f"writer-preference:{scope_kind}:{scope_id}:{key}"))


def observe_writer_activity(
    project: StoryProject,
    store: StoryStore,
    events: list[dict[str, Any]],
    *,
    scope_kind: str = "project",
    scope_id: str = "",
) -> dict[str, Any]:
    """Compile local behavior into reviewable preference hypotheses.

    The result is deliberately PROVISIONAL. It is not an instruction to the
    model and it cannot become a confirmed writer preference without the
    writer-owned mutation path.
    """

    normalized_scope = scope_kind.strip().casefold()
    if normalized_scope not in {"global", "project", "voice", "character", "scene_type"}:
        raise ValueError("unsupported writer preference scope")
    normalized_scope_id = _clean(scope_id, 240)
    changed: list[str] = []
    ignored_events = 0

    for raw in events[:100]:
        if not isinstance(raw, dict):
            ignored_events += 1
            continue
        inferred = _hypothesis(raw)
        if inferred is None:
            ignored_events += 1
            continue
        key, statement = inferred
        preference_id = _preference_id(project, normalized_scope, normalized_scope_id, key)
        existing = next(
            iter(store.rows("SELECT * FROM writer_preferences WHERE preference_id=?", (preference_id,))),
            None,
        )
        if existing is not None and str(existing["status"]) in {"CONFIRMED", "IGNORED", "SUPERSEDED"}:
            # Explicit writer review outranks future behavioral inference.
            continue
        evidence = StoryStore.decode_json(existing["evidence_json"], []) if existing is not None else []
        signatures = {
            str(item.get("signature")) for item in evidence if isinstance(item, dict) and item.get("signature")
        }
        signature = _event_signature(raw)
        if signature not in signatures:
            evidence.append(
                {
                    "signature": signature,
                    "type": _clean(raw.get("type"), 80),
                    "timestamp_utc": _clean(raw.get("timestamp_utc"), 80),
                    "tool_id": _clean(raw.get("tool_id"), 120),
                    "summary": _clean(raw.get("summary"), 500),
                    "before": _clean(raw.get("before"), 300),
                    "after": _clean(raw.get("after"), 300),
                    "related_operation_id": _clean(raw.get("related_operation_id") or raw.get("operation_id"), 160),
                }
            )
        evidence = evidence[-24:]
        store.connection.execute(
            """
            INSERT INTO writer_preferences(
                preference_id,scope_kind,scope_id,statement,status,confidence,evidence_json
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(preference_id) DO UPDATE SET
                statement=excluded.statement,
                status=CASE
                    WHEN writer_preferences.status IN ('CONFIRMED','IGNORED','SUPERSEDED')
                    THEN writer_preferences.status ELSE 'PROVISIONAL' END,
                confidence=CASE
                    WHEN writer_preferences.status IN ('CONFIRMED','IGNORED','SUPERSEDED')
                    THEN writer_preferences.confidence ELSE excluded.confidence END,
                evidence_json=CASE
                    WHEN writer_preferences.status IN ('CONFIRMED','IGNORED','SUPERSEDED')
                    THEN writer_preferences.evidence_json ELSE excluded.evidence_json END
            """,
            (
                preference_id,
                normalized_scope,
                normalized_scope_id,
                statement,
                "PROVISIONAL",
                _confidence(len(evidence)),
                StoryStore.encode_json(evidence),
            ),
        )
        changed.append(preference_id)

    if changed:
        commit_writer_state(project, store)
    return {
        "observed": len(events[:100]),
        "updated_preferences": list(dict.fromkeys(changed)),
        "ignored_events": ignored_events,
        "writer_review_required": True,
    }


def writer_model(
    store: StoryStore,
    *,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    include_ignored: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if scope_kind:
        clauses.append("scope_kind=?")
        params.append(scope_kind.strip().casefold())
    if scope_id is not None:
        clauses.append("scope_id=?")
        params.append(_clean(scope_id, 240))
    if not include_ignored:
        clauses.append("status NOT IN ('IGNORED','SUPERSEDED')")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(max(1, min(int(limit), 500)))
    preferences: list[dict[str, Any]] = []
    for row in store.rows(
        f"SELECT * FROM writer_preferences{where} ORDER BY CASE status WHEN 'CONFIRMED' THEN 0 ELSE 1 END, "
        "confidence DESC,rowid LIMIT ?",  # noqa: S608 - where contains fixed clauses only
        tuple(params),
    ):
        item = dict(row)
        item["evidence"] = StoryStore.decode_json(item.pop("evidence_json", None), [])
        item["evidence_count"] = len(item["evidence"])
        item["reviewable"] = item["status"] == "PROVISIONAL"
        preferences.append(item)
    return {
        "preferences": preferences,
        "confirmed_count": sum(item["status"] == "CONFIRMED" for item in preferences),
        "provisional_count": sum(item["status"] == "PROVISIONAL" for item in preferences),
        "behavioral_inference_is_canon": False,
    }


def explain_preference(store: StoryStore, preference_id: str) -> dict[str, Any]:
    row = next(
        iter(store.rows("SELECT * FROM writer_preferences WHERE preference_id=?", (preference_id,))),
        None,
    )
    if row is None:
        raise KeyError("writer preference not found")
    item = dict(row)
    evidence = StoryStore.decode_json(item.pop("evidence_json", None), [])
    item["evidence"] = evidence
    item["explanation"] = (
        f"This {str(item['status']).lower()} preference is supported by {len(evidence)} bounded local behavior "
        "event(s). The writer can confirm it, ignore it, edit it, or supersede it."
    )
    item["behavioral_inference_is_canon"] = False
    return item
