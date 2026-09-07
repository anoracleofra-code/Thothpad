from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.story.adapters import DocxAdapter, GenericFolderAdapter, MarkdownAdapter, PlainTextAdapter, SourceAdapter
from backend.story.adapters.base import SourceCandidate
from backend.story.authority import SourceRole
from backend.story.claims import detect_claim_conflicts, extract_profile_claims
from backend.story.classify import classify_source, infer_default_authority
from backend.story.entities import sync_entities_from_source
from backend.story.extractors import compile_explicit_story_state, reconcile_explicit_story_state
from backend.story.persistence import hydrate_writer_state
from backend.story.project import StoryProject
from backend.story.sources import SourceChunk, SourceDocument, SourceRoleHint
from backend.story.store import StoryStore
from backend.story.units import sync_story_units

_WORD = re.compile(r"[\w'-]+", re.UNICODE)


def _hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _override_hash(value: dict[str, Any]) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _stable_id(namespace: uuid.UUID, value: str) -> str:
    return str(uuid.uuid5(namespace, value))


def _project_namespace(project_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(project_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"thothpad-project:{project_id}")


def _source_id(project: StoryProject, relative_path: str, existing_id: str | None = None) -> str:
    if existing_id:
        return existing_id
    return _stable_id(_project_namespace(project.project_id), f"source:{relative_path.casefold()}")


def _chunks(source_id: str, text: str, structures: list[Any], *, maximum_chars: int = 6_000) -> list[SourceChunk]:
    ranges: list[tuple[str, int, int]] = []
    if structures:
        for structure in structures:
            start = max(0, int(structure.start_offset))
            end = min(len(text), int(structure.end_offset))
            if end > start:
                ranges.append((str(structure.title), start, end))
    if not ranges:
        ranges = [("", 0, len(text))]

    result: list[SourceChunk] = []
    ordinal = 0
    for heading, range_start, range_end in ranges:
        cursor = range_start
        while cursor < range_end:
            end = min(range_end, cursor + maximum_chars)
            if end < range_end:
                newline = text.rfind("\n", cursor + maximum_chars // 2, end)
                if newline > cursor:
                    end = newline + 1
            piece = text[cursor:end]
            if piece.strip():
                piece_hash = _hash_bytes(piece.encode("utf-8"))
                chunk_id = str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"thothpad-chunk:{source_id}:{ordinal}:{piece_hash}")
                )
                result.append(
                    SourceChunk(
                        chunk_id=chunk_id,
                        source_id=source_id,
                        ordinal=ordinal,
                        heading=heading,
                        start_offset=cursor,
                        end_offset=end,
                        text=piece,
                        content_hash=_hash_bytes(piece.encode("utf-8")),
                    )
                )
                ordinal += 1
            if end <= cursor:
                break
            cursor = end
    return result


@dataclass(slots=True)
class ProjectUnderstanding:
    project_id: str
    root_name: str
    readable_documents: int = 0
    unreadable_documents: int = 0
    unchanged_documents: int = 0
    changed_documents: int = 0
    new_documents: int = 0
    removed_documents: int = 0
    role_counts: dict[str, int] = field(default_factory=dict)
    likely_manuscripts: list[dict[str, Any]] = field(default_factory=list)
    metadata_location: str = "project"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "root_name": self.root_name,
            "readable_documents": self.readable_documents,
            "unreadable_documents": self.unreadable_documents,
            "unchanged_documents": self.unchanged_documents,
            "changed_documents": self.changed_documents,
            "new_documents": self.new_documents,
            "removed_documents": self.removed_documents,
            "role_counts": dict(self.role_counts),
            "likely_manuscripts": list(self.likely_manuscripts),
            "metadata_location": self.metadata_location,
        }


