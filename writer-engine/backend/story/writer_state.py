from __future__ import annotations

import json
import uuid
from typing import Any

from backend.story.authority import AuthorityStatus, KnowledgeStatus, ThreadStatus
from backend.story.branches import (
    add_branch_overlay,
    create_branch,
    prepare_branch_merge,
    rebase_branch,
    record_completed_branch_merge,
)
from backend.story.causality import add_causal_edge, add_decision, add_opposition
from backend.story.claims import ClaimEvidenceInput, create_claim
from backend.story.knowledge import set_character_knowledge
from backend.story.persistence import commit_writer_state
from backend.story.project import StoryProject
from backend.story.promises import upsert_promise_item
from backend.story.reader import set_reader_state
from backend.story.relationships import set_relationship_state
from backend.story.store import StoryStore
from backend.story.threads import upsert_thread
from backend.story.timeline import add_timeline_event, set_world_state

_WRITER_CLAIM_STATUSES = frozenset(
    {
        AuthorityStatus.AUTHOR_LOCKED,
        AuthorityStatus.CONFIRMED_CANON,
        AuthorityStatus.AUTHOR_INTENT,
        AuthorityStatus.PROVISIONAL,
        AuthorityStatus.OPEN,
        AuthorityStatus.CONTESTED,
        AuthorityStatus.SUPERSEDED,
        AuthorityStatus.ARCHIVED,
    }
)


def _require_story_unit(store: StoryStore, story_unit_id: str) -> None:
    if next(iter(store.rows("SELECT 1 FROM story_units WHERE story_unit_id=?", (story_unit_id,))), None) is None:
        raise KeyError("story unit not found")


def _persist(project: StoryProject, store: StoryStore) -> None:
    commit_writer_state(project, store)


def _require_branch(store: StoryStore, branch_id: str) -> None:
    if next(iter(store.rows("SELECT 1 FROM branches WHERE branch_id=?", (branch_id,))), None) is None:
        raise KeyError("branch not found")


def _require_entity(store: StoryStore, entity_id: str) -> None:
    if store.entity(entity_id) is None:
        raise KeyError("entity not found")


def _require_claim(store: StoryStore, claim_id: str) -> None:
    if next(iter(store.rows("SELECT 1 FROM claims WHERE claim_id=?", (claim_id,))), None) is None:
        raise KeyError("claim not found")


def put_writer_entity(
    project: StoryProject,
    store: StoryStore,
    *,
    canonical_name: str,
    entity_type: str,
    description: str = "",
    aliases: list[str] | None = None,
    status: AuthorityStatus | str = AuthorityStatus.CONFIRMED_CANON,
    entity_id: str | None = None,
) -> dict[str, Any]:
    canonical_name = canonical_name.strip()
    entity_type = entity_type.strip().casefold()
    if not canonical_name or not entity_type:
        raise ValueError("writer entity requires a name and type")
    normalized = AuthorityStatus(status)
    if normalized in {AuthorityStatus.INFERENCE, AuthorityStatus.SUGGESTION}:
        raise ValueError("writer-owned entities cannot be stored as model inference/suggestion")
    identifier = entity_id or str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"thothpad-writer-entity:{project.project_id}:{entity_type}:{canonical_name.casefold()}",
        )
    )
    store.upsert_entity(
        identifier,
        canonical_name,
        entity_type,
        description=description.strip(),
        confidence=1.0,
        status=normalized,
        metadata={"created_by": "writer"},
    )
    store.add_entity_alias(identifier, canonical_name, confidence=1.0, confirmed=True)
    for alias in aliases or []:
        value = alias.strip()
        if value:
            store.add_entity_alias(identifier, value, confidence=1.0, confirmed=True)
    _persist(project, store)
    return {
        "entity_id": identifier,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "description": description.strip(),
        "status": str(normalized),
    }


def add_writer_entity_alias(
    project: StoryProject,
    store: StoryStore,
    *,
    entity_id: str,
    alias: str,
) -> dict[str, Any]:
    _require_entity(store, entity_id)
    alias = alias.strip()
    if not alias:
        raise ValueError("alias must not be empty")
    store.add_entity_alias(entity_id, alias, confidence=1.0, confirmed=True)
    _persist(project, store)
    return {"entity_id": entity_id, "alias": alias, "user_confirmed": True}


