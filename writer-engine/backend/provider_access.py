"""Explicit model discovery and browser sign-in for the native settings dialog."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from backend.codex_provider import CodexSession

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_BASE = "https://generativelanguage.googleapis.com/v1beta"
MAX_BODY = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def request_json(url: str, headers: dict[str, str] | None = None, *,
                 form: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = dict(headers or {})
    if any(any(ord(c) < 32 or ord(c) > 126 for c in value) for value in headers.values()):
        raise ValueError("The provider credential contains invalid header characters.")
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    deadline = time.monotonic() + 25
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            chunks = []
            size = 0
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError("Provider response timed out.")
                chunk = response.read1(min(65536, MAX_BODY + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BODY:
                    raise ValueError("Provider response was too large.")
            raw = b"".join(chunks)
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Invalid provider response.")
        return result
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("Authorization failed. Enter a valid provider API key or sign in again.") from None
        raise ValueError(f"Provider request failed (HTTP {exc.code}). Try again later.") from None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise ValueError("Provider request failed. Check your connection and try again.") from None


def google_headers(secret: str, base_url: str) -> dict[str, str]:
    if base_url.rstrip("/") != GOOGLE_BASE:
        raise ValueError("Google OAuth credentials can only be used with the official Gemini endpoint.")
    try:
        credential = json.loads(secret)
        if not isinstance(credential, dict) or credential.get("type") != "thothpad_google_oauth":
            raise ValueError
        fields = {key: credential[key] for key in ("client_id", "client_secret", "refresh_token", "project_id")}
        if any(not isinstance(v, str) or not v or len(v) > 8192 or any(c in v for c in "\r\n")
               for v in fields.values()):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ValueError("Google sign-in is missing or invalid. Sign in again in Model Settings.") from None
    tokens = request_json(GOOGLE_TOKEN_URL, form={
        "grant_type": "refresh_token", "client_id": fields["client_id"],
        "client_secret": fields["client_secret"], "refresh_token": fields["refresh_token"],
    })
    access_token = tokens.get("access_token")
    if (not isinstance(access_token, str) or not access_token or len(access_token) > 16384
            or any(ord(c) < 33 or ord(c) > 126 for c in access_token)):
        raise ValueError("Google did not return an access token. Sign in again.")
    return {"Authorization": "Bearer " + access_token, "x-goog-user-project": fields["project_id"]}


class BrowserLogin:
    def __init__(self, provider: str, client: Any = None):
        self.provider = provider
        self.result: dict[str, Any] = {"pending": True}
        self.cancelled = threading.Event()
        self.expires = time.monotonic() + 600
        self.verifier = secrets.token_urlsafe(48)
        self.state = secrets.token_urlsafe(32)
        self.path = "/callback/" + secrets.token_urlsafe(24)
        self.client: dict[str, str] = {}
        if provider == "gemini_oauth":
            installed = client.get("installed") if isinstance(client, dict) else None
            if not isinstance(installed, dict):
                raise ValueError("Import a Google OAuth client JSON for a Desktop app, not a web client.")
            for key in ("client_id", "client_secret", "project_id"):
                value = installed.get(key)
                if not isinstance(value, str) or not value or len(value) > 4096 or any(c in value for c in "\r\n"):
                    raise ValueError("Google Desktop OAuth client JSON is incomplete.")
                self.client[key] = value
        elif provider != "openrouter":
            raise ValueError("Browser OAuth is not supported for this provider.")
        session = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *args: Any) -> None:
                pass  # Callback URLs contain single-use secrets: never log them.

            def do_GET(self) -> None:
                url = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(url.query)
                valid = (url.path == session.path and len(self.path) < 16384
                         and not session.cancelled.is_set())
                if session.provider == "gemini_oauth":
                    valid = valid and hmac.compare_digest(query.get("state", [""])[0], session.state)
                if not valid:
                    self.send_error(404)
                    return
                code = query.get("code", [""])[0]
                try:
                    if query.get("error") or not code or len(code) > 8192:
                        raise ValueError("Sign-in was cancelled or refused. Try Sign in again.")
                    credential = session.exchange(code)
                    if not session.cancelled.is_set():
                        session.result = {"pending": False, "credential": credential}
                    message = b"Sign-in complete. Return to ThothPad and click Save to keep this connection."
                except (ValueError, OSError):
                    session.result = {"pending": False, "error": "Sign-in failed or was refused. Please try again."}
                    message = b"Sign-in failed. Return to ThothPad and try again."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(message)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(message)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.server.timeout = 0.25
        self.callback = f"http://127.0.0.1:{self.server.server_port}{self.path}"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")
        params = {"code_challenge": challenge, "code_challenge_method": "S256"}
        if provider == "openrouter":
            params["callback_url"] = self.callback
            self.url = "https://openrouter.ai/auth?" + urllib.parse.urlencode(params)
        else:
            params.update(client_id=self.client["client_id"], redirect_uri=self.callback,
                          response_type="code", access_type="offline", prompt="consent", state=self.state,
                          scope="https://www.googleapis.com/auth/cloud-platform "
                                "https://www.googleapis.com/auth/generative-language.retriever")
            self.url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            while self.result.get("pending") and not self.cancelled.is_set() and time.monotonic() < self.expires:
                self.server.handle_request()
            if self.result.get("pending"):
                self.result = {"pending": False, "error": "Sign-in expired or was cancelled. Try again."}
        finally:
            self.server.server_close()

    def exchange(self, code: str) -> str:
        if self.provider == "openrouter":
            result = request_json("https://openrouter.ai/api/v1/auth/keys", body={
                "code": code, "code_verifier": self.verifier, "code_challenge_method": "S256",
            })
            key = result.get("key")
            if (not isinstance(key, str) or not key or len(key) > 8192
                    or any(ord(c) < 33 or ord(c) > 126 for c in key)):
                raise ValueError("OpenRouter did not return an API key.")
            return key
        result = request_json(GOOGLE_TOKEN_URL, form={
            "code": code, "code_verifier": self.verifier, "redirect_uri": self.callback,
            "grant_type": "authorization_code", "client_id": self.client["client_id"],
            "client_secret": self.client["client_secret"],
        })
        refresh = result.get("refresh_token")
        if not isinstance(refresh, str) or not refresh or len(refresh) > 8192:
            raise ValueError("Google did not grant offline access. Try signing in again.")
        return json.dumps({"type": "thothpad_google_oauth", **self.client, "refresh_token": refresh})

    def close(self) -> None:
        self.cancelled.set()


_sessions: dict[str, BrowserLogin | CodexSession] = {}
_lock = threading.Lock()


def model_catalog(provider: dict[str, Any]) -> list[str]:
    from backend.llm_clients import DEFAULT_ENDPOINTS, SUPPORTED_KINDS, _validated_url

    kind = str(provider.get("provider", ""))
    if kind not in SUPPORTED_KINDS:
        raise ValueError("Unsupported model provider.")
    if kind == "codex":
        with CodexSession() as codex:
            return codex.models()
    base = _validated_url(provider.get("base_url"))
    if urllib.parse.urlsplit(base).query:
        raise ValueError("Use a base endpoint without a query string.")
    key = str(provider.get("api_key", ""))
    if kind == "gemini_oauth":
        headers = google_headers(key, base)
    elif kind == "gemini":
        headers = {"x-goog-api-key": key}
    elif kind == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        headers = {"Authorization": "Bearer " + key} if key else {}
    if kind in {"openai", "gemini", "anthropic"} and not key:
        raise ValueError("Enter your provider API key to update its model list.")
    if kind == "openrouter":
        base = "https://openrouter.ai/api/v1"
        headers = {}
    if kind in {"opencode_zen", "opencode_go"}:
        base = DEFAULT_ENDPOINTS[kind]
        headers = {"Cache-Control": "no-cache"}
    suffix = "/tags" if kind == "ollama" else "/models"
    url = base + suffix
    models: list[tuple[int, str]] = []
    pagination: dict[str, str] = {}
    seen: set[str] = set()
    deadline = time.monotonic() + 25
    for _ in range(20):
        if time.monotonic() > deadline:
            raise ValueError("Model discovery timed out. Your old list is unchanged.")
        page = request_json(url + ("?" + urllib.parse.urlencode(pagination) if pagination else ""), headers)
        rows = page.get("models" if kind in {"gemini", "gemini_oauth", "ollama"} else "data")
        if not isinstance(rows, list):
            raise ValueError("Provider returned an invalid model list.")
        for row in rows:
            if not isinstance(row, dict):
                continue
            if kind in {"gemini", "gemini_oauth"}:
                if "generateContent" not in row.get("supportedGenerationMethods", []):
                    continue
                model_id = str(row.get("name", "")).removeprefix("models/")
            else:
                model_id = row.get("name" if kind == "ollama" else "id", "")
            if isinstance(model_id, str) and model_id.strip() and len(model_id) <= 256 and "\x00" not in model_id:
                if len(models) >= 10000:
                    raise ValueError("Provider model catalog exceeds the 10,000-model limit.")
                created = row.get("created", 0)
                models.append((created if isinstance(created, int) else 0, model_id.strip()))
        token = page.get("nextPageToken") if kind in {"gemini", "gemini_oauth"} else None
        if kind == "anthropic" and page.get("has_more"):
            token = page.get("last_id")
            if not token:
                raise ValueError("Provider returned incomplete pagination.")
        if not token:
            def model_order(item: tuple[int, str]) -> tuple[bool, int, str]:
                free = item[1].endswith(":free") if kind == "openrouter" else (
                    kind in {"opencode_zen", "opencode_go"} and (item[1].endswith("-free") or item[1] == "big-pickle")
                )
                return not free, -item[0], item[1]

            models.sort(key=model_order)
            ids = list(dict.fromkeys(item[1] for item in models))
            if not ids:
                raise ValueError("Provider returned no usable text models. Your old list is unchanged.")
            return ids
        if not isinstance(token, str) or len(token) > 4096 or token in seen:
            raise ValueError("Provider returned invalid pagination.")
        seen.add(token)
        pagination = {"after_id" if kind == "anthropic" else "pageToken": token}
    raise ValueError("Provider returned too many model pages.")


def provider_access(params: dict[str, Any]) -> dict[str, Any]:
    action = params.get("action")
    if action == "models":
        provider = params.get("provider")
        if not isinstance(provider, dict):
            raise ValueError("Provider settings are required.")
        return {"models": model_catalog(provider)}
    if action == "logout_codex":
        with CodexSession() as codex:
            codex.call("account/logout", {})
        return {"signed_out": True}
    with _lock:
        for key, old in list(_sessions.items()):
            expires = old.expires if isinstance(old, BrowserLogin) else old.deadline
            if time.monotonic() >= expires:
                old.close()
                del _sessions[key]
        if action == "begin":
            if len(_sessions) >= 4:
                raise ValueError("Another sign-in is already running. Cancel it first.")
            kind = params.get("kind")
            session: BrowserLogin | CodexSession
            if kind == "codex":
                session = CodexSession()
                try:
                    login = session.call("account/login/start", {"type": "chatgpt"})
                    url = str(login.get("authUrl", ""))
                    parsed = urllib.parse.urlsplit(url)
                    if parsed.scheme != "https" or parsed.hostname not in {"auth.openai.com", "chatgpt.com"}:
                        raise ValueError("Codex returned an unexpected sign-in URL.")
                    session.deadline = time.monotonic() + 600
                except Exception:
                    session.close()
                    raise
            else:
                session = BrowserLogin(str(kind), params.get("client"))
                url = session.url
            session_id = secrets.token_urlsafe(32)
            _sessions[session_id] = session
            return {"session_id": session_id, "url": url}
        session_id = str(params.get("session_id", ""))
        session = _sessions.get(session_id)  # type: ignore[assignment]
        if session is None:
            raise ValueError("Sign-in session expired. Try Sign in again.")
        if action == "cancel":
            session.close()
            del _sessions[session_id]
            return {"cancelled": True}
        if action != "poll":
            raise ValueError("Unsupported provider access action.")
        if isinstance(session, BrowserLogin):
            result = session.result
        else:
            result = {"pending": True}
            while True:
                event = session.event(wait=False)
                if not event:
                    break
                if event.get("method") == "account/login/completed":
                    success = event.get("params", {}).get("success") is True
                    result = {"pending": False, "signed_in": success}
                    if not success:
                        result["error"] = "Codex sign-in failed or was cancelled. Try again."
                    break
        if not result.get("pending"):
            session.close()
            del _sessions[session_id]
        return result
