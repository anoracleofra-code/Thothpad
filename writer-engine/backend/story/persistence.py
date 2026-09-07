from __future__ import annotations

from typing import Any

from backend.story.project import StoryProject
from backend.story.store import StoryStore

_COMPILER_RELATION = "compiled_from_explicit_field"


def _rows(store: StoryStore, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in store.rows(sql, parameters)]


def _story_unit_locator(store: StoryStore, story_unit_id: str | None) -> dict[str, Any] | None:
    if not story_unit_id:
        return None
    row = next(
        iter(
            store.rows(
                """
                SELECT u.story_unit_id,u.anchor_signature,u.kind,u.display_title,
                       s.relative_path AS source_path
                FROM story_units u LEFT JOIN sources s ON s.source_id=u.source_id
                WHERE u.story_unit_id=?
                """,
                (story_unit_id,),
            )
        ),
        None,
    )
    if row is None:
        return None
    return {
        "story_unit_id": row["story_unit_id"],
        "anchor_signature": row["anchor_signature"],
        "kind": row["kind"],
        "display_title": row["display_title"],
        "source_path": row["source_path"],
    }


def _attach_locator(store: StoryStore, item: dict[str, Any], key: str = "story_unit_id") -> None:
    locator = _story_unit_locator(store, str(item.get(key) or "") or None)
    if locator is not None:
        item[f"{key}_locator"] = locator


def _source_locator(store: StoryStore, source_id: str | None) -> dict[str, Any] | None:
    if not source_id:
        return None
    row = next(
        iter(
            store.rows(
                """
                SELECT source_id,project_id,relative_path,display_name,format,content_hash,
                       authority_default,tombstoned
                FROM sources WHERE source_id=?
                """,
                (source_id,),
            )
        ),
        None,
    )
    if row is None:
        return None
    return dict(row)


def _compiler_derivative_ids(store: StoryStore, dependent_kind: str) -> set[str]:
    return {
        str(row["dependent_id"])
        for row in store.rows(
            """
            SELECT dependent_id FROM dependencies
            WHERE dependent_kind=? AND relation=?
            """,
            (dependent_kind, _COMPILER_RELATION),
        )
    }


def _without_compiler_derivatives(
    store: StoryStore,
    rows: list[dict[str, Any]],
    *,
    dependent_kind: str,
    id_key: str,
) -> list[dict[str, Any]]:
    compiler_ids = _compiler_derivative_ids(store, dependent_kind)
    return [item for item in rows if str(item.get(id_key, "")) not in compiler_ids]


def _writer_claims(store: StoryStore) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in store.rows("SELECT * FROM claims WHERE created_by='writer' ORDER BY created_at,claim_id"):
        item = dict(row)
        item["literal_value"] = StoryStore.decode_json(item.pop("literal_value_json", None), None)
        item["qualifiers"] = StoryStore.decode_json(item.pop("qualifiers_json", None), {})
        evidence: list[dict[str, Any]] = []
        for evidence_row in store.rows(
            "SELECT * FROM claim_evidence WHERE claim_id=? ORDER BY evidence_id",
            (item["claim_id"],),
        ):
            record = dict(evidence_row)
            record.pop("evidence_id", None)
            locator = _source_locator(store, record.get("source_id"))
            if locator is not None:
                record["source_locator"] = locator
            _attach_locator(store, record)
            evidence.append(record)
        item["evidence"] = evidence
        result.append(item)
    return result


def _serialize_timeline(store: StoryStore) -> list[dict[str, Any]]:
    rows = _rows(store, "SELECT * FROM timeline_events ORDER BY rowid")
    for item in rows:
        item["metadata"] = StoryStore.decode_json(item.pop("metadata_json", None), {})
        _attach_locator(store, item)
    return rows


def _serialize_world_state(store: StoryStore) -> list[dict[str, Any]]:
    rows = _without_compiler_derivatives(
        store,
        _rows(store, "SELECT * FROM world_state ORDER BY rowid"),
        dependent_kind="world_state",
        id_key="state_id",
    )
    for item in rows:
        item["value"] = StoryStore.decode_json(item.pop("value_json", None), None)
    return rows


def _serialize_knowledge(store: StoryStore) -> list[dict[str, Any]]:
    rows = _without_compiler_derivatives(
        store,
        _rows(store, "SELECT * FROM knowledge_state ORDER BY rowid"),
        dependent_kind="knowledge_state",
        id_key="knowledge_id",
    )
    for item in rows:
        _attach_locator(store, item, "acquired_at")
    return rows


def _serialize_reader(store: StoryStore) -> list[dict[str, Any]]:
    rows = _rows(store, "SELECT * FROM reader_state ORDER BY rowid")
    for item in rows:
        _attach_locator(store, item)
    return rows


def _serialize_relationships(store: StoryStore) -> list[dict[str, Any]]:
    rows = _rows(store, "SELECT * FROM relationships ORDER BY rowid")
    for item in rows:
        item["state"] = StoryStore.decode_json(item.pop("state_json", None), {})
    return rows


def _serialize_threads(store: StoryStore) -> list[dict[str, Any]]:
    rows = _without_compiler_derivatives(
        store,
        _rows(store, "SELECT * FROM threads ORDER BY rowid"),
        dependent_kind="thread",
        id_key="thread_id",
    )
    for item in rows:
        item["metadata"] = StoryStore.decode_json(item.pop("metadata_json", None), {})
    return rows


def _serialize_promises(store: StoryStore) -> list[dict[str, Any]]:
    rows = _without_compiler_derivatives(
        store,
        _rows(store, "SELECT * FROM promise_items ORDER BY rowid"),
        dependent_kind="promise_item",
        id_key="item_id",
    )
    for item in rows:
        item["metadata"] = StoryStore.decode_json(item.pop("metadata_json", None), {})
    return rows


def _serialize_decisions(store: StoryStore) -> list[dict[str, Any]]:
    rows = _rows(store, "SELECT * FROM decisions ORDER BY rowid")
    for item in rows:
        item["metadata"] = StoryStore.decode_json(item.pop("metadata_json", None), {})
        _attach_locator(store, item)
    return rows


def _serialize_opposition(store: StoryStore) -> list[dict[str, Any]]:
    rows = _rows(store, "SELECT * FROM opposition_state ORDER BY rowid")
    for item in rows:
        item["metadata"] = StoryStore.decode_json(item.pop("metadata_json", None), {})
        _attach_locator(store, item)
    return rows


