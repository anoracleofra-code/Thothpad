from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.story.authority import AuthorityStatus, SourceRole


@dataclass(slots=True, frozen=True)
class SourceRoleHint:
    role: SourceRole
    confidence: float
    reason: str = ""


@dataclass(slots=True)
class SourceDocument:
    source_id: str
    project_id: str
    relative_path: str
    display_name: str
    format: str
    content_hash: str
    size: int
    mtime_ns: int
    readability_status: str = "readable"
    roles: list[SourceRoleHint] = field(default_factory=list)
    authority_default: AuthorityStatus = AuthorityStatus.PROVISIONAL
    branch_scope: str = "mainline"
    story_scope: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    adapter_origin: str = "generic"


@dataclass(slots=True, frozen=True)
class SourceChunk:
    chunk_id: str
    source_id: str
    ordinal: int
    heading: str
    start_offset: int
    end_offset: int
    text: str
    content_hash: str


@dataclass(slots=True, frozen=True)
class ExtractedStructure:
    kind: str
    title: str
    start_offset: int
    end_offset: int
    level: int = 0


@dataclass(slots=True, frozen=True)
class LinkHint:
    target: str
    label: str
    start_offset: int
    end_offset: int
    kind: str = "document_link"


def project_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()
