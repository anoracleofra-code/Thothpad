from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend import config
from backend.atomic_io import atomic_write_text
from backend.validation import validate_json_path, validate_profile, validate_profile_name


def _profile_paths() -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for directory in (config.BUILTIN_PROFILES_DIR, config.PROFILES_DIR):
        if directory.exists():
            for path in sorted(directory.glob("*.json")):
                paths[path.stem] = path
    return paths


def list_profiles() -> list[dict[str, Any]]:
    out = []
    for name, path in sorted(_profile_paths().items()):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append({"name": data.get("name", name), "path": str(path), "profile": data})
        except (OSError, json.JSONDecodeError):
            out.append({"name": name, "path": str(path), "error": "invalid profile"})
    return out


def get_profile(name: str) -> dict[str, Any]:
    return load_profile(name)


def save_profile(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    config.ensure_dirs()
    safe_name = validate_profile_name(name)
    data = deepcopy(validate_profile(profile))
    data["name"] = safe_name
    path = config.PROFILES_DIR / f"{safe_name}.json"
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return {"name": safe_name, "path": str(path), "profile": data}


def import_profile(*, path: str | None = None, profile: dict[str, Any] | None = None, name: str | None = None) -> dict[str, Any]:  # noqa: E501
    if (path is None) == (profile is None):
        raise ValueError("provide exactly one of path or profile")
    if path is not None:
        source = validate_json_path(path, must_exist=True)
        if source.stat().st_size > config.MAX_PROFILE_BYTES:
            raise ValueError(f"profile exceeds the {config.MAX_PROFILE_BYTES}-byte limit")
        try:
            profile = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("profile import is not valid JSON") from exc
        selected_name = name or source.stem
    else:
        selected_name = name or str((profile or {}).get("name", ""))
    return save_profile(validate_profile_name(selected_name), validate_profile(profile or {}))


def export_profile(name: str, path: str | None = None, *, overwrite: bool = False) -> dict[str, Any]:
    data = get_profile(validate_profile_name(name))
    serialized = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    result = {"name": name, "profile": data, "json": serialized}
    if path is not None:
        destination = validate_json_path(path)
        if destination.exists() and not overwrite:
            raise ValueError("profile export path already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(destination, serialized)
        result["path"] = str(destination)
    return result


def load_profile(name: str | None = None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    profile_name = validate_profile_name(name or config.DEFAULT_PROFILE)
    paths = _profile_paths()
    path = paths.get(profile_name) or paths.get(config.DEFAULT_PROFILE)
    if path is None:
        path = config.PROFILES_DIR / f"{config.DEFAULT_PROFILE}.json"
    if not path.exists():
        data: dict[str, Any] = {
            "name": config.DEFAULT_PROFILE,
            "register_target": "direct, concrete prose",
            "default_mode": "diagnose",
            "preserve": ["meaning", "facts", "POV"],
            "prefer": ["specific nouns", "plain verbs", "varied rhythm"],
            "avoid": ["generic AI cadence", "fake contrast", "abstract mic-drop endings"],
            "hard_bans": [],
            "soft_flags": [],
            "analyzer_weights": {},
        }
    else:
        data = json.loads(path.read_text(encoding="utf-8"))

    merged = deepcopy(data)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged
