from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from backend.story.authority import AuthorityStatus, SourceRole, is_authoritative
from backend.story.branches import branch_freshness, effective_branch_overlays
from backend.story.project import StoryProject
from backend.story.query import StoryQueryEngine
from backend.story.store import StoryStore

_WORD = re.compile(r"[\w'-]{3,}", re.UNICODE)
_STOP = frozenset(
    {
        "about",
        "after",
        "also",
        "and",
        "are",
        "does",
        "from",
        "have",
        "into",
        "know",
        "that",
        "the",
        "their",
        "this",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
    }
)


class EpistemicMode(StrEnum):
    AUTHOR_OMNISCIENT = "author_omniscient"
    CURRENT_POV = "current_pov"
    CHARACTER = "character"
    READER = "reader"
    COLD_READER = "cold_reader"
    MANUSCRIPT_ONLY = "manuscript_only"
    WORLD_REFERENCE_ONLY = "world_reference_only"
    CUSTOM = "custom"


@dataclass(slots=True)
class ContextItem:
    source_id: str
    path: str
    chunk_id: str
    heading: str
    text: str
    start_offset: int
    end_offset: int
    authority: str
    roles: list[str]
    reason: str
    pinned: bool = False

    def to_retrieved(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "text": self.text,
            "source_id": self.source_id,
            "chunk_id": self.chunk_id,
            "heading": self.heading,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "authority": self.authority,
            "roles": self.roles,
            "reason": self.reason,
            "pinned": self.pinned,
        }


@dataclass(slots=True)
class CompiledContext:
    mode: EpistemicMode
    branch_id: str = "mainline"
    items: list[ContextItem] = field(default_factory=list)
    excluded: list[dict[str, str]] = field(default_factory=list)
    story_claims: list[dict[str, Any]] = field(default_factory=list)
    knowledge_state: list[dict[str, Any]] = field(default_factory=list)
    reader_state: list[dict[str, Any]] = field(default_factory=list)
    branch_overlays: list[dict[str, Any]] = field(default_factory=list)
    epistemic_boundary: dict[str, Any] = field(default_factory=dict)
    used_chars: int = 0
    maximum_chars: int = 40_000

    def retrieved(self) -> list[dict[str, Any]]:
        return [item.to_retrieved() for item in self.items]

    def model_state(self) -> dict[str, Any]:
        return {
            "objective_claims": list(self.story_claims),
            "character_knowledge": list(self.knowledge_state),
            "reader_state": list(self.reader_state),
            "branch_overlays": list(self.branch_overlays),
        }

    def inspector(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "branch_id": self.branch_id,
            "budget": {"used_chars": self.used_chars, "maximum_chars": self.maximum_chars},
            "included": [
                {
                    "path": item.path,
                    "heading": item.heading,
                    "authority": item.authority,
                    "roles": item.roles,
                    "reason": item.reason,
                    "pinned": item.pinned,
                }
                for item in self.items
            ],
            "excluded": list(self.excluded),
            "story_claim_count": len(self.story_claims),
            "knowledge_state_count": len(self.knowledge_state),
            "reader_state_count": len(self.reader_state),
            "branch_overlay_count": len(self.branch_overlays),
            "epistemic_boundary": dict(self.epistemic_boundary),
        }


