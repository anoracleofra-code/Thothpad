"""Optional Codex app-server adapter. Never reads or copies another app's tokens."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from backend import config
from backend.process_supervisor import _close_windows_handle, _create_windows_kill_job


def codex_executable() -> str:
    configured = os.environ.get("THOTHPAD_CODEX_EXECUTABLE")
    if configured and Path(configured).is_file() and Path(configured).name.lower() in {"codex", "codex.exe"}:
        return configured
    found = shutil.which("codex")
    if found and Path(found).suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return found
    # npm's Windows shim requires a shell. Use the installed native binary instead.
    roots = [Path(found).parent] if found else []
    if os.name == "nt" and os.environ.get("APPDATA"):
        roots.append(Path(os.environ["APPDATA"]) / "npm")
    if os.name == "nt" and os.environ.get("USERPROFILE"):
        native = Path(os.environ["USERPROFILE"]) / ".local" / "bin" / "codex.exe"
        if native.is_file():
            return str(native)
    for root in roots:
        package = root / "node_modules" / "@openai" / "codex"
        for candidate in sorted(package.glob("node_modules/@openai/codex-*/vendor/*/bin/codex.exe")):
            if candidate.is_file():
                return str(candidate)
    raise ValueError("Install the official Codex CLI to use ChatGPT sign-in, then reopen Model Settings.")


class CodexSession:
    def __init__(self, timeout: float = 25):
        executable = codex_executable()
        self.deadline = time.monotonic() + timeout
        self._counter = 0
        self._job: int | None = None
        self._events: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=512)
        self._deferred: deque[dict[str, Any]] = deque()
        self._stopped = threading.Event()
        self._directory = tempfile.TemporaryDirectory(prefix="thothpad-codex-")
        # This child-only home isolates login, logout, plugins and settings from
        # the user's Codex app/CLI. No subscription credentials are copied.
        auth_home = config.DATA_DIR / "codex"
        auth_home.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not any(
            marker in k.upper() for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
        )}
        env["CODEX_HOME"] = str(auth_home)
        args = [executable, "app-server", "--listen", "stdio://",
                "-c", 'cli_auth_credentials_store="keyring"',
                "-c", 'forced_login_method="chatgpt"',
                "-c", 'model_provider="openai"', "-c", 'web_search="disabled"',
                "-c", "project_doc_max_bytes=0", "-c", "tools.view_image=false"]
        for feature in ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "multi_agent",
                        "browser_use", "computer_use", "view_image", "image_generation", "code_mode",
                        "code_mode_host", "shell_snapshot", "memories", "skill_search", "workspace_dependencies"):
            args.extend(["--disable", feature])
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
            self.process = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=self._directory.name, env=env,
                creationflags=creationflags,
            )
            if os.name == "nt":
                self._job = _create_windows_kill_job(self.process.pid)
            self._reader = threading.Thread(target=self._read, daemon=True)
            self._reader.start()
            self.call("initialize", {"clientInfo": {"name": "thothpad", "version": config.ENGINE_VERSION}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _read(self) -> None:
        assert self.process.stdout is not None
        total_bytes = 0
        try:
            while not self._stopped.is_set():
                line = self.process.stdout.readline(config.MAX_LLM_RESPONSE_BYTES + 1)
                if not line or len(line) > config.MAX_LLM_RESPONSE_BYTES:
                    break
                total_bytes += len(line)
                if total_bytes > config.MAX_LLM_RESPONSE_BYTES:
                    break
                event = json.loads(line)
                if not isinstance(event, dict):
                    break
                self._events.put_nowait(event)
        except (ValueError, OSError, queue.Full):
            pass
        finally:
            try:
                self._events.put_nowait(None)
            except queue.Full:
                pass

    def send(self, value: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(value).encode("utf-8") + b"\n")
        self.process.stdin.flush()

    def event(self, wait: bool = True) -> dict[str, Any]:
        if self._deferred:
            return self._deferred.popleft()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Codex timed out. Try again or increase the model timeout.")
        try:
            value = self._events.get(timeout=remaining) if wait else self._events.get_nowait()
        except queue.Empty:
            if not wait:
                return {}
            raise ValueError("Codex timed out.") from None
        if value is None:
            raise ValueError("Codex stopped. Check that the official CLI is up to date and sign in again.")
        if "method" in value and "id" in value:
            # This integration never grants native tools, approvals or permission expansion.
            self.send({"id": value["id"], "error": {"code": -32601, "message": "Not available in ThothPad"}})
        return value

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._counter += 1
        request_id = self._counter
        self.send({"id": request_id, "method": method, "params": params})
        deferred: list[dict[str, Any]] = []
        try:
            while True:
                value = self.event()
                if value.get("id") == request_id and "method" not in value:
                    if "error" in value:
                        # Provider errors may contain prompts or authentication details.
                        raise ValueError(f"Codex could not complete {method}. Check sign-in and CLI compatibility.")
                    result = value.get("result")
                    if not isinstance(result, dict):
                        raise ValueError("Invalid Codex response.")
                    return result
                if "id" not in value:
                    deferred.append(value)
        finally:
            self._deferred.extend(deferred)

    def require_login(self) -> None:
        account = self.call("account/read", {"refreshToken": False}).get("account")
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            raise ValueError("Choose Sign in in Model Settings to connect your ChatGPT account to ThothPad.")

    def models(self) -> list[str]:
        self.require_login()
        result: list[str] = []
        cursor = None
        for _ in range(20):
            page = self.call("model/list", {"limit": 100, "includeHidden": False, "cursor": cursor})
            for row in page.get("data", []):
                if isinstance(row, dict) and isinstance(row.get("model"), str):
                    result.append(row["model"])
            cursor = page.get("nextCursor")
            if not cursor:
                return list(dict.fromkeys(result))
        raise ValueError("Codex returned too many model pages.")

    def complete(self, messages: list[dict[str, str]], model: str) -> str:
        self.require_login()
        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        conversation = [{"role": m.get("role"), "content": m.get("content", "")}
                        for m in messages if m.get("role") != "system"]
        thread = self.call("thread/start", {
            "model": model, "cwd": self._directory.name, "ephemeral": True,
            "approvalPolicy": "never", "sandbox": "readOnly", "baseInstructions": system,
            "developerInstructions": "Return only the requested writing response. Do not use native tools.",
        })
        info = thread.get("thread")
        if not isinstance(info, dict) or not isinstance(info.get("id"), str):
            raise ValueError("Codex returned an invalid conversation identifier.")
        thread_id = info["id"]
        self.call("turn/start", {"threadId": thread_id, "input": [{
            "type": "text", "text": json.dumps(conversation, ensure_ascii=False),
        }]})
        text = ""
        while True:
            event = self.event()
            params = event.get("params", {})
            if params.get("threadId") != thread_id:
                continue
            if event.get("method") == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage":
                    text = str(item.get("text", ""))
            if event.get("method") == "turn/completed":
                if params.get("turn", {}).get("status") != "completed":
                    raise ValueError("Codex did not complete the response. Check your allowance and sign-in.")
                if not text.strip():
                    raise ValueError("Codex returned no text.")
                return text

    def close(self) -> None:
        self._stopped.set()
        process = getattr(self, "process", None)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()
        _close_windows_handle(self._job)
        self._job = None
        self._directory.cleanup()

    def __enter__(self) -> CodexSession:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
