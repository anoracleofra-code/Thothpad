from __future__ import annotations

import re
from typing import Any

from backend.llm_clients import provider_is_remote, scrub_provider

TASKS = frozenset(
    {
        "chat",
        "extraction",
        "continuity",
        "structural",
        "creative",
        "line_edit",
        "cold_reader",
        "council",
        "lens",
        "reader_experience",
    }
)
QUALITY_PRESETS = frozenset({"fast", "balanced", "quality"})
PRIVACY_PRESETS = frozenset({"local_only", "prefer_local", "allow_remote"})

_CONTINUITY = re.compile(r"\b(continuity|contradict|timeline|canon|know|knowledge|inconsisten)\w*\b", re.I)
_STRUCTURE = re.compile(r"\b(structur|scene|chapter|beat|pacing|arc|causal|decision|opposition)\w*\b", re.I)
_CREATIVE = re.compile(r"\b(brainstorm|write|rewrite|alternative|idea|invent|imagine|prose|dialogue)\w*\b", re.I)
_LINE = re.compile(r"\b(line edit|sentence|wording|grammar|rhythm|cadence|repetition|style)\b", re.I)
_READER = re.compile(r"\b(cold reader|reader know|reveal|foreshadow|dramatic irony|reader expect)\w*\b", re.I)
_LENS = re.compile(r"\b(lens|motif|pattern|theme|symbol)\w*\b", re.I)


def classify_story_task(prompt: str, *, explicit_task: str | None = None) -> str:
    explicit = str(explicit_task or "").strip().casefold()
    if explicit:
        if explicit not in TASKS:
            raise ValueError("unsupported story routing task")
        return explicit
    value = str(prompt or "")[:20_000]
    if _READER.search(value):
        return "cold_reader"
    if _CONTINUITY.search(value):
        return "continuity"
    if _LINE.search(value):
        return "line_edit"
    if _STRUCTURE.search(value):
        return "structural"
    if _LENS.search(value):
        return "lens"
    if _CREATIVE.search(value):
        return "creative"
    return "chat"


def _quality_generation(quality: str, task: str) -> dict[str, Any]:
    if quality == "fast":
        return {
            "temperature": 0.55 if task not in {"creative", "chat"} else 0.7,
            "max_tokens": 2048,
            "intent": "latency",
        }
    if quality == "quality":
        return {
            "temperature": 0.45 if task not in {"creative", "chat"} else 0.8,
            "max_tokens": 8192,
            "intent": "depth",
        }
    return {
        "temperature": 0.5 if task not in {"creative", "chat"} else 0.7,
        "max_tokens": 4096,
        "intent": "balanced",
    }


def _candidate(value: dict[str, Any], ordinal: int) -> dict[str, Any] | None:
    provider = str(value.get("provider", "")).strip()
    model = str(value.get("model", "")).strip()
    base_url = str(value.get("base_url", "")).strip()
    if not provider or not model or not base_url:
        return None
    clean = {
        "provider": provider[:80],
        "model": model[:256],
        "base_url": base_url[:2_000],
        "roles": [str(item).strip().casefold()[:80] for item in value.get("roles", []) if str(item).strip()][:20],
        "quality": str(value.get("quality", "balanced")).strip().casefold(),
        "priority": int(value.get("priority", ordinal)),
        "ordinal": ordinal,
    }
    if clean["quality"] not in QUALITY_PRESETS:
        clean["quality"] = "balanced"
    try:
        clean["remote"] = provider_is_remote(clean)
    except ValueError:
        return None
    return clean


def route_story_model(
    *,
    prompt: str,
    candidates: list[dict[str, Any]],
    fallback: dict[str, Any] | None = None,
    task: str | None = None,
    quality: str = "balanced",
    privacy: str = "prefer_local",
) -> dict[str, Any]:
    normalized_quality = str(quality).strip().casefold()
    normalized_privacy = str(privacy).strip().casefold()
    if normalized_quality not in QUALITY_PRESETS:
        raise ValueError("unsupported story routing quality preset")
    if normalized_privacy not in PRIVACY_PRESETS:
        raise ValueError("unsupported story routing privacy preset")
    routed_task = classify_story_task(prompt, explicit_task=task)

    normalized = [item for index, value in enumerate(candidates[:100]) if (item := _candidate(value, index))]
    fallback_item = _candidate(dict(fallback or {}), len(normalized)) if fallback else None
    if fallback_item and not any(
        item["provider"] == fallback_item["provider"]
        and item["model"] == fallback_item["model"]
        and item["base_url"] == fallback_item["base_url"]
        for item in normalized
    ):
        normalized.append(fallback_item)

    eligible = [item for item in normalized if normalized_privacy != "local_only" or not item["remote"]]
    if not eligible:
        raise PermissionError("local_only routing has no eligible local model candidate")

    def rank(item: dict[str, Any]) -> tuple[int, int, int, int, int]:
        roles = set(item["roles"])
        role_match = routed_task in roles
        general = "chat" in roles or "general" in roles or not roles
        quality_match = item["quality"] == normalized_quality
        local_preference = normalized_privacy == "prefer_local" and not item["remote"]
        return (
            0 if role_match else 1 if general else 2,
            0 if quality_match else 1,
            0 if local_preference else 1,
            int(item["priority"]),
            int(item["ordinal"]),
        )

    eligible.sort(key=rank)
    selected = eligible[0]
    generation = _quality_generation(normalized_quality, routed_task)
    provider = {
        "provider": selected["provider"],
        "model": selected["model"],
        "base_url": selected["base_url"],
        "temperature": generation["temperature"],
        "max_tokens": generation["max_tokens"],
    }
    return {
        "task": routed_task,
        "quality": normalized_quality,
        "privacy": normalized_privacy,
        "selected": provider,
        "selected_is_remote": bool(selected["remote"]),
        "reason": {
            "task_role_match": routed_task in set(selected["roles"]),
            "quality_match": selected["quality"] == normalized_quality,
            "local_preference_applied": normalized_privacy == "prefer_local" and not selected["remote"],
            "candidate_count": len(normalized),
            "eligible_count": len(eligible),
            "generation_intent": generation["intent"],
        },
        "provider_agnostic": True,
    }


def enforce_story_privacy(provider: dict[str, Any] | None, privacy: str) -> dict[str, Any]:
    normalized = str(privacy or "prefer_local").strip().casefold()
    if normalized not in PRIVACY_PRESETS:
        raise ValueError("unsupported story routing privacy preset")
    remote = provider_is_remote(provider)
    if normalized == "local_only" and remote:
        raise PermissionError("local_only Story Intelligence routing blocked a remote model request")
    return {
        "privacy": normalized,
        "selected_is_remote": remote,
        "provider": scrub_provider(provider),
        "allowed": True,
    }
