from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.story.project import StoryProject
from backend.story.store import StoryStore


def project_model_fingerprint(project: StoryProject, store: StoryStore) -> dict[str, Any]:
    """Return a path/ID-independent semantic fingerprint for layout-equivalence tests."""

    entities = [
        (str(row["canonical_name"]).casefold(), str(row["entity_type"]).casefold())
        for row in store.rows(
            "SELECT canonical_name,entity_type FROM entities WHERE status<>'ARCHIVED' "
            "ORDER BY canonical_name COLLATE NOCASE"
        )
    ]
    entity_names = {
        str(row["entity_id"]): str(row["canonical_name"]).casefold()
        for row in store.rows("SELECT entity_id,canonical_name FROM entities")
    }
    claims = []
    for row in store.rows(
        "SELECT subject_entity_id,predicate,literal_value_json,status,branch_id FROM claims "
        "WHERE status NOT IN ('SUPERSEDED','ARCHIVED') ORDER BY predicate,claim_id"
    ):
        claims.append(
            (
                entity_names.get(str(row["subject_entity_id"] or ""), ""),
                str(row["predicate"]),
                StoryStore.decode_json(row["literal_value_json"], None),
                str(row["status"]),
                str(row["branch_id"]),
            )
        )
    units = [
        (str(row["kind"]), str(row["display_title"]).casefold(), str(row["content_hash"]))
        for row in store.rows(
            "SELECT kind,display_title,content_hash FROM story_units WHERE branch_id='mainline' "
            "ORDER BY kind,display_title"
        )
        if str(row["kind"]) != "project"
    ]
    payload = {"entities": entities, "claims": claims, "story_units": units}
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "project_id": project.project_id,
        "fingerprint": digest,
        "semantic_payload": payload,
        "filesystem_paths_included": False,
        "stable_ids_included": False,
    }


def compare_model_fingerprints(fingerprints: list[dict[str, Any]]) -> dict[str, Any]:
    digests = [str(item.get("fingerprint") or "") for item in fingerprints]
    return {
        "equivalent": bool(digests) and len(set(digests)) == 1,
        "fingerprints": digests,
        "project_count": len(digests),
    }
