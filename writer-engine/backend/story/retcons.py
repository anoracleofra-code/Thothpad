from __future__ import annotations

from collections import deque
from typing import Any

from backend.story.branches import mark_branch_stale
from backend.story.store import StoryStore


def _record_label(store: StoryStore, kind: str, identifier: str) -> str:
    """Return a bounded human-readable label for a dependency node.

    Labels are descriptive only. They never participate in authority or impact
    traversal, so a missing/stale display row cannot change the dependency
    result itself.
    """

    queries: dict[str, tuple[str, str]] = {
        "source": ("SELECT relative_path AS label FROM sources WHERE source_id=?", "source"),
        "story_unit": (
            "SELECT display_title AS label FROM story_units WHERE story_unit_id=?",
            "story unit",
        ),
        "thread": ("SELECT title AS label FROM threads WHERE thread_id=?", "thread"),
        "promise_item": ("SELECT title AS label FROM promise_items WHERE item_id=?", "promise"),
        "decision": ("SELECT description AS label FROM decisions WHERE decision_id=?", "decision"),
        "opposition": (
            "SELECT description AS label FROM opposition_state WHERE opposition_id=?",
            "opposition",
        ),
        "author_decision": (
            "SELECT title AS label FROM author_decisions WHERE author_decision_id=?",
            "author decision",
        ),
    }
    if kind == "claim":
        row = next(
            iter(
                store.rows(
                    """
                    SELECT c.predicate,c.literal_value_json,e.canonical_name
                    FROM claims c LEFT JOIN entities e ON e.entity_id=c.subject_entity_id
                    WHERE c.claim_id=?
                    """,
                    (identifier,),
                )
            ),
            None,
        )
        if row is not None:
            subject = str(row["canonical_name"] or "").strip()
            value = StoryStore.decode_json(row["literal_value_json"], None)
            value_text = "" if value is None else f" = {value}"
            return f"{subject + ' · ' if subject else ''}{row['predicate']}{value_text}"[:500]
    elif kind == "knowledge_state":
        row = next(
            iter(
                store.rows(
                    """
                    SELECT k.state,e.canonical_name,c.predicate
                    FROM knowledge_state k
                    JOIN entities e ON e.entity_id=k.character_id
                    JOIN claims c ON c.claim_id=k.claim_id
                    WHERE k.knowledge_id=?
                    """,
                    (identifier,),
                )
            ),
            None,
        )
        if row is not None:
            return f"{row['canonical_name']} · {row['state']} · {row['predicate']}"[:500]
    elif kind == "world_state":
        row = next(
            iter(
                store.rows(
                    """
                    SELECT w.state_type,w.value_json,e.canonical_name
                    FROM world_state w LEFT JOIN entities e ON e.entity_id=w.entity_id
                    WHERE w.state_id=?
                    """,
                    (identifier,),
                )
            ),
            None,
        )
        if row is not None:
            value = StoryStore.decode_json(row["value_json"], None)
            return f"{row['canonical_name'] or 'World'} · {row['state_type']} = {value}"[:500]
    elif kind == "reader_state":
        row = next(
            iter(
                store.rows(
                    """
                    SELECT r.state,c.predicate,u.display_title
                    FROM reader_state r
                    LEFT JOIN claims c ON c.claim_id=r.claim_id
                    LEFT JOIN story_units u ON u.story_unit_id=r.story_unit_id
                    WHERE r.reader_state_id=?
                    """,
                    (identifier,),
                )
            ),
            None,
        )
        if row is not None:
            return f"Reader · {row['state']} · {row['predicate'] or row['display_title'] or identifier}"[:500]
    elif kind == "relationship":
        row = next(
            iter(
                store.rows(
                    """
                    SELECT r.relationship_type,a.canonical_name AS a_name,b.canonical_name AS b_name
                    FROM relationships r
                    JOIN entities a ON a.entity_id=r.entity_a
                    JOIN entities b ON b.entity_id=r.entity_b
                    WHERE r.relationship_id=?
                    """,
                    (identifier,),
                )
            ),
            None,
        )
        if row is not None:
            return f"{row['a_name']} ↔ {row['b_name']} · {row['relationship_type']}"[:500]
    elif kind == "causal_edge":
        row = next(
            iter(
                store.rows(
                    "SELECT cause_kind,cause_id,relation,effect_kind,effect_id FROM causal_edges WHERE edge_id=?",
                    (identifier,),
                )
            ),
            None,
        )
        if row is not None:
            return (f"{row['cause_kind']}:{row['cause_id']} {row['relation']} {row['effect_kind']}:{row['effect_id']}")[
                :500
            ]
    elif kind == "scene_contract":
        row = next(
            iter(
                store.rows(
                    """
                    SELECT u.display_title FROM scene_contracts c
                    JOIN story_units u ON u.story_unit_id=c.story_unit_id
                    WHERE c.story_unit_id=?
                    """,
                    (identifier,),
                )
            ),
            None,
        )
        if row is not None:
            return f"Scene contract · {row['display_title']}"[:500]
    elif kind in queries:
        sql, fallback = queries[kind]
        row = next(iter(store.rows(sql, (identifier,))), None)
        if row is not None and str(row["label"] or "").strip():
            return str(row["label"]).strip()[:500]
        return f"{fallback} · {identifier}"[:500]
    return f"{kind} · {identifier}"[:500]


def retcon_impact(
    store: StoryStore,
    *,
    source_kind: str,
    source_id: str,
    maximum_nodes: int = 5_000,
) -> dict[str, Any]:
    """Return dependency impact without automatically changing story truth."""

    queue = deque([(source_kind, source_id, 0)])
    seen = {(source_kind, source_id)}
    affected: list[dict[str, Any]] = []
    while queue and len(seen) < maximum_nodes:
        kind, identifier, depth = queue.popleft()
        for row in store.dependents(kind, identifier):
            target = (row["dependent_kind"], row["dependent_id"])
            affected.append(
                {
                    "source_kind": kind,
                    "source_id": identifier,
                    "dependent_kind": row["dependent_kind"],
                    "dependent_id": row["dependent_id"],
                    "relation": row["relation"],
                    "depth": depth + 1,
                    "label": _record_label(store, row["dependent_kind"], row["dependent_id"]),
                }
            )
            if target not in seen:
                seen.add(target)
                queue.append((target[0], target[1], depth + 1))

    counts: dict[str, int] = {}
    for item in affected:
        counts[item["dependent_kind"]] = counts.get(item["dependent_kind"], 0) + 1
    return {
        "root": {
            "kind": source_kind,
            "id": source_id,
            "label": _record_label(store, source_kind, source_id),
        },
        "affected": affected,
        "counts": counts,
        "truncated": bool(queue),
    }


def invalidate_source_dependents(store: StoryStore, source_id: str) -> dict[str, Any]:
    impact = retcon_impact(store, source_kind="source", source_id=source_id)
    store.connection.execute("UPDATE claim_evidence SET stale=1 WHERE source_id=?", (source_id,))
    for row in store.rows("SELECT branch_id FROM branches WHERE branch_id<>'mainline' AND status='ACTIVE'"):
        mark_branch_stale(store, row["branch_id"])
    return impact
