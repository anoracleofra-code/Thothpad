from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from backend.story.persistence import commit_writer_state
from backend.story.project import StoryProject
from backend.story.store import StoryStore

_WORD = re.compile(r"[\w'-]{3,}", re.UNICODE)
_STOP = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "and",
        "are",
        "because",
        "does",
        "else",
        "find",
        "from",
        "have",
        "into",
        "places",
        "that",
        "the",
        "their",
        "them",
        "this",
        "tries",
        "where",
        "which",
        "with",
        "would",
    }
)


def _lens_id(project: StoryProject, name: str) -> str:
    try:
        namespace = uuid.UUID(project.project_id)
    except ValueError:
        namespace = uuid.uuid5(uuid.NAMESPACE_URL, f"thothpad-project:{project.project_id}")
    return str(uuid.uuid5(namespace, f"story-lens:{name.casefold()}"))


def put_story_lens(
    project: StoryProject,
    store: StoryStore,
    *,
    name: str,
    definition: str,
    status: str = "ACTIVE",
    lens_id: str | None = None,
) -> dict[str, Any]:
    name = name.strip()[:200]
    definition = definition.strip()[:4_000]
    if not name or not definition:
        raise ValueError("Story Lens requires a name and natural-language definition")
    normalized_status = status.strip().upper()
    if normalized_status not in {"ACTIVE", "PAUSED", "ARCHIVED"}:
        raise ValueError("unsupported Story Lens status")
    identifier = lens_id or _lens_id(project, name)
    store.connection.execute(
        """
        INSERT INTO story_lenses(lens_id,name,definition,status,created_by)
        VALUES(?,?,?,?,'writer')
        ON CONFLICT(lens_id) DO UPDATE SET
            name=excluded.name,definition=excluded.definition,status=excluded.status,created_by='writer'
        """,
        (identifier, name, definition, normalized_status),
    )
    commit_writer_state(project, store)
    return {
        "lens_id": identifier,
        "name": name,
        "definition": definition,
        "status": normalized_status,
        "created_by": "writer",
    }


def list_story_lenses(store: StoryStore, *, include_archived: bool = False) -> list[dict[str, Any]]:
    where = "" if include_archived else " WHERE status<>'ARCHIVED'"
    return [
        dict(row)
        for row in store.rows(
            f"SELECT * FROM story_lenses{where} ORDER BY name COLLATE NOCASE,lens_id"  # noqa: S608
        )
    ]


def _terms(store: StoryStore, definition: str) -> tuple[list[str], list[str]]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in _WORD.findall(definition.casefold()):
        if term in _STOP or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= 32:
            break
    entity_names: list[str] = []
    folded = definition.casefold()
    for row in store.rows("SELECT alias FROM entity_aliases ORDER BY length(alias) DESC LIMIT 1000"):
        alias = str(row["alias"])
        if len(alias) >= 2 and alias.casefold() in folded:
            entity_names.append(alias)
            if len(entity_names) >= 12:
                break
    return terms, entity_names


def _unit_for_offset(store: StoryStore, source_id: str, offset: int) -> str | None:
    row = next(
        iter(
            store.rows(
                """
                SELECT story_unit_id FROM story_units
                WHERE source_id=? AND start_offset<=? AND end_offset>?
                ORDER BY (end_offset-start_offset),ordinal LIMIT 1
                """,
                (source_id, offset, offset),
            )
        ),
        None,
    )
    return str(row["story_unit_id"]) if row is not None else None


def run_story_lens(
    store: StoryStore,
    lens_id: str,
    *,
    maximum_findings: int = 100,
) -> dict[str, Any]:
    lens = next(iter(store.rows("SELECT * FROM story_lenses WHERE lens_id=?", (lens_id,))), None)
    if lens is None:
        raise KeyError("Story Lens not found")
    if str(lens["status"]) == "ARCHIVED":
        raise ValueError("archived Story Lens cannot be run")
    terms, entity_names = _terms(store, str(lens["definition"]))
    search_terms = list(dict.fromkeys([*entity_names, *terms]))
    if not search_terms:
        return {
            "lens": dict(lens),
            "compiled": {"terms": [], "entities": []},
            "findings": [],
            "retrieval_only": True,
        }

    maximum = max(1, min(int(maximum_findings), 500))
    rows = store.search_chunks(search_terms, limit=min(maximum * 3, 500))
    findings: list[dict[str, Any]] = []
    store.connection.execute("DELETE FROM lens_findings WHERE lens_id=?", (lens_id,))
    for row in rows:
        text = str(row["text"])
        folded = text.casefold()
        matched = [term for term in search_terms if term.casefold() in folded]
        if not matched:
            continue
        first = min(folded.find(term.casefold()) for term in matched if folded.find(term.casefold()) >= 0)
        excerpt_start = max(0, first - 120)
        excerpt_end = min(len(text), first + max(len(term) for term in matched) + 180)
        excerpt = text[excerpt_start:excerpt_end]
        absolute_start = int(row["start_offset"]) + excerpt_start
        absolute_end = int(row["start_offset"]) + excerpt_end
        evidence_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        story_unit_id = _unit_for_offset(store, str(row["source_id"]), absolute_start)
        finding_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"thothpad-lens-finding:{lens_id}:{row['source_id']}:{absolute_start}:{absolute_end}:{evidence_hash}",
            )
        )
        statement = f"Candidate evidence matches lens retrieval terms: {', '.join(matched[:8])}"
        store.connection.execute(
            """
            INSERT INTO lens_findings(
                finding_id,lens_id,story_unit_id,source_id,start_offset,end_offset,
                statement,evidence_hash,confidence
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                finding_id,
                lens_id,
                story_unit_id,
                row["source_id"],
                absolute_start,
                absolute_end,
                statement,
                evidence_hash,
                0.5,
            ),
        )
        findings.append(
            {
                "finding_id": finding_id,
                "lens_id": lens_id,
                "story_unit_id": story_unit_id,
                "source_id": row["source_id"],
                "path": row["relative_path"],
                "heading": row["heading"],
                "start_offset": absolute_start,
                "end_offset": absolute_end,
                "excerpt": excerpt,
                "evidence_hash": evidence_hash,
                "matched_terms": matched[:8],
                "statement": statement,
                "semantic_conclusion": False,
            }
        )
        if len(findings) >= maximum:
            break
    store.commit()
    return {
        "lens": dict(lens),
        "compiled": {"terms": terms, "entities": entity_names},
        "findings": findings,
        "retrieval_only": True,
        "semantic_conclusion": False,
        "note": (
            "These are exact evidence candidates selected by deterministic lexical/entity retrieval. "
            "An LLM may interpret them, but retrieval alone never proves the lens statement."
        ),
    }


def story_lens(store: StoryStore, lens_id: str) -> dict[str, Any]:
    lens = next(iter(store.rows("SELECT * FROM story_lenses WHERE lens_id=?", (lens_id,))), None)
    if lens is None:
        raise KeyError("Story Lens not found")
    findings: list[dict[str, Any]] = []
    for row in store.rows(
        """
        SELECT f.*,s.relative_path FROM lens_findings f
        LEFT JOIN sources s ON s.source_id=f.source_id
        WHERE f.lens_id=? ORDER BY s.relative_path,f.start_offset LIMIT 500
        """,
        (lens_id,),
    ):
        item = dict(row)
        source_id = str(item.get("source_id") or "")
        start = int(item.get("start_offset") or 0)
        end = int(item.get("end_offset") or start)
        excerpt = ""
        for chunk in store.rows(
            """
            SELECT start_offset,end_offset,text FROM source_chunks
            WHERE source_id=? AND end_offset>? AND start_offset<? ORDER BY ordinal
            """,
            (source_id, start, end),
        ):
            local_start = max(start, int(chunk["start_offset"])) - int(chunk["start_offset"])
            local_end = min(end, int(chunk["end_offset"])) - int(chunk["start_offset"])
            excerpt += str(chunk["text"])[local_start:local_end]
        item["excerpt"] = excerpt
        item["evidence_current"] = hashlib.sha256(excerpt.encode("utf-8")).hexdigest() == item["evidence_hash"]
        findings.append(item)
    return {"lens": dict(lens), "findings": findings}
