from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from backend.story.sources import ExtractedStructure, LinkHint, SourceRoleHint


@dataclass(slots=True, frozen=True)
class SourceCandidate:
    path: Path
    relative_path: str
    size: int
    mtime_ns: int


@dataclass(slots=True)
class ExtractedDocument:
    text: str
    structures: list[ExtractedStructure]
    links: list[LinkHint]
    metadata: dict[str, object]


class SourceAdapter(ABC):
    name = "base"
    extensions: frozenset[str] = frozenset()

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() in self.extensions

    @abstractmethod
    def extract(self, candidate: SourceCandidate) -> ExtractedDocument:
        raise NotImplementedError

    def suggest_roles(self, candidate: SourceCandidate, extracted: ExtractedDocument) -> list[SourceRoleHint]:
        del candidate, extracted
        return []
