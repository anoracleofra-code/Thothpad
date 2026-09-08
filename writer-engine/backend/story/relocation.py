from __future__ import annotations

import json
from typing import Any

from backend.story.exchange import export_story_bundle
from backend.story.project import StoryProject
from backend.story.store import StoryStore


def relocation_readiness(project: StoryProject, store: StoryStore) -> dict[str, Any]:
    bundle = export_story_bundle(project, store)
    encoded = json.dumps(bundle, ensure_ascii=False)
    root_text = str(project.root)
    metadata_text = str(project.metadata_dir)
    gates = {
        "no_source_text": bundle.get("contains_source_text") is False,
        "no_absolute_paths_flag": bundle.get("contains_absolute_paths") is False,
        "current_root_not_serialized": root_text not in encoded,
        "metadata_path_not_serialized": metadata_text not in encoded,
        "project_rules_are_portable": isinstance(bundle.get("manifest"), dict),
        "writer_state_is_portable": isinstance(bundle.get("state"), dict),
    }
    return {
        "project_id": project.project_id,
        "bundle_version": bundle.get("bundle_version"),
        "gates": gates,
        "all_green": all(gates.values()),
        "relocation_requires_source_files_to_exist_at_destination": True,
    }
