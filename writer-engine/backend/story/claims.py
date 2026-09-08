from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from backend.story.authority import AuthorityStatus, SourceRole
from backend.story.sources import SourceRoleHint
from backend.story.store import StoryStore

_FIELD = re.compile(r"(?m)^(?:[-*]\s*)?(?:\*\*)?([A-Za-z][A-Za-z0-9 /_'’-]{1,48})(?:\*\*)?\s*:\s*(.+?)\s*$")
_EMPTY_VALUE = re.compile(r"^(?:tbd|unknown|open|n/?a|none yet|\?)$", re.IGNORECASE)
_IGNORED_FIELDS = {
    "status",
    "source",
    "sources",
    "notes",
    "note",
    "created",
    "updated",
    "tags",
}


@dataclass(slots=True, frozen=True)
class ClaimEvidenceInput:
    source_id: str
    start_offset: int
    end_offset: int
    quote: str
    source_hash: str
    story_unit_id: str | None = None
    evidence_type: str = "source_span"
    weight: float = 1.0


def _predicate(field: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", field.casefold()).strip("_")
    return value[:64] or "attribute"


def claim_status_from_source(source_authority: str, value: str) -> AuthorityStatus:
    if _EMPTY_VALUE.fullmatch(value.strip()):
        return AuthorityStatus.OPEN
    try:
        authority = AuthorityStatus(source_authority)
    except ValueError:
        return AuthorityStatus.PROVISIONAL
    if authority in {
        AuthorityStatus.AUTHOR_LOCKED,
        AuthorityStatus.CONFIRMED_CANON,
        AuthorityStatus.MANUSCRIPT_OBSERVED,
        AuthorityStatus.COMPILED_CANON,
    }:
        return AuthorityStatus.COMPILED_CANON
    if authority == AuthorityStatus.ARCHIVED:
        return AuthorityStatus.ARCHIVED
    if authority == AuthorityStatus.AUTHOR_INTENT:
        return AuthorityStatus.AUTHOR_INTENT
    return AuthorityStatus.PROVISIONAL


def explicit_field_claim_key(
    *,
    source_id: str,
    subject_entity_id: str | None,
    predicate: str,
    value: str,
    namespace: str = "profile",
) -> str:
    normalized_value = " ".join(value.casefold().split())
    return f"thothpad-{namespace}-claim:{source_id}:{subject_entity_id or ''}:{predicate}:{normalized_value}"


def create_claim(
    store: StoryStore,
    *,
    project_id: str,
    subject_entity_id: str | None,
    predicate: str,
    literal_value: Any = None,
    object_entity_id: str | None = None,
    status: AuthorityStatus = AuthorityStatus.PROVISIONAL,
    branch_id: str = "mainline",
    scope_id: str | None = None,
    confidence: float = 1.0,
    created_by: str = "compiler",
    evidence: ClaimEvidenceInput | None = None,
    stable_key: str | None = None,
) -> str:
    claim_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            stable_key or f"thothpad-claim:{project_id}:{subject_entity_id}:{predicate}:{literal_value}:{branch_id}",
        )
    )
    existing = next(
        iter(store.rows("SELECT claim_id,created_by FROM claims WHERE claim_id=?", (claim_id,))),
        None,
    )
    if existing is None:
        store.add_claim(
            {
                "claim_id": claim_id,
                "project_id": project_id,
                "subject_entity_id": subject_entity_id,
                "predicate": predicate,
                "object_entity_id": object_entity_id,
                "literal_value": literal_value,
                "status": status,
                "branch_id": branch_id,
                "scope_id": scope_id,
                "confidence": confidence,
                "created_by": created_by,
            }
        )
    elif existing["created_by"] == "compiler" and created_by == "compiler":
        # Deterministic compiler records are a rebuildable view. Refresh their
        # derived authority/value when a source override changes, or reactivate
        # a previously superseded claim when the exact fact returns.
        store.connection.execute(
            """
            UPDATE claims
            SET subject_entity_id=?,predicate=?,object_entity_id=?,literal_value_json=?,
                status=?,branch_id=?,scope_id=?,confidence=?
            WHERE claim_id=? AND created_by='compiler'
            """,
            (
                subject_entity_id,
                predicate,
                object_entity_id,
                json.dumps(literal_value, ensure_ascii=False, separators=(",", ":")),
                str(AuthorityStatus(status)),
                branch_id,
                scope_id,
                float(confidence),
                claim_id,
            ),
        )
    if evidence is not None:
        already = next(
            iter(
                store.rows(
                    """
                    SELECT evidence_id FROM claim_evidence
                    WHERE claim_id=? AND source_id=? AND start_offset=? AND end_offset=?
                    """,
                    (claim_id, evidence.source_id, evidence.start_offset, evidence.end_offset),
                )
            ),
            None,
        )
        if already is None:
            store.add_claim_evidence(
                {
                    "claim_id": claim_id,
                    "source_id": evidence.source_id,
                    "story_unit_id": evidence.story_unit_id,
                    "start_offset": evidence.start_offset,
                    "end_offset": evidence.end_offset,
                    "quote_hash": hashlib.sha256(evidence.quote.encode("utf-8")).hexdigest(),
                    "source_hash": evidence.source_hash,
                    "evidence_type": evidence.evidence_type,
                    "weight": evidence.weight,
                }
            )
        else:
            store.connection.execute(
                """
                UPDATE claim_evidence
                SET story_unit_id=?,quote_hash=?,source_hash=?,evidence_type=?,weight=?,stale=0
                WHERE evidence_id=?
                """,
                (
                    evidence.story_unit_id,
                    hashlib.sha256(evidence.quote.encode("utf-8")).hexdigest(),
                    evidence.source_hash,
                    evidence.evidence_type,
                    float(evidence.weight),
                    already["evidence_id"],
                ),
            )
    return claim_id


