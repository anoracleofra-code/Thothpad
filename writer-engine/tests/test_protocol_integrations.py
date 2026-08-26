from __future__ import annotations

import io
import json
import sys

from fastapi.testclient import TestClient

from backend import mcp_server
from backend.build_sidecar import smoke_check
from backend.main import app


def test_mcp_error_preserves_parsed_request_id(monkeypatch):
    request = {"jsonrpc": "2.0", "id": "mcp-17", "method": "tools/call", "params": {"name": "missing", "arguments": {}}}
    reader = io.StringIO(json.dumps(request) + "\n")
    writer = io.StringIO()
    monkeypatch.setattr(sys, "stdin", reader)
    monkeypatch.setattr(sys, "stdout", writer)
    assert mcp_server.main() == 0
    response = json.loads(writer.getvalue())
    assert response["id"] == "mcp-17"
    assert "error" in response


def test_api_engine_validation_errors_are_4xx():
    response = TestClient(app).post(
        "/api/diagnose",
        json={"text": "Draft", "profile": "../../outside"},
    )
    assert response.status_code == 422
    assert "Profile names" in response.json()["detail"]


def test_pyinstaller_build_smoke_checks_required_inputs():
    result = smoke_check(require_spacy_model=False)
    assert result["ready"] is True
    assert str(result["spec"]).endswith("writer-engine.spec")