def persist_writer_state(project: StoryProject, store: StoryStore) -> dict[str, int]:
    """Persist writer-owned Story Engine state outside the disposable cache."""

    writer_claims = _writer_claims(store)
    world_state = _serialize_world_state(store)
    knowledge_state = _serialize_knowledge(store)
    reader_state = _serialize_reader(store)
    relationships = _serialize_relationships(store)
    timeline_events = _serialize_timeline(store)
    threads = _serialize_threads(store)
    promise_items = _serialize_promises(store)
    decisions = _serialize_decisions(store)
    causal_edges = _rows(store, "SELECT * FROM causal_edges ORDER BY rowid")
    opposition_state = _serialize_opposition(store)

    referenced_entity_ids: set[str] = set()
    for claim in writer_claims:
        for key in ("subject_entity_id", "object_entity_id"):
            if claim.get(key):
                referenced_entity_ids.add(str(claim[key]))
    for item in world_state:
        if item.get("entity_id"):
            referenced_entity_ids.add(str(item["entity_id"]))
    for item in knowledge_state:
        if item.get("character_id"):
            referenced_entity_ids.add(str(item["character_id"]))
    for item in relationships:
        referenced_entity_ids.update({str(item["entity_a"]), str(item["entity_b"])})
    for item in decisions:
        if item.get("agent_entity_id"):
            referenced_entity_ids.add(str(item["agent_entity_id"]))
    for item in opposition_state:
        if item.get("source_entity_id"):
            referenced_entity_ids.add(str(item["source_entity_id"]))
    referenced_entity_ids.update(
        str(row["entity_id"])
        for row in store.rows("SELECT DISTINCT entity_id FROM entity_aliases WHERE user_confirmed=1")
    )
    referenced_entity_ids.update(
        str(row["entity_id"])
        for row in store.rows("SELECT entity_id FROM entities WHERE status<>'PROVISIONAL'")
    )

    writer_entities: list[dict[str, Any]] = []
    writer_aliases: list[dict[str, Any]] = []
    for entity_id in sorted(referenced_entity_ids):
        entity = store.entity(entity_id)
        if entity is None:
            continue
        record = dict(entity)
        record["metadata"] = StoryStore.decode_json(record.pop("metadata_json", None), {})
        writer_entities.append(record)
        writer_aliases.extend(
            dict(row)
            for row in store.rows(
                "SELECT entity_id,alias,confidence,user_confirmed FROM entity_aliases WHERE entity_id=? ORDER BY alias",
                (entity_id,),
            )
        )

    writer_claim_ids = {str(item["claim_id"]) for item in writer_claims}
    writer_claim_relations = [
        dict(row)
        for row in store.rows("SELECT * FROM claim_relations ORDER BY left_claim_id,relation,right_claim_id")
        if str(row["left_claim_id"]) in writer_claim_ids or str(row["right_claim_id"]) in writer_claim_ids
    ]

    branches = _rows(
        store,
        "SELECT * FROM branches WHERE branch_id<>'mainline' ORDER BY rowid",
    )
    for item in branches:
        item["assumptions"] = StoryStore.decode_json(item.pop("assumptions_json", None), [])
        _attach_locator(store, item, "fork_story_unit")

    overlays = _rows(store, "SELECT * FROM branch_overlays ORDER BY rowid")
    for item in overlays:
        item["payload"] = StoryStore.decode_json(item.pop("payload_json", None), {})

    merge_history = _rows(
        store,
        """
        SELECT merge_id,branch_id,overlay_id,target_record_kind,target_record_id,completed_at
        FROM branch_merge_history ORDER BY completed_at,branch_id,overlay_id
        """,
    )

    contracts = _rows(store, "SELECT * FROM scene_contracts ORDER BY story_unit_id")
    for item in contracts:
        item["contract"] = StoryStore.decode_json(item.pop("contract_json", None), {})
        _attach_locator(store, item)

    author_decisions = _rows(store, "SELECT * FROM author_decisions ORDER BY rowid")
    for item in author_decisions:
        _attach_locator(store, item)
    preferences = _rows(store, "SELECT * FROM writer_preferences ORDER BY rowid")
    for item in preferences:
        item["evidence"] = StoryStore.decode_json(item.pop("evidence_json", None), [])
    story_lenses = _rows(store, "SELECT * FROM story_lenses WHERE created_by='writer' ORDER BY name,lens_id")

    project.state.update(
        {
            "writer_entities": writer_entities,
            "writer_entity_aliases": writer_aliases,
            "writer_claims": writer_claims,
            "writer_claim_relations": writer_claim_relations,
            "timeline_events": timeline_events,
            "world_state": world_state,
            "knowledge_state": knowledge_state,
            "reader_state": reader_state,
            "relationships": relationships,
            "threads": threads,
            "promise_items": promise_items,
            "decisions": decisions,
            "causal_edges": causal_edges,
            "opposition_state": opposition_state,
            "branches": branches,
            "branch_overlays": overlays,
            "branch_merge_history": merge_history,
            "scene_contracts": contracts,
            "author_decisions": author_decisions,
            "writer_preferences": preferences,
            "story_lenses": story_lenses,
        }
    )
    project.save_state()
    return {
        "writer_entities": len(writer_entities),
        "writer_entity_aliases": len(writer_aliases),
        "writer_claims": len(writer_claims),
        "writer_claim_relations": len(writer_claim_relations),
        "timeline_events": len(timeline_events),
        "world_state": len(world_state),
        "knowledge_state": len(knowledge_state),
        "reader_state": len(reader_state),
        "relationships": len(relationships),
        "threads": len(threads),
        "promise_items": len(promise_items),
        "decisions": len(decisions),
        "causal_edges": len(causal_edges),
        "opposition_state": len(opposition_state),
        "branches": len(branches),
        "branch_overlays": len(overlays),
        "branch_merge_history": len(merge_history),
        "scene_contracts": len(contracts),
        "author_decisions": len(author_decisions),
        "writer_preferences": len(preferences),
        "story_lenses": len(story_lenses),
    }


def _state_list(project: StoryProject, key: str) -> list[dict[str, Any]]:
    value = project.state.get(key, [])
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _story_unit_exists(store: StoryStore, story_unit_id: str | None) -> bool:
    if not story_unit_id:
        return False
    return next(
        iter(store.rows("SELECT 1 FROM story_units WHERE story_unit_id=?", (story_unit_id,))),
        None,
    ) is not None


