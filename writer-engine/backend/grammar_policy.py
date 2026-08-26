from __future__ import annotations

import urllib.parse
from typing import Any

from backend.llm_clients import _is_loopback, _validated_url
from backend.validation import bounded_int as _bounded_int
from backend.validation import require_json_boolean as _bool

PROVIDERS = {"harper", "languagetool", "prowritingaid"}
DIALECTS = {"en-US", "en-GB", "en-CA", "en-AU", "en-IN"}
PWA_LANGUAGES = {"en_US", "en_UK", "en_AU", "en_CA", "en", "es"}
PWA_STYLES = {
    "General", "Academic", "Business", "Technical", "Creative", "Casual",
    "Web", "Script", "Legal",
}
PWA_REPORTS = {
    "grammar", "style", "spelling", "readability", "overused", "cliches",
    "diction", "repeats", "sentence", "sticky", "transition", "all",
}

def _bounded_string(value: Any, name: str, maximum: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters")
    return value


def _is_remote(url: str) -> bool:
    return not _is_loopback(urllib.parse.urlsplit(url).hostname)


def approved_grammar(
    value: Any,
    *,
    live: bool,
    consent: bool = False,
) -> dict[str, Any] | None:
    if value is None:
        return {
            "provider": "harper",
            "dialect": "en-US",
            "include_spelling": False,
            "max_findings": 100000,
            "timeout": 10,
        }
    if not isinstance(value, dict):
        raise ValueError("grammar must be an object")
    if "enabled" in value and not _bool(value["enabled"], "grammar.enabled"):
        return None

    provider = _bounded_string(value.get("provider", "harper"), "grammar.provider", 32)
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported grammar provider: {provider}")
    max_findings = _bounded_int(value.get("max_findings", 100000), "grammar.max_findings", 1, 100000)
    timeout = _bounded_int(value.get("timeout", 20), "grammar.timeout", 1, 120)

    if provider == "harper":
        dialect = _bounded_string(value.get("dialect", "en-US"), "grammar.dialect", 8)
        if dialect not in DIALECTS:
            raise ValueError("grammar.dialect is unsupported")
        include_spelling = _bool(value.get("include_spelling", False), "grammar.include_spelling")
        return {
            "provider": provider,
            "dialect": dialect,
            "include_spelling": include_spelling,
            "max_findings": max_findings,
            "timeout": timeout,
        }

    if live:
        raise ValueError("network grammar providers are unavailable during live analysis")
    if type(consent) is not bool or not consent:
        raise ValueError("remote grammar review requires explicit consent")

    if provider == "languagetool":
        url = _validated_url(value.get("url", "http://127.0.0.1:8081/v2/check"))
        if _is_remote(url):
            username = _bounded_string(value.get("username"), "grammar.username", 256)
            api_key = _bounded_string(value.get("api_key"), "grammar.api_key", 4096)
            if not username or not api_key:
                raise ValueError("remote LanguageTool requires username and API key")
        else:
            username = ""
            api_key = ""
        return {
            "provider": provider,
            "url": url,
            "language": _bounded_string(value.get("language", "en-US"), "grammar.language", 32),
            "username": username,
            "api_key": api_key,
            "max_findings": max_findings,
            "timeout": timeout,
            "remote": _is_remote(url),
        }

    api_key = _bounded_string(value.get("api_key"), "grammar.api_key", 4096, required=True)
    language = _bounded_string(value.get("language", "en_US"), "grammar.language", 8)
    style = _bounded_string(value.get("style", "Creative"), "grammar.style", 32)
    if language not in PWA_LANGUAGES:
        raise ValueError("grammar.language is unsupported by ProWritingAid")
    if style not in PWA_STYLES:
        raise ValueError("grammar.style is unsupported by ProWritingAid")
    raw_reports = value.get("reports", ["grammar", "style", "spelling"])
    if not isinstance(raw_reports, list) or not raw_reports or len(raw_reports) > 20:
        raise ValueError("grammar.reports must be a non-empty array")
    reports = [_bounded_string(item, "grammar.reports item", 32) for item in raw_reports]
    if any(report not in PWA_REPORTS for report in reports):
        raise ValueError("grammar.reports contains an unsupported report")
    return {
        "provider": provider,
        "url": "https://api.prowritingaid.com",
        "api_key": api_key,
        "language": language,
        "style": style,
        "reports": reports,
        "max_findings": max_findings,
        "timeout": timeout,
        "remote": True,
    }