class ContextCompiler:
    def __init__(self, project: StoryProject, store: StoryStore) -> None:
        self.project = project
        self.store = store

    @staticmethod
    def terms(prompt: str, extra: list[str] | None = None) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in [prompt, *(extra or [])]:
            for term in _WORD.findall(value.casefold()):
                if term in _STOP or term in seen:
                    continue
                seen.add(term)
                result.append(term)
                if len(result) >= 48:
                    return result
        return result

    def _roles(self, source_id: str) -> list[str]:
        return [row["role"] for row in self.store.source_roles(source_id) if row["confidence"] >= 0.5]

    def _claim_record(self, claim_id: str) -> dict[str, Any] | None:
        grounded = self.store.claim_with_evidence(claim_id)
        if grounded is None:
            return None
        raw = grounded["claim"]
        subject_name = ""
        subject_id = raw.get("subject_entity_id")
        if subject_id:
            entity = self.store.entity(str(subject_id))
            if entity is not None:
                subject_name = str(entity["canonical_name"])
        evidence: list[dict[str, Any]] = []
        for item in grounded["evidence"][:6]:
            roles = self._roles(item["source_id"])
            evidence.append(
                {
                    "path": item["relative_path"],
                    "story_unit_id": item["story_unit_id"],
                    "start_offset": item["start_offset"],
                    "end_offset": item["end_offset"],
                    "quote_hash": item["quote_hash"],
                    "source_hash": item["source_hash"],
                    "stale": bool(item["stale"]),
                    "evidence_type": item["evidence_type"],
                    "weight": item["weight"],
                    "roles": roles,
                }
            )
        grounding_status = "ungrounded"
        if evidence:
            grounding_status = "current" if any(not item["stale"] for item in evidence) else "stale"
        return {
            "claim_id": raw["claim_id"],
            "subject_entity_id": subject_id,
            "subject": subject_name,
            "predicate": raw["predicate"],
            "value": StoryStore.decode_json(raw.get("literal_value_json"), None),
            "status": raw["status"],
            "confidence": raw["confidence"],
            "qualifiers": StoryStore.decode_json(raw.get("qualifiers_json"), {}),
            "grounding_status": grounding_status,
            "evidence": evidence,
        }

    @staticmethod
    def _claim_has_role(claim: dict[str, Any], allowed: set[str]) -> bool:
        return any(set(item.get("roles", [])) & allowed for item in claim.get("evidence", []))

    def _unit_visible_at(
        self,
        unit_id: str | None,
        *,
        target_path: str,
        target_end: int | None,
        manuscript_positions: dict[str, int],
        target_order: int | None,
    ) -> bool:
        if not unit_id:
            return True
        unit = next(
            iter(
                self.store.rows(
                    """
                    SELECT u.start_offset,u.end_offset,s.relative_path
                    FROM story_units u LEFT JOIN sources s ON s.source_id=u.source_id
                    WHERE u.story_unit_id=? AND u.branch_id='mainline'
                    """,
                    (unit_id,),
                )
            ),
            None,
        )
        if unit is None:
            return False
        path = str(unit["relative_path"] or "")
        if path.casefold() == target_path.casefold():
            return target_end is not None and int(unit["start_offset"]) < target_end
        position = manuscript_positions.get(path.casefold())
        return target_order is not None and position is not None and position < target_order

    @staticmethod
    def _mode_allows(mode: EpistemicMode, roles: list[str]) -> bool:
        role_set = set(roles)
        if mode in {
            EpistemicMode.CURRENT_POV,
            EpistemicMode.CHARACTER,
            EpistemicMode.READER,
            EpistemicMode.COLD_READER,
            EpistemicMode.MANUSCRIPT_ONLY,
        }:
            return SourceRole.MANUSCRIPT in role_set
        if mode == EpistemicMode.WORLD_REFERENCE_ONLY:
            return bool(role_set & {SourceRole.WORLD_REFERENCE, SourceRole.TIMELINE_REFERENCE})
        return True

    def compile(
        self,
        *,
        prompt: str,
        mode: EpistemicMode | str = EpistemicMode.AUTHOR_OMNISCIENT,
        maximum_chars: int = 40_000,
        current_document: str = "",
        active_story_unit: str | None = None,
        active_source_path: str = "",
        active_document_end: int | None = None,
        active_character: str = "",
        branch_id: str = "mainline",
        user_pins: list[str] | None = None,
    ) -> CompiledContext:
        normalized_mode = EpistemicMode(mode)
        normalized_branch = str(branch_id or "mainline").strip() or "mainline"
        if normalized_branch != "mainline":
            freshness = branch_freshness(self.store, normalized_branch)
            branch = next(
                iter(self.store.rows("SELECT status FROM branches WHERE branch_id=?", (normalized_branch,))),
                None,
            )
            if branch is None:
                raise KeyError("branch not found")
            if str(branch["status"]) == "DISCARDED":
                raise ValueError("discarded branch cannot be used as active story context")
        else:
            freshness = None
        maximum_chars = max(1_000, min(int(maximum_chars), 250_000))
        result = CompiledContext(
            mode=normalized_mode,
            branch_id=normalized_branch,
            maximum_chars=maximum_chars,
        )
        extra = [active_character] if active_character else []
        terms = self.terms(prompt, extra)
        pins = {Path(item).as_posix().casefold() for item in (user_pins or [])}

        position_bounded = normalized_mode in {
            EpistemicMode.CURRENT_POV,
            EpistemicMode.CHARACTER,
            EpistemicMode.READER,
            EpistemicMode.COLD_READER,
        }
        target_path = Path(active_source_path).as_posix() if active_source_path else ""
        target_end = active_document_end
        if active_story_unit:
            target_unit = next(
                iter(
                    self.store.rows(
                        """
                        SELECT u.source_id,u.end_offset,s.relative_path
                        FROM story_units u LEFT JOIN sources s ON s.source_id=u.source_id
                        WHERE u.story_unit_id=? AND u.branch_id='mainline'
                        """,
                        (active_story_unit,),
                    )
                ),
                None,
            )
            if target_unit is not None:
                if not target_path:
                    target_path = str(target_unit["relative_path"] or "")
                if target_end is None:
                    target_end = int(target_unit["end_offset"])

        target_folded = target_path.casefold()
        manuscript_order = self.project.active_manuscripts()
        manuscript_positions = {path.casefold(): index for index, path in enumerate(manuscript_order)}
        target_order = manuscript_positions.get(target_folded)
        if position_bounded:
            result.epistemic_boundary = {
                "position_bounded": True,
                "source_path": target_path,
                "visible_end": target_end,
                "cross_file_order": "writer_owned" if target_order is not None else "unresolved",
            }
        if freshness is not None:
            result.epistemic_boundary["branch_freshness"] = {
                "stale": bool(freshness["stale"]),
                "parent_branch": freshness["parent_branch"],
            }

        candidates = self.store.search_chunks(terms, limit=120)
        scored: list[tuple[float, Any, list[str], bool, int | None]] = []
        current_folded = Path(current_document).as_posix().casefold() if current_document else ""
        for row in candidates:
            roles = self._roles(row["source_id"])
            path_folded = row["relative_path"].casefold()
            pinned = path_folded in pins
            if current_folded and (path_folded == current_folded or current_folded.endswith("/" + path_folded)):
                result.excluded.append(
                    {
                        "path": row["relative_path"],
                        "reason": "current document supplied separately at the active epistemic boundary"
                        if position_bounded
                        else "current document supplied separately",
                    }
                )
                continue
            if not self._mode_allows(normalized_mode, roles):
                result.excluded.append({"path": row["relative_path"], "reason": f"excluded by {normalized_mode} mask"})
                continue

            allowed_chars: int | None = None
            if position_bounded and SourceRole.MANUSCRIPT in set(roles) and target_folded:
                if path_folded == target_folded:
                    if target_end is not None:
                        if int(row["start_offset"]) >= target_end:
                            result.excluded.append(
                                {"path": row["relative_path"], "reason": "after active story position"}
                            )
                            continue
                        allowed_chars = max(0, target_end - int(row["start_offset"]))
                else:
                    candidate_order = manuscript_positions.get(path_folded)
                    if target_order is None or candidate_order is None:
                        result.excluded.append(
                            {
                                "path": row["relative_path"],
                                "reason": "cross-file manuscript order unresolved; excluded to prevent future leakage",
                            }
                        )
                        continue
                    if candidate_order > target_order:
                        result.excluded.append(
                            {"path": row["relative_path"], "reason": "later than active story position"}
                        )
                        continue
            authority = row["authority_default"]
            score = 0.0
            score += 8.0 if pinned else 0.0
            score += 2.0 if is_authoritative(authority) else 0.0
            score += 1.0 if SourceRole.MANUSCRIPT in set(roles) else 0.0
            score += max(0.0, min(4.0, -float(row["rank"]))) if row["rank"] is not None else 0.0
            scored.append((score, row, roles, pinned, allowed_chars))

        scored.sort(key=lambda item: (-item[0], item[1]["relative_path"].casefold(), item[1]["ordinal"]))
        used_sources: set[str] = set()
        for score, row, roles, pinned, allowed_chars in scored:
            del score
            remaining = maximum_chars - result.used_chars
            if remaining <= 0:
                result.excluded.append({"path": row["relative_path"], "reason": "context budget exhausted"})
                continue
            text_limit = remaining if allowed_chars is None else min(remaining, allowed_chars)
            text = row["text"][:text_limit]
            if not text:
                continue
            reason = "pinned by writer" if pinned else "task/entity relevance"
            if row["source_id"] not in used_sources and is_authoritative(row["authority_default"]):
                reason += "; authoritative source"
            result.items.append(
                ContextItem(
                    source_id=row["source_id"],
                    path=row["relative_path"],
                    chunk_id=row["chunk_id"],
                    heading=row["heading"],
                    text=text,
                    start_offset=row["start_offset"],
                    end_offset=min(row["start_offset"] + len(text), row["end_offset"]),
                    authority=row["authority_default"],
                    roles=roles,
                    reason=reason,
                    pinned=pinned,
                )
            )
            used_sources.add(row["source_id"])
            result.used_chars += len(text)
            if len(result.items) >= 24:
                break

        mentioned_entities: list[str] = []
        prompt_folded = prompt.casefold()
        for alias in self.store.rows("SELECT entity_id,alias FROM entity_aliases ORDER BY length(alias) DESC"):
            if alias["alias"].casefold() in prompt_folded:
                mentioned_entities.append(alias["entity_id"])
        active_character_id = ""
        if active_character:
            matches = self.store.resolve_entities(active_character)
            if matches:
                active_character_id = str(matches[0]["entity_id"])
                mentioned_entities.append(active_character_id)

        unique_entities = list(dict.fromkeys(str(item) for item in mentioned_entities))[:12]
        if normalized_mode not in {
            EpistemicMode.CURRENT_POV,
            EpistemicMode.CHARACTER,
            EpistemicMode.READER,
            EpistemicMode.COLD_READER,
        }:
            for entity_id in unique_entities:
                for row in self.store.query_claims(subject_entity_id=entity_id):
                    claim = self._claim_record(str(row["claim_id"]))
                    if claim is None:
                        continue
                    if claim["status"] in {AuthorityStatus.SUPERSEDED, AuthorityStatus.ARCHIVED}:
                        continue
                    if normalized_mode == EpistemicMode.MANUSCRIPT_ONLY and not self._claim_has_role(
                        claim, {SourceRole.MANUSCRIPT.value}
                    ):
                        continue
                    if normalized_mode == EpistemicMode.WORLD_REFERENCE_ONLY and not self._claim_has_role(
                        claim,
                        {SourceRole.WORLD_REFERENCE.value, SourceRole.TIMELINE_REFERENCE.value},
                    ):
                        continue
                    result.story_claims.append(claim)
                    if len(result.story_claims) >= 48:
                        break
                if len(result.story_claims) >= 48:
                    break

        knowledge_characters: list[str] = []
        if normalized_mode in {EpistemicMode.CURRENT_POV, EpistemicMode.CHARACTER}:
            if active_character_id:
                knowledge_characters.append(active_character_id)
        elif normalized_mode == EpistemicMode.AUTHOR_OMNISCIENT:
            for entity_id in unique_entities:
                entity = self.store.entity(entity_id)
                if entity is not None and str(entity["entity_type"]).casefold() == "character":
                    knowledge_characters.append(entity_id)

        for character_id in dict.fromkeys(knowledge_characters):
            character = self.store.entity(character_id)
            character_name = str(character["canonical_name"]) if character is not None else ""
            for row in self.store.rows(
                """
                SELECT * FROM knowledge_state
                WHERE character_id=? AND branch_id='mainline'
                ORDER BY acquired_at,knowledge_id
                """,
                (character_id,),
            ):
                acquired_at = str(row["acquired_at"] or "") or None
                if position_bounded and not self._unit_visible_at(
                    acquired_at,
                    target_path=target_path,
                    target_end=target_end,
                    manuscript_positions=manuscript_positions,
                    target_order=target_order,
                ):
                    continue
                claim = self._claim_record(str(row["claim_id"]))
                if claim is None:
                    continue
                if claim["status"] in {AuthorityStatus.SUPERSEDED, AuthorityStatus.ARCHIVED, AuthorityStatus.OPEN}:
                    continue
                result.knowledge_state.append(
                    {
                        "knowledge_id": row["knowledge_id"],
                        "character_id": character_id,
                        "character": character_name,
                        "state": row["state"],
                        "acquired_at": acquired_at,
                        "confidence": row["confidence"],
                        "claim": claim,
                    }
                )
                if len(result.knowledge_state) >= 48:
                    break

        if normalized_mode in {EpistemicMode.READER, EpistemicMode.COLD_READER}:
            query = StoryQueryEngine(self.project, self.store)
            for row in query.reader_state(through_story_unit=active_story_unit):
                claim_id = str(row.get("claim_id") or "")
                claim = self._claim_record(claim_id) if claim_id else None
                result.reader_state.append(
                    {
                        "reader_state_id": row["reader_state_id"],
                        "state": row["state"],
                        "story_unit_id": row.get("story_unit_id"),
                        "confidence": row["confidence"],
                        "claim": claim,
                    }
                )
                if len(result.reader_state) >= 48:
                    break

        if normalized_branch != "mainline":
            for overlay in effective_branch_overlays(self.store, normalized_branch, maximum=96):
                if position_bounded:
                    overlay_unit = str(overlay.get("payload", {}).get("story_unit_id") or "") or str(
                        overlay.get("fork_story_unit") or ""
                    )
                    if overlay_unit and not self._unit_visible_at(
                        overlay_unit,
                        target_path=target_path,
                        target_end=target_end,
                        manuscript_positions=manuscript_positions,
                        target_order=target_order,
                    ):
                        continue
                result.branch_overlays.append(
                    {
                        "overlay_id": overlay["overlay_id"],
                        "branch_id": overlay["branch_id"],
                        "record_kind": overlay["record_kind"],
                        "record_id": overlay["record_id"],
                        "operation": overlay["operation"],
                        "payload": overlay["payload"],
                        "fork_story_unit": overlay.get("fork_story_unit"),
                        "branch_status": overlay.get("branch_status"),
                        "authority": "BRANCH_ONLY",
                    }
                )
        return result
