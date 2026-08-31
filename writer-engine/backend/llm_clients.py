from __future__ import annotations

import ipaddress
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from backend import config
from backend.validation import bounded_int as _bounded_int

OPENAI_KINDS = {"openai_compatible", "openai", "openrouter", "lmstudio", "llama_cpp"}
SUPPORTED_KINDS = OPENAI_KINDS | {"anthropic", "ollama", "gemini"}
DEFAULT_ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/v1",
    "ollama": "http://127.0.0.1:11434/api",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    error: str | None = None


def _provider(provider_config: dict[str, Any] | None) -> dict[str, Any]:
    supplied = dict(provider_config or {})
    desktop = supplied.pop("_desktop_no_environment", False) is True
    merged = (
        {
            "provider": "openai_compatible",
            "base_url": "http://127.0.0.1:1234/v1",
            "api_key": "",
            "model": "local-model",
            "temperature": 0.7,
        }
        if desktop
        else dict(config.DEFAULT_PROVIDER_CONFIG)
    )
    default_identity = (
        str(merged.get("provider", "openai_compatible")),
        str(merged.get("base_url", "")),
    )
    merged.update(supplied)
    supplied_identity = (
        str(merged.get("provider", "openai_compatible")),
        str(merged.get("base_url", "")),
    )
    if not desktop and "api_key" not in supplied and supplied_identity != default_identity:
        # Endpoint and credential selection are atomic. A caller cannot retarget
        # a key inherited from the harness environment to another origin.
        merged["api_key"] = ""
    kind = str(merged.get("provider", "openai_compatible"))
    if kind in DEFAULT_ENDPOINTS and not supplied.get("base_url"):
        merged["base_url"] = DEFAULT_ENDPOINTS[kind]
    if kind in {"anthropic", "ollama", "gemini"} and "api_key" not in supplied:
        merged["api_key"] = ""
    return merged


def scrub_provider(provider_config: dict[str, Any] | None) -> dict[str, Any]:
    merged = _provider(provider_config)
    merged.pop("_desktop_no_environment", None)
    if "api_key" in merged:
        merged["api_key"] = "***"
    return merged


def _is_loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validated_url(value: Any) -> str:
    url = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("provider base_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("provider base_url must not contain credentials")
    if parsed.fragment:
        raise ValueError("provider base_url must not contain a fragment")
    try:
        parsed.port  # noqa: B018 -- deliberate: accessing .port raises on invalid ports
    except ValueError as exc:
        raise ValueError("provider base_url contains an invalid port") from exc
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise ValueError("HTTP provider endpoints are allowed only on loopback")
    return url


def provider_is_remote(provider_config: dict[str, Any] | None) -> bool:
    provider = _provider(provider_config)
    parsed = urllib.parse.urlsplit(_validated_url(provider.get("base_url")))
    return not _is_loopback(parsed.hostname)


def _endpoint(base_url: str, suffix: str) -> str:
    if base_url.endswith(suffix):
        return base_url
    return base_url + suffix


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = _validated_url(newurl)
        if _origin(req.full_url) != _origin(target):
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "refusing cross-origin model redirect",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, target)


def _bounded_float(value: Any, name: str, minimum: float, maximum: float) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return parsed


