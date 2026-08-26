from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend import config

PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


def validate_profile_name(name: str) -> str:
    if not isinstance(name, str) or not PROFILE_NAME_RE.fullmatch(name):
        raise ValueError("Profile names must be 1-64 ASCII letters, numbers, hyphens, or underscores.")
    return name


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid run id")
    return run_id


def validate_text(text: str, *, live: bool = False) -> str:
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    limit = config.MAX_LIVE_TEXT_CHARS if live else config.MAX_TEXT_CHARS
    if len(text) > limit:
        raise ValueError(f"text exceeds the {limit}-character limit")
    if not live:
        utf16_units = len(text) + sum(ord(char) > 0xFFFF for char in text)
        if utf16_units > config.MAX_TEXT_UTF16_UNITS:
            raise ValueError(
                f"text exceeds the {config.MAX_TEXT_UTF16_UNITS}-UTF-16-unit limit"
            )
    return text


def validate_passes(passes: int) -> int:
    if isinstance(passes, bool):
        raise ValueError(f"passes must be between 1 and {config.MAX_PASSES}")
    value = int(passes)
    if value < 1 or value > config.MAX_PASSES:
        raise ValueError(f"passes must be between 1 and {config.MAX_PASSES}")
    return value


def validate_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(documents) > config.MAX_DOCUMENTS:
        raise ValueError(f"documents exceeds the {config.MAX_DOCUMENTS}-document limit")
    total = 0
    for document in documents:
        text = document.get("text", "")
        if not isinstance(text, str):
            raise ValueError("document text must be a string")
        if len(text) > config.MAX_TEXT_CHARS:
            raise ValueError(f"a document exceeds the {config.MAX_TEXT_CHARS}-character limit")
        utf16_units = len(text) + sum(ord(char) > 0xFFFF for char in text)
        if utf16_units > config.MAX_TEXT_UTF16_UNITS:
            raise ValueError(
                f"a document exceeds the {config.MAX_TEXT_UTF16_UNITS}-UTF-16-unit limit"
            )
        total += len(text)
    if total > config.MAX_MANUSCRIPT_CHARS:
        raise ValueError(f"manuscript exceeds the {config.MAX_MANUSCRIPT_CHARS}-character limit")
    return documents


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("profile must be an object")
    encoded = json.dumps(profile, ensure_ascii=False).encode("utf-8")
    if len(encoded) > config.MAX_PROFILE_BYTES:
        raise ValueError(f"profile exceeds the {config.MAX_PROFILE_BYTES}-byte limit")
    return profile


def validate_json_path(value: str, *, must_exist: bool = False) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("path must be a non-empty string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("profile import/export paths must be absolute")
    path = path.resolve(strict=False)
    if path.suffix.lower() != ".json":
        raise ValueError("profile import/export paths must end in .json")
    if must_exist and not path.is_file():
        raise ValueError("profile import path does not exist or is not a file")
    return path


def require_json_boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a JSON boolean")
    return value


def strict_bool_arg(args: dict[str, Any], name: str, default: bool = False) -> bool:
    if name not in args:
        return default
    value = args[name]
    if type(value) is not bool:
        raise ValueError(f"{name} must be a JSON boolean")
    return value


bool_arg = strict_bool_arg


def bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    # bool subclasses int in Python, but JSON true/false must never silently
    # become numeric configuration values such as page sizes or timeouts.
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def reject_json_constant(value: str, error: type[Exception] = ValueError) -> None:
    raise error(f"non-finite JSON value is not allowed: {value}")
