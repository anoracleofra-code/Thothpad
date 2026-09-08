from __future__ import annotations

import re
import uuid
from pathlib import PurePosixPath

from backend.story.authority import AuthorityStatus, SourceRole
from backend.story.sources import ExtractedStructure, LinkHint, SourceRoleHint
from backend.story.store import StoryStore

_TRAILING_KIND = re.compile(
    r"\s+(?:character|profile|sheet|bio|biography|notes?)$",
    re.IGNORECASE,
)


def _entity_id(name: str, entity_type: str) -> str:
    key = " ".join(name.casefold().split())
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"thothpad-entity:{entity_type}:{key}"))


def _title_from_path(relative_path: str) -> str:
    stem = PurePosixPath(relative_path).stem.replace("_", " ").replace("-", " ")
    return " ".join(stem.split()).strip()


def _character_title(relative_path: str) -> str:
    return _TRAILING_KIND.sub("", _title_from_path(relative_path)).strip()


def sync_entities_from_source(
    store: StoryStore,
    *,
    source_id: str,
    relative_path: str,
    text: str,
    roles: list[SourceRoleHint],
    links: list[LinkHint],
    structures: list[ExtractedStructure] | None = None,
) -> list[str]:
    created: list[str] = []
    character_confidence = max(
        (hint.confidence for hint in roles if hint.role == SourceRole.CHARACTER_REFERENCE),
        default=0.0,
    )
    if character_confidence >= 0.65:
        heading_name = ""
        for structure in structures or []:
            if structure.kind == "heading" and structure.level <= 2 and structure.title.strip():
                heading_name = structure.title.strip()
                break
        name = heading_name or _character_title(relative_path)
        if name and name.casefold() not in {"characters", "character", "cast", "people"}:
            entity_id = _entity_id(name, "character")
            store.upsert_entity(
                entity_id,
                name,
                "character",
                confidence=character_confidence,
                status=AuthorityStatus.PROVISIONAL,
                metadata={"discovered_from": source_id},
            )
            created.append(entity_id)

    for link in links:
        label = " ".join((link.label or link.target).split()).strip()
        if not label or len(label) > 160:
            continue
        entity_id = _entity_id(label, "concept")
        store.upsert_entity(
            entity_id,
            label,
            "concept",
            confidence=0.72 if link.kind == "wikilink" else 0.55,
            status=AuthorityStatus.PROVISIONAL,
            metadata={"discovered_from": source_id, "link_kind": link.kind},
        )
        created.append(entity_id)

    store.replace_mentions_for_source(source_id)
    aliases = list(store.rows("SELECT entity_id, alias, confidence FROM entity_aliases"))
    for alias in aliases:
        surface = alias["alias"]
        if len(surface) < 2:
            continue
        pattern = re.compile(rf"(?<![\w']){re.escape(surface)}(?![\w'])", re.IGNORECASE)
        for match in pattern.finditer(text):
            store.add_mention(
                alias["entity_id"],
                source_id,
                match.start(),
                match.end(),
                match.group(0),
                float(alias["confidence"]),
            )
    return sorted(set(created))


def merge_entities(store: StoryStore, source_entity_id: str, target_entity_id: str) -> None:
    """Merge one reviewed duplicate into another without guessing identity."""

    if source_entity_id == target_entity_id:
        return
    source = store.entity(source_entity_id)
    target = store.entity(target_entity_id)
    if source is None or target is None:
        raise KeyError("entity not found")
    aliases = list(store.rows("SELECT alias, confidence FROM entity_aliases WHERE entity_id=?", (source_entity_id,)))
    for alias in aliases:
        store.add_entity_alias(target_entity_id, alias["alias"], confidence=alias["confidence"], confirmed=True)
    with store.transaction():
        store.connection.execute(
            "UPDATE entity_mentions SET entity_id=? WHERE entity_id=?", (target_entity_id, source_entity_id)
        )
        store.connection.execute(
            "UPDATE claims SET subject_entity_id=? WHERE subject_entity_id=?", (target_entity_id, source_entity_id)
        )
        store.connection.execute(
            "UPDATE claims SET object_entity_id=? WHERE object_entity_id=?", (target_entity_id, source_entity_id)
        )
        store.connection.execute("DELETE FROM entities WHERE entity_id=?", (source_entity_id,))
