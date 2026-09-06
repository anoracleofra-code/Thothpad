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


def test_openrouter_missing_key_is_actionable_without_network(monkeypatch):
    monkeypatch.setattr("backend.llm_clients._request_json", lambda *args: pytest.fail("must not send"))
    result = complete_chat(MESSAGES, {
        "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1",
        "model": "example/model:free", "_desktop_no_environment": True,
    })
    assert result.error and "including for :free" in result.error and "Save" in result.error


@pytest.mark.parametrize("status", [401, 402, 403, 404, 429, 500])
def test_http_failures_keep_status_without_leaking_response_or_key(status, monkeypatch):
    def fail(*args):
        raise urllib.error.HTTPError("https://secret.example", status, "secret echoed", {}, io.BytesIO(b"private text"))
    monkeypatch.setattr("backend.llm_clients._request_json", fail)
    result = complete_chat(MESSAGES, {
        "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "api_key": "private-key",
    })
    assert result.error and f"HTTP {status}:" in result.error
    assert "private" not in result.error and "secret" not in result.error


def test_opencode_unavailable_model_is_actionable_without_echoing_provider_text(monkeypatch):
    def fail(*args):
        body = b'{"error":{"message":"Upstream request failed: Model is unavailable. private-key"}}'
        raise urllib.error.HTTPError("https://opencode.ai", 400, "", {}, io.BytesIO(body))
    monkeypatch.setattr("backend.llm_clients._request_json", fail)
    result = complete_chat(MESSAGES, {"provider": "opencode_zen", "model": "deepseek-v4-flash-free", "api_key": "private-key"})
    assert result.error and "unavailable upstream" in result.error
    assert "private" not in result.error


def test_null_completion_is_not_the_literal_text_none(monkeypatch):
    monkeypatch.setattr("backend.llm_clients._request_json", lambda *args: {"choices": [{"message": {"content": None}}]})
    result = complete_chat(MESSAGES, {"provider": "openai_compatible"})
    assert result.text == "" and result.error == "provider returned no text"


@pytest.mark.parametrize(("kind", "model", "suffix", "response"), [
    ("opencode_zen", "test-model-free", "/chat/completions", {"choices": [{"message": {"content": "Hello"}}]}),
    ("opencode_zen", "muse-test-free", "/responses", {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]}),
    ("opencode_zen", "claude-test", "/messages", {"content": [{"type": "text", "text": "Hello"}]}),
    ("opencode_zen", "gemini-test", "/models/gemini-test:generateContent", {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}),
    ("opencode_zen", "minimax-test", "/chat/completions", {"choices": [{"message": {"content": "Hello"}}]}),
    ("opencode_go", "minimax-test", "/messages", {"content": [{"type": "text", "text": "Hello"}]}),
    ("opencode_go", "qwen-test", "/messages", {"content": [{"type": "text", "text": "Hello"}]}),
    ("opencode_go", "gpt-test", "/responses", {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello"}]}]}),
])
def test_opencode_wire_formats_and_credentials(kind, model, suffix, response, monkeypatch):
    calls = []
    def request(url, payload, headers, timeout):
        calls.append((url, payload, headers))
        return response
    monkeypatch.setattr("backend.llm_clients._request_json", request)
    result = complete_chat(MESSAGES, {"provider": kind, "model": model, "api_key": "test-key"})
    assert result.text == "Hello" and result.error is None
    base = "https://opencode.ai/zen" + ("/go" if kind == "opencode_go" else "") + "/v1"
    assert calls[0][0] == base + suffix
    assert any("test-key" in value for value in calls[0][2].values())
    if suffix == "/responses":
        assert calls[0][1]["store"] is False
        assert calls[0][1]["input"] == MESSAGES
    assert len(calls) == 1


@pytest.mark.parametrize(("kind", "model"), [
    ("opencode_zen", "paid-model"), ("opencode_zen", "example-free"), ("opencode_go", "example-free"),
])
def test_opencode_never_uses_paid_models_without_a_key(kind, model, monkeypatch):
    monkeypatch.setattr("backend.llm_clients._request_json", lambda *args: pytest.fail("must not send"))
    result = complete_chat(MESSAGES, {"provider": kind, "model": model})
    assert result.error and "API key" in result.error


def test_opencode_rejects_credential_retargeting(monkeypatch):
    monkeypatch.setattr("backend.llm_clients._request_json", lambda *args: pytest.fail("must not send"))
    result = complete_chat(MESSAGES, {"provider": "opencode_go", "model": "test", "api_key": "secret", "base_url": "https://elsewhere.test"})
    assert result.error and "official endpoint" in result.error


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