class ProjectIngestor:
    def __init__(
        self,
        project: StoryProject,
        store: StoryStore,
        *,
        generic: GenericFolderAdapter | None = None,
        adapters: list[SourceAdapter] | None = None,
    ) -> None:
        self.project = project
        self.store = store
        self.generic = generic or GenericFolderAdapter()
        self.adapters = adapters or [MarkdownAdapter(), PlainTextAdapter(), DocxAdapter()]

    def _adapter(self, candidate: SourceCandidate) -> SourceAdapter | None:
        return next((adapter for adapter in self.adapters if adapter.supports(candidate.path)), None)

    def _override_roles(self, override: dict[str, Any], inferred: list[SourceRoleHint]) -> list[SourceRoleHint]:
        roles = override.get("roles")
        if not isinstance(roles, list) or not roles:
            return inferred
        result: list[SourceRoleHint] = []
        for role in roles:
            try:
                normalized = SourceRole(str(role))
            except ValueError:
                continue
            result.append(SourceRoleHint(normalized, 1.0, "writer override"))
        return result or inferred

    def ingest(self) -> ProjectUnderstanding:
        present_paths: set[str] = set()
        role_counter: Counter[str] = Counter()
        summary = ProjectUnderstanding(
            project_id=self.project.project_id,
            root_name=self.project.root.name,
            metadata_location="application_data" if self.project.metadata_is_external else "project",
        )

        for candidate in self.generic.enumerate_sources(self.project.root):
            present_paths.add(candidate.relative_path)
            adapter = self._adapter(candidate)
            if adapter is None:
                continue
            existing = self.store.source_by_path(candidate.relative_path)
            rule_override = self.project.source_rule_override(candidate.relative_path)
            explicit_override = self.project.source_override(candidate.relative_path)
            override = {**rule_override, **explicit_override}
            override_hash = _override_hash(override)
            existing_metadata = (
                StoryStore.decode_json(existing["metadata_json"], {}) if existing is not None else {}
            )
            unchanged = (
                existing is not None
                and existing["size"] == candidate.size
                and existing["mtime_ns"] == candidate.mtime_ns
                and existing_metadata.get("override_hash") == override_hash
            )
            if unchanged:
                summary.unchanged_documents += 1
                summary.readable_documents += 1
                for role in self.store.source_roles(existing["source_id"]):
                    if role["confidence"] >= 0.5:
                        role_counter[role["role"]] += 1
                continue

            raw_hash = _hash_bytes(candidate.path.read_bytes())

            try:
                extracted = adapter.extract(candidate)
            except (OSError, ValueError):
                summary.unreadable_documents += 1
                continue

            inferred_roles = classify_source(candidate, extracted)
            roles = self._override_roles(override, inferred_roles)
            authority_override = override.get("authority") if isinstance(override.get("authority"), str) else None
            try:
                authority = infer_default_authority(roles, extracted, override=authority_override)
            except ValueError:
                authority = infer_default_authority(roles, extracted)

            source_id = _source_id(
                self.project,
                candidate.relative_path,
                existing["source_id"] if existing is not None else None,
            )
            source = SourceDocument(
                source_id=source_id,
                project_id=self.project.project_id,
                relative_path=candidate.relative_path,
                display_name=candidate.path.name,
                format=candidate.path.suffix.casefold().lstrip("."),
                content_hash=raw_hash,
                size=candidate.size,
                mtime_ns=candidate.mtime_ns,
                roles=roles,
                authority_default=authority,
                metadata={
                    "role_override": bool(override.get("roles")),
                    "authority_override": bool(authority_override),
                    "override_hash": override_hash,
                    "word_count": len(_WORD.findall(extracted.text)),
                    "text_chars": len(extracted.text),
                },
                adapter_origin=adapter.name,
            )
            self.store.upsert_source(source)
            # The source row now carries the new hash, so all old evidence from
            # this source becomes stale before deterministic extractors refresh
            # the spans/facts they can still prove.
            self.store.refresh_evidence_staleness(source_id)
            self.store.replace_chunks(source_id, _chunks(source_id, extracted.text, extracted.structures))
            self.store.replace_links(
                source_id,
                [
                    {
                        "target": link.target,
                        "label": link.label,
                        "start_offset": link.start_offset,
                        "end_offset": link.end_offset,
                        "kind": link.kind,
                    }
                    for link in extracted.links
                ],
            )
            sync_story_units(
                self.store,
                source_id=source_id,
                text=extracted.text,
                structures=extracted.structures,
                roles=roles,
            )
            entities = sync_entities_from_source(
                self.store,
                source_id=source_id,
                relative_path=candidate.relative_path,
                text=extracted.text,
                roles=roles,
                links=extracted.links,
                structures=extracted.structures,
            )
            subject_entity_id: str | None = None
            for entity_id in entities:
                entity = self.store.entity(entity_id)
                if entity is not None and entity["entity_type"] == "character":
                    subject_entity_id = entity_id
                    break
            extract_profile_claims(
                self.store,
                project_id=self.project.project_id,
                source_id=source_id,
                source_hash=raw_hash,
                source_authority=str(authority),
                subject_entity_id=subject_entity_id,
                text=extracted.text,
                roles=roles,
            )
            compile_explicit_story_state(
                self.store,
                project_id=self.project.project_id,
                source_id=source_id,
                source_hash=raw_hash,
                source_authority=str(authority),
                subject_entity_id=subject_entity_id,
                text=extracted.text,
                roles=roles,
            )
            summary.readable_documents += 1
            if existing is None:
                summary.new_documents += 1
            else:
                summary.changed_documents += 1
            for role in roles:
                if role.confidence >= 0.5:
                    role_counter[role.role.value] += 1

        missing = self.store.mark_missing_sources(self.project.project_id, present_paths)
        summary.removed_documents = len(missing)
        self.store.supersede_ungrounded_compiler_claims(self.project.project_id)
        reconcile_explicit_story_state(self.store)
        hydrate_writer_state(self.project, self.store)
        summary.role_counts = dict(sorted(role_counter.items()))
        detect_claim_conflicts(self.store)
        manuscripts = self.store.sources_for_role(SourceRole.MANUSCRIPT.value, minimum_confidence=0.55)
        summary.likely_manuscripts = [
            {"source_id": row["source_id"], "path": row["relative_path"], "confidence": row["confidence"]}
            for row in manuscripts[:10]
        ]
        self.store.commit()
        return summary


def open_and_ingest(root: str | Path) -> tuple[StoryProject, ProjectUnderstanding]:
    project = StoryProject.open(root)
    with StoryStore(project.cache_path) as store:
        understanding = ProjectIngestor(project, store).ingest()
    return project, understanding
