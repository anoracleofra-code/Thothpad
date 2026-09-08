from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.protocol import OPERATIONS, PROTOCOL_MAJOR, PROTOCOL_MINOR
from backend.story.project import PROJECT_SCHEMA_VERSION, STORY_STATE_SCHEMA_VERSION
from backend.story.store import SCHEMA_VERSION


def interface_fingerprint(story_tools: list[dict[str, Any]]) -> dict[str, Any]:
    surface = {
        "protocol": {"major": PROTOCOL_MAJOR, "minor": PROTOCOL_MINOR},
        "sidecar_operations": sorted(str(item) for item in OPERATIONS),
        "story_tools": sorted(
            (
                {
                    "id": str(item.get("id", "")),
                    "risk": str(item.get("risk", "")),
                }
                for item in story_tools
            ),
            key=lambda item: (item["id"], item["risk"]),
        ),
        "schemas": {
            "story_index": SCHEMA_VERSION,
            "project": PROJECT_SCHEMA_VERSION,
            "story_state": STORY_STATE_SCHEMA_VERSION,
        },
    }
    encoded = json.dumps(surface, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        **surface,
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "backward_compatibility_rule": (
            "Changes to operations, read-tool IDs/risks, or persisted schema versions change this fingerprint."
        ),
    }
