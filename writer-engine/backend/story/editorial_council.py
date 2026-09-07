from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from backend.story.audits import NarrativeAuditEngine
from backend.story.project import StoryProject
from backend.story.reader_intelligence import ReaderIntelligence
from backend.story.store import StoryStore

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[\w'-]+", re.UNICODE)


def _finding(
    reviewer: str,
    *,
    code: str,
    signal: str,
    observation: str,
    story_unit_id: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    interpretation_limit: str = "",
) -> dict[str, Any]:
    return {
        "reviewer": reviewer,
        "code": code,
        "signal": signal,
        "story_unit_id": story_unit_id,
        "observation": observation,
        "evidence": list(evidence or []),
        "interpretation_limit": interpretation_limit,
    }


@dataclass(frozen=True, slots=True)
class CouncilSnapshot:
    root_unit: dict[str, Any]
    scope_units: tuple[dict[str, Any], ...]
    audit: dict[str, Any]
    reader_expectations: dict[str, Any]
    reveal_fairness: dict[str, Any]
    conflicts: tuple[dict[str, Any], ...]
    interpretations: tuple[dict[str, Any], ...]
    lens_findings: tuple[dict[str, Any], ...]
    unit_texts: dict[str, str]


def _continuity(snapshot: CouncilSnapshot) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for conflict in snapshot.conflicts:
        findings.append(
            _finding(
                "continuity",
                code="tracked_story_conflict",
                signal="concern",
                story_unit_id=conflict.get("story_unit_id"),
                observation=(
                    f"Tracked {conflict.get('type', 'story')} conflict remains open: "
                    f"{conflict.get('conflict_id', '')}"
                ),
                evidence=conflict.get("claims", []),
                interpretation_limit="This reports a Story State conflict, not an automatic manuscript error.",
            )
        )
    if not findings:
        findings.append(
            _finding(
                "continuity",
                code="no_tracked_conflict",
                signal="support",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation="No open provenance-backed Story State conflict is currently attached to this scope.",
                interpretation_limit="Untracked continuity issues may still exist in prose.",
            )
        )
    return findings


def _character(snapshot: CouncilSnapshot) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for audit_finding in snapshot.audit.get("findings", []):
        if audit_finding.get("code") != "decision_without_tracked_consequence":
            continue
        findings.append(
            _finding(
                "character",
                code="decision_without_tracked_consequence",
                signal="concern",
                story_unit_id=audit_finding.get("story_unit_id"),
                observation=str(audit_finding.get("observation", "")),
                interpretation_limit=str(audit_finding.get("interpretation_limit", "")),
            )
        )
    decisions = snapshot.audit.get("decisions", [])
    if decisions and not findings:
        findings.append(
            _finding(
                "character",
                code="tracked_agency_chain",
                signal="support",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation=(
                    f"This scope contains {len(decisions)} tracked consequential decision(s) with no currently "
                    "unlinked decision flagged by the causal-state audit."
                ),
            )
        )
    if not decisions:
        findings.append(
            _finding(
                "character",
                code="agency_state_untracked",
                signal="observation",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation="No consequential character decisions are currently tracked in this scope.",
                interpretation_limit="This is a coverage note, not evidence that the scene lacks agency.",
            )
        )
    return findings


def _scene_architecture(snapshot: CouncilSnapshot) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for audit_finding in snapshot.audit.get("findings", []):
        if audit_finding.get("code") == "contract_without_tracked_opposition":
            findings.append(
                _finding(
                    "scene_architecture",
                    code="contract_without_tracked_opposition",
                    signal="concern",
                    story_unit_id=snapshot.root_unit["story_unit_id"],
                    observation=str(audit_finding.get("observation", "")),
                    interpretation_limit=str(audit_finding.get("interpretation_limit", "")),
                )
            )
    contracts = snapshot.audit.get("scene_contracts", [])
    opposition = snapshot.audit.get("opposition", [])
    if contracts and opposition and not findings:
        findings.append(
            _finding(
                "scene_architecture",
                code="contract_and_opposition_tracked",
                signal="support",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation="Reviewed scene-contract intent and tracked opposition are both represented in this scope.",
            )
        )
    if not contracts:
        findings.append(
            _finding(
                "scene_architecture",
                code="scene_contract_untracked",
                signal="observation",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation="No reviewed scene contract is currently attached to this scope.",
                interpretation_limit="A contract is optional; its absence is not a defect.",
            )
        )
    return findings


def _cold_reader(snapshot: CouncilSnapshot) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for reveal in snapshot.reveal_fairness.get("findings", []):
        status = str(reveal.get("status", ""))
        signal = "support" if status == "TRACKED_PRIOR_SETUP" else "concern"
        findings.append(
            _finding(
                "cold_reader",
                code=f"reveal_{status.casefold()}",
                signal=signal,
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation=str(reveal.get("observation", "")),
                evidence=list(reveal.get("prior_setup_evidence", []))
                + list(reveal.get("reveal_evidence", [])),
                interpretation_limit=str(reveal.get("interpretation_limit", "")),
            )
        )
    if not findings:
        questions = snapshot.reader_expectations.get("reader_questions", [])
        findings.append(
            _finding(
                "cold_reader",
                code="reader_forward_state",
                signal="observation",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation=f"The tracked reader state carries {len(questions)} open question(s) at this cutoff.",
            )
        )
    return findings


def _line_editor(snapshot: CouncilSnapshot) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for unit in snapshot.scope_units:
        unit_id = str(unit["story_unit_id"])
        text = snapshot.unit_texts.get(unit_id, "")
        sentences = [piece.strip() for piece in _SENTENCE.split(text) if piece.strip()]
        openings: dict[str, list[str]] = {}
        for sentence in sentences:
            words = _WORD.findall(sentence)
            if len(words) < 3:
                continue
            opening = " ".join(word.casefold() for word in words[:3])
            openings.setdefault(opening, []).append(sentence[:180])
        repeated = [(opening, excerpts) for opening, excerpts in openings.items() if len(excerpts) >= 2]
        for opening, excerpts in repeated[:3]:
            findings.append(
                _finding(
                    "line_editor",
                    code="repeated_sentence_opening",
                    signal="concern",
                    story_unit_id=unit_id,
                    observation=f"Multiple sentences begin with the same three-word pattern: “{opening}”.",
                    evidence=[{"excerpt": excerpt} for excerpt in excerpts[:4]],
                    interpretation_limit="This is a surface repetition observation, not an instruction to vary it.",
                )
            )
    if not findings:
        findings.append(
            _finding(
                "line_editor",
                code="no_repeated_opening_hotspot",
                signal="support",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation="No repeated three-word sentence-opening hotspot was detected in the bounded scope.",
                interpretation_limit=(
                    "This reviewer is intentionally narrow; Prose Intelligence remains the full line-analysis system."
                ),
            )
        )
    return findings


def _suspense(snapshot: CouncilSnapshot) -> list[dict[str, Any]]:
    questions = snapshot.reader_expectations.get("reader_questions", [])
    promises = snapshot.reader_expectations.get("dramatic_promises", [])
    ungrounded = [
        item for item in [*questions, *promises] if item.get("evidence_claim_id") and not item.get("evidence_claim")
    ]
    findings: list[dict[str, Any]] = []
    if ungrounded:
        findings.append(
            _finding(
                "suspense",
                code="forward_state_missing_grounding",
                signal="concern",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation=(
                    f"{len(ungrounded)} tracked forward-motion item(s) reference evidence that is no longer grounded."
                ),
            )
        )
    findings.append(
        _finding(
            "suspense",
            code="forward_motion_inventory",
            signal="observation",
            story_unit_id=snapshot.root_unit["story_unit_id"],
            observation=(
                f"At this cutoff ThothPad tracks {len(questions)} open reader question(s) and "
                f"{len(promises)} dramatic promise(s)."
            ),
        )
    )
    return findings


def _theme_motif(snapshot: CouncilSnapshot) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if snapshot.interpretations:
        findings.append(
            _finding(
                "theme_motif",
                code="tracked_interpretive_state",
                signal="observation",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation=(
                    f"This scope contains {len(snapshot.interpretations)} tracked theme/psyche/symbol "
                    "interpretation(s)."
                ),
                evidence=list(snapshot.interpretations[:12]),
                interpretation_limit="Interpretive state never becomes story canon through repetition.",
            )
        )
    if snapshot.lens_findings:
        findings.append(
            _finding(
                "theme_motif",
                code="story_lens_evidence",
                signal="observation",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation=f"{len(snapshot.lens_findings)} reusable Story Lens finding(s) intersect this scope.",
                evidence=list(snapshot.lens_findings[:12]),
            )
        )
    if not findings:
        findings.append(
            _finding(
                "theme_motif",
                code="interpretive_state_untracked",
                signal="observation",
                story_unit_id=snapshot.root_unit["story_unit_id"],
                observation="No theme/motif interpretation or Story Lens finding is currently tracked in this scope.",
                interpretation_limit=(
                    "This is a state-coverage observation, not evidence that the prose lacks theme or motif."
                ),
            )
        )
    return findings


_REVIEWERS: tuple[tuple[str, Callable[[CouncilSnapshot], list[dict[str, Any]]]], ...] = (
    ("continuity", _continuity),
    ("character", _character),
    ("scene_architecture", _scene_architecture),
    ("cold_reader", _cold_reader),
    ("line_editor", _line_editor),
    ("suspense", _suspense),
    ("theme_motif", _theme_motif),
)


class EditorialCouncil:
    def __init__(self, project: StoryProject, store: StoryStore) -> None:
        self.project = project
        self.store = store

    def _unit_text(self, unit: dict[str, Any]) -> str:
        source_id = str(unit.get("source_id") or "")
        if not source_id:
            return ""
        start = int(unit.get("start_offset") or 0)
        end = int(unit.get("end_offset") or 0)
        pieces: list[str] = []
        for row in self.store.rows(
            """
            SELECT start_offset,end_offset,text FROM source_chunks
            WHERE source_id=? AND end_offset>? AND start_offset<? ORDER BY ordinal
            """,
            (source_id, start, end),
        ):
            local_start = max(start, int(row["start_offset"])) - int(row["start_offset"])
            local_end = min(end, int(row["end_offset"])) - int(row["start_offset"])
            pieces.append(str(row["text"])[local_start:local_end])
        return "".join(pieces)[:40_000]

    def _snapshot(self, story_unit_id: str, *, branch_id: str) -> CouncilSnapshot:
        audit = NarrativeAuditEngine(self.project, self.store).audit_story_unit(story_unit_id, branch_id=branch_id)
        scope_ids = list(audit["scope_unit_ids"])
        placeholders = ",".join("?" for _ in scope_ids)
        scope_units = [
            dict(row)
            for row in self.store.rows(
                f"SELECT * FROM story_units WHERE story_unit_id IN ({placeholders}) ORDER BY ordinal,story_unit_id",  # noqa: S608
                tuple(scope_ids),
            )
        ]
        root = next(item for item in scope_units if item["story_unit_id"] == story_unit_id)
        reader = ReaderIntelligence(self.project, self.store)
        expectations = reader.reader_expectations_at(story_unit_id, branch_id=branch_id)
        fairness = reader.reveal_fairness(story_unit_id, branch_id=branch_id)

        conflicts: list[dict[str, Any]] = []
        for row in self.store.rows(
            "SELECT * FROM story_conflicts WHERE status='OPEN' ORDER BY conflict_id LIMIT 100"
        ):
            conflict = dict(row)
            related_claims: list[dict[str, Any]] = []
            matched_unit: str | None = None
            for claim_row in self.store.rows(
                """
                SELECT c.claim_id,c.predicate,c.status,e.story_unit_id,e.source_id,e.start_offset,e.end_offset
                FROM conflict_claims cc
                JOIN claims c ON c.claim_id=cc.claim_id
                LEFT JOIN claim_evidence e ON e.claim_id=c.claim_id AND e.stale=0
                WHERE cc.conflict_id=?
                ORDER BY c.claim_id,e.evidence_id
                """,
                (conflict["conflict_id"],),
            ):
                record = dict(claim_row)
                related_claims.append(record)
                if record.get("story_unit_id") in scope_ids:
                    matched_unit = str(record["story_unit_id"])
            if matched_unit:
                conflict["story_unit_id"] = matched_unit
                conflict["claims"] = related_claims[:20]
                conflicts.append(conflict)

        interpretations = [
            dict(row)
            for row in self.store.rows(
                f"SELECT * FROM interpretations WHERE story_unit_id IN ({placeholders}) ORDER BY rowid LIMIT 100",  # noqa: S608
                tuple(scope_ids),
            )
        ]
        for item in interpretations:
            item["evidence"] = StoryStore.decode_json(item.pop("evidence_json", None), [])
        lens_findings = [
            dict(row)
            for row in self.store.rows(
                f"SELECT * FROM lens_findings WHERE story_unit_id IN ({placeholders}) ORDER BY rowid LIMIT 100",  # noqa: S608
                tuple(scope_ids),
            )
        ]
        return CouncilSnapshot(
            root_unit=root,
            scope_units=tuple(scope_units),
            audit=audit,
            reader_expectations=expectations,
            reveal_fairness=fairness,
            conflicts=tuple(conflicts),
            interpretations=tuple(interpretations),
            lens_findings=tuple(lens_findings),
            unit_texts={str(unit["story_unit_id"]): self._unit_text(unit) for unit in scope_units},
        )

    @staticmethod
    def _synthesize(reviews: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        by_unit: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for review in reviews:
            for finding in review["findings"]:
                unit = str(finding.get("story_unit_id") or "")
                if not unit:
                    continue
                by_unit.setdefault(unit, {}).setdefault(str(finding.get("signal", "observation")), []).append(finding)

        agreement: list[dict[str, Any]] = []
        disagreement: list[dict[str, Any]] = []
        for unit, signals in by_unit.items():
            concerns = signals.get("concern", [])
            concern_reviewers = sorted({str(item["reviewer"]) for item in concerns})
            if len(concern_reviewers) >= 2:
                agreement.append(
                    {
                        "story_unit_id": unit,
                        "reviewer_count": len(concern_reviewers),
                        "reviewers": concern_reviewers,
                        "finding_codes": [str(item["code"]) for item in concerns],
                    }
                )
            supports = signals.get("support", [])
            if concerns and supports:
                disagreement.append(
                    {
                        "story_unit_id": unit,
                        "concern_reviewers": sorted({str(item["reviewer"]) for item in concerns}),
                        "support_reviewers": sorted({str(item["reviewer"]) for item in supports}),
                        "note": "Independent reviewers surfaced both concern and support signals in this scope.",
                    }
                )
        agreement.sort(key=lambda item: (-int(item["reviewer_count"]), str(item["story_unit_id"])))
        disagreement.sort(key=lambda item: str(item["story_unit_id"]))
        return agreement, disagreement

    def run(self, story_unit_id: str, *, branch_id: str = "mainline") -> dict[str, Any]:
        snapshot = self._snapshot(story_unit_id, branch_id=branch_id)
        results: dict[str, list[dict[str, Any]]] = {}
        # All SQLite/project reads have already happened. Workers receive only
        # immutable Python data, so no reviewer can mutate Story State or observe
        # another reviewer's output.
        with ThreadPoolExecutor(max_workers=len(_REVIEWERS), thread_name_prefix="thoth-council") as executor:
            futures = {name: executor.submit(reviewer, snapshot) for name, reviewer in _REVIEWERS}
            for name, _reviewer in _REVIEWERS:
                results[name] = futures[name].result()

        reviews = [
            {
                "reviewer": name,
                "read_only": True,
                "findings": results[name],
                "concern_count": sum(item.get("signal") == "concern" for item in results[name]),
            }
            for name, _reviewer in _REVIEWERS
        ]
        agreement, disagreement = self._synthesize(reviews)
        return {
            "story_unit_id": story_unit_id,
            "branch_id": branch_id,
            "reviewer_count": len(reviews),
            "parallel_read_only": True,
            "agent_chatter": False,
            "reviews": reviews,
            "agreement": agreement,
            "disagreement": disagreement,
            "diagnostic_only": True,
        }
