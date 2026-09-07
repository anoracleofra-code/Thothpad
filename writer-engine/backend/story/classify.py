from __future__ import annotations

import re
from pathlib import PurePosixPath

from backend.story.adapters.base import ExtractedDocument, SourceCandidate
from backend.story.authority import AuthorityStatus, SourceRole
from backend.story.sources import SourceRoleHint

_CHAPTER = re.compile(r"\b(?:chapter|chap\.?|prologue|epilogue)\b", re.IGNORECASE)
_CHARACTER_HEADINGS = re.compile(
    r"\b(?:appearance|personality|relationships?|goals?|wants?|secrets?|voice|backstory|history)\b",
    re.IGNORECASE,
)
_WORLD_HEADINGS = re.compile(
    r"\b(?:geography|government|culture|religion|economy|history|climate|population|language|magic)\b",
    re.IGNORECASE,
)
_OUTLINE_WORDS = re.compile(r"\b(?:outline|beat|act\s+[ivx0-9]+|scene\s+\d+|plot\s+point)\b", re.IGNORECASE)
_TIMELINE_WORDS = re.compile(r"\b(?:timeline|chronology|year\s+\d+|before|after|concurrent)\b", re.IGNORECASE)
_RESEARCH_WORDS = re.compile(r"\b(?:sources?|bibliography|research|references?|citation|doi|isbn)\b", re.IGNORECASE)
_ARCHIVE_WORDS = re.compile(r"\b(?:archived|obsolete|superseded|old\s+draft|deprecated)\b", re.IGNORECASE)
_PROVISIONAL_WORDS = re.compile(r"\b(?:maybe|possibly|idea|brainstorm|tentative|tbd|open question)\b", re.IGNORECASE)
_LOCKED_WORDS = re.compile(r"\b(?:locked canon|confirmed canon|author locked|final canon)\b", re.IGNORECASE)


def _bounded_excerpt(extracted: ExtractedDocument) -> str:
    heading_text = "\n".join(structure.title for structure in extracted.structures[:40])
    return (heading_text + "\n" + extracted.text[:24_000]).strip()


def classify_source(candidate: SourceCandidate, extracted: ExtractedDocument) -> list[SourceRoleHint]:
    """Return role hints. Paths are weak evidence only and never authority."""

    text = _bounded_excerpt(extracted)
    folded_path = candidate.relative_path.casefold()
    stem = PurePosixPath(candidate.relative_path).stem
    hints: dict[SourceRole, SourceRoleHint] = {}

    def add(role: SourceRole, confidence: float, reason: str) -> None:
        previous = hints.get(role)
        if previous is None or confidence > previous.confidence:
            hints[role] = SourceRoleHint(role=role, confidence=min(max(confidence, 0.0), 1.0), reason=reason)

    chapter_signals = len(_CHAPTER.findall(text[:12_000]))
    prose_lines = [line for line in extracted.text.splitlines()[:250] if line.strip()]
    dialogue_lines = sum(('"' in line or "“" in line or "”" in line) for line in prose_lines)
    long_paragraphs = sum(len(line.split()) >= 20 for line in prose_lines)
    if chapter_signals or (long_paragraphs >= 5 and dialogue_lines >= 2):
        add(SourceRole.MANUSCRIPT, 0.72 + min(chapter_signals, 3) * 0.06, "chapter/prose structure")
    if _OUTLINE_WORDS.search(text):
        add(SourceRole.OUTLINE, 0.72, "outline/beat language")
        add(SourceRole.PLOT_REFERENCE, 0.58, "plot structure language")
    if len(_CHARACTER_HEADINGS.findall(text)) >= 2:
        add(SourceRole.CHARACTER_REFERENCE, 0.82, "multiple character-profile headings")
    if len(_WORLD_HEADINGS.findall(text)) >= 2:
        add(SourceRole.WORLD_REFERENCE, 0.78, "multiple world/reference headings")
    if _TIMELINE_WORDS.search(text):
        add(SourceRole.TIMELINE_REFERENCE, 0.58, "chronology language")
    if _RESEARCH_WORDS.search(text):
        add(SourceRole.RESEARCH, 0.62, "research/reference language")
    if _ARCHIVE_WORDS.search(text) or any(part in {"archive", "archived"} for part in PurePosixPath(folded_path).parts):
        add(SourceRole.ARCHIVE, 0.68, "archive signal in content/path")
    if any(word in stem.casefold() for word in ("character", "cast", "people", "person")):
        add(SourceRole.CHARACTER_REFERENCE, 0.55, "filename hint")
    if any(word in stem.casefold() for word in ("world", "setting", "lore", "bible")):
        add(SourceRole.WORLD_REFERENCE, 0.55, "filename hint")
    if any(word in stem.casefold() for word in ("plot", "outline", "beats")):
        add(SourceRole.PLOT_REFERENCE, 0.55, "filename hint")
    if any(word in stem.casefold() for word in ("note", "idea", "brainstorm")):
        add(SourceRole.AUTHOR_NOTES, 0.55, "filename hint")

    if not hints:
        add(SourceRole.UNKNOWN, 1.0, "insufficient evidence")
    return sorted(hints.values(), key=lambda item: (-item.confidence, item.role.value))


def infer_default_authority(
    roles: list[SourceRoleHint],
    extracted: ExtractedDocument,
    *,
    override: str | None = None,
) -> AuthorityStatus:
    if override:
        return AuthorityStatus(override)
    text = extracted.text[:32_000]
    if _LOCKED_WORDS.search(text):
        return AuthorityStatus.CONFIRMED_CANON
    if _ARCHIVE_WORDS.search(text):
        return AuthorityStatus.ARCHIVED
    if _PROVISIONAL_WORDS.search(text):
        return AuthorityStatus.PROVISIONAL
    role_set = {hint.role for hint in roles if hint.confidence >= 0.6}
    if SourceRole.MANUSCRIPT in role_set:
        return AuthorityStatus.MANUSCRIPT_OBSERVED
    if SourceRole.OUTLINE in role_set or SourceRole.AUTHOR_NOTES in role_set:
        return AuthorityStatus.AUTHOR_INTENT
    return AuthorityStatus.PROVISIONAL