def put_writer_claim(
    project: StoryProject,
    store: StoryStore,
    *,
    predicate: str,
    literal_value: Any = None,
    subject_entity_id: str | None = None,
    object_entity_id: str | None = None,
    status: AuthorityStatus | str = AuthorityStatus.CONFIRMED_CANON,
    branch_id: str = "mainline",
    scope_id: str | None = None,
    confidence: float = 1.0,
    evidence: ClaimEvidenceInput | None = None,
    stable_key: str | None = None,
) -> str:
    predicate = predicate.strip()
    if not predicate:
        raise ValueError("writer claim predicate must not be empty")
    if subject_entity_id:
        _require_entity(store, subject_entity_id)
    if object_entity_id:
        _require_entity(store, object_entity_id)
    _require_branch(store, branch_id)
    normalized = AuthorityStatus(status)
    if normalized not in _WRITER_CLAIM_STATUSES:
        raise ValueError("writer claim status must be author-owned")
    claim_id = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=subject_entity_id,
        predicate=predicate,
        literal_value=literal_value,
        object_entity_id=object_entity_id,
        status=normalized,
        branch_id=branch_id,
        scope_id=scope_id,
        confidence=max(0.0, min(float(confidence), 1.0)),
        created_by="writer",
        evidence=evidence,
        stable_key=stable_key,
    )
    # If the deterministic compiler created this stable claim first, explicit
    # writer approval must elevate ownership rather than silently preserving the
    # compiler as authority.
    store.promote_claim(claim_id, normalized, approved_by="writer")
    _persist(project, store)
    return claim_id


def promote_writer_claim(
    project: StoryProject,
    store: StoryStore,
    *,
    claim_id: str,
    status: AuthorityStatus | str,
) -> dict[str, Any]:
    _require_claim(store, claim_id)
    normalized = AuthorityStatus(status)
    if normalized not in _WRITER_CLAIM_STATUSES:
        raise ValueError("writer claim status must be author-owned")
    store.promote_claim(claim_id, normalized, approved_by="writer")
    _persist(project, store)
    return {"claim_id": claim_id, "status": str(normalized), "created_by": "writer"}