def extract_profile_claims(
    store: StoryStore,
    *,
    project_id: str,
    source_id: str,
    source_hash: str,
    source_authority: str,
    subject_entity_id: str | None,
    text: str,
    roles: list[SourceRoleHint],
) -> list[str]:
    """Compile explicit `Field: value` records; never invent unstated facts."""

    if subject_entity_id is None:
        return []
    profile_confidence = max(
        (hint.confidence for hint in roles if hint.role == SourceRole.CHARACTER_REFERENCE),
        default=0.0,
    )
    if profile_confidence < 0.65:
        return []

    active: list[str] = []
    for match in _FIELD.finditer(text):
        field = match.group(1).strip()
        value = match.group(2).strip()
        if field.casefold() in _IGNORED_FIELDS or not value or len(value) > 2_000:
            continue
        claim_id = create_claim(
            store,
            project_id=project_id,
            subject_entity_id=subject_entity_id,
            predicate=_predicate(field),
            literal_value=value,
            status=claim_status_from_source(source_authority, value),
            confidence=profile_confidence,
            created_by="compiler",
            evidence=ClaimEvidenceInput(
                source_id=source_id,
                start_offset=match.start(),
                end_offset=match.end(),
                quote=match.group(0),
                source_hash=source_hash,
            ),
            stable_key=explicit_field_claim_key(
                source_id=source_id,
                subject_entity_id=subject_entity_id,
                predicate=_predicate(field),
                value=value,
            ),
        )
        active.append(claim_id)
    return active


def detect_claim_conflicts(store: StoryStore, *, subject_entity_id: str | None = None) -> list[str]:
    conditions = "WHERE c.branch_id='mainline'"
    params: tuple[Any, ...] = ()
    if subject_entity_id:
        conditions += " AND c.subject_entity_id=?"
        params = (subject_entity_id,)
    rows = list(
        store.rows(
            f"""
            SELECT c.claim_id,c.subject_entity_id,c.predicate,c.literal_value_json,c.status
            FROM claims c {conditions}
            ORDER BY c.subject_entity_id,c.predicate,c.claim_id
            """,  # noqa: S608 - fixed optional condition only
            params,
        )
    )
    groups: dict[tuple[str | None, str], list[Any]] = {}
    for row in rows:
        groups.setdefault((row["subject_entity_id"], row["predicate"]), []).append(row)

    conflicts: list[str] = []
    for (entity_id, predicate), claims in groups.items():
        live = [
            row
            for row in claims
            if row["status"] not in {AuthorityStatus.SUPERSEDED, AuthorityStatus.ARCHIVED, AuthorityStatus.OPEN}
        ]
        values = {row["literal_value_json"] for row in live if row["literal_value_json"] is not None}
        if len(values) <= 1 or len(live) <= 1:
            continue
        conflict_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"thothpad-conflict:{entity_id}:{predicate}:{'|'.join(sorted(values))}")
        )
        authoritative = sum(
            row["status"]
            in {
                AuthorityStatus.AUTHOR_LOCKED,
                AuthorityStatus.CONFIRMED_CANON,
                AuthorityStatus.MANUSCRIPT_OBSERVED,
                AuthorityStatus.COMPILED_CANON,
            }
            for row in live
        )
        severity = "high" if authoritative >= 2 else "review"
        store.connection.execute(
            """
            INSERT INTO story_conflicts(conflict_id,type,severity,status,resolution,metadata_json)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(conflict_id) DO UPDATE SET
                severity=excluded.severity,
                status=CASE
                    WHEN story_conflicts.status='SUPERSEDED' THEN 'OPEN'
                    ELSE story_conflicts.status
                END
            """,
            (conflict_id, "claim_value", severity, "OPEN", "", "{}"),
        )
        for row in live:
            store.connection.execute(
                "INSERT OR IGNORE INTO conflict_claims(conflict_id,claim_id) VALUES(?,?)",
                (conflict_id, row["claim_id"]),
            )
        conflicts.append(conflict_id)
    if conflicts:
        placeholders = ",".join("?" for _ in conflicts)
        store.connection.execute(
            f"""
            UPDATE story_conflicts SET status='SUPERSEDED'
            WHERE type='claim_value' AND status='OPEN'
              AND conflict_id NOT IN ({placeholders})
            """,  # noqa: S608 - placeholders only
            conflicts,
        )
    else:
        store.connection.execute(
            "UPDATE story_conflicts SET status='SUPERSEDED' WHERE type='claim_value' AND status='OPEN'"
        )
    return conflicts
