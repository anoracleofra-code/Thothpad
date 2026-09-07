from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.context import ContextCompiler, EpistemicMode
from backend.story.project import StoryProject
from backend.story.store import StoryStore


def _decode_claim(store: StoryStore, claim_id: str | None) -> dict[str, Any] | None:
    if not claim_id:
        return None
    grounded = store.claim_with_evidence(claim_id)
    if grounded is None:
        return None
    claim = dict(grounded["claim"])
    claim["literal_value"] = StoryStore.decode_json(claim.pop("literal_value_json", None), None)
    claim["qualifiers"] = StoryStore.decode_json(claim.pop("qualifiers_json", None), {})
    claim["evidence"] = [dict(item) for item in grounded["evidence"]]
    return claim


@dataclass(frozen=True, slots=True)
class StoryPosition:
    story_unit_id: str
    source_id: str
    path: str
    source_order: int | None
    ordinal: int
    start_offset: int
    end_offset: int


class ReaderIntelligence:
    """Deterministic reader-position reasoning over tracked Story State.

    The class intentionally reports *tracked* reader information and evidence.
    Missing setup is reported as untracked, never as proof that the manuscript
    itself failed to establish something.
    """

    def __init__(self, project: StoryProject, store: StoryStore) -> None:
        self.project = project
        self.store = store
        self.context = ContextCompiler(project, store)
        self._manuscript_order = {
            path.casefold(): index for index, path in enumerate(project.active_manuscripts())
        }

    def _resolve_entity(self, name: str) -> dict[str, Any] | None:
        matches = self.store.resolve_entities(name.strip())
        return dict(matches[0]) if matches else None

    def _reader_state_through(self, story_unit_id: str, *, branch_id: str) -> list[dict[str, Any]]:
        target = self._position(story_unit_id, branch_id=branch_id)
        visible: list[dict[str, Any]] = []
        for row in self.store.rows(
            "SELECT * FROM reader_state WHERE branch_id=? ORDER BY rowid",
            (branch_id,),
        ):
            item = dict(row)
            unit_id = str(item.get("story_unit_id") or "") or None
            if unit_id and not self._unit_at_or_before(unit_id, target):
                continue
            visible.append(item)
        return visible

    def _position(self, story_unit_id: str, *, branch_id: str = "mainline") -> StoryPosition:
        row = next(
            iter(
                self.store.rows(
                    """
                    SELECT u.story_unit_id,u.source_id,u.ordinal,u.start_offset,u.end_offset,
                           s.relative_path
                    FROM story_units u
                    LEFT JOIN sources s ON s.source_id=u.source_id
                    WHERE u.story_unit_id=? AND u.branch_id=?
                    """,
                    (story_unit_id, branch_id),
                )
            ),
            None,
        )
        if row is None:
            raise KeyError("story unit not found")
        path = str(row["relative_path"] or "")
        return StoryPosition(
            story_unit_id=str(row["story_unit_id"]),
            source_id=str(row["source_id"] or ""),
            path=path,
            source_order=self._manuscript_order.get(path.casefold()),
            ordinal=int(row["ordinal"] or 0),
            start_offset=int(row["start_offset"] or 0),
            end_offset=int(row["end_offset"] or 0),
        )

    def _unit_at_or_before(self, candidate_id: str | None, target: StoryPosition) -> bool:
        if not candidate_id:
            return True
        try:
            candidate = self._position(candidate_id)
        except KeyError:
            return False
        if candidate.source_id == target.source_id:
            return candidate.ordinal <= target.ordinal
        if candidate.source_order is None or target.source_order is None:
            return False
        return candidate.source_order < target.source_order

    def _evidence_position(self, evidence: dict[str, Any], target: StoryPosition) -> str:
        """Classify exact claim evidence relative to a reader cutoff."""

        story_unit_id = str(evidence.get("story_unit_id") or "")
        if story_unit_id:
            try:
                position = self._position(story_unit_id)
            except KeyError:
                return "unresolved"
            if position.source_id == target.source_id:
                if position.ordinal < target.ordinal:
                    return "prior"
                if position.ordinal == target.ordinal:
                    return "at_reveal"
                return "later"
            if position.source_order is None or target.source_order is None:
                return "unresolved"
            return "prior" if position.source_order < target.source_order else "later"

        source_id = str(evidence.get("source_id") or "")
        if not source_id:
            return "unresolved"
        source = self.store.source(source_id)
        if source is None:
            return "unresolved"
        roles = {
            str(role["role"])
            for role in self.store.source_roles(source_id)
            if float(role["confidence"]) >= 0.5
        }
        if "manuscript" not in roles:
            return "reference"
        path = str(source["relative_path"] or "")
        if source_id == target.source_id:
            start = int(evidence.get("start_offset") or 0)
            if start < target.start_offset:
                return "prior"
            if start < target.end_offset:
                return "at_reveal"
            return "later"
        source_order = self._manuscript_order.get(path.casefold())
        if source_order is None or target.source_order is None:
            return "unresolved"
        return "prior" if source_order < target.source_order else "later"

    def reader_expectations_at(
        self,
        story_unit_id: str,
        *,
        branch_id: str = "mainline",
        limit: int = 100,
    ) -> dict[str, Any]:
        target = self._position(story_unit_id, branch_id=branch_id)
        maximum = max(1, min(int(limit), 500))
        questions: list[dict[str, Any]] = []
        promises: list[dict[str, Any]] = []
        excluded_future = 0
        for row in self.store.rows(
            """
            SELECT * FROM promise_items
            WHERE branch_id=? AND state NOT IN ('RESOLVED','PAID','ABANDONED','SUPERSEDED')
            ORDER BY rowid
            """,
            (branch_id,),
        ):
            item = dict(row)
            opened_at = str(item.get("opened_at") or "") or None
            if opened_at and not self._unit_at_or_before(opened_at, target):
                excluded_future += 1
                continue
            item["metadata"] = StoryStore.decode_json(item.pop("metadata_json", None), {})
            item["evidence_claim"] = _decode_claim(self.store, item.get("evidence_claim_id"))
            if item.get("item_type") == "READER_QUESTION":
                questions.append(item)
            elif item.get("item_type") == "DRAMATIC_PROMISE":
                promises.append(item)
            if len(questions) + len(promises) >= maximum:
                break
        return {
            "story_unit_id": story_unit_id,
            "reader_questions": questions,
            "dramatic_promises": promises,
            "expectation_count": len(questions) + len(promises),
            "excluded_future_items": excluded_future,
            "diagnostic_only": True,
        }

    def reveal_fairness(
        self,
        story_unit_id: str,
        *,
        claim_id: str | None = None,
        branch_id: str = "mainline",
        limit: int = 100,
    ) -> dict[str, Any]:
        target = self._position(story_unit_id, branch_id=branch_id)
        maximum = max(1, min(int(limit), 500))
        reveal_rows = list(
            self.store.rows(
                """
                SELECT * FROM reader_state
                WHERE branch_id=? AND story_unit_id=? AND claim_id IS NOT NULL
                ORDER BY rowid
                """,
                (branch_id, story_unit_id),
            )
        )
        if claim_id:
            reveal_rows = [row for row in reveal_rows if str(row["claim_id"]) == claim_id]

        findings: list[dict[str, Any]] = []
        for row in reveal_rows[:maximum]:
            claim = _decode_claim(self.store, str(row["claim_id"]))
            if claim is None:
                continue
            buckets: dict[str, list[dict[str, Any]]] = {
                "prior": [],
                "at_reveal": [],
                "later": [],
                "reference": [],
                "unresolved": [],
            }
            for evidence in claim.get("evidence", []):
                bucket = self._evidence_position(evidence, target)
                buckets.setdefault(bucket, []).append(evidence)

            if buckets["prior"]:
                status = "TRACKED_PRIOR_SETUP"
                observation = "Tracked manuscript evidence for this reveal exists before the reader reaches this unit."
            elif buckets["at_reveal"]:
                status = "REVEAL_EVIDENCE_ONLY"
                observation = (
                    "Tracked manuscript evidence begins in the reveal unit; no earlier tracked setup was found."
                )
            elif buckets["reference"]:
                status = "REFERENCE_ONLY"
                observation = (
                    "The claim is grounded in project reference material, but no earlier tracked manuscript setup "
                    "was found."
                )
            else:
                status = "SETUP_UNTRACKED"
                observation = "No current tracked manuscript evidence establishes setup before this reveal."

            findings.append(
                {
                    "reader_state_id": row["reader_state_id"],
                    "claim_id": row["claim_id"],
                    "claim": claim,
                    "status": status,
                    "observation": observation,
                    "prior_setup_evidence": buckets["prior"][:12],
                    "reveal_evidence": buckets["at_reveal"][:12],
                    "reference_evidence": buckets["reference"][:12],
                    "later_evidence_count": len(buckets["later"]),
                    "unresolved_evidence_count": len(buckets["unresolved"]),
                    "interpretation_limit": (
                        "This audits tracked evidence placement only. Absence of tracked setup is not proof that "
                        "the prose is unfair."
                    ),
                }
            )
        return {
            "story_unit_id": story_unit_id,
            "diagnostic_only": True,
            "absence_means_untracked": True,
            "reveal_count": len(findings),
            "findings": findings,
        }

    def dramatic_irony_at(
        self,
        story_unit_id: str,
        *,
        character: str,
        branch_id: str = "mainline",
        limit: int = 100,
    ) -> dict[str, Any]:
        target = self._position(story_unit_id, branch_id=branch_id)
        character_row = self._resolve_entity(character)
        if character_row is None:
            return {
                "story_unit_id": story_unit_id,
                "character": character,
                "dramatic_irony": [],
                "diagnostic_only": True,
            }
        character_id = str(character_row["entity_id"])

        reader_claims = {
            str(item.get("claim_id"))
            for item in self._reader_state_through(story_unit_id, branch_id=branch_id)
            if item.get("claim_id") and str(item.get("state") or "").upper() in {"KNOWS", "REVEALED", "AWARE"}
        }
        character_claims: set[str] = set()
        for row in self.store.rows(
            """
            SELECT claim_id,state,acquired_at FROM knowledge_state
            WHERE character_id=? AND branch_id=? ORDER BY rowid
            """,
            (character_id, branch_id),
        ):
            acquired_at = str(row["acquired_at"] or "") or None
            if acquired_at and not self._unit_at_or_before(acquired_at, target):
                continue
            if str(row["state"]).upper() in {"KNOWS", "BELIEVES", "SUSPECTS"}:
                character_claims.add(str(row["claim_id"]))

        differences: list[dict[str, Any]] = []
        for reader_claim in sorted(reader_claims - character_claims):
            claim = _decode_claim(self.store, reader_claim)
            if claim is None:
                continue
            differences.append(
                {
                    "claim_id": reader_claim,
                    "claim": claim,
                    "reader_state": "AVAILABLE",
                    "character_state": "NOT_TRACKED_AS_KNOWN_OR_SUSPECTED",
                    "observation": (
                        "The tracked reader state includes this claim by the cutoff, while the selected character's "
                        "tracked knowledge/belief state does not."
                    ),
                }
            )
            if len(differences) >= max(1, min(int(limit), 500)):
                break
        return {
            "story_unit_id": story_unit_id,
            "character": character_row.get("canonical_name", character),
            "character_id": character_id,
            "dramatic_irony": differences,
            "diagnostic_only": True,
            "absence_means_untracked": True,
        }

    def cold_reader_at(
        self,
        story_unit_id: str,
        *,
        prompt: str = "What can a reader know, wonder, and reasonably expect here?",
        branch_id: str = "mainline",
        maximum_chars: int = 40_000,
    ) -> dict[str, Any]:
        target = self._position(story_unit_id, branch_id=branch_id)
        compiled = self.context.compile(
            prompt=prompt,
            mode=EpistemicMode.COLD_READER,
            maximum_chars=max(1_000, min(int(maximum_chars), 100_000)),
            active_story_unit=story_unit_id,
            active_source_path=target.path,
            active_document_end=target.end_offset,
            branch_id=branch_id,
        )
        expectations = self.reader_expectations_at(story_unit_id, branch_id=branch_id)
        fairness = self.reveal_fairness(story_unit_id, branch_id=branch_id)
        return {
            "story_unit_id": story_unit_id,
            "mode": "cold_reader",
            "evidence": compiled.retrieved(),
            "reader_state": compiled.reader_state,
            "reader_questions": expectations["reader_questions"],
            "dramatic_promises": expectations["dramatic_promises"],
            "reveal_fairness": fairness,
            "inspector": compiled.inspector(),
            "hard_boundary": {
                "source_path": target.path,
                "visible_end": target.end_offset,
                "writer_owned_cross_file_order": target.source_order is not None,
            },
        }
