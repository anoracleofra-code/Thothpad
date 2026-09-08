from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.story.project import StoryProject
from backend.story.store import StoryStore

_WORD = re.compile(r"[\w'-]{2,}", re.UNICODE)


def _bounded(value: int, maximum: int = 100) -> int:
    return max(1, min(int(value), maximum))


def _decoded(row: Any) -> dict[str, Any]:
    item = dict(row)
    for key in list(item):
        if key.endswith("_json"):
            item[key[:-5]] = StoryStore.decode_json(item.pop(key), None)
    return item


@dataclass(slots=True)
class StoryExplorer:
    project: StoryProject
    store: StoryStore

    def explore(self, query: str, *, branch_id: str = "mainline", limit: int = 50) -> dict[str, Any]:
        terms = [token.casefold() for token in _WORD.findall(query)][:12]
        maximum = _bounded(limit, 100)
        if not terms:
            return {"query": query, "nodes": [], "edges": [], "coverage": {"reason": "empty_query"}}
        likes = [f"%{term}%" for term in terms[:4]]
        nodes: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def add(kind: str, identifier: str, label: str, **extra: Any) -> None:
            key = (kind, identifier)
            if not identifier or key in seen or len(nodes) >= maximum:
                return
            seen.add(key)
            nodes.append({"kind": kind, "id": identifier, "label": label, **extra})

        entity_conditions = " OR ".join(
            "lower(e.canonical_name) LIKE ? OR lower(COALESCE(a.alias,'')) LIKE ?" for _ in likes
        )
        entity_params = [value for like in likes for value in (like, like)]
        for row in self.store.rows(
            f"""
            SELECT DISTINCT e.* FROM entities e
            LEFT JOIN entity_aliases a ON a.entity_id=e.entity_id
            WHERE {entity_conditions}
            ORDER BY e.canonical_name COLLATE NOCASE LIMIT ?
            """,  # noqa: S608 - fixed condition fragments with bound values
            [*entity_params, maximum],
        ):
            add("entity", row["entity_id"], row["canonical_name"], entity_type=row["entity_type"], status=row["status"])

        claim_conditions = " OR ".join(
            "lower(c.predicate) LIKE ? OR lower(COALESCE(c.literal_value_json,'')) LIKE ? "
            "OR lower(COALESCE(e.canonical_name,'')) LIKE ?"
            for _ in likes
        )
        claim_params = [value for like in likes for value in (like, like, like)]
        for row in self.store.rows(
            f"""
            SELECT c.*,e.canonical_name AS subject_name
            FROM claims c LEFT JOIN entities e ON e.entity_id=c.subject_entity_id
            WHERE c.branch_id=? AND ({claim_conditions})
            ORDER BY c.created_at,c.claim_id LIMIT ?
            """,  # noqa: S608 - fixed condition fragments with bound values
            [branch_id, *claim_params, maximum],
        ):
            literal = StoryStore.decode_json(row["literal_value_json"], None)
            label = f"{row['predicate']}: {literal}" if literal is not None else str(row["predicate"])
            add("claim", row["claim_id"], label, status=row["status"], created_by=row["created_by"])

        unit_conditions = " OR ".join("lower(u.display_title) LIKE ?" for _ in likes)
        for row in self.store.rows(
            f"""
            SELECT u.*,s.relative_path FROM story_units u
            LEFT JOIN sources s ON s.source_id=u.source_id
            WHERE u.branch_id=? AND ({unit_conditions})
            ORDER BY s.relative_path COLLATE NOCASE,u.ordinal LIMIT ?
            """,  # noqa: S608 - fixed condition fragments with bound values
            [branch_id, *likes, maximum],
        ):
            add(
                "story_unit",
                row["story_unit_id"],
                row["display_title"],
                unit_kind=row["kind"],
                path=row["relative_path"],
            )

        for table, id_col, label_col, kind in (
            ("threads", "thread_id", "title", "thread"),
            ("promise_items", "item_id", "title", "promise_item"),
            ("decisions", "decision_id", "description", "decision"),
        ):
            label_conditions = " OR ".join(f"lower({label_col}) LIKE ?" for _ in likes)
            for row in self.store.rows(
                f"SELECT * FROM {table} WHERE branch_id=? AND ({label_conditions}) ORDER BY rowid LIMIT ?",  # noqa: S608 - table/column names are fixed constants
                [branch_id, *likes, maximum],
            ):
                add(kind, row[id_col], row[label_col], state=row["state"] if "state" in row.keys() else row["status"])

        evidence = []
        if len(nodes) < maximum:
            evidence = [
                dict(chunk_row)
                for chunk_row in self.store.search_chunks(
                    terms,
                    limit=min(24, maximum - len(nodes)),
                )
            ]
            for evidence_row in evidence:
                add(
                    "evidence",
                    evidence_row["chunk_id"],
                    evidence_row["heading"] or evidence_row["relative_path"],
                    path=evidence_row["relative_path"],
                    start_offset=evidence_row["start_offset"],
                    end_offset=evidence_row["end_offset"],
                    excerpt=evidence_row["text"][:700],
                )

        ids_by_kind: dict[str, set[str]] = {}
        for node in nodes:
            ids_by_kind.setdefault(node["kind"], set()).add(node["id"])
        edges: list[dict[str, Any]] = []
        entity_ids = ids_by_kind.get("entity", set())
        if entity_ids:
            placeholders = ",".join("?" for _ in entity_ids)
            for row in self.store.rows(
                (
                    f"SELECT * FROM relationships WHERE branch_id=? AND "
                    f"(entity_a IN ({placeholders}) OR entity_b IN ({placeholders})) LIMIT 100"
                ),  # noqa: S608
                [branch_id, *entity_ids, *entity_ids],
            ):
                edges.append(
                    {
                        "kind": "relationship",
                        "from": row["entity_a"],
                        "to": row["entity_b"],
                        "label": row["relationship_type"],
                        "state": StoryStore.decode_json(row["state_json"], {}),
                    }
                )
        for row in self.store.rows(
            "SELECT * FROM dependencies ORDER BY source_kind,source_id,dependent_kind,dependent_id LIMIT 500"
        ):
            if (row["source_kind"], row["source_id"]) in seen or (row["dependent_kind"], row["dependent_id"]) in seen:
                edges.append(
                    {
                        "kind": "dependency",
                        "from_kind": row["source_kind"],
                        "from": row["source_id"],
                        "to_kind": row["dependent_kind"],
                        "to": row["dependent_id"],
                        "label": row["relation"],
                    }
                )
                if len(edges) >= 200:
                    break
        return {
            "query": query,
            "branch_id": branch_id,
            "nodes": nodes,
            "edges": edges,
            "coverage": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "bounded": True,
                "semantic_conclusion": False,
            },
        }
