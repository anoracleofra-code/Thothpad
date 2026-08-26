import io
import urllib.error
import urllib.request

import pytest

from backend.llm_clients import SafeRedirectHandler, _request_json, complete_chat, scrub_provider

MESSAGES = [
    {"role": "system", "content": "Keep the voice."},
    {"role": "user", "content": "Rewrite this."},
]


def test_openai_compatible_adapter(monkeypatch):
    captured = {}

    def fake_request(url, payload, headers, timeout):
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {"choices": [{"message": {"content": "Revised."}}]}

    monkeypatch.setattr("backend.llm_clients._request_json", fake_request)
    result = complete_chat(MESSAGES, {
        "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:1234/v1",
        "api_key": "secret",
        "model": "local",
        "temperature": 0.5,
        "max_tokens": 800,
        "timeout": 30,
    })
    assert result.text == "Revised."
    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"]["max_tokens"] == 800


def test_anthropic_adapter_extracts_system_prompt(monkeypatch):
    captured = {}

    def fake_request(url, payload, headers, timeout):
        captured.update(url=url, payload=payload, headers=headers)
        return {"content": [{"type": "text", "text": "Anthropic revision."}]}

    monkeypatch.setattr("backend.llm_clients._request_json", fake_request)
    result = complete_chat(MESSAGES, {
        "provider": "anthropic",
        "api_key": "secret",
        "model": "claude-test",
        "temperature": 0.4,
    })
    assert result.text == "Anthropic revision."
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["payload"]["system"] == "Keep the voice."
    assert captured["payload"]["messages"] == [{"role": "user", "content": "Rewrite this."}]
    assert captured["headers"]["x-api-key"] == "secret"


def test_ollama_adapter_uses_local_chat_api(monkeypatch):
    captured = {}

    def fake_request(url, payload, headers, timeout):
        captured.update(url=url, payload=payload, headers=headers)
        return {"message": {"role": "assistant", "content": "Ollama revision."}}

    monkeypatch.setattr("backend.llm_clients._request_json", fake_request)
    result = complete_chat(MESSAGES, {"provider": "ollama", "model": "qwen"})
    assert result.text == "Ollama revision."
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["options"]["num_predict"] == 4096


@pytest.mark.parametrize("url", ["http://example.com/v1", "ftp://localhost/v1", "https://user:pass@example.com/v1"])
def test_unsafe_provider_endpoints_are_rejected(url):
    result = complete_chat(MESSAGES, {"provider": "openai", "base_url": url, "model": "test"})
    assert result.error


def test_loopback_http_supports_ipv4_ipv6_and_localhost(monkeypatch):
    monkeypatch.setattr(
        "backend.llm_clients._request_json",
        lambda *args: {"choices": [{"message": {"content": "ok"}}]},
    )
    for url in ("http://localhost:1234/v1", "http://127.0.0.1:1234/v1", "http://[::1]:1234/v1"):
        assert complete_chat(MESSAGES, {"provider": "openai_compatible", "base_url": url}).error is None


def test_cross_origin_redirect_with_credentials_is_refused():
    request = urllib.request.Request("https://api.example.test/v1")
    request.add_header("Authorization", "Bearer secret")
    with pytest.raises(urllib.error.HTTPError, match="cross-origin"):
        SafeRedirectHandler().redirect_request(
            request,
            io.BytesIO(),
            307,
            "redirect",
            {},
            "https://other.example.test/v1",
        )


def test_cross_origin_redirect_without_credentials_is_refused():
    request = urllib.request.Request("https://api.example.test/v1")
    with pytest.raises(urllib.error.HTTPError, match="cross-origin"):
        SafeRedirectHandler().redirect_request(
            request,
            io.BytesIO(),
            307,
            "redirect",
            {},
            "https://other.example.test/v1",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("temperature", -0.1), ("temperature", 2.1), ("max_tokens", 0), ("max_tokens", 32769), ("timeout", 0), ("timeout", 601)],
)
def test_provider_numeric_limits(field, value):
    result = complete_chat(MESSAGES, {
        "provider": "openai_compatible",
        "base_url": "http://127.0.0.1:1234/v1",
        field: value,
    })
    assert result.error and field in result.error


def test_scrub_provider_never_returns_credentials():
    assert scrub_provider({"provider": "anthropic", "api_key": "secret"})["api_key"] == "***"


def test_provider_response_size_is_bounded(monkeypatch):
    from backend import config

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Opener:
        def open(self, request, timeout):
            return Response(b"{}x")

    monkeypatch.setattr(config, "MAX_LLM_RESPONSE_BYTES", 2)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="response exceeds"):
        _request_json("https://example.test/v1", {}, {}, 10)
