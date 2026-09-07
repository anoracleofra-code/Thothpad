from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

from backend.story.authority import AuthorityStatus, SourceRole
from backend.story.sources import ExtractedStructure, SourceRoleHint
from backend.story.store import StoryStore

_CHAPTER = re.compile(
    r"^(?:chapter|chap\.?|ch\.?)[\s:_-]*"
    r"(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)?\b",
    re.IGNORECASE,
)
_PART = re.compile(r"^(?:part|book|volume)[\s:_-]+", re.IGNORECASE)
_SCENE = re.compile(r"^(?:scene)[\s:_-]+", re.IGNORECASE)
_SPECIAL_CHAPTER = re.compile(r"^(?:prologue|epilogue|interlude|prelude|afterword)\b", re.IGNORECASE)


@dataclass(slots=True, frozen=True)
class StoryUnitCandidate:
    kind: str
    title: str
    start_offset: int
    end_offset: int
    status: AuthorityStatus
    parent_hint: str = ""


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _anchor_signature(kind: str, title: str, body: str) -> str:
    opening = " ".join(body[:600].split())
    payload = f"{kind}\n{_normalized(title)}\n{opening}".encode()
    return hashlib.sha256(payload).hexdigest()


def _content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _kind_for_heading(structure: ExtractedStructure) -> str:
    title = structure.title.strip()
    if _PART.search(title):
        return "part"
    if _CHAPTER.search(title) or _SPECIAL_CHAPTER.search(title):
        return "chapter"
    if _SCENE.search(title):
        return "scene"
    if structure.level <= 1:
        return "chapter"
    return "scene"


def detect_story_units(
    text: str,
    structures: list[ExtractedStructure],
    roles: list[SourceRoleHint],
) -> list[StoryUnitCandidate]:
    manuscript_confidence = max(
        (hint.confidence for hint in roles if hint.role == SourceRole.MANUSCRIPT),
        default=0.0,
    )
    if manuscript_confidence < 0.55:
        return []

    candidates: list[StoryUnitCandidate] = []
    for structure in structures:
        if structure.kind != "heading":
            continue
        candidates.append(
            StoryUnitCandidate(
                kind=_kind_for_heading(structure),
                title=structure.title.strip(),
                start_offset=structure.start_offset,
                end_offset=structure.end_offset,
                status=AuthorityStatus.MANUSCRIPT_OBSERVED,
            )
        )
    if not candidates:
        candidates.append(
            StoryUnitCandidate(
                kind="manuscript",
                title="Manuscript",
                start_offset=0,
                end_offset=len(text),
                status=AuthorityStatus.MANUSCRIPT_OBSERVED,
            )
        )
    return candidates


def sync_story_units(
    store: StoryStore,
    *,
    source_id: str,
    text: str,
    structures: list[ExtractedStructure],
    roles: list[SourceRoleHint],
) -> list[str]:
    candidates = detect_story_units(text, structures, roles)
    active_ids: set[str] = set()
    previous_parent: str | None = None
    parent_by_kind: dict[str, str] = {}

    for ordinal, candidate in enumerate(candidates):
        body = text[candidate.start_offset:candidate.end_offset]
        anchor = _anchor_signature(candidate.kind, candidate.title, body)
        existing = store.find_unit_by_anchor(anchor)
        source = store.source(source_id)
        project_seed = str(source["project_id"]) if source is not None else source_id
        deterministic_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"thothpad-story-unit:{project_seed}:{anchor}",
            )
        )
        unit_id = existing["story_unit_id"] if existing is not None else deterministic_id

        # Identical structural anchors are rare but possible (for example a
        # duplicated placeholder scene). Avoid corrupting an already-present
        # unit while still keeping ordinary move/rename/rebuild identity
        # independent of the source path.
        collision = next(
            iter(
                store.rows(
                    "SELECT source_id,anchor_signature FROM story_units WHERE story_unit_id=?",
                    (unit_id,),
                )
            ),
            None,
        )
        if (
            existing is None
            and collision is not None
            and collision["source_id"] != source_id
            and collision["anchor_signature"] == anchor
        ):
            unit_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"thothpad-story-unit-collision:{project_seed}:{anchor}:{source_id}",
                )
            )

        parent_id: str | None = None
        if candidate.kind == "scene":
            parent_id = parent_by_kind.get("chapter") or parent_by_kind.get("part")
        elif candidate.kind == "chapter":
            parent_id = parent_by_kind.get("part")
        elif candidate.kind == "beat":
            parent_id = parent_by_kind.get("scene") or parent_by_kind.get("chapter")
        elif candidate.kind not in {"part", "manuscript"}:
            parent_id = previous_parent

        record: dict[str, Any] = {
            "story_unit_id": unit_id,
            "kind": candidate.kind,
            "parent_id": parent_id,
            "source_id": source_id,
            "display_title": candidate.title,
            "ordinal": ordinal,
            "start_offset": candidate.start_offset,
            "end_offset": candidate.end_offset,
            "anchor_signature": anchor,
            "content_hash": _content_hash(body),
            "status": candidate.status,
            "branch_id": "mainline",
            "metadata": {"detector": "deterministic-heading"},
        }
        store.add_story_unit(record)
        active_ids.add(unit_id)
        parent_by_kind[candidate.kind] = unit_id
        previous_parent = unit_id

    store.delete_units_for_source_except(source_id, active_ids)
    return sorted(active_ids)
