from __future__ import annotations

import re
import uuid

from backend.story.authority import AuthorityStatus, KnowledgeStatus, SourceRole, ThreadStatus
from backend.story.claims import (
    ClaimEvidenceInput,
    claim_status_from_source,
    create_claim,
    explicit_field_claim_key,
)
from backend.story.knowledge import set_character_knowledge
from backend.story.promises import upsert_promise_item
from backend.story.sources import SourceRoleHint
from backend.story.store import StoryStore
from backend.story.threads import upsert_thread
from backend.story.timeline import set_world_state

_FIELD = re.compile(
    r"(?m)^(?:[-*]\s*)?(?:\*\*)?"
    r"([A-Za-z][A-Za-z0-9 /_'’-]{1,48})(?:\*\*)?\s*:\s*(.+?)\s*$"
)
_EMPTY_VALUE = re.compile(r"^(?:tbd|unknown|open|n/?a|none yet|\?)$", re.IGNORECASE)

_KNOWLEDGE_FIELDS = {
    "knows": KnowledgeStatus.KNOWS,
    "believes": KnowledgeStatus.BELIEVES,
    "suspects": KnowledgeStatus.SUSPECTS,
    "disbelieves": KnowledgeStatus.DISBELIEVES,
    "conceals": KnowledgeStatus.CONCEALS,
    "unaware": KnowledgeStatus.UNAWARE,
}
_WORLD_FIELDS = {
    "location": "location",
    "current location": "location",
    "resource": "resource",
    "resources": "resources",
    "injury": "injury",
    "injuries": "injuries",
    "deadline": "deadline",
}
_PROMISE_FIELDS = {
    "promise": "DRAMATIC_PROMISE",
    "dramatic promise": "DRAMATIC_PROMISE",
    "reader question": "READER_QUESTION",
    "setup": "SETUP",
    "foreshadowing": "FORESHADOWING",
    "payoff": "PAYOFF",
}
_THREAD_FIELDS = {
    "thread": ThreadStatus.OPEN,
    "plot thread": ThreadStatus.OPEN,
    "active thread": ThreadStatus.ACTIVE,
    "resolved thread": ThreadStatus.RESOLVED,
}
_INACTIVE_CLAIM_STATUSES = {
    AuthorityStatus.SUPERSEDED.value,
    AuthorityStatus.ARCHIVED.value,
    AuthorityStatus.OPEN.value,
}
_COMPILER_RELATION = "compiled_from_explicit_field"


