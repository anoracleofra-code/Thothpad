from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time

import pytest

from backend import config
from backend.analyzers.calibration import CalibrationAnalyzer
from backend.analyzers.external_tools import ExternalToolsAnalyzer
from backend.desktop_engine import analyze_text
from backend.external_policy import approved_external_tools
from backend.llm_clients import complete_chat
from backend.sidecar import (
    MAX_HEADER_BYTES,
    MAX_HEADER_COUNT,
    PROTOCOL_MAJOR,
    ProtocolError,
    SidecarServer,
    _terminate_process_tree,
    dispatch,
    encode_frame,
    read_frame,
)


def request(operation, **params):
    return {
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": 0,
        "request_id": "request-1",
        "document_id": "document-1",
        "document_revision": 3,
        "operation": operation,
        "params": params,
    }


def test_request_endpoint_cannot_retarget_environment_credential(monkeypatch):
    from backend.llm_clients import _provider

    monkeypatch.setattr(config, "DEFAULT_PROVIDER_CONFIG", {
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "ENV-SECRET-SENTINEL",
        "model": "configured",
        "temperature": 0.7,
    })
    provider = _provider({"base_url": "https://caller.example/v1"})
    assert provider["api_key"] == ""


def test_mcp_rejects_string_false_persistence():
    from backend.mcp_server import tool_call

    with pytest.raises(ValueError, match="persist must be a JSON boolean"):
        tool_call("prose_diagnose", {"text": "Draft", "persist": "false"})


def test_languagetool_redirect_requires_approved_target():
    import urllib.error
    import urllib.request

    from backend.analyzers.external_tools import _ApprovedRedirectHandler

    request_value = urllib.request.Request(
        "https://approved.example/check", data=b"text=Draft", method="POST"
    )
    with pytest.raises(urllib.error.HTTPError, match="not approved"):
        _ApprovedRedirectHandler({"https://approved.example/check"}).redirect_request(
            request_value,
            None,
            302,
            "redirect",
            {},
            "https://unapproved.example/check",
        )


def test_one_shot_worker_enforces_response_limit():
    payload = request(
        "analyze_document",
        text="It was not fear but memory. " * 200,
        profile="creative-default",
        confirm_adverbs=False,
        persist=False,
    )
    environment = dict(os.environ)
    environment["THOTHPAD_MAX_RESPONSE_BYTES"] = "1024"
    process = subprocess.run(
        [sys.executable, "-m", "backend.sidecar", "--thothpad-worker"],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        timeout=20,
        env=environment,
        check=False,
    )
    assert process.returncode == 0
    assert len(process.stdout) <= 1024
    response = json.loads(process.stdout)
    assert response["ok"] is False
    assert "response exceeds" in response["message"]


@pytest.mark.parametrize(
    "payload",
    [
        b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
        (b"X: y\r\n" * (MAX_HEADER_COUNT + 1)) + b"\r\n",
        b"X: " + (b"a" * MAX_HEADER_BYTES) + b"\r\n\r\n",
        b"Content-Length: 11\r\n\r\n{\"x\": NaN}",
    ],
)
def test_frame_rejects_duplicate_excessive_and_nonfinite_input(payload):
    with pytest.raises(ProtocolError):
        read_frame(io.BytesIO(payload))


@pytest.mark.parametrize(
    "request_id",
    ["", "has a space", "../escape", "x" * 129, 7, True],
)
def test_sidecar_rejects_unbounded_or_non_identifier_request_ids(request_id):
    message = request("initialize")
    message["request_id"] = request_id
    writer = io.BytesIO()
    server = SidecarServer(io.BytesIO(encode_frame(message)), writer)

    assert server.serve() == 0
    writer.seek(0)
    response = read_frame(writer)
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "message",
    [
        request("analyze_document", text="Draft", persist="false"),
        request("analyze_document", text="Draft", confirm_adverbs="true"),
        request("export_profile", name="creative-default", overwrite="false"),
        request(
            "rewrite",
            text="Draft",
            provider={"base_url": "https://example.test/v1"},
            consent="true",
        ),
    ],
)
def test_desktop_booleans_must_be_json_booleans(message):
    with pytest.raises(ValueError, match="JSON boolean"):
        dispatch(message)


def test_desktop_provider_does_not_inherit_default_api_key(monkeypatch):
    captured = {}
    monkeypatch.setattr(config, "DEFAULT_PROVIDER_CONFIG", {
        "provider": "openai_compatible",
        "base_url": "https://default.example/v1",
        "api_key": "inherited-secret",
        "model": "default",
        "temperature": 0.7,
    })
    monkeypatch.setattr(
        "backend.llm_clients._request_json",
        lambda url, payload, headers, timeout: captured.update(headers=headers) or {
            "choices": [{"message": {"content": "ok"}}]
        },
    )
    result = complete_chat(
        [{"role": "user", "content": "Draft"}],
        {
            "_desktop_no_environment": True,
            "provider": "openai_compatible",
            "base_url": "https://caller.example/v1",
        },
    )
    assert result.error is None
    assert captured["headers"]["Authorization"] == ""


