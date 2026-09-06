import io
import json
import queue
import urllib.error
import urllib.parse
import urllib.request

import pytest

from backend import codex_provider
from backend import provider_access as access
from backend.llm_clients import complete_chat, provider_is_remote, scrub_provider

CLIENT = {"installed": {"client_id": "desktop.apps.googleusercontent.com",
                        "client_secret": "test-client-secret", "project_id": "test-project"}}
GOOGLE_SECRET = json.dumps({"type": "thothpad_google_oauth", **CLIENT["installed"],
                            "refresh_token": "test-refresh-token"})


def test_google_refresh_is_official_and_never_uses_api_key_header(monkeypatch):
    calls = []

    def token_request(url, **kwargs):
        calls.append((url, kwargs))
        return {"access_token": "test-access-token"}

    monkeypatch.setattr(access, "request_json", token_request)
    captured = {}

    def inference(url, payload, headers, timeout):
        captured.update(url=url, headers=headers)
        return {"candidates": [{"content": {"parts": [{"text": "Revised prose."}]}}]}

    monkeypatch.setattr("backend.llm_clients._request_json", inference)
    result = complete_chat([{"role": "user", "content": "Test prose"}], {
        "provider": "gemini_oauth", "model": "gemini-test", "api_key": GOOGLE_SECRET,
    })
    assert result.text == "Revised prose."
    assert calls[0][0] == access.GOOGLE_TOKEN_URL
    assert calls[0][1]["form"]["grant_type"] == "refresh_token"
    assert captured["headers"] == {"Authorization": "Bearer test-access-token", "x-goog-user-project": "test-project"}
    assert scrub_provider({"provider": "gemini_oauth", "api_key": GOOGLE_SECRET})["api_key"] == "***"
    with pytest.raises(ValueError, match="official"):
        access.google_headers(GOOGLE_SECRET, "https://evil.example/v1beta")
    assert len(calls) == 1


@pytest.mark.parametrize("secret", ["", "api-key-not-oauth", "{}", "[]", '{"type":"other"}'])
def test_google_rejects_invalid_stored_credentials(secret):
    with pytest.raises(ValueError, match="Sign in again"):
        access.google_headers(secret, access.GOOGLE_BASE)


@pytest.mark.parametrize("client", [None, {}, {"web": CLIENT["installed"]}, {"installed": {"client_id": "x"}}])
def test_google_requires_desktop_registration(client):
    with pytest.raises(ValueError):
        access.BrowserLogin("gemini_oauth", client)


@pytest.mark.parametrize("kind", ["openrouter", "gemini_oauth"])
def test_oauth_pkce_callback_state_exchange_and_no_secrets_in_browser(kind, monkeypatch):
    calls = []

    def exchange(url, **kwargs):
        calls.append((url, kwargs))
        return {"key": "issued-test-key", "refresh_token": "issued-test-refresh"}

    monkeypatch.setattr(access, "request_json", exchange)
    session = access.BrowserLogin(kind, CLIENT)
    try:
        parsed = urllib.parse.urlsplit(session.url)
        query = urllib.parse.parse_qs(parsed.query)
        assert parsed.scheme == "https"
        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"][0] != session.verifier
        assert "test-client-secret" not in session.url
        assert session.server.server_address[0] == "127.0.0.1"
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(session.callback + "-wrong?code=test-code", timeout=2)
        assert rejected.value.code == 404
        assert not calls
        if kind == "gemini_oauth":
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(session.callback + "?code=test-code&state=wrong", timeout=2)
            assert not calls
        callback = session.callback + "?" + urllib.parse.urlencode({"code": "test-code", "state": session.state})
        with urllib.request.urlopen(callback, timeout=2) as response:
            body = response.read().decode()
            assert "issued-test" not in body
            assert response.headers["Cache-Control"] == "no-store"
        session.thread.join(2)
        assert not session.thread.is_alive()
        assert session.result["pending"] is False
        payload = calls[0][1]["body" if kind == "openrouter" else "form"]
        assert payload["code_verifier"] == session.verifier
        if kind == "openrouter":
            assert session.result["credential"] == "issued-test-key"
        else:
            assert json.loads(session.result["credential"])["refresh_token"] == "issued-test-refresh"
    finally:
        session.close()
        session.thread.join(2)


