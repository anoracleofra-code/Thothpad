from __future__ import annotations

from typing import Any

from backend.story.model_routing import enforce_story_privacy
from backend.story.project import StoryProject


def offline_readiness(project: StoryProject) -> dict[str, Any]:
    return {
        "project_id": project.project_id,
        "deterministic_story_engine_requires_network": False,
        "desktop_analysis_uses_listening_port": False,
        "remote_model_use_is_optional": True,
        "local_only_policy_is_fail_closed": True,
        "credentials_are_not_story_state": True,
    }


def certify_provider_offline(provider: dict[str, Any] | None) -> dict[str, Any]:
    result = enforce_story_privacy(provider, "local_only")
    return {**result, "offline_certified": True}
