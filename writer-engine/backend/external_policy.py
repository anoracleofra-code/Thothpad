from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.llm_clients import _validated_url


def _strict_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a JSON boolean")
    return value


def _trusted_executable(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be an absolute executable path")
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{name} must be an existing absolute executable path")
    return str(path.resolve())


def approved_external_tools(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("external_tools must be an object")
    if not _strict_bool(value.get("consent", False), "external_tools.consent"):
        raise ValueError("external tool execution requires explicit consent")
    approved: dict[str, Any] = {}
    if "proselint" in value and _strict_bool(value["proselint"], "external_tools.proselint"):
        approved["proselint"] = True
    if value.get("vale_path") is not None:
        approved["vale_path"] = _trusted_executable(value["vale_path"], "external_tools.vale_path")
    if value.get("harper_path") is not None:
        approved["harper_path"] = _trusted_executable(value["harper_path"], "external_tools.harper_path")
    language_tool = value.get("languagetool")
    if language_tool is not None:
        if not isinstance(language_tool, dict):
            raise ValueError("external_tools.languagetool must be an object")
        url = _validated_url(language_tool.get("url"))
        endpoints = language_tool.get("approved_endpoints")
        approved_endpoints = (
            {_validated_url(item) for item in endpoints}
            if isinstance(endpoints, list) else set()
        )
        if url not in approved_endpoints:
            raise ValueError("LanguageTool URL must appear in approved_endpoints")
        approved["languagetool"] = {
            "url": url,
            "language": str(language_tool.get("language", "en-US")),
            "approved_endpoints": sorted(approved_endpoints),
        }
    return approved
