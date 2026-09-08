from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from backend.story.authority import AuthorityStatus, SourceRole
from backend.story.branches import record_completed_branch_merge
from backend.story.claims import create_claim
from backend.story.context import ContextCompiler
from backend.story.exchange import export_story_bundle, import_story_bundle
from backend.story.indexing import run_index_batch
from backend.story.ingest import ProjectIngestor
from backend.story.lenses import put_story_lens
from backend.story.maintenance import rebuild_story_index
from backend.story.migrations import bind_legacy_workspace
from backend.story.persistence import persist_writer_state
from backend.story.project import StoryProject
from backend.story.promises import upsert_promise_item
from backend.story.proposals import mark_proposal_reviewed, proposal, submit_story_proposal
from backend.story.query import StoryQueryEngine
from backend.story.store import StoryStore
from backend.story.threads import upsert_thread
from backend.story.timeline import set_world_state
from backend.story.tools import invoke_story_tool
from backend.story.writer_state import (
    add_writer_branch_overlay,
    add_writer_entity_alias,
    complete_writer_branch_merge,
    create_writer_branch,
    prepare_writer_branch_merge,
    promote_writer_claim,
    put_author_decision,
    put_writer_causal_edge,
    put_writer_character_knowledge,
    put_writer_claim,
    put_writer_decision,
    put_writer_entity,
    put_writer_opposition,
    put_writer_preference,
    put_writer_promise,
    put_writer_reader_state,
    put_writer_relationship,
    put_writer_thread,
    put_writer_timeline_event,
    put_writer_world_state,
    rebase_writer_branch,
)
from backend.story.writer_state import (
    set_scene_contract as put_scene_contract,
)

WRITER_MUTATION_KINDS = frozenset(
    {
        "entity",
        "entity_alias",
        "claim",
        "promote_claim",
        "timeline_event",
        "world_state",
        "character_knowledge",
        "reader_state",
        "relationship",
        "thread",
        "promise",
        "decision",
        "causal_edge",
        "opposition",
        "scene_contract",
        "author_decision",
        "writer_preference",
        "story_lens",
    }
)


def _bounded_writer_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        raise ValueError("writer mutation payload is nested too deeply")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value[:10_000]
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not (-1e12 <= value <= 1e12):
            raise ValueError("writer mutation number is outside the supported range")
        return value
    if isinstance(value, list):
        if len(value) > 100:
            raise ValueError("writer mutation arrays are limited to 100 items")
        return [_bounded_writer_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("writer mutation objects are limited to 100 fields")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 120:
                raise ValueError("writer mutation object keys must be 1-120 character strings")
            result[key] = _bounded_writer_value(item, depth=depth + 1)
        return result
    raise ValueError("writer mutation payload contains an unsupported value type")


def _writer_mutation_result(kind: str, record_id: str, result: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mutation": kind,
        "record_id": record_id,
        "writer_owned": True,
        "persisted": True,
    }
    if result is not None:
        payload["record"] = result
    return payload


def _relative_source_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("source path must be a project-relative path")
    return path.as_posix()


@contextmanager
def story_runtime(
    root: str | Path,
    *,
    initialize: bool = True,
    ingest: bool = True,
) -> Iterator[tuple[StoryProject, StoryStore]]:
    if not initialize and not StoryProject.is_initialized(root):
        raise PermissionError("Story Project has not been initialized by ThothPad")
    project = StoryProject.open(root)
    store = StoryStore(project.cache_path)
    try:
        if ingest:
            ProjectIngestor(project, store).ingest()
        yield project, store
    finally:
        store.close()


def export_story_project(root: str | Path) -> dict[str, Any]:
    with story_runtime(root, initialize=False) as (project, store):
        return export_story_bundle(project, store)


def import_story_project(
    root: str | Path,
    bundle: dict[str, Any],
    *,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not StoryProject.is_initialized(root):
        raise PermissionError("Story Project has not been initialized by ThothPad")
    return import_story_bundle(root, bundle, writer_confirmed=writer_confirmed)


def rebuild_story_project_index(
    root: str | Path,
    *,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not StoryProject.is_initialized(root):
        raise PermissionError("Story Project has not been initialized by ThothPad")
    return rebuild_story_index(root, writer_confirmed=writer_confirmed)


def bind_legacy_story_workspace(
    root: str | Path,
    *,
    workspace_path: str | Path,
    manuscript_path: str,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not StoryProject.is_initialized(root):
        raise PermissionError("Story Project has not been initialized by ThothPad")
    with story_runtime(root, initialize=False) as (project, store):
        return bind_legacy_workspace(
            project,
            store,
            workspace_path=workspace_path,
            manuscript_path=manuscript_path,
            writer_confirmed=writer_confirmed,
        )


def index_story_project_batch(
    root: str | Path,
    *,
    maximum_documents: int = 100,
    reset: bool = False,
) -> dict[str, Any]:
    if not StoryProject.is_initialized(root):
        raise PermissionError("Story Project has not been initialized by ThothPad")
    return run_index_batch(root, maximum_documents=maximum_documents, reset=reset)


def submit_story_project_proposal(
    root: str | Path,
    *,
    proposal_kind: str,
    target_mutation: str,
    payload: dict[str, Any],
    branch_id: str = "mainline",
    story_unit_id: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    created_by: str = "model",
) -> dict[str, Any]:
    if not StoryProject.is_initialized(root):
        raise PermissionError("Story Project has not been initialized by ThothPad")
    mutation = target_mutation.strip().casefold()
    if mutation not in WRITER_MUTATION_KINDS:
        raise ValueError("proposal target mutation is not writer-reviewable")
    safe_payload = _bounded_writer_value(payload)
    assert isinstance(safe_payload, dict)
    project = StoryProject.open(root)
    return submit_story_proposal(
        project,
        proposal_kind=proposal_kind,
        target_mutation=mutation,
        payload=safe_payload,
        branch_id=branch_id,
        story_unit_id=story_unit_id,
        evidence=[dict(item) for item in (evidence or [])[:100] if isinstance(item, dict)],
        created_by=created_by,
    )


def review_story_project_proposal(
    root: str | Path,
    *,
    proposal_id: str,
    decision: str,
    payload_override: dict[str, Any] | None = None,
    note: str = "",
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("reviewing a Story Engine proposal requires explicit writer confirmation")
    if not StoryProject.is_initialized(root):
        raise PermissionError("Story Project has not been initialized by ThothPad")
    normalized = decision.strip().upper()
    project = StoryProject.open(root)
    existing = proposal(project, proposal_id)
    if existing is None:
        raise KeyError("proposal not found")
    if existing.get("status") != "PROPOSED":
        raise ValueError("proposal has already been reviewed")

    applied_record_id = ""
    applied: dict[str, Any] | None = None
    if normalized == "ACCEPTED":
        if str(existing.get("branch_id") or "mainline") != "mainline":
            raise ValueError("alternate-branch proposals must be applied through the branch workflow")
        mutation = str(existing.get("target_mutation") or "").casefold()
        payload = payload_override if payload_override is not None else existing.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("proposal payload must be an object")
        applied = apply_story_writer_mutation(
            root,
            mutation,
            payload,
            writer_confirmed=True,
        )
        applied_record_id = str(applied.get("record_id") or "")
    elif normalized != "REJECTED":
        raise ValueError("proposal decision must be ACCEPTED or REJECTED")

    reviewed_project = StoryProject.open(root)
    reviewed = mark_proposal_reviewed(
        reviewed_project,
        proposal_id=proposal_id,
        decision=normalized,
        applied_record_id=applied_record_id,
        note=note,
    )
    return {"reviewed": reviewed, "applied": applied}


def project_understanding(root: str | Path, *, initialize: bool = True) -> dict[str, Any]:
    with story_runtime(root, initialize=initialize) as (project, store):
        result = StoryQueryEngine(project, store).get_project_understanding()
        result["active_manuscripts"] = project.active_manuscripts()
        return result


def project_sources(
    root: str | Path,
    *,
    initialize: bool = True,
    offset: int = 0,
    limit: int = 100,
    role: str | None = None,
) -> dict[str, Any]:
    safe_offset = max(0, int(offset))
    safe_limit = max(1, min(int(limit), 500))
    if role:
        SourceRole(role)
    with story_runtime(root, initialize=initialize) as (project, store):
        query = StoryQueryEngine(project, store)
        rows = query.project_sources(role=role)
        selected = rows[safe_offset : safe_offset + safe_limit]
        sources: list[dict[str, Any]] = []
        for source in selected:
            roles = [dict(row) for row in store.source_roles(source["source_id"])]
            sources.append(
                {
                    "source_id": source["source_id"],
                    "path": source["relative_path"],
                    "display_name": source["display_name"],
                    "format": source["format"],
                    "authority": source["authority_default"],
                    "roles": roles,
                    "metadata": StoryStore.decode_json(source.get("metadata_json"), {}),
                }
            )
        next_offset = safe_offset + len(selected)
        return {
            "project_id": project.project_id,
            "sources": sources,
            "active_manuscripts": project.active_manuscripts(),
            "offset": safe_offset,
            "next_offset": next_offset if next_offset < len(rows) else None,
            "total": len(rows),
        }


def set_manuscript_order(root: str | Path, paths: list[str]) -> dict[str, Any]:
    normalized = [_relative_source_path(path) for path in paths]
    if len({path.casefold() for path in normalized}) != len(normalized):
        raise ValueError("manuscript order must not contain duplicate paths")
    with story_runtime(root, initialize=True) as (project, store):
        for path in normalized:
            source = store.source_by_path(path)
            if source is None:
                raise KeyError(f"project source not found: {path}")
            roles = {row["role"] for row in store.source_roles(source["source_id"]) if float(row["confidence"]) >= 0.5}
            if SourceRole.MANUSCRIPT.value not in roles:
                raise ValueError(f"source is not classified as manuscript: {path}")
        project.set_active_manuscripts(normalized)
        return {
            "project_id": project.project_id,
            "active_manuscripts": project.active_manuscripts(),
            "writer_owned": True,
        }


def set_source_override(
    root: str | Path,
    relative_path: str,
    *,
    roles: list[str] | None = None,
    authority: str | None = None,
    pattern: str | None = None,
) -> dict[str, Any]:
    path = _relative_source_path(relative_path)
    normalized_roles = [SourceRole(role).value for role in (roles or [])]
    normalized_authority = AuthorityStatus(authority).value if authority else None

    with story_runtime(root, initialize=True) as (project, store):
        source = store.source_by_path(path)
        if source is None:
            raise KeyError("project source not found")
        if pattern and project._relative_rule_pattern(pattern) is None:
            raise ValueError("source rule pattern must be project-relative")
        override = project.source_override(path)
        if roles is not None:
            override["roles"] = normalized_roles
        if authority is not None:
            override["authority"] = normalized_authority
        project.set_source_override(path, override)
        if pattern:
            project.add_source_rule(pattern, override)
        ProjectIngestor(project, store).ingest()
        updated = store.source_by_path(path)
        if updated is None:
            raise RuntimeError("source disappeared while applying override")
        return {
            "path": path,
            "authority": updated["authority_default"],
            "roles": [dict(row) for row in store.source_roles(updated["source_id"])],
            "writer_override": True,
            "learned_rule": pattern or "",
        }


def apply_story_writer_mutation(
    root: str | Path,
    mutation: str,
    payload: dict[str, Any],
    *,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    """Apply one explicit desktop-owned Story Model mutation.

    This function is intentionally not part of the read-only Story tool/MCP
    surface. The native app must obtain explicit writer confirmation first.
    """

    if not writer_confirmed:
        raise PermissionError("changing writer-owned story state requires explicit writer confirmation")
    kind = mutation.strip().casefold()
    if kind not in WRITER_MUTATION_KINDS:
        raise ValueError("unsupported writer mutation kind")
    if not isinstance(payload, dict):
        raise ValueError("writer mutation payload must be an object")
    safe = _bounded_writer_value(payload)
    assert isinstance(safe, dict)

    with story_runtime(root, initialize=True) as (project, store):
        if kind == "entity":
            result = put_writer_entity(
                project,
                store,
                canonical_name=str(safe.get("canonical_name", "")),
                entity_type=str(safe.get("entity_type", "")),
                description=str(safe.get("description", "")),
                aliases=(
                    [str(item) for item in safe.get("aliases", [])] if isinstance(safe.get("aliases"), list) else []
                ),
                status=str(safe.get("status", AuthorityStatus.CONFIRMED_CANON.value)),
                entity_id=str(safe.get("entity_id")) if safe.get("entity_id") else None,
            )
            return _writer_mutation_result(kind, str(result["entity_id"]), result)
        if kind == "entity_alias":
            result = add_writer_entity_alias(
                project,
                store,
                entity_id=str(safe.get("entity_id", "")),
                alias=str(safe.get("alias", "")),
            )
            return _writer_mutation_result(kind, str(result["entity_id"]), result)
        if kind == "claim":
            claim_id = put_writer_claim(
                project,
                store,
                predicate=str(safe.get("predicate", "")),
                literal_value=safe.get("literal_value"),
                subject_entity_id=str(safe.get("subject_entity_id")) if safe.get("subject_entity_id") else None,
                object_entity_id=str(safe.get("object_entity_id")) if safe.get("object_entity_id") else None,
                status=str(safe.get("status", AuthorityStatus.CONFIRMED_CANON.value)),
                branch_id=str(safe.get("branch_id", "mainline")),
                scope_id=str(safe.get("scope_id")) if safe.get("scope_id") else None,
                confidence=float(safe.get("confidence", 1.0)),
                stable_key=str(safe.get("stable_key")) if safe.get("stable_key") else None,
            )
            grounded = StoryQueryEngine(project, store)._grounded_claim_summary(claim_id)
            return _writer_mutation_result(kind, claim_id, grounded)
        if kind == "promote_claim":
            result = promote_writer_claim(
                project,
                store,
                claim_id=str(safe.get("claim_id", "")),
                status=str(safe.get("status", AuthorityStatus.CONFIRMED_CANON.value)),
            )
            return _writer_mutation_result(kind, str(result["claim_id"]), result)
        if kind == "timeline_event":
            record_id = put_writer_timeline_event(
                project,
                store,
                title=str(safe.get("title", "")),
                story_unit_id=str(safe.get("story_unit_id")) if safe.get("story_unit_id") else None,
                time_start=str(safe.get("time_start")) if safe.get("time_start") is not None else None,
                time_end=str(safe.get("time_end")) if safe.get("time_end") is not None else None,
                precision=str(safe.get("precision", "OPEN")),
                branch_id=str(safe.get("branch_id", "mainline")),
                status=str(safe.get("status", AuthorityStatus.CONFIRMED_CANON.value)),
                metadata=safe.get("metadata") if isinstance(safe.get("metadata"), dict) else None,
                event_id=str(safe.get("event_id")) if safe.get("event_id") else None,
            )
            return _writer_mutation_result(kind, record_id)
        if kind == "world_state":
            record_id = put_writer_world_state(
                project,
                store,
                entity_id=str(safe.get("entity_id")) if safe.get("entity_id") else None,
                state_type=str(safe.get("state_type", "")),
                value=safe.get("value"),
                valid_from=str(safe.get("valid_from")) if safe.get("valid_from") is not None else None,
                valid_until=str(safe.get("valid_until")) if safe.get("valid_until") is not None else None,
                branch_id=str(safe.get("branch_id", "mainline")),
                status=str(safe.get("status", AuthorityStatus.CONFIRMED_CANON.value)),
                evidence_claim_id=(str(safe.get("evidence_claim_id")) if safe.get("evidence_claim_id") else None),
                state_id=str(safe.get("state_id")) if safe.get("state_id") else None,
            )
            return _writer_mutation_result(kind, record_id)
        if kind == "character_knowledge":
            record_id = put_writer_character_knowledge(
                project,
                store,
                character_id=str(safe.get("character_id", "")),
                claim_id=str(safe.get("claim_id", "")),
                state=str(safe.get("state", "KNOWS")),
                acquired_at=str(safe.get("acquired_at")) if safe.get("acquired_at") else None,
                branch_id=str(safe.get("branch_id", "mainline")),
                confidence=float(safe.get("confidence", 1.0)),
                source_claim_id=str(safe.get("source_claim_id")) if safe.get("source_claim_id") else None,
                knowledge_id=str(safe.get("knowledge_id")) if safe.get("knowledge_id") else None,
            )
            return _writer_mutation_result(kind, record_id)
        if kind == "reader_state":
            record_id = put_writer_reader_state(
                project,
                store,
                claim_id=str(safe.get("claim_id")) if safe.get("claim_id") else None,
                state=str(safe.get("state", "KNOWS")),
                story_unit_id=str(safe.get("story_unit_id")) if safe.get("story_unit_id") else None,
                branch_id=str(safe.get("branch_id", "mainline")),
                confidence=float(safe.get("confidence", 1.0)),
                reader_state_id=(str(safe.get("reader_state_id")) if safe.get("reader_state_id") else None),
            )
            return _writer_mutation_result(kind, record_id)
        if kind == "relationship":
            record_id = put_writer_relationship(
                project,
                store,
                entity_a=str(safe.get("entity_a", "")),
                entity_b=str(safe.get("entity_b", "")),
                relationship_type=str(safe.get("relationship_type", "")),
                state=safe.get("state", {}),
                valid_from=str(safe.get("valid_from")) if safe.get("valid_from") is not None else None,
                valid_until=str(safe.get("valid_until")) if safe.get("valid_until") is not None else None,
                branch_id=str(safe.get("branch_id", "mainline")),
                evidence_claim_id=(str(safe.get("evidence_claim_id")) if safe.get("evidence_claim_id") else None),
                relationship_id=(str(safe.get("relationship_id")) if safe.get("relationship_id") else None),
            )
            return _writer_mutation_result(kind, record_id)
        if kind == "thread":
            record_id = put_writer_thread(
                project,
                store,
                title=str(safe.get("title", "")),
                state=str(safe.get("state", "OPEN")),
                opened_at=str(safe.get("opened_at")) if safe.get("opened_at") else None,
                last_advanced_at=(str(safe.get("last_advanced_at")) if safe.get("last_advanced_at") else None),
                resolved_at=str(safe.get("resolved_at")) if safe.get("resolved_at") else None,
                branch_id=str(safe.get("branch_id", "mainline")),
                metadata=safe.get("metadata") if isinstance(safe.get("metadata"), dict) else None,
                thread_id=str(safe.get("thread_id")) if safe.get("thread_id") else None,
            )
            return _writer_mutation_result(kind, record_id)
        if kind == "promise":
            record_id = put_writer_promise(
                project,
                store,
                item_type=str(safe.get("item_type", "READER_QUESTION")),
                title=str(safe.get("title", "")),
                state=str(safe.get("state", "OPEN")),
                opened_at=str(safe.get("opened_at")) if safe.get("opened_at") else None,
                resolved_at=str(safe.get("resolved_at")) if safe.get("resolved_at") else None,
                branch_id=str(safe.get("branch_id", "mainline")),
                evidence_claim_id=(str(safe.get("evidence_claim_id")) if safe.get("evidence_claim_id") else None),
                metadata=safe.get("metadata") if isinstance(safe.get("metadata"), dict) else None,
                item_id=str(safe.get("item_id")) if safe.get("item_id") else None,
            )
            return _writer_mutation_result(kind, record_id)
        if kind == "decision":
            record_id = put_writer_decision(
                project,
                store,
                description=str(safe.get("description", "")),
                agent_entity_id=(str(safe.get("agent_entity_id")) if safe.get("agent_entity_id") else None),
                story_unit_id=str(safe.get("story_unit_id")) if safe.get("story_unit_id") else None,
                branch_id=str(safe.get("branch_id", "mainline")),
                status=str(safe.get("status", AuthorityStatus.CONFIRMED_CANON.value)),
                metadata=safe.get("metadata") if isinstance(safe.get("metadata"), dict) else None,
                decision_id=str(safe.get("decision_id")) if safe.get("decision_id") else None,
            )
            return _writer_mutation_result(kind, record_id)
        if kind == "causal_edge":
            record_id = put_writer_causal_edge(
                project,
                store,
                cause_kind=str(safe.get("cause_kind", "")),
                cause_id=str(safe.get("cause_id", "")),
                effect_kind=str(safe.get("effect_kind", "")),
                effect_id=str(safe.get("effect_id", "")),
                relation=str(safe.get("relation", "causes")),
                branch_id=str(safe.get("branch_id", "mainline")),
                confidence=float(safe.get("confidence", 1.0)),
                evidence_claim_id=(str(safe.get("evidence_claim_id")) if safe.get("evidence_claim_id") else None),
                edge_id=str(safe.get("edge_id")) if safe.get("edge_id") else None,
            )
            return _writer_mutation_result(kind, record_id)
        if kind == "opposition":
            record_id = put_writer_opposition(
                project,
                store,
                objective_id=str(safe.get("objective_id", "")),
                description=str(safe.get("description", "")),
                source_entity_id=(str(safe.get("source_entity_id")) if safe.get("source_entity_id") else None),
                story_unit_id=str(safe.get("story_unit_id")) if safe.get("story_unit_id") else None,
                branch_id=str(safe.get("branch_id", "mainline")),
                metadata=safe.get("metadata") if isinstance(safe.get("metadata"), dict) else None,
                opposition_id=str(safe.get("opposition_id")) if safe.get("opposition_id") else None,
            )
            return _writer_mutation_result(kind, record_id)
        if kind == "scene_contract":
            contract_value = safe.get("contract")
            contract: dict[str, Any] = contract_value if isinstance(contract_value, dict) else {}
            result = put_scene_contract(
                project,
                store,
                story_unit_id=str(safe.get("story_unit_id", "")),
                contract=contract,
                status=str(safe.get("status", AuthorityStatus.AUTHOR_LOCKED.value)),
            )
            return _writer_mutation_result(kind, str(result["story_unit_id"]), result)
        if kind == "author_decision":
            result = put_author_decision(
                project,
                store,
                title=str(safe.get("title", "")),
                decision=str(safe.get("decision", "")),
                rationale=str(safe.get("rationale", "")),
                revisit_trigger=str(safe.get("revisit_trigger", "")),
                story_unit_id=str(safe.get("story_unit_id")) if safe.get("story_unit_id") else None,
                branch_id=str(safe.get("branch_id", "mainline")),
                status=str(safe.get("status", AuthorityStatus.AUTHOR_LOCKED.value)),
                author_decision_id=(str(safe.get("author_decision_id")) if safe.get("author_decision_id") else None),
            )
            return _writer_mutation_result(kind, str(result["author_decision_id"]), result)
        if kind == "story_lens":
            result = put_story_lens(
                project,
                store,
                name=str(safe.get("name", "")),
                definition=str(safe.get("definition", "")),
                status=str(safe.get("status", "ACTIVE")),
                lens_id=str(safe.get("lens_id")) if safe.get("lens_id") else None,
            )
            return _writer_mutation_result(kind, str(result["lens_id"]), result)
        result = put_writer_preference(
            project,
            store,
            statement=str(safe.get("statement", "")),
            scope_kind=str(safe.get("scope_kind", "project")),
            scope_id=str(safe.get("scope_id", "")),
            status=str(safe.get("status", "CONFIRMED")),
            confidence=float(safe.get("confidence", 1.0)),
            evidence=[item for item in safe.get("evidence", []) if isinstance(item, dict)]
            if isinstance(safe.get("evidence"), list)
            else [],
            preference_id=str(safe.get("preference_id")) if safe.get("preference_id") else None,
        )
        return _writer_mutation_result(kind, str(result["preference_id"]), result)


def call_story_tool(
    root: str | Path,
    tool_id: str,
    arguments: dict[str, Any] | None = None,
    *,
    initialize: bool = True,
) -> dict[str, Any]:
    auto_ingest = tool_id not in {"get_indexing_status", "get_migration_status"}
    with story_runtime(root, initialize=initialize, ingest=auto_ingest) as (project, store):
        return invoke_story_tool(
            tool_id,
            dict(arguments or {}),
            query=StoryQueryEngine(project, store),
            context=ContextCompiler(project, store),
        )


def observe_story_writer_model(
    root: str | Path,
    events: list[dict[str, Any]],
    *,
    scope_kind: str = "project",
    scope_id: str = "",
) -> dict[str, Any]:
    """Compile local desktop activity into provisional writer-model evidence.

    This operation is intentionally desktop-only. It never confirms a
    preference and is not exposed through MCP or the model-call tool surface.
    """

    from backend.story.writer_model import observe_writer_activity

    bounded_events = [dict(item) for item in events[:100] if isinstance(item, dict)]
    with story_runtime(root, initialize=True) as (project, store):
        return observe_writer_activity(
            project,
            store,
            bounded_events,
            scope_kind=scope_kind,
            scope_id=scope_id,
        )


def create_story_branch(
    root: str | Path,
    *,
    parent_branch: str = "mainline",
    fork_story_unit: str | None = None,
    assumptions: list[str] | None = None,
    branch_id: str | None = None,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("creating an alternate branch requires explicit writer confirmation")
    clean_assumptions = [str(item).strip()[:2_000] for item in (assumptions or []) if str(item).strip()][:100]
    with story_runtime(root, initialize=True) as (project, store):
        created = create_writer_branch(
            project,
            store,
            parent_branch=parent_branch,
            fork_story_unit=fork_story_unit,
            assumptions=clean_assumptions,
            branch_id=branch_id,
        )
        return StoryQueryEngine(project, store).compare_branch(created)


def add_story_branch_overlay(
    root: str | Path,
    *,
    branch_id: str,
    record_kind: str,
    record_id: str,
    operation: str,
    payload: dict[str, Any] | None = None,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("adding a branch change requires explicit writer confirmation")
    kind = record_kind.strip()[:120]
    identifier = record_id.strip()[:240]
    if not kind or not identifier:
        raise ValueError("record_kind and record_id are required")
    clean_payload = dict(payload or {})
    with story_runtime(root, initialize=True) as (project, store):
        overlay_id = add_writer_branch_overlay(
            project,
            store,
            branch_id=branch_id,
            record_kind=kind,
            record_id=identifier,
            operation=operation,
            payload=clean_payload,
        )
        comparison = StoryQueryEngine(project, store).compare_branch(branch_id)
        comparison["created_overlay_id"] = overlay_id
        return comparison


def rebase_story_branch(
    root: str | Path,
    branch_id: str,
    *,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("rebasing an alternate branch requires explicit writer confirmation")
    with story_runtime(root, initialize=True) as (project, store):
        return rebase_writer_branch(project, store, branch_id)


def prepare_story_branch_merge(
    root: str | Path,
    branch_id: str,
    overlay_ids: list[str],
) -> dict[str, Any]:
    with story_runtime(root, initialize=True) as (_project, store):
        prepared = prepare_writer_branch_merge(store, branch_id, [str(item) for item in overlay_ids])
        for operation in prepared.get("operations", []):
            if not isinstance(operation, dict) or operation.get("operation") == "ADD":
                continue
            operation["retcon_impact"] = StoryQueryEngine(_project, store).retcon_impact(
                source_kind=str(operation.get("record_kind", "")),
                source_id=str(operation.get("record_id", "")),
                maximum_nodes=5_000,
            )
        return prepared


_BRANCH_APPLY_KINDS = frozenset({"claim", "world_state", "thread", "promise_item", "scene_contract"})
_BRANCH_WRITER_STATUSES = frozenset(
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


def _mainline_record_exists(store: StoryStore, table: str, key: str, identifier: str) -> bool:
    allowed = {
        ("claims", "claim_id"),
        ("world_state", "state_id"),
        ("threads", "thread_id"),
        ("promise_items", "item_id"),
    }
    if (table, key) not in allowed:
        raise ValueError("unsupported branch merge record table")
    return (
        next(
            iter(
                store.rows(
                    f"SELECT 1 FROM {table} WHERE {key}=? AND branch_id='mainline'",  # noqa: S608
                    (identifier,),
                )
            ),
            None,
        )
        is not None
    )


def _branch_status(value: Any, default: AuthorityStatus) -> AuthorityStatus:
    normalized = AuthorityStatus(str(value or default.value))
    if normalized not in _BRANCH_WRITER_STATUSES:
        raise ValueError("branch merge cannot promote model-only authority status")
    return normalized


def _apply_claim_overlay(
    project: StoryProject,
    store: StoryStore,
    branch_id: str,
    operation: str,
    record_id: str,
    payload: dict[str, Any],
) -> str:
    exists = _mainline_record_exists(store, "claims", "claim_id", record_id)
    if operation == "DELETE":
        if not exists:
            raise KeyError("mainline claim not found")
        store.connection.execute(
            "UPDATE claims SET status=?,created_by='writer' WHERE claim_id=? AND branch_id='mainline'",
            (AuthorityStatus.SUPERSEDED.value, record_id),
        )
        return record_id
    if operation == "REPLACE":
        if not exists:
            raise KeyError("mainline claim not found")
        existing = next(iter(store.rows("SELECT * FROM claims WHERE claim_id=?", (record_id,))))
        subject = payload.get("subject_entity_id", existing["subject_entity_id"])
        object_id = payload.get("object_entity_id", existing["object_entity_id"])
        if subject and store.entity(str(subject)) is None:
            raise KeyError("claim subject entity not found")
        if object_id and store.entity(str(object_id)) is None:
            raise KeyError("claim object entity not found")
        predicate = str(payload.get("predicate", existing["predicate"])).strip()
        if not predicate:
            raise ValueError("claim predicate must not be empty")
        literal = payload.get(
            "literal_value",
            StoryStore.decode_json(existing["literal_value_json"], None),
        )
        qualifiers = payload.get(
            "qualifiers",
            StoryStore.decode_json(existing["qualifiers_json"], {}),
        )
        status = _branch_status(payload.get("status"), AuthorityStatus.CONFIRMED_CANON)
        store.connection.execute(
            """
            UPDATE claims SET
                subject_entity_id=?,predicate=?,object_entity_id=?,literal_value_json=?,qualifiers_json=?,
                status=?,scope_id=?,confidence=?,created_by='writer'
            WHERE claim_id=? AND branch_id='mainline'
            """,
            (
                str(subject) if subject else None,
                predicate,
                str(object_id) if object_id else None,
                StoryStore.encode_json(literal),
                StoryStore.encode_json(qualifiers if isinstance(qualifiers, dict) else {}),
                status.value,
                payload.get("scope_id", existing["scope_id"]),
                max(0.0, min(float(payload.get("confidence", existing["confidence"])), 1.0)),
                record_id,
            ),
        )
        return record_id

    if exists:
        raise ValueError("ADD claim overlay collides with an existing mainline claim")
    subject = str(payload.get("subject_entity_id")) if payload.get("subject_entity_id") else None
    object_id = str(payload.get("object_entity_id")) if payload.get("object_entity_id") else None
    if subject and store.entity(subject) is None:
        raise KeyError("claim subject entity not found")
    if object_id and store.entity(object_id) is None:
        raise KeyError("claim object entity not found")
    predicate = str(payload.get("predicate", "")).strip()
    if not predicate:
        raise ValueError("ADD claim overlay requires predicate")
    status = _branch_status(payload.get("status"), AuthorityStatus.CONFIRMED_CANON)
    claim_id = create_claim(
        store,
        project_id=project.project_id,
        subject_entity_id=subject,
        predicate=predicate,
        literal_value=payload.get("literal_value"),
        object_entity_id=object_id,
        status=status,
        branch_id="mainline",
        scope_id=str(payload.get("scope_id")) if payload.get("scope_id") else None,
        confidence=max(0.0, min(float(payload.get("confidence", 1.0)), 1.0)),
        created_by="writer",
        stable_key=f"thothpad-branch-merge:{branch_id}:claim:{record_id}",
    )
    store.promote_claim(claim_id, status, approved_by="writer")
    return claim_id


def _apply_world_state_overlay(
    store: StoryStore,
    operation: str,
    record_id: str,
    payload: dict[str, Any],
) -> str:
    exists = _mainline_record_exists(store, "world_state", "state_id", record_id)
    if operation == "DELETE":
        if not exists:
            raise KeyError("mainline world state not found")
        store.connection.execute(
            "UPDATE world_state SET status=? WHERE state_id=? AND branch_id='mainline'",
            (AuthorityStatus.SUPERSEDED.value, record_id),
        )
        return record_id
    if operation == "ADD" and exists:
        raise ValueError("ADD world-state overlay collides with existing mainline state")
    if operation == "REPLACE" and not exists:
        raise KeyError("mainline world state not found")
    entity_id = str(payload.get("entity_id")) if payload.get("entity_id") else None
    if entity_id and store.entity(entity_id) is None:
        raise KeyError("world-state entity not found")
    state_type = str(payload.get("state_type", "")).strip()
    if not state_type:
        raise ValueError("world-state overlay requires state_type")
    evidence_claim = str(payload.get("evidence_claim_id")) if payload.get("evidence_claim_id") else None
    if (
        evidence_claim
        and next(iter(store.rows("SELECT 1 FROM claims WHERE claim_id=?", (evidence_claim,))), None) is None
    ):
        raise KeyError("world-state evidence claim not found")
    set_world_state(
        store,
        entity_id=entity_id,
        state_type=state_type,
        value=payload.get("value"),
        valid_from=str(payload.get("valid_from")) if payload.get("valid_from") is not None else None,
        valid_until=str(payload.get("valid_until")) if payload.get("valid_until") is not None else None,
        branch_id="mainline",
        status=_branch_status(payload.get("status"), AuthorityStatus.CONFIRMED_CANON),
        evidence_claim_id=evidence_claim,
        state_id=record_id,
    )
    return record_id


def _apply_thread_overlay(store: StoryStore, operation: str, record_id: str, payload: dict[str, Any]) -> str:
    exists = _mainline_record_exists(store, "threads", "thread_id", record_id)
    if operation == "DELETE":
        if not exists:
            raise KeyError("mainline thread not found")
        store.connection.execute(
            "UPDATE threads SET state='ABANDONED' WHERE thread_id=? AND branch_id='mainline'",
            (record_id,),
        )
        return record_id
    if operation == "ADD" and exists:
        raise ValueError("ADD thread overlay collides with existing mainline thread")
    if operation == "REPLACE" and not exists:
        raise KeyError("mainline thread not found")
    title = str(payload.get("title", "")).strip()
    if not title:
        raise ValueError("thread overlay requires title")
    upsert_thread(
        store,
        title=title,
        state=str(payload.get("state", "OPEN")),
        opened_at=str(payload.get("opened_at")) if payload.get("opened_at") else None,
        last_advanced_at=str(payload.get("last_advanced_at")) if payload.get("last_advanced_at") else None,
        resolved_at=str(payload.get("resolved_at")) if payload.get("resolved_at") else None,
        branch_id="mainline",
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
        thread_id=record_id,
    )
    return record_id


def _apply_promise_overlay(store: StoryStore, operation: str, record_id: str, payload: dict[str, Any]) -> str:
    exists = _mainline_record_exists(store, "promise_items", "item_id", record_id)
    if operation == "DELETE":
        if not exists:
            raise KeyError("mainline promise item not found")
        store.connection.execute(
            "UPDATE promise_items SET state='ABANDONED' WHERE item_id=? AND branch_id='mainline'",
            (record_id,),
        )
        return record_id
    if operation == "ADD" and exists:
        raise ValueError("ADD promise overlay collides with existing mainline item")
    if operation == "REPLACE" and not exists:
        raise KeyError("mainline promise item not found")
    title = str(payload.get("title", "")).strip()
    if not title:
        raise ValueError("promise overlay requires title")
    evidence_claim = str(payload.get("evidence_claim_id")) if payload.get("evidence_claim_id") else None
    if (
        evidence_claim
        and next(iter(store.rows("SELECT 1 FROM claims WHERE claim_id=?", (evidence_claim,))), None) is None
    ):
        raise KeyError("promise evidence claim not found")
    upsert_promise_item(
        store,
        item_type=str(payload.get("item_type", "READER_QUESTION")),
        title=title,
        state=str(payload.get("state", "OPEN")),
        opened_at=str(payload.get("opened_at")) if payload.get("opened_at") else None,
        resolved_at=str(payload.get("resolved_at")) if payload.get("resolved_at") else None,
        branch_id="mainline",
        evidence_claim_id=evidence_claim,
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
        item_id=record_id,
    )
    return record_id


def _apply_scene_contract_overlay(
    store: StoryStore,
    operation: str,
    record_id: str,
    payload: dict[str, Any],
) -> str:
    story_unit_id = str(payload.get("story_unit_id") or record_id).strip()
    if next(iter(store.rows("SELECT 1 FROM story_units WHERE story_unit_id=?", (story_unit_id,))), None) is None:
        raise KeyError("scene-contract story unit not found")
    if operation == "DELETE":
        cursor = store.connection.execute("DELETE FROM scene_contracts WHERE story_unit_id=?", (story_unit_id,))
        if cursor.rowcount == 0:
            raise KeyError("mainline scene contract not found")
        return story_unit_id
    contract = payload.get("contract")
    if not isinstance(contract, dict):
        raise ValueError("scene-contract overlay requires a contract object")
    status = _branch_status(payload.get("status"), AuthorityStatus.AUTHOR_LOCKED)
    if status not in {
        AuthorityStatus.AUTHOR_LOCKED,
        AuthorityStatus.CONFIRMED_CANON,
        AuthorityStatus.PROVISIONAL,
    }:
        raise ValueError("unsupported scene-contract authority status")
    exists = (
        next(iter(store.rows("SELECT 1 FROM scene_contracts WHERE story_unit_id=?", (story_unit_id,))), None)
        is not None
    )
    if operation == "ADD" and exists:
        raise ValueError("ADD scene-contract overlay collides with an existing contract")
    if operation == "REPLACE" and not exists:
        raise KeyError("mainline scene contract not found")
    store.connection.execute(
        """
        INSERT INTO scene_contracts(story_unit_id,contract_json,status,updated_by)
        VALUES(?,?,?,'writer')
        ON CONFLICT(story_unit_id) DO UPDATE SET
            contract_json=excluded.contract_json,status=excluded.status,updated_by='writer'
        """,
        (story_unit_id, StoryStore.encode_json(contract), status.value),
    )
    return story_unit_id


def _apply_branch_overlay(
    project: StoryProject,
    store: StoryStore,
    branch_id: str,
    item: dict[str, Any],
) -> dict[str, str]:
    kind = str(item.get("record_kind", "")).strip()
    operation = str(item.get("operation", "")).strip().upper()
    record_id = str(item.get("record_id", "")).strip()
    payload = _bounded_writer_value(item.get("payload", {}))
    if kind not in _BRANCH_APPLY_KINDS:
        raise ValueError(f"branch merge record kind is not yet safely applicable: {kind}")
    if operation not in {"ADD", "REPLACE", "DELETE"} or not record_id or not isinstance(payload, dict):
        raise ValueError("invalid branch overlay")
    if kind == "claim":
        target = _apply_claim_overlay(project, store, branch_id, operation, record_id, payload)
    elif kind == "world_state":
        target = _apply_world_state_overlay(store, operation, record_id, payload)
    elif kind == "thread":
        target = _apply_thread_overlay(store, operation, record_id, payload)
    elif kind == "promise_item":
        target = _apply_promise_overlay(store, operation, record_id, payload)
    else:
        target = _apply_scene_contract_overlay(store, operation, record_id, payload)
    return {
        "overlay_id": str(item["overlay_id"]),
        "target_record_kind": kind,
        "target_record_id": target,
    }


def apply_story_branch_merge(
    root: str | Path,
    branch_id: str,
    overlay_ids: list[str],
    *,
    expected_parent_revision: str,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    """Apply reviewed branch overlays to mainline atomically and durably."""

    if not writer_confirmed:
        raise PermissionError("applying a branch merge requires explicit writer confirmation")
    if not expected_parent_revision:
        raise ValueError("branch merge requires expected_parent_revision from the reviewed preparation")
    with story_runtime(root, initialize=True) as (project, store):
        prepared = prepare_writer_branch_merge(store, branch_id, [str(item) for item in overlay_ids])
        if prepared["expected_parent_revision"] != expected_parent_revision:
            raise ValueError("mainline changed after branch merge review; prepare the merge again")
        applied: list[dict[str, str]] = []
        with store.connection:
            for operation in prepared["operations"]:
                applied.append(_apply_branch_overlay(project, store, branch_id, operation))
            result = record_completed_branch_merge(
                store,
                branch_id,
                applied,
                writer_confirmed=True,
                expected_parent_revision=expected_parent_revision,
            )
            # Persist while the SQLite transaction is still open. If the atomic
            # state-file write fails, the DB transaction rolls back too.
            persist_writer_state(project, store)
        result["applied"] = applied
        result["writer_owned"] = True
        result["persisted"] = True
        return result


def record_story_branch_merge(
    root: str | Path,
    branch_id: str,
    applied: list[dict[str, str]],
    *,
    expected_parent_revision: str,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("recording a branch merge requires explicit writer confirmation")
    with story_runtime(root, initialize=True) as (project, store):
        return complete_writer_branch_merge(
            project,
            store,
            branch_id,
            [dict(item) for item in applied],
            expected_parent_revision=expected_parent_revision,
        )


def set_scene_contract(
    root: str | Path,
    story_unit_id: str,
    contract: dict[str, Any],
    *,
    status: str = "AUTHOR_LOCKED",
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("saving a scene contract requires explicit writer confirmation")
    with story_runtime(root, initialize=True) as (project, store):
        return put_scene_contract(
            project,
            store,
            story_unit_id=story_unit_id,
            contract=dict(contract),
            status=AuthorityStatus(status),
        )


def set_author_decision(
    root: str | Path,
    *,
    title: str,
    decision: str,
    rationale: str = "",
    revisit_trigger: str = "",
    story_unit_id: str | None = None,
    branch_id: str = "mainline",
    author_decision_id: str | None = None,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("saving an author decision requires explicit writer confirmation")
    with story_runtime(root, initialize=True) as (project, store):
        return put_author_decision(
            project,
            store,
            title=title.strip()[:500],
            decision=decision.strip()[:5000],
            rationale=rationale.strip()[:5000],
            revisit_trigger=revisit_trigger.strip()[:2000],
            story_unit_id=story_unit_id,
            branch_id=branch_id,
            author_decision_id=author_decision_id,
        )


def set_writer_preference(
    root: str | Path,
    *,
    preference_id: str,
    scope_kind: str,
    scope_id: str,
    statement: str,
    status: str,
    confidence: float,
    evidence: list[dict[str, Any]] | None = None,
    writer_confirmed: bool = False,
) -> dict[str, Any]:
    if not writer_confirmed:
        raise PermissionError("saving a writer preference requires explicit writer confirmation")
    with story_runtime(root, initialize=True) as (project, store):
        return put_writer_preference(
            project,
            store,
            preference_id=preference_id.strip()[:240],
            scope_kind=scope_kind.strip()[:120],
            scope_id=scope_id.strip()[:240],
            statement=statement.strip()[:5000],
            status=status.strip()[:120],
            confidence=confidence,
            evidence=list(evidence or []),
        )