def _request_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for name, value in headers.items():
        if value:
            request.add_header(name, value)
    opener = urllib.request.build_opener(SafeRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(config.MAX_LLM_RESPONSE_BYTES + 1)
    if len(raw) > config.MAX_LLM_RESPONSE_BYTES:
        raise ValueError(f"provider response exceeds the {config.MAX_LLM_RESPONSE_BYTES}-byte limit")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("provider response must be a JSON object")
    return value


def _anthropic_messages(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    system_parts: list[str] = []
    conversation: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content", ""))
        if role == "system":
            system_parts.append(content)
        elif role in {"user", "assistant"}:
            conversation.append({"role": role, "content": content})
    return "\n\n".join(system_parts), conversation


def _gemini_messages(
    messages: list[dict[str, str]],
) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content", ""))
        if role == "system":
            system_parts.append(content)
            continue
        if role not in {"user", "assistant"} or not content:
            continue
        gemini_role = "user" if role == "user" else "model"
        if contents and contents[-1].get("role") == gemini_role:
            parts = contents[-1].get("parts")
            if isinstance(parts, list):
                parts.append({"text": content})
                continue
        contents.append({"role": gemini_role, "parts": [{"text": content}]})
    return "\n\n".join(system_parts), contents


def complete_chat(
    messages: list[dict[str, str]],
    provider_config: dict[str, Any] | None = None,
) -> LLMResponse:
    provider = _provider(provider_config)
    kind = str(provider.get("provider", "openai_compatible"))
    model = str(provider.get("model", "local-model"))
    if kind not in SUPPORTED_KINDS:
        return LLMResponse("", kind, model, f"unsupported provider: {kind}")

    try:
        base_url = _validated_url(provider.get("base_url"))
        temperature_limit = 1.0 if kind == "anthropic" else 2.0
        temperature = _bounded_float(provider.get("temperature", 0.7), "temperature", 0.0, temperature_limit)
        max_tokens = _bounded_int(provider.get("max_tokens", 4096), "max_tokens", 1, 32768)
        timeout = _bounded_float(provider.get("timeout", 180), "timeout", 1.0, 600.0)
        api_key = str(provider.get("api_key", ""))

        if kind == "anthropic":
            system, conversation = _anthropic_messages(messages)
            payload: dict[str, Any] = {
                "model": model,
                "messages": conversation,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if system:
                payload["system"] = system
            data = _request_json(
                _endpoint(base_url, "/messages"),
                payload,
                {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                timeout,
            )
            blocks = data.get("content")
            if not isinstance(blocks, list):
                raise ValueError("response did not contain Anthropic content blocks")
            text = "".join(
                str(block.get("text", ""))
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        elif kind == "gemini":
            system, contents = _gemini_messages(messages)
            if not contents:
                raise ValueError("Gemini request requires at least one user/assistant message")
            payload = {
                "contents": contents,
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                },
            }
            if system:
                payload["systemInstruction"] = {"parts": [{"text": system}]}
            model_path = urllib.parse.quote(model.strip(), safe="")
            data = _request_json(
                _endpoint(base_url, f"/models/{model_path}:generateContent"),
                payload,
                {"x-goog-api-key": api_key},
                timeout,
            )
            candidates = data.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                raise ValueError("response did not contain Gemini candidates")
            first = candidates[0]
            if not isinstance(first, dict):
                raise ValueError("Gemini candidate was not an object")
            response_content = first.get("content")
            if not isinstance(response_content, dict):
                raise ValueError("Gemini candidate did not contain content")
            parts = response_content.get("parts")
            if not isinstance(parts, list):
                raise ValueError("Gemini candidate did not contain content.parts")
            text = "".join(
                str(part.get("text", ""))
                for part in parts
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        elif kind == "ollama":
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            data = _request_json(
                _endpoint(base_url, "/chat"),
                payload,
                {"Authorization": f"Bearer {api_key}" if api_key else ""},
                timeout,
            )
            message = data.get("message")
            if not isinstance(message, dict):
                raise ValueError("response did not contain Ollama message.content")
            text = str(message.get("content", ""))
        else:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            data = _request_json(
                _endpoint(base_url, "/chat/completions"),
                payload,
                {"Authorization": f"Bearer {api_key}" if api_key else ""},
                timeout,
            )
            try:
                text = str(data["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError("response did not contain choices[0].message.content") from exc
        if not text.strip():
            raise ValueError("provider returned no text")
        return LLMResponse(text.strip(), kind, model)
    except (ValueError, TypeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return LLMResponse("", kind, model, str(exc))