def _normalized_field(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _predicate(field: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", field.casefold()).strip("_")
    return value[:64] or "attribute"


def _role_confidence(roles: list[SourceRoleHint], allowed: set[SourceRole]) -> float:
    return max((hint.confidence for hint in roles if hint.role in allowed), default=0.0)


def _claim_for_field(
    store: StoryStore,
    *,
    project_id: str,
    source_id: str,
    source_hash: str,
    source_authority: str,
    subject_entity_id: str | None,
    field: str,
    value: str,
    start: int,
    end: int,
    quote: str,
    confidence: float,
    namespace: str,
) -> str:
    predicate = _predicate(field)
    return create_claim(
        store,
        project_id=project_id,
        subject_entity_id=subject_entity_id,
        predicate=predicate,
        literal_value=value,
        status=claim_status_from_source(source_authority, value),
        confidence=confidence,
        created_by="compiler",
        evidence=ClaimEvidenceInput(
            source_id=source_id,
            start_offset=start,
            end_offset=end,
            quote=quote,
            source_hash=source_hash,
            evidence_type="explicit_story_state",
        ),
        stable_key=explicit_field_claim_key(
            source_id=source_id,
            subject_entity_id=subject_entity_id,
            predicate=predicate,
            value=value,
            namespace=namespace,
        ),
    )


def _claim_status(store: StoryStore, claim_id: str) -> str:
    row = next(iter(store.rows("SELECT status FROM claims WHERE claim_id=?", (claim_id,))), None)
    return str(row["status"]) if row is not None else AuthorityStatus.PROVISIONAL.value


def compile_explicit_story_state(
    store: StoryStore,
    *,
    project_id: str,
    source_id: str,
    source_hash: str,
    source_authority: str,
    subject_entity_id: str | None,
    text: str,
    roles: list[SourceRoleHint],
) -> list[str]:
    """Compile only explicit labeled author state; never infer state from prose."""

    character_confidence = _role_confidence(roles, {SourceRole.CHARACTER_REFERENCE})
    architecture_confidence = _role_confidence(
        roles,
        {SourceRole.OUTLINE, SourceRole.PLOT_REFERENCE, SourceRole.AUTHOR_NOTES},
    )
    derived_ids: list[str] = []

    for match in _FIELD.finditer(text):
        field = _normalized_field(match.group(1))
        value = match.group(2).strip()
        if not value or len(value) > 2_000 or _EMPTY_VALUE.fullmatch(value):
            continue

        if subject_entity_id is not None and character_confidence >= 0.65:
            if field in _KNOWLEDGE_FIELDS:
                claim_id = _claim_for_field(
                    store,
                    project_id=project_id,
                    source_id=source_id,
                    source_hash=source_hash,
                    source_authority=source_authority,
                    subject_entity_id=subject_entity_id,
                    field=field,
                    value=value,
                    start=match.start(),
                    end=match.end(),
                    quote=match.group(0),
                    confidence=character_confidence,
                    namespace="profile",
                )
                if _claim_status(store, claim_id) not in _INACTIVE_CLAIM_STATUSES:
                    knowledge_id = set_character_knowledge(
                        store,
                        character_id=subject_entity_id,
                        claim_id=claim_id,
                        state=_KNOWLEDGE_FIELDS[field],
                        confidence=character_confidence,
                        source_claim_id=claim_id,
                    )
                    store.add_dependency("claim", claim_id, "knowledge_state", knowledge_id, _COMPILER_RELATION)
                    derived_ids.append(knowledge_id)
                continue

            if field in _WORLD_FIELDS:
                claim_id = _claim_for_field(
                    store,
                    project_id=project_id,
                    source_id=source_id,
                    source_hash=source_hash,
                    source_authority=source_authority,
                    subject_entity_id=subject_entity_id,
                    field=field,
                    value=value,
                    start=match.start(),
                    end=match.end(),
                    quote=match.group(0),
                    confidence=character_confidence,
                    namespace="profile",
                )
                status = _claim_status(store, claim_id)
                if status not in _INACTIVE_CLAIM_STATUSES:
                    state_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"thothpad-explicit-world:{claim_id}:{_WORLD_FIELDS[field]}",
                        )
                    )
                    set_world_state(
                        store,
                        entity_id=subject_entity_id,
                        state_type=_WORLD_FIELDS[field],
                        value=value,
                        status=status,
                        evidence_claim_id=claim_id,
                        state_id=state_id,
                    )
                    store.add_dependency("claim", claim_id, "world_state", state_id, _COMPILER_RELATION)
                    derived_ids.append(state_id)
                continue

        if architecture_confidence >= 0.55 and field in _PROMISE_FIELDS:
            claim_id = _claim_for_field(
                store,
                project_id=project_id,
                source_id=source_id,
                source_hash=source_hash,
                source_authority=source_authority,
                subject_entity_id=None,
                field=field,
                value=value,
                start=match.start(),
                end=match.end(),
                quote=match.group(0),
                confidence=architecture_confidence,
                namespace="architecture",
            )
            status = _claim_status(store, claim_id)
            if status not in _INACTIVE_CLAIM_STATUSES:
                item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"thothpad-explicit-promise:{claim_id}"))
                upsert_promise_item(
                    store,
                    item_type=_PROMISE_FIELDS[field],
                    title=value,
                    evidence_claim_id=claim_id,
                    metadata={
                        "compiler": "explicit_field",
                        "authority_status": status,
                        "source_id": source_id,
                    },
                    item_id=item_id,
                )
                store.add_dependency("claim", claim_id, "promise_item", item_id, _COMPILER_RELATION)
                derived_ids.append(item_id)
            continue

        if architecture_confidence >= 0.55 and field in _THREAD_FIELDS:
            claim_id = _claim_for_field(
                store,
                project_id=project_id,
                source_id=source_id,
                source_hash=source_hash,
                source_authority=source_authority,
                subject_entity_id=None,
                field=field,
                value=value,
                start=match.start(),
                end=match.end(),
                quote=match.group(0),
                confidence=architecture_confidence,
                namespace="architecture",
            )
            status = _claim_status(store, claim_id)
            if status not in _INACTIVE_CLAIM_STATUSES:
                thread_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"thothpad-explicit-thread:{claim_id}"))
                upsert_thread(
                    store,
                    title=value,
                    state=_THREAD_FIELDS[field],
                    metadata={
                        "compiler": "explicit_field",
                        "evidence_claim_id": claim_id,
                        "authority_status": status,
                        "source_id": source_id,
                    },
                    thread_id=thread_id,
                )
                store.add_dependency("claim", claim_id, "thread", thread_id, _COMPILER_RELATION)
                derived_ids.append(thread_id)

    return derived_ids


def reconcile_explicit_story_state(store: StoryStore) -> int:
    """Remove only compiler-owned derivatives whose exact source proof is no longer current."""

    stale = list(
        store.rows(
            """
            SELECT d.source_id,d.dependent_kind,d.dependent_id
            FROM dependencies d
            JOIN claims c ON c.claim_id=d.source_id
            WHERE d.source_kind='claim' AND d.relation=?
              AND (
                  c.status IN (?,?,?)
                  OR NOT EXISTS (
                      SELECT 1 FROM claim_evidence e
                      WHERE e.claim_id=c.claim_id AND e.stale=0
                  )
              )
            """,
            (
                _COMPILER_RELATION,
                AuthorityStatus.SUPERSEDED.value,
                AuthorityStatus.ARCHIVED.value,
                AuthorityStatus.OPEN.value,
            ),
        )
    )
    tables = {
        "knowledge_state": ("knowledge_state", "knowledge_id"),
        "world_state": ("world_state", "state_id"),
        "promise_item": ("promise_items", "item_id"),
        "thread": ("threads", "thread_id"),
    }
    removed = 0
    for row in stale:
        target = tables.get(str(row["dependent_kind"]))
        if target is None:
            continue
        table, key = target
        cursor = store.connection.execute(
            f"DELETE FROM {table} WHERE {key}=?",  # noqa: S608 - fixed table/key allowlist above
            (row["dependent_id"],),
        )
        removed += cursor.rowcount
        store.connection.execute(
            """
            DELETE FROM dependencies
            WHERE source_kind='claim' AND source_id=?
              AND dependent_kind=? AND dependent_id=? AND relation=?
            """,
            (row["source_id"], row["dependent_kind"], row["dependent_id"], _COMPILER_RELATION),
        )
    return removed