def test_oauth_cancel_closes_listener():
    session = access.BrowserLogin("openrouter")
    session.close()
    session.thread.join(2)
    assert not session.thread.is_alive()
    assert session.server.fileno() == -1
    assert "credential" not in session.result


@pytest.mark.parametrize(("kind", "suffix", "field", "row", "header"), [
    ("openai", "/models", "data", {"id": "model-a"}, "Authorization"),
    ("openai_compatible", "/models", "data", {"id": "model-a"}, "Authorization"),
    ("lmstudio", "/models", "data", {"id": "model-a"}, "Authorization"),
    ("llama_cpp", "/models", "data", {"id": "model-a"}, "Authorization"),
    ("ollama", "/tags", "models", {"name": "model-a"}, "Authorization"),
    ("anthropic", "/models", "data", {"id": "model-a"}, "x-api-key"),
    ("gemini", "/models", "models", {"name": "models/model-a", "supportedGenerationMethods": ["generateContent"]}, "x-goog-api-key"),
])
def test_provider_specific_model_discovery(kind, suffix, field, row, header, monkeypatch):
    calls = []

    def request(url, headers):
        calls.append((url, headers))
        return {field: [row]}

    monkeypatch.setattr(access, "request_json", request)
    assert access.model_catalog({"provider": kind, "base_url": "https://example.test/api/v1", "api_key": "test-key"}) == ["model-a"]
    assert calls[0][0] == "https://example.test/api/v1" + suffix
    assert "test-key" in calls[0][1][header]


@pytest.mark.parametrize("kind", ["openai", "anthropic", "gemini"])
def test_missing_key_is_explained_without_request(kind, monkeypatch):
    monkeypatch.setattr(access, "request_json", lambda *args: pytest.fail("must not send"))
    with pytest.raises(ValueError, match="API key"):
        access.model_catalog({"provider": kind, "base_url": "https://example.test/v1"})


@pytest.mark.parametrize("kind", ["opencode_zen", "opencode_go"])
def test_opencode_catalogs_are_public_isolated_and_free_first(kind, monkeypatch):
    calls = []
    def request(url, headers):
        calls.append((url, headers))
        return {"data": [{"id": "paid", "created": 99}, {"id": "new-free", "created": 3}, {"id": "big-pickle", "created": 1}]}
    monkeypatch.setattr(access, "request_json", request)
    assert access.model_catalog({"provider": kind, "base_url": "https://example.test/v1", "api_key": "never-send"}) == ["new-free", "big-pickle", "paid"]
    assert calls[0][0] == "https://opencode.ai/zen" + ("/go" if kind == "opencode_go" else "") + "/v1/models"
    assert "Authorization" not in calls[0][1]


