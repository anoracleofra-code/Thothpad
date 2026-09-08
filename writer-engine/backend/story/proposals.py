from __future__ import annotations

import copy
import uuid
from typing import Any

from backend.story.project import StoryProject

PROPOSAL_STATUSES = frozenset({"PROPOSED", "ACCEPTED", "REJECTED", "SUPERSEDED"})


def _proposals(project: StoryProject) -> list[dict[str, Any]]:
    value = project.state.get("story_proposals")
    if not isinstance(value, list):
        value = []
        project.state["story_proposals"] = value
    return value


def submit_story_proposal(
    project: StoryProject,
    *,
    proposal_kind: str,
    target_mutation: str,
    payload: dict[str, Any],
    branch_id: str = "mainline",
    story_unit_id: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    created_by: str = "model",
    proposal_id: str | None = None,
) -> dict[str, Any]:
    if not proposal_kind.strip() or not target_mutation.strip():
        raise ValueError("proposal_kind and target_mutation are required")
    proposal_id = proposal_id or str(uuid.uuid4())
    record = {
        "proposal_id": proposal_id,
        "proposal_kind": proposal_kind[:120],
        "target_mutation": target_mutation[:120],
        "payload": copy.deepcopy(payload),
        "branch_id": branch_id[:240] or "mainline",
        "story_unit_id": story_unit_id[:240] if story_unit_id else None,
        "evidence": copy.deepcopy((evidence or [])[:100]),
        "created_by": created_by[:120],
        "status": "PROPOSED",
        "review": {},
    }
    proposals = _proposals(project)
    for index, existing in enumerate(proposals):
        if isinstance(existing, dict) and existing.get("proposal_id") == proposal_id:
            if existing.get("status") != "PROPOSED":
                raise ValueError("reviewed proposal cannot be silently replaced")
            proposals[index] = record
            break
    else:
        proposals.append(record)
    project.state["story_proposals"] = proposals[-20_000:]
    project.save_state()
    return copy.deepcopy(record)


def list_story_proposals(
    project: StoryProject,
    *,
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    normalized = status.strip().upper() if isinstance(status, str) and status.strip() else None
    if normalized is not None and normalized not in PROPOSAL_STATUSES:
        raise ValueError("unknown proposal status")
    result = []
    for item in reversed(_proposals(project)):
        if not isinstance(item, dict):
            continue
        if normalized is not None and item.get("status") != normalized:
            continue
        result.append(copy.deepcopy(item))
        if len(result) >= max(1, min(int(limit), 1_000)):
            break
    return result


def proposal(project: StoryProject, proposal_id: str) -> dict[str, Any] | None:
    for item in _proposals(project):
        if isinstance(item, dict) and item.get("proposal_id") == proposal_id:
            return item
    return None


def mark_proposal_reviewed(
    project: StoryProject,
    *,
    proposal_id: str,
    decision: str,
    applied_record_id: str = "",
    note: str = "",
) -> dict[str, Any]:
    normalized = decision.strip().upper()
    if normalized not in {"ACCEPTED", "REJECTED"}:
        raise ValueError("proposal decision must be ACCEPTED or REJECTED")
    record = proposal(project, proposal_id)
    if record is None:
        raise KeyError("proposal not found")
    if record.get("status") != "PROPOSED":
        raise ValueError("proposal has already been reviewed")
    record["status"] = normalized
    record["review"] = {
        "writer_confirmed": True,
        "applied_record_id": applied_record_id[:240],
        "note": note[:4_000],
    }
    project.save_state()
    return copy.deepcopy(record)