def _resolve_story_unit(
    store: StoryStore,
    story_unit_id: str | None,
    locator: Any,
) -> str | None:
    if story_unit_id and _story_unit_exists(store, story_unit_id):
        return story_unit_id
    if not isinstance(locator, dict):
        return None
    anchor = str(locator.get("anchor_signature", "")).strip()
    source_path = str(locator.get("source_path", "")).strip()
    if anchor:
        rows = list(
            store.rows(
                """
                SELECT u.story_unit_id,s.relative_path
                FROM story_units u LEFT JOIN sources s ON s.source_id=u.source_id
                WHERE u.anchor_signature=? ORDER BY u.story_unit_id
                """,
                (anchor,),
            )
        )
        if source_path:
            exact = [row for row in rows if str(row["relative_path"] or "").casefold() == source_path.casefold()]
            if len(exact) == 1:
                return str(exact[0]["story_unit_id"])
        if len(rows) == 1:
            return str(rows[0]["story_unit_id"])
    return None


def _resolve_source(store: StoryStore, locator: Any, source_id: str | None) -> tuple[str | None, bool]:
    """Resolve durable evidence to the current project and report whether it is stale."""

    if isinstance(locator, dict):
        expected_hash = str(locator.get("content_hash", ""))
        expected_path = str(locator.get("relative_path", ""))
    else:
        expected_hash = ""
        expected_path = ""

    row = None
    if source_id:
        row = next(iter(store.rows("SELECT * FROM sources WHERE source_id=?", (source_id,))), None)
    if row is None and expected_path:
        row = next(
            iter(store.rows("SELECT * FROM sources WHERE relative_path=? AND tombstoned=0", (expected_path,))),
            None,
        )
    if row is None and expected_hash:
        candidates = list(
            store.rows(
                "SELECT * FROM sources WHERE content_hash=? AND tombstoned=0 ORDER BY relative_path",
                (expected_hash,),
            )
        )
        if len(candidates) == 1:
            row = candidates[0]
    if row is not None:
        stale = bool(row["tombstoned"]) or (bool(expected_hash) and str(row["content_hash"]) != expected_hash)
        return str(row["source_id"]), stale

    if not isinstance(locator, dict) or not source_id or not expected_path:
        return None, True

    # Preserve historical provenance even when the original source has vanished.
    # The tombstone is data only: it is never indexed or treated as current truth.
    try:
        store.connection.execute(
            """
            INSERT INTO sources(
                source_id,project_id,relative_path,display_name,format,content_hash,size,mtime_ns,
                readability_status,authority_default,branch_scope,story_scope,metadata_json,
                adapter_origin,tombstoned
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
            """,
            (
                source_id,
                str(locator.get("project_id", "historical")),
                expected_path,
                str(locator.get("display_name") or expected_path.rsplit("/", 1)[-1]),
                str(locator.get("format", "unknown")),
                expected_hash,
                0,
                0,
                "historical",
                str(locator.get("authority_default", "PROVISIONAL")),
                "mainline",
                "",
                StoryStore.encode_json({"durable_provenance_tombstone": True}),
                "durable-provenance",
            ),
        )
    except Exception:
        return None, True
    return source_id, True


def _entity_exists(store: StoryStore, entity_id: str | None) -> bool:
    return bool(entity_id) and store.entity(str(entity_id)) is not None


def _claim_exists(store: StoryStore, claim_id: str | None) -> bool:
    if not claim_id:
        return False
    return next(iter(store.rows("SELECT 1 FROM claims WHERE claim_id=?", (claim_id,))), None) is not None


def _branch_exists(store: StoryStore, branch_id: str) -> bool:
    return next(iter(store.rows("SELECT 1 FROM branches WHERE branch_id=?", (branch_id,))), None) is not None


def _cache_has_writer_state(store: StoryStore) -> bool:
    checks = (
        "SELECT 1 FROM claims WHERE created_by='writer' LIMIT 1",
        "SELECT 1 FROM entity_aliases WHERE user_confirmed=1 LIMIT 1",
        "SELECT 1 FROM timeline_events LIMIT 1",
        "SELECT 1 FROM reader_state LIMIT 1",
        "SELECT 1 FROM relationships LIMIT 1",
        "SELECT 1 FROM decisions LIMIT 1",
        "SELECT 1 FROM causal_edges LIMIT 1",
        "SELECT 1 FROM opposition_state LIMIT 1",
        "SELECT 1 FROM branches WHERE branch_id<>'mainline' LIMIT 1",
        "SELECT 1 FROM scene_contracts LIMIT 1",
        "SELECT 1 FROM author_decisions LIMIT 1",
        "SELECT 1 FROM writer_preferences LIMIT 1",
        "SELECT 1 FROM story_lenses WHERE created_by='writer' LIMIT 1",
    )
    return any(next(iter(store.rows(sql)), None) is not None for sql in checks)