def test_remote_desktop_provider_requires_explicit_consent(monkeypatch):
    monkeypatch.setattr("backend.sidecar.run_pipeline", lambda value: {"provider": value.provider})
    remote = request(
        "rewrite",
        text="Draft",
        provider={"provider": "openai_compatible", "base_url": "https://example.test/v1"},
    )
    with pytest.raises(ValueError, match="explicit consent"):
        dispatch(remote)
    remote["params"]["consent"] = True
    provider = dispatch(remote)["provider"]
    assert provider["_desktop_no_environment"] is True
    assert "api_key" not in provider


def test_external_tools_require_consent_trusted_paths_and_approved_endpoints(tmp_path):
    executable = tmp_path / "vale.exe"
    executable.write_bytes(b"")
    with pytest.raises(ValueError, match="explicit consent"):
        approved_external_tools({"vale_path": str(executable)})
    with pytest.raises(ValueError, match="absolute executable"):
        approved_external_tools({"consent": True, "vale_path": "vale"})
    with pytest.raises(ValueError, match="approved_endpoints"):
        approved_external_tools({
            "consent": True,
            "languagetool": {"url": "https://language.example/v2/check", "approved_endpoints": []},
        })
    approved = approved_external_tools({"consent": True, "vale_path": str(executable)})
    assert approved == {"vale_path": str(executable.resolve())}


def test_ordinary_analysis_never_invokes_external_tools(monkeypatch):
    monkeypatch.setattr(
        ExternalToolsAnalyzer,
        "analyze",
        lambda *args: (_ for _ in ()).throw(AssertionError("external analyzer invoked")),
    )
    analyze_text("Mara counted twelve coins.", preset="full")


def test_external_subprocess_receives_sanitized_environment(tmp_path, monkeypatch):
    executable = tmp_path / "vale.exe"
    executable.write_bytes(b"")
    captured = {}

    class Result:
        stdout = "{}"

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return Result()

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setattr("backend.analyzers.external_tools.subprocess.run", fake_run)
    ExternalToolsAnalyzer().analyze("Draft", {"_approved_external_tools": {"vale_path": str(executable)}})
    assert "OPENAI_API_KEY" not in captured["env"]
    assert "PATH" not in captured["env"]


def test_desktop_profile_exchange_rejects_paths(tmp_path):
    with pytest.raises(ValueError, match="JSON only"):
        dispatch(request("import_profile", path=str(tmp_path / "profile.json")))
    with pytest.raises(ValueError, match="returns JSON only"):
        dispatch(request("export_profile", name="creative-default", path=str(tmp_path / "profile.json")))


def test_calibration_profile_cannot_escape_user_calibration_directory():
    result = CalibrationAnalyzer().analyze("Draft", {"calibration_profile": "../../outside.json"})
    assert result.metrics["active"] is False
    assert "not a path" in result.metrics["error"]


def test_oversized_response_keeps_request_correlation(monkeypatch):
    monkeypatch.setattr(config, "MAX_RESPONSE_BYTES", 512)
    writer = io.BytesIO()
    server = SidecarServer(io.BytesIO(), writer)
    server._write({
        "request_id": "large-1",
        "document_id": "doc-1",
        "document_revision": 8,
        "ok": True,
        "result": {"text": "x" * 5_000},
    })
    writer.seek(0)
    response = read_frame(writer)
    assert response["request_id"] == "large-1"
    assert response["document_id"] == "doc-1"
    assert response["document_revision"] == 8
    assert response["error"]["code"] == "response_too_large"


def test_shutdown_waits_for_accepted_thread_and_releases_slot(monkeypatch):
    from backend import sidecar

    original = sidecar.dispatch

    def slow_dispatch(message, **kwargs):
        if message["operation"] == "analyze_region":
            time.sleep(0.03)
        return original(message, **kwargs)

    monkeypatch.setattr(sidecar, "dispatch", slow_dispatch)
    slow = request("analyze_region", text="Mara noticed it.")
    slow["request_id"] = "slow"
    shutdown = request("shutdown")
    shutdown["request_id"] = "shutdown"
    reader = io.BytesIO(encode_frame(slow) + encode_frame(shutdown))
    writer = io.BytesIO()
    server = SidecarServer(reader, writer)
    assert server.serve() == 0
    assert server._inflight == {}
    writer.seek(0)
    responses = []
    while response := read_frame(writer):
        responses.append(response)
    by_id = {response["request_id"]: response for response in responses}
    assert "cancelled" in by_id["slow"]["error"]["message"]
    assert by_id["shutdown"]["result"]["shutting_down"] is True


def test_process_tree_termination_reaches_worker_descendants():
    child_source = "import time; time.sleep(60)"
    parent_source = (
        "import subprocess,sys,time; "
        f"child=subprocess.Popen([sys.executable,'-c',{child_source!r}]); "
        "print(child.pid,flush=True); time.sleep(60)"
    )
    creation_flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_source],
        stdout=subprocess.PIPE,
        text=True,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )
    assert parent.stdout is not None
    child_pid = int(parent.stdout.readline().strip())
    try:
        _terminate_process_tree(parent)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except OSError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("worker descendant survived process-tree termination")
        assert parent.poll() is not None
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)


def test_utf16_index_is_constructed_once_per_desktop_input(monkeypatch):
    from backend import text_utils

    original = text_utils.Utf16Index
    calls = 0

    def counted(text):
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(text_utils, "Utf16Index", counted)
    analyze_text("Mara \U0001f600 noticed it and moved quickly.", preset="live")
    assert calls == 1