def put_writer_timeline_event(
    project: StoryProject,
    store: StoryStore,
    *,
    title: str,
    story_unit_id: str | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    precision: str = "OPEN",
    branch_id: str = "mainline",
    status: AuthorityStatus | str = AuthorityStatus.CONFIRMED_CANON,
    metadata: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> str:
    if story_unit_id:
        _require_story_unit(store, story_unit_id)
    _require_branch(store, branch_id)
    identifier = add_timeline_event(
        store,
        title=title.strip(),
        story_unit_id=story_unit_id,
        time_start=time_start,
        time_end=time_end,
        precision=precision.strip().upper() or "OPEN",
        branch_id=branch_id,
        status=AuthorityStatus(status),
        metadata=metadata,
        event_id=event_id,
    )
    _persist(project, store)
    return identifier


def put_writer_world_state(
    project: StoryProject,
    store: StoryStore,
    *,
    entity_id: str | None,
    state_type: str,
    value: Any,
    valid_from: str | None = None,
    valid_until: str | None = None,
    branch_id: str = "mainline",
    status: AuthorityStatus | str = AuthorityStatus.CONFIRMED_CANON,
    evidence_claim_id: str | None = None,
    state_id: str | None = None,
) -> str:
    if entity_id:
        _require_entity(store, entity_id)
    if evidence_claim_id:
        _require_claim(store, evidence_claim_id)
    _require_branch(store, branch_id)
    identifier = set_world_state(
        store,
        entity_id=entity_id,
        state_type=state_type.strip(),
        value=value,
        valid_from=valid_from,
        valid_until=valid_until,
        branch_id=branch_id,
        status=AuthorityStatus(status),
        evidence_claim_id=evidence_claim_id,
        state_id=state_id,
    )
    if evidence_claim_id:
        store.add_dependency("claim", evidence_claim_id, "world_state", identifier, "supports")
    _persist(project, store)
    return identifier


def put_writer_character_knowledge(
    project: StoryProject,
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
    _require_entity(store, character_id)
    _require_claim(store, claim_id)
    if source_claim_id:
        _require_claim(store, source_claim_id)
    if acquired_at:
        _require_story_unit(store, acquired_at)
    _require_branch(store, branch_id)
    identifier = set_character_knowledge(
        store,
        character_id=character_id,
        claim_id=claim_id,
        state=state,
        acquired_at=acquired_at,
        branch_id=branch_id,
        confidence=max(0.0, min(float(confidence), 1.0)),
        source_claim_id=source_claim_id,
        knowledge_id=knowledge_id,
    )
    store.add_dependency("claim", claim_id, "knowledge_state", identifier, "known_or_believed_as")
    if source_claim_id and source_claim_id != claim_id:
        store.add_dependency("claim", source_claim_id, "knowledge_state", identifier, "supports")
    _persist(project, store)
    return identifier


def put_writer_reader_state(
    project: StoryProject,
    store: StoryStore,
    *,
    claim_id: str | None,
    state: str,
    story_unit_id: str | None,
    branch_id: str = "mainline",
    confidence: float = 1.0,
    reader_state_id: str | None = None,
) -> str:
    if claim_id:
        _require_claim(store, claim_id)
    if story_unit_id:
        _require_story_unit(store, story_unit_id)
    _require_branch(store, branch_id)
    identifier = set_reader_state(
        store,
        claim_id=claim_id,
        state=state,
        story_unit_id=story_unit_id,
        branch_id=branch_id,
        confidence=max(0.0, min(float(confidence), 1.0)),
        reader_state_id=reader_state_id,
    )
    if claim_id:
        store.add_dependency("claim", claim_id, "reader_state", identifier, "reader_access")
    _persist(project, store)
    return identifier


def put_writer_relationship(
    project: StoryProject,
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
    _require_entity(store, entity_a)
    _require_entity(store, entity_b)
    if evidence_claim_id:
        _require_claim(store, evidence_claim_id)
    _require_branch(store, branch_id)
    identifier = set_relationship_state(
        store,
        entity_a=entity_a,
        entity_b=entity_b,
        relationship_type=relationship_type.strip(),
        state=state,
        valid_from=valid_from,
        valid_until=valid_until,
        branch_id=branch_id,
        evidence_claim_id=evidence_claim_id,
        relationship_id=relationship_id,
    )
    if evidence_claim_id:
        store.add_dependency("claim", evidence_claim_id, "relationship", identifier, "supports")
    _persist(project, store)
    return identifier


def put_writer_thread(
    project: StoryProject,
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
    _require_branch(store, branch_id)
    identifier = upsert_thread(
        store,
        title=title.strip(),
        state=state,
        opened_at=opened_at,
        last_advanced_at=last_advanced_at,
        resolved_at=resolved_at,
        branch_id=branch_id,
        metadata=metadata,
        thread_id=thread_id,
    )
    _persist(project, store)
    return identifier


def put_writer_promise(
    project: StoryProject,
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
    if evidence_claim_id:
        _require_claim(store, evidence_claim_id)
    _require_branch(store, branch_id)
    identifier = upsert_promise_item(
        store,
        item_type=item_type,
        title=title.strip(),
        state=state,
        opened_at=opened_at,
        resolved_at=resolved_at,
        branch_id=branch_id,
        evidence_claim_id=evidence_claim_id,
        metadata=metadata,
        item_id=item_id,
    )
    if evidence_claim_id:
        store.add_dependency("claim", evidence_claim_id, "promise_item", identifier, "supports")
    _persist(project, store)
    return identifier


def put_writer_decision(
    project: StoryProject,
    store: StoryStore,
    *,
    description: str,
    agent_entity_id: str | None = None,
    story_unit_id: str | None = None,
    branch_id: str = "mainline",
    status: AuthorityStatus | str = AuthorityStatus.CONFIRMED_CANON,
    metadata: dict[str, Any] | None = None,
    decision_id: str | None = None,
) -> str:
    if agent_entity_id:
        _require_entity(store, agent_entity_id)
    if story_unit_id:
        _require_story_unit(store, story_unit_id)
    _require_branch(store, branch_id)
    identifier = add_decision(
        store,
        description=description.strip(),
        agent_entity_id=agent_entity_id,
        story_unit_id=story_unit_id,
        branch_id=branch_id,
        status=AuthorityStatus(status),
        metadata=metadata,
        decision_id=decision_id,
    )
    if story_unit_id:
        store.add_dependency("story_unit", story_unit_id, "decision", identifier, "contains")
    _persist(project, store)
    return identifier


def put_writer_causal_edge(
    project: StoryProject,
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
    if evidence_claim_id:
        _require_claim(store, evidence_claim_id)
    _require_branch(store, branch_id)
    identifier = add_causal_edge(
        store,
        cause_kind=cause_kind.strip(),
        cause_id=cause_id.strip(),
        effect_kind=effect_kind.strip(),
        effect_id=effect_id.strip(),
        relation=relation.strip() or "causes",
        branch_id=branch_id,
        confidence=max(0.0, min(float(confidence), 1.0)),
        evidence_claim_id=evidence_claim_id,
        edge_id=edge_id,
    )
    store.add_dependency(cause_kind.strip(), cause_id.strip(), "causal_edge", identifier, "cause_endpoint")
    store.add_dependency(effect_kind.strip(), effect_id.strip(), "causal_edge", identifier, "effect_endpoint")
    if evidence_claim_id:
        store.add_dependency("claim", evidence_claim_id, "causal_edge", identifier, "supports")
    _persist(project, store)
    return identifier


def put_writer_opposition(
    project: StoryProject,
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
    if source_entity_id:
        _require_entity(store, source_entity_id)
    if story_unit_id:
        _require_story_unit(store, story_unit_id)
    _require_branch(store, branch_id)
    identifier = add_opposition(
        store,
        objective_id=objective_id.strip(),
        description=description.strip(),
        source_entity_id=source_entity_id,
        story_unit_id=story_unit_id,
        branch_id=branch_id,
        metadata=metadata,
        opposition_id=opposition_id,
    )
    if story_unit_id:
        store.add_dependency("story_unit", story_unit_id, "opposition", identifier, "contains")
    _persist(project, store)
    return identifier


def set_scene_contract(
    project: StoryProject,
    store: StoryStore,
    *,
    story_unit_id: str,
    contract: dict[str, Any],
    status: AuthorityStatus | str = AuthorityStatus.AUTHOR_LOCKED,
) -> dict[str, Any]:
    _require_story_unit(store, story_unit_id)
    normalized = AuthorityStatus(status)
    if normalized not in {AuthorityStatus.AUTHOR_LOCKED, AuthorityStatus.CONFIRMED_CANON, AuthorityStatus.PROVISIONAL}:
        raise ValueError("writer scene contracts must be AUTHOR_LOCKED, CONFIRMED_CANON, or PROVISIONAL")
    store.connection.execute(
        """
        INSERT INTO scene_contracts(story_unit_id,contract_json,status,updated_by)
        VALUES(?,?,?,'writer')
        ON CONFLICT(story_unit_id) DO UPDATE SET
            contract_json=excluded.contract_json,status=excluded.status,updated_by='writer'
        """,
        (story_unit_id, json.dumps(contract, ensure_ascii=False), str(normalized)),
    )
    store.add_dependency("story_unit", story_unit_id, "scene_contract", story_unit_id, "defines")
    _persist(project, store)
    return {
        "story_unit_id": story_unit_id,
        "contract": dict(contract),
        "status": str(normalized),
        "updated_by": "writer",
    }


def put_author_decision(
    project: StoryProject,
    store: StoryStore,
    *,
    title: str,
    decision: str,
    rationale: str = "",
    revisit_trigger: str = "",
    story_unit_id: str | None = None,
    branch_id: str = "mainline",
    status: AuthorityStatus | str = AuthorityStatus.AUTHOR_LOCKED,
    author_decision_id: str | None = None,
) -> dict[str, Any]:
    if story_unit_id:
        _require_story_unit(store, story_unit_id)
    normalized = AuthorityStatus(status)
    if normalized not in {
        AuthorityStatus.AUTHOR_LOCKED,
        AuthorityStatus.CONFIRMED_CANON,
        AuthorityStatus.AUTHOR_INTENT,
    }:
        raise ValueError("author decisions must remain writer-owned intent/canon")
    if (
        branch_id != "mainline"
        and next(iter(store.rows("SELECT 1 FROM branches WHERE branch_id=?", (branch_id,))), None) is None
    ):
        raise KeyError("branch not found")
    identifier = author_decision_id or str(uuid.uuid4())
    store.connection.execute(
        """
        INSERT INTO author_decisions(
            author_decision_id,title,decision,rationale,revisit_trigger,story_unit_id,branch_id,status
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(author_decision_id) DO UPDATE SET
            title=excluded.title,decision=excluded.decision,rationale=excluded.rationale,
            revisit_trigger=excluded.revisit_trigger,story_unit_id=excluded.story_unit_id,
            branch_id=excluded.branch_id,status=excluded.status
        """,
        (
            identifier,
            title.strip(),
            decision.strip(),
            rationale.strip(),
            revisit_trigger.strip(),
            story_unit_id,
            branch_id,
            str(normalized),
        ),
    )
    if story_unit_id:
        store.add_dependency("story_unit", story_unit_id, "author_decision", identifier, "governs")
    _persist(project, store)
    return {
        "author_decision_id": identifier,
        "title": title.strip(),
        "decision": decision.strip(),
        "rationale": rationale.strip(),
        "revisit_trigger": revisit_trigger.strip(),
        "story_unit_id": story_unit_id,
        "branch_id": branch_id,
        "status": str(normalized),
    }


def put_writer_preference(
    project: StoryProject,
    store: StoryStore,
    *,
    statement: str,
    scope_kind: str = "project",
    scope_id: str = "",
    status: str = "CONFIRMED",
    confidence: float = 1.0,
    evidence: list[dict[str, Any]] | None = None,
    preference_id: str | None = None,
) -> dict[str, Any]:
    statement = statement.strip()
    if not statement:
        raise ValueError("writer preference statement must not be empty")
    scope_kind = scope_kind.strip().casefold()
    if scope_kind not in {"global", "project", "voice", "character", "scene_type"}:
        raise ValueError("unsupported writer preference scope")
    normalized_status = status.strip().upper()
    if normalized_status not in {"PROVISIONAL", "CONFIRMED", "IGNORED", "SUPERSEDED"}:
        raise ValueError("unsupported writer preference status")
    identifier = preference_id or str(uuid.uuid4())
    bounded_confidence = max(0.0, min(float(confidence), 1.0))
    store.connection.execute(
        """
        INSERT INTO writer_preferences(
            preference_id,scope_kind,scope_id,statement,status,confidence,evidence_json
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(preference_id) DO UPDATE SET
            scope_kind=excluded.scope_kind,scope_id=excluded.scope_id,statement=excluded.statement,
            status=excluded.status,confidence=excluded.confidence,evidence_json=excluded.evidence_json
        """,
        (
            identifier,
            scope_kind,
            scope_id,
            statement,
            normalized_status,
            bounded_confidence,
            json.dumps(evidence or [], ensure_ascii=False),
        ),
    )
    _persist(project, store)
    return {
        "preference_id": identifier,
        "scope_kind": scope_kind,
        "scope_id": scope_id,
        "statement": statement,
        "status": normalized_status,
        "confidence": bounded_confidence,
        "evidence": list(evidence or []),
    }


def create_writer_branch(
    project: StoryProject,
    store: StoryStore,
    *,
    parent_branch: str = "mainline",
    fork_story_unit: str | None = None,
    assumptions: list[str] | None = None,
    branch_id: str | None = None,
) -> str:
    if fork_story_unit:
        _require_story_unit(store, fork_story_unit)
    identifier = create_branch(
        store,
        parent_branch=parent_branch,
        fork_story_unit=fork_story_unit,
        assumptions=assumptions,
        branch_id=branch_id,
    )
    _persist(project, store)
    return identifier


def add_writer_branch_overlay(
    project: StoryProject,
    store: StoryStore,
    *,
    branch_id: str,
    record_kind: str,
    record_id: str,
    operation: str,
    payload: dict[str, Any] | None = None,
    overlay_id: str | None = None,
) -> str:
    identifier = add_branch_overlay(
        store,
        branch_id=branch_id,
        record_kind=record_kind,
        record_id=record_id,
        operation=operation,
        payload=payload,
        overlay_id=overlay_id,
    )
    _persist(project, store)
    return identifier


def prepare_writer_branch_merge(
    store: StoryStore,
    branch_id: str,
    overlay_ids: list[str],
) -> dict[str, Any]:
    return prepare_branch_merge(store, branch_id, overlay_ids)


def rebase_writer_branch(project: StoryProject, store: StoryStore, branch_id: str) -> dict[str, Any]:
    result = rebase_branch(store, branch_id, writer_confirmed=True)
    _persist(project, store)
    return result


def complete_writer_branch_merge(
    project: StoryProject,
    store: StoryStore,
    branch_id: str,
    applied: list[dict[str, str]],
    *,
    expected_parent_revision: str,
) -> dict[str, Any]:
    result = record_completed_branch_merge(
        store,
        branch_id,
        applied,
        writer_confirmed=True,
        expected_parent_revision=expected_parent_revision,
    )
    _persist(project, store)
    return result