def hydrate_writer_state(project: StoryProject, store: StoryStore) -> dict[str, Any]:
    """Rebuild writer-owned cache tables from durable project state.

    Stable story-unit references that cannot currently be resolved remain in the
    durable JSON and are reported as orphans rather than being rewritten or lost.
    """

    if not project.state_was_existing and _cache_has_writer_state(store):
        # First upgraded run: preserve any pre-durable writer state before the
        # compiled projection is replaced.
        persist_writer_state(project, store)

    store.connection.execute("DELETE FROM branch_merge_history")
    store.connection.execute("DELETE FROM branch_overlays")
    store.connection.execute("DELETE FROM branches WHERE branch_id<>'mainline'")
    store.connection.execute("DELETE FROM scene_contracts")
    store.connection.execute("DELETE FROM author_decisions")
    store.connection.execute("DELETE FROM writer_preferences")
    store.connection.execute("DELETE FROM story_lenses")

    orphans: list[dict[str, Any]] = []

    for item in _state_list(project, "writer_entities"):
        entity_id = str(item.get("entity_id", "")).strip()
        name = str(item.get("canonical_name", "")).strip()
        entity_type = str(item.get("entity_type", "custom")).strip() or "custom"
        if not entity_id or not name:
            orphans.append({"kind": "writer_entity", "record_id": entity_id})
            continue
        store.upsert_entity(
            entity_id,
            name,
            entity_type,
            description=str(item.get("description", "")),
            branch_scope=str(item.get("branch_scope", "mainline")),
            confidence=float(item.get("confidence", 1.0)),
            status=str(item.get("status", "PROVISIONAL")),
            metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        )

    for item in _state_list(project, "writer_entity_aliases"):
        entity_id = str(item.get("entity_id", "")).strip()
        alias = str(item.get("alias", "")).strip()
        if not entity_id or not alias or not _entity_exists(store, entity_id):
            orphans.append({"kind": "writer_entity_alias", "record_id": alias, "entity_id": entity_id})
            continue
        store.add_entity_alias(
            entity_id,
            alias,
            confidence=float(item.get("confidence", 1.0)),
            confirmed=bool(item.get("user_confirmed", False)),
        )

    writer_claim_items = _state_list(project, "writer_claims")
    for item in writer_claim_items:
        claim_id = str(item.get("claim_id", "")).strip()
        predicate = str(item.get("predicate", "")).strip()
        if not claim_id or not predicate:
            orphans.append({"kind": "writer_claim", "record_id": claim_id})
            continue
        subject_id = str(item.get("subject_entity_id") or "") or None
        object_id = str(item.get("object_entity_id") or "") or None
        if subject_id and not _entity_exists(store, subject_id):
            orphans.append({"kind": "claim_subject", "record_id": claim_id, "entity_id": subject_id})
            subject_id = None
        if object_id and not _entity_exists(store, object_id):
            orphans.append({"kind": "claim_object", "record_id": claim_id, "entity_id": object_id})
            object_id = None
        store.connection.execute(
            """
            INSERT INTO claims(
                claim_id,project_id,subject_entity_id,predicate,object_entity_id,literal_value_json,
                qualifiers_json,status,branch_id,scope_id,valid_from,valid_until,confidence,created_by,
                supersedes_claim_id,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'writer',NULL,COALESCE(?,CURRENT_TIMESTAMP))
            ON CONFLICT(claim_id) DO UPDATE SET
                project_id=excluded.project_id,subject_entity_id=excluded.subject_entity_id,
                predicate=excluded.predicate,object_entity_id=excluded.object_entity_id,
                literal_value_json=excluded.literal_value_json,qualifiers_json=excluded.qualifiers_json,
                status=excluded.status,branch_id=excluded.branch_id,scope_id=excluded.scope_id,
                valid_from=excluded.valid_from,valid_until=excluded.valid_until,
                confidence=excluded.confidence,created_by='writer'
            """,
            (
                claim_id,
                project.project_id,
                subject_id,
                predicate,
                object_id,
                StoryStore.encode_json(item.get("literal_value")),
                StoryStore.encode_json(item.get("qualifiers", {})),
                str(item.get("status", "PROVISIONAL")),
                str(item.get("branch_id", "mainline")),
                item.get("scope_id"),
                item.get("valid_from"),
                item.get("valid_until"),
                float(item.get("confidence", 1.0)),
                item.get("created_at"),
            ),
        )

    for item in writer_claim_items:
        claim_id = str(item.get("claim_id", "")).strip()
        if not claim_id or not _claim_exists(store, claim_id):
            continue
        supersedes = str(item.get("supersedes_claim_id") or "") or None
        if supersedes and not _claim_exists(store, supersedes):
            orphans.append({"kind": "claim_supersedes", "record_id": claim_id, "claim_id": supersedes})
            supersedes = None
        store.connection.execute(
            "UPDATE claims SET supersedes_claim_id=? WHERE claim_id=?",
            (supersedes, claim_id),
        )
        for evidence in item.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            durable_source_id = str(evidence.get("source_id") or "") or None
            source_id, resolved_stale = _resolve_source(
                store,
                evidence.get("source_locator"),
                durable_source_id,
            )
            if not source_id:
                orphans.append(
                    {"kind": "claim_evidence_source", "record_id": claim_id, "source_id": durable_source_id}
                )
                continue
            original_story_unit = str(evidence.get("story_unit_id") or "") or None
            story_unit_id = _resolve_story_unit(
                store,
                original_story_unit,
                evidence.get("story_unit_id_locator"),
            )
            if original_story_unit and not story_unit_id:
                orphans.append(
                    {
                        "kind": "claim_evidence_story_unit",
                        "record_id": claim_id,
                        "story_unit_id": original_story_unit,
                    }
                )
            start = int(evidence.get("start_offset", 0))
            end = int(evidence.get("end_offset", start))
            existing_evidence = next(
                iter(
                    store.rows(
                        """
                        SELECT evidence_id,stale FROM claim_evidence
                        WHERE claim_id=? AND source_id=? AND start_offset=? AND end_offset=?
                        ORDER BY evidence_id LIMIT 1
                        """,
                        (claim_id, source_id, start, end),
                    )
                ),
                None,
            )
            durable_stale = int(bool(evidence.get("stale", False)) or resolved_stale)
            if existing_evidence is None:
                store.add_claim_evidence(
                    {
                        "claim_id": claim_id,
                        "source_id": source_id,
                        "story_unit_id": story_unit_id,
                        "start_offset": start,
                        "end_offset": end,
                        "quote_hash": str(evidence.get("quote_hash", "")),
                        "source_hash": str(evidence.get("source_hash", "")),
                        "evidence_type": str(evidence.get("evidence_type", "source_span")),
                        "weight": float(evidence.get("weight", 1.0)),
                        "stale": durable_stale,
                    }
                )
            else:
                store.connection.execute(
                    """
                    UPDATE claim_evidence SET
                        story_unit_id=COALESCE(story_unit_id,?),
                        weight=max(weight,?),
                        stale=min(stale,?)
                    WHERE evidence_id=?
                    """,
                    (
                        story_unit_id,
                        float(evidence.get("weight", 1.0)),
                        durable_stale,
                        existing_evidence["evidence_id"],
                    ),
                )

    for item in _state_list(project, "writer_claim_relations"):
        left = str(item.get("left_claim_id", "")).strip()
        right = str(item.get("right_claim_id", "")).strip()
        relation = str(item.get("relation", "")).strip()
        if not relation or not _claim_exists(store, left) or not _claim_exists(store, right):
            orphans.append({"kind": "claim_relation", "record_id": f"{left}:{relation}:{right}"})
            continue
        store.connection.execute(
            "INSERT OR IGNORE INTO claim_relations(left_claim_id,relation,right_claim_id) VALUES(?,?,?)",
            (left, relation, right),
        )

    pending = _state_list(project, "branches")
    inserted: set[str] = {"mainline"}
    while pending:
        progress = False
        remaining: list[dict[str, Any]] = []
        for item in pending:
            branch_id = str(item.get("branch_id", "")).strip()
            parent = str(item.get("parent_branch") or "mainline").strip()
            if not branch_id or branch_id == "mainline":
                continue
            if parent not in inserted:
                remaining.append(item)
                continue
            original_fork_story_unit = str(item.get("fork_story_unit") or "") or None
            fork_story_unit = _resolve_story_unit(
                store,
                original_fork_story_unit,
                item.get("fork_story_unit_locator"),
            )
            if original_fork_story_unit and fork_story_unit is None:
                orphans.append(
                    {
                        "kind": "branch_fork_story_unit",
                        "record_id": branch_id,
                        "story_unit_id": original_fork_story_unit,
                    }
                )
            store.connection.execute(
                """
                INSERT INTO branches(
                    branch_id,parent_branch,fork_story_unit,base_revision,status,assumptions_json
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    branch_id,
                    parent,
                    fork_story_unit,
                    str(item.get("base_revision", "")),
                    str(item.get("status", "ACTIVE")),
                    StoryStore.encode_json(item.get("assumptions", [])),
                ),
            )
            inserted.add(branch_id)
            progress = True
        if not progress:
            for item in remaining:
                orphans.append(
                    {
                        "kind": "branch_parent",
                        "record_id": str(item.get("branch_id", "")),
                        "parent_branch": str(item.get("parent_branch", "")),
                    }
                )
            break
        pending = remaining

    overlay_ids: set[str] = set()
    for item in _state_list(project, "branch_overlays"):
        overlay_id = str(item.get("overlay_id", "")).strip()
        branch_id = str(item.get("branch_id", "")).strip()
        if not overlay_id or branch_id not in inserted:
            orphans.append({"kind": "branch_overlay", "record_id": overlay_id, "branch_id": branch_id})
            continue
        store.connection.execute(
            """
            INSERT INTO branch_overlays(
                overlay_id,branch_id,record_kind,record_id,operation,payload_json
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                overlay_id,
                branch_id,
                str(item.get("record_kind", "")),
                str(item.get("record_id", "")),
                str(item.get("operation", "ADD")),
                StoryStore.encode_json(item.get("payload", {})),
            ),
        )
        overlay_ids.add(overlay_id)

    for item in _state_list(project, "branch_merge_history"):
        overlay_id = str(item.get("overlay_id", "")).strip()
        branch_id = str(item.get("branch_id", "")).strip()
        if overlay_id not in overlay_ids or branch_id not in inserted:
            orphans.append({"kind": "branch_merge_history", "record_id": overlay_id, "branch_id": branch_id})
            continue
        store.connection.execute(
            """
            INSERT INTO branch_merge_history(
                merge_id,branch_id,overlay_id,target_record_kind,target_record_id,completed_at
            ) VALUES(?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP))
            """,
            (
                str(item.get("merge_id", "")),
                branch_id,
                overlay_id,
                str(item.get("target_record_kind", "")),
                str(item.get("target_record_id", "")),
                item.get("completed_at"),
            ),
        )

    for item in _state_list(project, "timeline_events"):
        event_id = str(item.get("event_id", "")).strip()
        branch_id = str(item.get("branch_id", "mainline"))
        if not event_id or not _branch_exists(store, branch_id):
            orphans.append({"kind": "timeline_event", "record_id": event_id, "branch_id": branch_id})
            continue
        original_story_unit = str(item.get("story_unit_id") or "") or None
        story_unit_id = _resolve_story_unit(store, original_story_unit, item.get("story_unit_id_locator"))
        if original_story_unit and not story_unit_id:
            orphans.append(
                {"kind": "timeline_story_unit", "record_id": event_id, "story_unit_id": original_story_unit}
            )
        store.connection.execute(
            """
            INSERT INTO timeline_events(
                event_id,title,story_unit_id,time_start,time_end,precision,branch_id,status,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id) DO UPDATE SET
                title=excluded.title,story_unit_id=excluded.story_unit_id,time_start=excluded.time_start,
                time_end=excluded.time_end,precision=excluded.precision,branch_id=excluded.branch_id,
                status=excluded.status,metadata_json=excluded.metadata_json
            """,
            (
                event_id,
                str(item.get("title", "")),
                story_unit_id,
                item.get("time_start"),
                item.get("time_end"),
                str(item.get("precision", "OPEN")),
                branch_id,
                str(item.get("status", "PROVISIONAL")),
                StoryStore.encode_json(item.get("metadata", {})),
            ),
        )

    for item in _state_list(project, "world_state"):
        state_id = str(item.get("state_id", "")).strip()
        branch_id = str(item.get("branch_id", "mainline"))
        entity_id = str(item.get("entity_id") or "") or None
        evidence_claim = str(item.get("evidence_claim_id") or "") or None
        if not state_id or not _branch_exists(store, branch_id):
            orphans.append({"kind": "world_state", "record_id": state_id, "branch_id": branch_id})
            continue
        if entity_id and not _entity_exists(store, entity_id):
            orphans.append({"kind": "world_state_entity", "record_id": state_id, "entity_id": entity_id})
            entity_id = None
        if evidence_claim and not _claim_exists(store, evidence_claim):
            orphans.append({"kind": "world_state_evidence", "record_id": state_id, "claim_id": evidence_claim})
            evidence_claim = None
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
                str(item.get("state_type", "")),
                StoryStore.encode_json(item.get("value")),
                item.get("valid_from"),
                item.get("valid_until"),
                branch_id,
                str(item.get("status", "PROVISIONAL")),
                evidence_claim,
            ),
        )

    for item in _state_list(project, "knowledge_state"):
        knowledge_id = str(item.get("knowledge_id", "")).strip()
        character_id = str(item.get("character_id", "")).strip()
        claim_id = str(item.get("claim_id", "")).strip()
        branch_id = str(item.get("branch_id", "mainline"))
        if (
            not knowledge_id
            or not _entity_exists(store, character_id)
            or not _claim_exists(store, claim_id)
            or not _branch_exists(store, branch_id)
        ):
            orphans.append({"kind": "knowledge_state", "record_id": knowledge_id})
            continue
        original_acquired = str(item.get("acquired_at") or "") or None
        acquired_at = _resolve_story_unit(store, original_acquired, item.get("acquired_at_locator"))
        if original_acquired and acquired_at is None:
            orphans.append(
                {"kind": "knowledge_acquired_at", "record_id": knowledge_id, "story_unit_id": original_acquired}
            )
        source_claim = str(item.get("source_claim_id") or "") or None
        if source_claim and not _claim_exists(store, source_claim):
            orphans.append({"kind": "knowledge_source_claim", "record_id": knowledge_id, "claim_id": source_claim})
            source_claim = None
        store.connection.execute(
            """
            INSERT INTO knowledge_state(
                knowledge_id,character_id,claim_id,state,acquired_at,branch_id,confidence,source_claim_id
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(knowledge_id) DO UPDATE SET
                character_id=excluded.character_id,claim_id=excluded.claim_id,state=excluded.state,
                acquired_at=excluded.acquired_at,branch_id=excluded.branch_id,
                confidence=excluded.confidence,source_claim_id=excluded.source_claim_id
            """,
            (
                knowledge_id,
                character_id,
                claim_id,
                str(item.get("state", "KNOWS")),
                acquired_at,
                branch_id,
                float(item.get("confidence", 1.0)),
                source_claim,
            ),
        )

    for item in _state_list(project, "reader_state"):
        record_id = str(item.get("reader_state_id", "")).strip()
        branch_id = str(item.get("branch_id", "mainline"))
        claim_id = str(item.get("claim_id") or "") or None
        if not record_id or not _branch_exists(store, branch_id):
            orphans.append({"kind": "reader_state", "record_id": record_id})
            continue
        if claim_id and not _claim_exists(store, claim_id):
            orphans.append({"kind": "reader_state_claim", "record_id": record_id, "claim_id": claim_id})
            claim_id = None
        original_story_unit = str(item.get("story_unit_id") or "") or None
        story_unit_id = _resolve_story_unit(store, original_story_unit, item.get("story_unit_id_locator"))
        if original_story_unit and story_unit_id is None:
            orphans.append(
                {"kind": "reader_story_unit", "record_id": record_id, "story_unit_id": original_story_unit}
            )
        store.connection.execute(
            """
            INSERT INTO reader_state(reader_state_id,claim_id,state,story_unit_id,branch_id,confidence)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(reader_state_id) DO UPDATE SET
                claim_id=excluded.claim_id,state=excluded.state,story_unit_id=excluded.story_unit_id,
                branch_id=excluded.branch_id,confidence=excluded.confidence
            """,
            (
                record_id,
                claim_id,
                str(item.get("state", "KNOWS")),
                story_unit_id,
                branch_id,
                float(item.get("confidence", 1.0)),
            ),
        )

    for item in _state_list(project, "relationships"):
        relationship_id = str(item.get("relationship_id", "")).strip()
        entity_a = str(item.get("entity_a", "")).strip()
        entity_b = str(item.get("entity_b", "")).strip()
        branch_id = str(item.get("branch_id", "mainline"))
        evidence_claim = str(item.get("evidence_claim_id") or "") or None
        if (
            not relationship_id
            or not _entity_exists(store, entity_a)
            or not _entity_exists(store, entity_b)
            or not _branch_exists(store, branch_id)
        ):
            orphans.append({"kind": "relationship", "record_id": relationship_id})
            continue
        if evidence_claim and not _claim_exists(store, evidence_claim):
            orphans.append({"kind": "relationship_evidence", "record_id": relationship_id})
            evidence_claim = None
        store.connection.execute(
            """
            INSERT INTO relationships(
                relationship_id,entity_a,entity_b,relationship_type,state_json,valid_from,valid_until,
                branch_id,evidence_claim_id
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(relationship_id) DO UPDATE SET
                entity_a=excluded.entity_a,entity_b=excluded.entity_b,
                relationship_type=excluded.relationship_type,state_json=excluded.state_json,
                valid_from=excluded.valid_from,valid_until=excluded.valid_until,
                branch_id=excluded.branch_id,evidence_claim_id=excluded.evidence_claim_id
            """,
            (
                relationship_id,
                entity_a,
                entity_b,
                str(item.get("relationship_type", "custom")),
                StoryStore.encode_json(item.get("state", {})),
                item.get("valid_from"),
                item.get("valid_until"),
                branch_id,
                evidence_claim,
            ),
        )

    for item in _state_list(project, "threads"):
        thread_id = str(item.get("thread_id", "")).strip()
        branch_id = str(item.get("branch_id", "mainline"))
        if not thread_id or not _branch_exists(store, branch_id):
            orphans.append({"kind": "thread", "record_id": thread_id})
            continue
        store.connection.execute(
            """
            INSERT INTO threads(
                thread_id,title,state,opened_at,last_advanced_at,resolved_at,branch_id,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(thread_id) DO UPDATE SET
                title=excluded.title,state=excluded.state,opened_at=excluded.opened_at,
                last_advanced_at=excluded.last_advanced_at,resolved_at=excluded.resolved_at,
                branch_id=excluded.branch_id,metadata_json=excluded.metadata_json
            """,
            (
                thread_id,
                str(item.get("title", "")),
                str(item.get("state", "OPEN")),
                item.get("opened_at"),
                item.get("last_advanced_at"),
                item.get("resolved_at"),
                branch_id,
                StoryStore.encode_json(item.get("metadata", {})),
            ),
        )

    for item in _state_list(project, "promise_items"):
        item_id = str(item.get("item_id", "")).strip()
        branch_id = str(item.get("branch_id", "mainline"))
        evidence_claim = str(item.get("evidence_claim_id") or "") or None
        if not item_id or not _branch_exists(store, branch_id):
            orphans.append({"kind": "promise_item", "record_id": item_id})
            continue
        if evidence_claim and not _claim_exists(store, evidence_claim):
            orphans.append({"kind": "promise_evidence", "record_id": item_id, "claim_id": evidence_claim})
            evidence_claim = None
        store.connection.execute(
            """
            INSERT INTO promise_items(
                item_id,item_type,title,state,opened_at,resolved_at,branch_id,evidence_claim_id,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(item_id) DO UPDATE SET
                item_type=excluded.item_type,title=excluded.title,state=excluded.state,
                opened_at=excluded.opened_at,resolved_at=excluded.resolved_at,
                branch_id=excluded.branch_id,evidence_claim_id=excluded.evidence_claim_id,
                metadata_json=excluded.metadata_json
            """,
            (
                item_id,
                str(item.get("item_type", "READER_QUESTION")),
                str(item.get("title", "")),
                str(item.get("state", "OPEN")),
                item.get("opened_at"),
                item.get("resolved_at"),
                branch_id,
                evidence_claim,
                StoryStore.encode_json(item.get("metadata", {})),
            ),
        )

    for item in _state_list(project, "decisions"):
        decision_id = str(item.get("decision_id", "")).strip()
        branch_id = str(item.get("branch_id", "mainline"))
        agent_id = str(item.get("agent_entity_id") or "") or None
        if not decision_id or not _branch_exists(store, branch_id):
            orphans.append({"kind": "decision", "record_id": decision_id})
            continue
        if agent_id and not _entity_exists(store, agent_id):
            orphans.append({"kind": "decision_agent", "record_id": decision_id, "entity_id": agent_id})
            agent_id = None
        original_story_unit = str(item.get("story_unit_id") or "") or None
        story_unit_id = _resolve_story_unit(store, original_story_unit, item.get("story_unit_id_locator"))
        if original_story_unit and story_unit_id is None:
            orphans.append(
                {"kind": "decision_story_unit", "record_id": decision_id, "story_unit_id": original_story_unit}
            )
        store.connection.execute(
            """
            INSERT INTO decisions(
                decision_id,agent_entity_id,story_unit_id,description,branch_id,status,metadata_json
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(decision_id) DO UPDATE SET
                agent_entity_id=excluded.agent_entity_id,story_unit_id=excluded.story_unit_id,
                description=excluded.description,branch_id=excluded.branch_id,
                status=excluded.status,metadata_json=excluded.metadata_json
            """,
            (
                decision_id,
                agent_id,
                story_unit_id,
                str(item.get("description", "")),
                branch_id,
                str(item.get("status", "PROVISIONAL")),
                StoryStore.encode_json(item.get("metadata", {})),
            ),
        )

    for item in _state_list(project, "causal_edges"):
        edge_id = str(item.get("edge_id", "")).strip()
        branch_id = str(item.get("branch_id", "mainline"))
        evidence_claim = str(item.get("evidence_claim_id") or "") or None
        if not edge_id or not _branch_exists(store, branch_id):
            orphans.append({"kind": "causal_edge", "record_id": edge_id})
            continue
        if evidence_claim and not _claim_exists(store, evidence_claim):
            orphans.append({"kind": "causal_evidence", "record_id": edge_id, "claim_id": evidence_claim})
            evidence_claim = None
        store.connection.execute(
            """
            INSERT INTO causal_edges(
                edge_id,cause_kind,cause_id,effect_kind,effect_id,relation,branch_id,confidence,evidence_claim_id
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(edge_id) DO UPDATE SET
                cause_kind=excluded.cause_kind,cause_id=excluded.cause_id,
                effect_kind=excluded.effect_kind,effect_id=excluded.effect_id,
                relation=excluded.relation,branch_id=excluded.branch_id,
                confidence=excluded.confidence,evidence_claim_id=excluded.evidence_claim_id
            """,
            (
                edge_id,
                str(item.get("cause_kind", "")),
                str(item.get("cause_id", "")),
                str(item.get("effect_kind", "")),
                str(item.get("effect_id", "")),
                str(item.get("relation", "causes")),
                branch_id,
                float(item.get("confidence", 1.0)),
                evidence_claim,
            ),
        )

    for item in _state_list(project, "opposition_state"):
        opposition_id = str(item.get("opposition_id", "")).strip()
        branch_id = str(item.get("branch_id", "mainline"))
        source_entity = str(item.get("source_entity_id") or "") or None
        if not opposition_id or not _branch_exists(store, branch_id):
            orphans.append({"kind": "opposition", "record_id": opposition_id})
            continue
        if source_entity and not _entity_exists(store, source_entity):
            orphans.append(
                {"kind": "opposition_entity", "record_id": opposition_id, "entity_id": source_entity}
            )
            source_entity = None
        original_story_unit = str(item.get("story_unit_id") or "") or None
        story_unit_id = _resolve_story_unit(store, original_story_unit, item.get("story_unit_id_locator"))
        if original_story_unit and story_unit_id is None:
            orphans.append(
                {"kind": "opposition_story_unit", "record_id": opposition_id, "story_unit_id": original_story_unit}
            )
        store.connection.execute(
            """
            INSERT INTO opposition_state(
                opposition_id,objective_id,source_entity_id,description,story_unit_id,branch_id,metadata_json
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(opposition_id) DO UPDATE SET
                objective_id=excluded.objective_id,source_entity_id=excluded.source_entity_id,
                description=excluded.description,story_unit_id=excluded.story_unit_id,
                branch_id=excluded.branch_id,metadata_json=excluded.metadata_json
            """,
            (
                opposition_id,
                str(item.get("objective_id", "")),
                source_entity,
                str(item.get("description", "")),
                story_unit_id,
                branch_id,
                StoryStore.encode_json(item.get("metadata", {})),
            ),
        )

    for item in _state_list(project, "scene_contracts"):
        original_story_unit_id = str(item.get("story_unit_id", "")).strip()
        story_unit_id = _resolve_story_unit(store, original_story_unit_id, item.get("story_unit_id_locator"))
        if not story_unit_id:
            orphans.append({"kind": "scene_contract", "record_id": original_story_unit_id})
            continue
        store.connection.execute(
            """
            INSERT INTO scene_contracts(story_unit_id,contract_json,status,updated_by)
            VALUES(?,?,?,?)
            """,
            (
                story_unit_id,
                StoryStore.encode_json(item.get("contract", {})),
                str(item.get("status", "PROVISIONAL")),
                str(item.get("updated_by", "writer")),
            ),
        )

    for item in _state_list(project, "author_decisions"):
        original_story_unit_id = str(item.get("story_unit_id") or "") or None
        story_unit_id = _resolve_story_unit(store, original_story_unit_id, item.get("story_unit_id_locator"))
        if original_story_unit_id and story_unit_id is None:
            orphans.append(
                {
                    "kind": "author_decision_story_unit",
                    "record_id": str(item.get("author_decision_id", "")),
                    "story_unit_id": original_story_unit_id,
                }
            )
        store.connection.execute(
            """
            INSERT INTO author_decisions(
                author_decision_id,title,decision,rationale,revisit_trigger,story_unit_id,branch_id,status
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                str(item.get("author_decision_id", "")),
                str(item.get("title", "")),
                str(item.get("decision", "")),
                str(item.get("rationale", "")),
                str(item.get("revisit_trigger", "")),
                story_unit_id,
                str(item.get("branch_id", "mainline")),
                str(item.get("status", "AUTHOR_LOCKED")),
            ),
        )

    for item in _state_list(project, "writer_preferences"):
        store.connection.execute(
            """
            INSERT INTO writer_preferences(
                preference_id,scope_kind,scope_id,statement,status,confidence,evidence_json
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                str(item.get("preference_id", "")),
                str(item.get("scope_kind", "project")),
                str(item.get("scope_id", "")),
                str(item.get("statement", "")),
                str(item.get("status", "PROVISIONAL")),
                float(item.get("confidence", 0.5)),
                StoryStore.encode_json(item.get("evidence", [])),
            ),
        )

    for item in _state_list(project, "story_lenses"):
        lens_id = str(item.get("lens_id", "")).strip()
        name = str(item.get("name", "")).strip()
        definition = str(item.get("definition", "")).strip()
        status = str(item.get("status", "ACTIVE")).strip().upper()
        if not lens_id or not name or not definition or status not in {"ACTIVE", "PAUSED", "ARCHIVED"}:
            orphans.append({"kind": "story_lens", "record_id": lens_id})
            continue
        store.connection.execute(
            """
            INSERT INTO story_lenses(lens_id,name,definition,status,created_by)
            VALUES(?,?,?,?,'writer')
            """,
            (lens_id, name, definition, status),
        )

    # Dependencies are a compiled graph, not a second authority store. Rebuild
    # semantic edges from the durable writer-owned records after hydration so
    # Retcon Impact remains correct even when story-index.sqlite was deleted.
    for row in store.rows(
        "SELECT knowledge_id,claim_id,source_claim_id FROM knowledge_state"
    ):
        store.add_dependency("claim", row["claim_id"], "knowledge_state", row["knowledge_id"], "known_or_believed_as")
        if row["source_claim_id"] and row["source_claim_id"] != row["claim_id"]:
            store.add_dependency("claim", row["source_claim_id"], "knowledge_state", row["knowledge_id"], "supports")
    for row in store.rows("SELECT state_id,evidence_claim_id FROM world_state WHERE evidence_claim_id IS NOT NULL"):
        store.add_dependency("claim", row["evidence_claim_id"], "world_state", row["state_id"], "supports")
    for row in store.rows("SELECT reader_state_id,claim_id FROM reader_state WHERE claim_id IS NOT NULL"):
        store.add_dependency("claim", row["claim_id"], "reader_state", row["reader_state_id"], "reader_access")
    for row in store.rows(
        "SELECT relationship_id,evidence_claim_id FROM relationships WHERE evidence_claim_id IS NOT NULL"
    ):
        store.add_dependency("claim", row["evidence_claim_id"], "relationship", row["relationship_id"], "supports")
    for row in store.rows("SELECT item_id,evidence_claim_id FROM promise_items WHERE evidence_claim_id IS NOT NULL"):
        store.add_dependency("claim", row["evidence_claim_id"], "promise_item", row["item_id"], "supports")
    for row in store.rows("SELECT decision_id,story_unit_id FROM decisions WHERE story_unit_id IS NOT NULL"):
        store.add_dependency("story_unit", row["story_unit_id"], "decision", row["decision_id"], "contains")
    for row in store.rows("SELECT * FROM causal_edges"):
        store.add_dependency(row["cause_kind"], row["cause_id"], "causal_edge", row["edge_id"], "cause_endpoint")
        store.add_dependency(row["effect_kind"], row["effect_id"], "causal_edge", row["edge_id"], "effect_endpoint")
        if row["evidence_claim_id"]:
            store.add_dependency("claim", row["evidence_claim_id"], "causal_edge", row["edge_id"], "supports")
    for row in store.rows("SELECT opposition_id,story_unit_id FROM opposition_state WHERE story_unit_id IS NOT NULL"):
        store.add_dependency("story_unit", row["story_unit_id"], "opposition", row["opposition_id"], "contains")
    for row in store.rows("SELECT story_unit_id FROM scene_contracts"):
        store.add_dependency("story_unit", row["story_unit_id"], "scene_contract", row["story_unit_id"], "defines")
    for row in store.rows(
        "SELECT author_decision_id,story_unit_id FROM author_decisions WHERE story_unit_id IS NOT NULL"
    ):
        store.add_dependency(
            "story_unit",
            row["story_unit_id"],
            "author_decision",
            row["author_decision_id"],
            "governs",
        )

    store.commit()
    writer_entity_count = next(
        iter(store.rows("SELECT COUNT(*) AS count FROM entities WHERE status<>'PROVISIONAL'"))
    )["count"]
    writer_claim_count = next(
        iter(store.rows("SELECT COUNT(*) AS count FROM claims WHERE created_by='writer'"))
    )["count"]
    return {
        "writer_entities": writer_entity_count,
        "writer_claims": writer_claim_count,
        "timeline_events": next(iter(store.rows("SELECT COUNT(*) AS count FROM timeline_events")))["count"],
        "world_state": len(_state_list(project, "world_state")),
        "knowledge_state": len(_state_list(project, "knowledge_state")),
        "reader_state": next(iter(store.rows("SELECT COUNT(*) AS count FROM reader_state")))["count"],
        "relationships": next(iter(store.rows("SELECT COUNT(*) AS count FROM relationships")))["count"],
        "threads": len(_state_list(project, "threads")),
        "promise_items": len(_state_list(project, "promise_items")),
        "decisions": next(iter(store.rows("SELECT COUNT(*) AS count FROM decisions")))["count"],
        "causal_edges": next(iter(store.rows("SELECT COUNT(*) AS count FROM causal_edges")))["count"],
        "opposition_state": next(iter(store.rows("SELECT COUNT(*) AS count FROM opposition_state")))["count"],
        "branches": len(inserted) - 1,
        "branch_overlays": len(overlay_ids),
        "scene_contracts": next(iter(store.rows("SELECT COUNT(*) AS count FROM scene_contracts")))["count"],
        "author_decisions": next(iter(store.rows("SELECT COUNT(*) AS count FROM author_decisions")))["count"],
        "writer_preferences": next(iter(store.rows("SELECT COUNT(*) AS count FROM writer_preferences")))["count"],
        "story_lenses": next(iter(store.rows("SELECT COUNT(*) AS count FROM story_lenses")))["count"],
        "orphans": orphans,
    }