def test_catalog_pagination_and_gemini_text_filter(monkeypatch):
    urls = []

    def request(url, headers):
        urls.append(url)
        if len(urls) == 1:
            return {"models": [{"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}], "nextPageToken": "opaque/next?"}
        return {"models": [{"name": "models/text", "supportedGenerationMethods": ["generateContent"]}]}

    monkeypatch.setattr(access, "request_json", request)
    assert access.model_catalog({"provider": "gemini", "base_url": access.GOOGLE_BASE, "api_key": "test"}) == ["text"]
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(urls[1]).query) == {"pageToken": ["opaque/next?"]}


def test_repeated_page_token_fails(monkeypatch):
    monkeypatch.setattr(access, "request_json", lambda *args: {"data": [{"id": "a"}], "has_more": True, "last_id": "same"})
    with pytest.raises(ValueError, match="pagination"):
        access.model_catalog({"provider": "anthropic", "base_url": "https://api.anthropic.com/v1", "api_key": "test"})


def test_catalog_remote_http_and_redirects_rejected(monkeypatch):
    monkeypatch.setattr(access, "request_json", lambda *args: pytest.fail("must not send"))
    with pytest.raises(ValueError, match="loopback"):
        access.model_catalog({"provider": "openai_compatible", "base_url": "http://remote.example/v1"})
    assert access.NoRedirect().redirect_request(None) is None


def test_codex_chat_uses_isolated_home_no_key_and_preserves_early_events(monkeypatch, tmp_path):
    events = [
        {"id": 1, "result": {}},
        {"id": 2, "result": {"account": {"type": "chatgpt"}}},
        {"id": 3, "result": {"thread": {"id": "thread-1"}}},
        {"method": "item/completed", "params": {"threadId": "thread-1", "item": {"type": "agentMessage", "text": "Rewritten."}}},
        {"method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"status": "completed"}}},
        {"id": 4, "result": {}},
    ]
    captured = {}

    class Process:
        def __init__(self, args, **kwargs):
            captured.update(args=args, **kwargs)
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b"\n".join(json.dumps(e).encode() for e in events) + b"\n")
            self.pid = 123

        def poll(self):
            return 0

        def wait(self, timeout):
            captured["requests"] = [json.loads(line) for line in self.stdin.getvalue().splitlines()]

    monkeypatch.setattr(codex_provider, "codex_executable", lambda: "/test/native-codex")
    monkeypatch.setattr(codex_provider.subprocess, "Popen", Process)
    monkeypatch.setattr(codex_provider, "_create_windows_kill_job", lambda pid: None)
    monkeypatch.setattr(codex_provider.config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-inherit")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "other-app"))
    result = complete_chat([{"role": "system", "content": "Writer rules"}, {"role": "user", "content": "Prose"}], {
        "provider": "codex", "model": "test-model",
    })
    assert result.text == "Rewritten."
    assert provider_is_remote({"provider": "codex"})
    assert provider_is_remote({"provider": "codex", "base_url": "http://localhost/v1"})
    assert "OPENAI_API_KEY" not in captured["env"]
    assert captured["env"]["CODEX_HOME"] == str(tmp_path / "codex")
    assert "--disable" in captured["args"]
    assert 'cli_auth_credentials_store="keyring"' in captured["args"]
    request = next(r for r in captured["requests"] if r.get("method") == "thread/start")
    assert request["params"]["ephemeral"] is True
    assert request["params"]["sandbox"] == "readOnly"
    assert request["params"]["approvalPolicy"] == "never"
    assert request["params"]["baseInstructions"] == "Writer rules"


def test_codex_rejects_provider_side_approval(monkeypatch):
    session = object.__new__(codex_provider.CodexSession)
    session._deferred = codex_provider.deque()
    session._events = queue.Queue()
    session.deadline = codex_provider.time.monotonic() + 5
    session._events.put({"id": "approval", "method": "item/commandExecution/requestApproval"})
    replies = []
    monkeypatch.setattr(session, "send", replies.append)
    session.event()
    assert replies[0]["error"]["code"] == -32601


def test_codex_missing_runtime_is_actionable(monkeypatch):
    monkeypatch.setattr(codex_provider, "codex_executable", lambda: (_ for _ in ()).throw(ValueError("Install the official Codex CLI")))
    result = complete_chat([{"role": "user", "content": "test"}], {"provider": "codex"})
    assert result.error == "Install the official Codex CLI"


def test_provider_access_session_cancellation_and_expiration():
    result = access.provider_access({"action": "begin", "kind": "openrouter"})
    session_id = result["session_id"]
    session = access._sessions[session_id]
    assert access.provider_access({"action": "poll", "session_id": session_id}) == {"pending": True}
    assert access.provider_access({"action": "cancel", "session_id": session_id}) == {"cancelled": True}
    session.thread.join(2)
    assert session_id not in access._sessions
    with pytest.raises(ValueError, match="expired"):
        access.provider_access({"action": "poll", "session_id": session_id})


def test_header_error_does_not_echo_credential():
    with pytest.raises(ValueError) as error:
        access.request_json("https://example.test", {"Authorization": "Bearer test-secret\r\nInjected"})
    assert "test-secret" not in str(error.value)


def test_sidecar_exposes_access_and_codex_requires_remote_consent(monkeypatch):
    from backend.sidecar import _desktop_provider, dispatch
    monkeypatch.setattr(access, "model_catalog", lambda provider: ["fake/model"])
    result = dispatch({"operation": "provider_access", "action": "models", "provider": {"provider": "codex"}})
    assert result == {"models": ["fake/model"]}
    with pytest.raises(ValueError, match="consent"):
        _desktop_provider({"provider": {"provider": "codex", "base_url": "http://localhost/v1"}, "consent": False})
