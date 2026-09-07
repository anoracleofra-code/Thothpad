from __future__ import annotations

from enum import StrEnum


class SourceRole(StrEnum):
    MANUSCRIPT = "manuscript"
    OUTLINE = "outline"
    CHARACTER_REFERENCE = "character_reference"
    WORLD_REFERENCE = "world_reference"
    PLOT_REFERENCE = "plot_reference"
    TIMELINE_REFERENCE = "timeline_reference"
    RESEARCH = "research"
    AUTHOR_NOTES = "author_notes"
    STYLE_REFERENCE = "style_reference"
    ALTERNATE = "alternate"
    ARCHIVE = "archive"
    UNKNOWN = "unknown"


class AuthorityStatus(StrEnum):
    AUTHOR_LOCKED = "AUTHOR_LOCKED"
    CONFIRMED_CANON = "CONFIRMED_CANON"
    MANUSCRIPT_OBSERVED = "MANUSCRIPT_OBSERVED"
    AUTHOR_INTENT = "AUTHOR_INTENT"
    COMPILED_CANON = "COMPILED_CANON"
    PROVISIONAL = "PROVISIONAL"
    INFERENCE = "INFERENCE"
    SUGGESTION = "SUGGESTION"
    OPEN = "OPEN"
    CONTESTED = "CONTESTED"
    SUPERSEDED = "SUPERSEDED"
    ARCHIVED = "ARCHIVED"


class KnowledgeStatus(StrEnum):
    KNOWS = "KNOWS"
    BELIEVES = "BELIEVES"
    SUSPECTS = "SUSPECTS"
    DISBELIEVES = "DISBELIEVES"
    CONCEALS = "CONCEALS"
    UNAWARE = "UNAWARE"


class BranchStatus(StrEnum):
    ACTIVE = "ACTIVE"
    MERGED = "MERGED"
    DISCARDED = "DISCARDED"
    STALE_NEEDS_REBASE = "STALE_NEEDS_REBASE"


class ThreadStatus(StrEnum):
    OPEN = "OPEN"
    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"
    RESOLVED = "RESOLVED"
    ABANDONED = "ABANDONED"
    INTENTIONALLY_UNRESOLVED = "INTENTIONALLY_UNRESOLVED"


AUTHORITATIVE_STATUSES = frozenset(
    {
        AuthorityStatus.AUTHOR_LOCKED,
        AuthorityStatus.CONFIRMED_CANON,
        AuthorityStatus.MANUSCRIPT_OBSERVED,
        AuthorityStatus.COMPILED_CANON,
    }
)

MODEL_WRITABLE_STATUSES = frozenset(
    {AuthorityStatus.INFERENCE, AuthorityStatus.SUGGESTION}
)


def model_may_create(status: AuthorityStatus | str) -> bool:
    """Return whether an untrusted model may originate a record at this status."""

    try:
        normalized = AuthorityStatus(status)
    except ValueError:
        return False
    return normalized in MODEL_WRITABLE_STATUSES


def is_authoritative(status: AuthorityStatus | str) -> bool:
    try:
        return AuthorityStatus(status) in AUTHORITATIVE_STATUSES
    except ValueError:
        return False
