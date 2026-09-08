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


def test_story_sidecar_exposes_project_understanding_and_safe_story_tool(tmp_path):
    root = tmp_path / "story"
    root.mkdir()
    (root / "chapter.md").write_text(
        '# Chapter One\nAlice entered the room and said, "No."\n',
        encoding="utf-8",
    )
    understanding = dispatch(request("story_project_understanding", project_root=str(root)))
    assert understanding["source_count"] == 1
    result = dispatch(
        request(
            "story_tool",
            project_root=str(root),
            tool_id="get_project_understanding",
            arguments={},
        )
    )
    assert result["project_id"] == understanding["project_id"]


def test_story_sidecar_source_override_is_relative_and_immediate(tmp_path):
    root = tmp_path / "story"
    root.mkdir()
    source = root / "notes.txt"
    source.write_text("Loose unresolved notes.", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))

    with pytest.raises(ValueError, match="project-relative"):
        dispatch(
            request(
                "story_set_source_override",
                project_root=str(root),
                path="../outside.txt",
                roles=["world_reference"],
            )
        )

    result = dispatch(
        request(
            "story_set_source_override",
            project_root=str(root),
            path="notes.txt",
            roles=["world_reference"],
            authority="CONFIRMED_CANON",
        )
    )
    assert result["authority"] == "CONFIRMED_CANON"
    assert result["roles"][0]["role"] == "world_reference"


def test_story_branch_desktop_mutations_require_explicit_writer_confirmation_and_stay_out_of_mcp(tmp_path):
    from backend.mcp_server import TOOLS

    root = tmp_path / "story"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice waits.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))

    with pytest.raises(PermissionError, match="writer confirmation"):
        dispatch(
            request(
                "story_branch_create",
                project_root=str(root),
                assumptions=["Alice leaves instead"],
            )
        )

    created = dispatch(
        request(
            "story_branch_create",
            project_root=str(root),
            assumptions=["Alice leaves instead"],
            branch_id="ALT-DESKTOP",
            writer_confirmed=True,
        )
    )
    assert created["branch"]["branch_id"] == "ALT-DESKTOP"

    denied_overlay = request(
        "story_branch_add_overlay",
        project_root=str(root),
        branch_id="ALT-DESKTOP",
        record_kind="claim",
        record_id="alice-leaves",
        payload={"predicate": "leaves", "value": True},
    )
    denied_overlay["params"]["operation"] = "ADD"
    with pytest.raises(PermissionError, match="writer confirmation"):
        dispatch(denied_overlay)

    approved_overlay = request(
        "story_branch_add_overlay",
        project_root=str(root),
        branch_id="ALT-DESKTOP",
        record_kind="claim",
        record_id="alice-leaves",
        payload={"predicate": "leaves", "value": True},
        writer_confirmed=True,
    )
    approved_overlay["params"]["operation"] = "ADD"
    changed = dispatch(approved_overlay)
    assert changed["created_overlay_id"]
    listed = dispatch(
        request(
            "story_tool",
            project_root=str(root),
            tool_id="list_branches",
            arguments={},
        )
    )
    assert listed["branches"][0]["branch_id"] == "ALT-DESKTOP"

    mcp_names = {tool["name"] for tool in TOOLS}
    for operation in {
        "story_branch_create",
        "story_branch_add_overlay",
        "story_branch_rebase",
        "story_branch_prepare_merge",
        "story_branch_apply_merge",
        "story_writer_model_observe",
        "story_model_route",
    }:
        assert operation not in mcp_names


def test_story_branch_desktop_boolean_confirmation_is_strict(tmp_path):
    root = tmp_path / "story"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice waits.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))
    with pytest.raises(ValueError, match="JSON boolean"):
        dispatch(
            request(
                "story_branch_create",
                project_root=str(root),
                writer_confirmed="true",
            )
        )


def test_story_recovery_and_restore_require_writer_confirmation_and_stay_out_of_mcp(tmp_path):
    from backend.mcp_server import TOOLS

    root = tmp_path / "story-recovery"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice waits.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))
    backup = dispatch(request("story_state_backup", project_root=str(root)))
    assert backup["backup_name"].startswith("story-state-")

    with pytest.raises(PermissionError, match="writer confirmation"):
        dispatch(request("story_recover", project_root=str(root)))
    with pytest.raises(PermissionError, match="writer confirmation"):
        dispatch(
            request(
                "story_state_restore",
                project_root=str(root),
                backup_name=backup["backup_name"],
            )
        )
    with pytest.raises(ValueError, match="JSON boolean"):
        dispatch(
            request(
                "story_state_restore",
                project_root=str(root),
                backup_name=backup["backup_name"],
                writer_confirmed="true",
            )
        )
    with pytest.raises(ValueError, match="invalid Story State backup name"):
        dispatch(
            request(
                "story_state_restore",
                project_root=str(root),
                backup_name="../story-state-deadbeefdeadbeef.json",
                writer_confirmed=True,
            )
        )

    names = {tool["name"] for tool in TOOLS}
    assert "story_recover" not in names
    assert "story_state_backup" not in names
    assert "story_state_restore" not in names


def test_story_branch_apply_merge_is_atomic_durable_and_writer_confirmed(tmp_path):
    from backend.story.project import StoryProject

    root = tmp_path / "story"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice waits.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))
    dispatch(
        request(
            "story_branch_create",
            project_root=str(root),
            branch_id="ALT-APPLY",
            assumptions=["Alice leaves instead"],
            writer_confirmed=True,
        )
    )
    overlay_request = request(
        "story_branch_add_overlay",
        project_root=str(root),
        branch_id="ALT-APPLY",
        record_kind="claim",
        record_id="alice-leaves",
        payload={"predicate": "alice_leaves", "literal_value": True},
        writer_confirmed=True,
    )
    overlay_request["params"]["operation"] = "ADD"
    overlay_id = dispatch(overlay_request)["created_overlay_id"]
    prepared = dispatch(
        request(
            "story_branch_prepare_merge",
            project_root=str(root),
            branch_id="ALT-APPLY",
            overlay_ids=[overlay_id],
        )
    )

    with pytest.raises(PermissionError, match="writer confirmation"):
        dispatch(
            request(
                "story_branch_apply_merge",
                project_root=str(root),
                branch_id="ALT-APPLY",
                overlay_ids=[overlay_id],
                expected_parent_revision=prepared["expected_parent_revision"],
            )
        )

    merged = dispatch(
        request(
            "story_branch_apply_merge",
            project_root=str(root),
            branch_id="ALT-APPLY",
            overlay_ids=[overlay_id],
            expected_parent_revision=prepared["expected_parent_revision"],
            writer_confirmed=True,
        )
    )
    assert merged["branch_status"] == "MERGED"
    target_claim_id = merged["applied"][0]["target_record_id"]
    claims = dispatch(
        request(
            "story_tool",
            project_root=str(root),
            tool_id="query_claims",
            arguments={"predicate": "alice_leaves"},
        )
    )
    assert claims["claims"][0]["claim_id"] == target_claim_id
    assert claims["claims"][0]["created_by"] == "writer"

    project = StoryProject.open(root)
    project.cache_path.unlink()
    dispatch(request("story_project_understanding", project_root=str(root)))
    rebuilt = dispatch(
        request(
            "story_tool",
            project_root=str(root),
            tool_id="query_claims",
            arguments={"predicate": "alice_leaves"},
        )
    )
    assert rebuilt["claims"][0]["claim_id"] == target_claim_id


def test_story_branch_apply_merge_rolls_back_every_overlay_on_unsupported_kind(tmp_path):
    root = tmp_path / "story"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice waits.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))
    dispatch(
        request(
            "story_branch_create",
            project_root=str(root),
            branch_id="ALT-ROLLBACK",
            writer_confirmed=True,
        )
    )

    ids = []
    for record_kind, record_id, payload in (
        ("claim", "alice-leaves", {"predicate": "alice_leaves", "literal_value": True}),
        ("interpretation", "symbolic-reading", {"reading": "the bell means memory"}),
    ):
        overlay = request(
            "story_branch_add_overlay",
            project_root=str(root),
            branch_id="ALT-ROLLBACK",
            record_kind=record_kind,
            record_id=record_id,
            payload=payload,
            writer_confirmed=True,
        )
        overlay["params"]["operation"] = "ADD"
        ids.append(dispatch(overlay)["created_overlay_id"])
    prepared = dispatch(
        request(
            "story_branch_prepare_merge",
            project_root=str(root),
            branch_id="ALT-ROLLBACK",
            overlay_ids=ids,
        )
    )

    with pytest.raises(ValueError, match="not yet safely applicable"):
        dispatch(
            request(
                "story_branch_apply_merge",
                project_root=str(root),
                branch_id="ALT-ROLLBACK",
                overlay_ids=ids,
                expected_parent_revision=prepared["expected_parent_revision"],
                writer_confirmed=True,
            )
        )
    claims = dispatch(
        request(
            "story_tool",
            project_root=str(root),
            tool_id="query_claims",
            arguments={"predicate": "alice_leaves"},
        )
    )
    assert claims["claims"] == []
    comparison = dispatch(
        request(
            "story_tool",
            project_root=str(root),
            tool_id="compare_branch",
            arguments={"branch_id": "ALT-ROLLBACK"},
        )
    )
    assert comparison["comparison"]["merged_overlay_ids"] == []


def test_story_branch_apply_merge_rejects_stale_review_after_mainline_change(tmp_path):
    root = tmp_path / "story"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nAlice waits.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))
    dispatch(
        request(
            "story_branch_create",
            project_root=str(root),
            branch_id="ALT-STALE-APPLY",
            writer_confirmed=True,
        )
    )
    overlay = request(
        "story_branch_add_overlay",
        project_root=str(root),
        branch_id="ALT-STALE-APPLY",
        record_kind="claim",
        record_id="alice-leaves",
        payload={"predicate": "alice_leaves", "literal_value": True},
        writer_confirmed=True,
    )
    overlay["params"]["operation"] = "ADD"
    overlay_id = dispatch(overlay)["created_overlay_id"]
    prepared = dispatch(
        request(
            "story_branch_prepare_merge",
            project_root=str(root),
            branch_id="ALT-STALE-APPLY",
            overlay_ids=[overlay_id],
        )
    )

    dispatch(
        request(
            "story_writer_mutation",
            project_root=str(root),
            mutation="claim",
            payload={"predicate": "new_mainline_fact", "literal_value": True},
            writer_confirmed=True,
        )
    )
    with pytest.raises(ValueError, match="stale|prepare the merge again"):
        dispatch(
            request(
                "story_branch_apply_merge",
                project_root=str(root),
                branch_id="ALT-STALE-APPLY",
                overlay_ids=[overlay_id],
                expected_parent_revision=prepared["expected_parent_revision"],
                writer_confirmed=True,
            )
        )
    claims = dispatch(
        request(
            "story_tool",
            project_root=str(root),
            tool_id="query_claims",
            arguments={"predicate": "alice_leaves"},
        )
    )
    assert claims["claims"] == []


def test_story_branch_prepare_merge_reports_durable_retcon_impact(tmp_path):
    from backend.story.project import StoryProject

    root = tmp_path / "story"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nMara knows the bell is cracked.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))
    entity = dispatch(
        request(
            "story_writer_mutation",
            project_root=str(root),
            mutation="entity",
            payload={"canonical_name": "Mara", "entity_type": "character"},
            writer_confirmed=True,
        )
    )["record_id"]
    claim = dispatch(
        request(
            "story_writer_mutation",
            project_root=str(root),
            mutation="claim",
            payload={
                "subject_entity_id": entity,
                "predicate": "bell_condition",
                "literal_value": "cracked",
            },
            writer_confirmed=True,
        )
    )["record_id"]
    dispatch(
        request(
            "story_writer_mutation",
            project_root=str(root),
            mutation="character_knowledge",
            payload={"character_id": entity, "claim_id": claim, "state": "KNOWS"},
            writer_confirmed=True,
        )
    )

    # Prove the semantic dependency is not just an in-memory/cache artifact.
    project = StoryProject.open(root)
    project.cache_path.unlink()
    dispatch(request("story_project_understanding", project_root=str(root)))

    dispatch(
        request(
            "story_branch_create",
            project_root=str(root),
            branch_id="ALT-RETCON",
            writer_confirmed=True,
        )
    )
    overlay = request(
        "story_branch_add_overlay",
        project_root=str(root),
        branch_id="ALT-RETCON",
        record_kind="claim",
        record_id=claim,
        payload={"literal_value": "whole"},
        writer_confirmed=True,
    )
    overlay["params"]["operation"] = "REPLACE"
    overlay_id = dispatch(overlay)["created_overlay_id"]
    prepared = dispatch(
        request(
            "story_branch_prepare_merge",
            project_root=str(root),
            branch_id="ALT-RETCON",
            overlay_ids=[overlay_id],
        )
    )
    impact = prepared["operations"][0]["retcon_impact"]
    assert impact["root"]["label"].startswith("Mara · bell_condition")
    assert impact["counts"]["knowledge_state"] == 1
    assert impact["affected"][0]["label"].startswith("Mara · KNOWS · bell_condition")


def test_story_writer_mutation_is_desktop_only_confirmed_and_persistent(tmp_path):
    from backend.mcp_server import TOOLS

    root = tmp_path / "story"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nMara finds the bell.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))

    with pytest.raises(PermissionError, match="writer confirmation"):
        dispatch(
            request(
                "story_writer_mutation",
                project_root=str(root),
                mutation="entity",
                payload={"canonical_name": "Mara", "entity_type": "character"},
            )
        )

    entity = dispatch(
        request(
            "story_writer_mutation",
            project_root=str(root),
            mutation="entity",
            payload={
                "canonical_name": "Mara",
                "entity_type": "character",
                "aliases": ["Captain Mara"],
            },
            writer_confirmed=True,
        )
    )
    assert entity["writer_owned"] is True
    mara_id = entity["record_id"]

    claim = dispatch(
        request(
            "story_writer_mutation",
            project_root=str(root),
            mutation="claim",
            payload={
                "subject_entity_id": mara_id,
                "predicate": "bell_is_cursed",
                "literal_value": True,
                "status": "CONFIRMED_CANON",
            },
            writer_confirmed=True,
        )
    )
    claim_id = claim["record_id"]
    dispatch(
        request(
            "story_writer_mutation",
            project_root=str(root),
            mutation="character_knowledge",
            payload={
                "character_id": mara_id,
                "claim_id": claim_id,
                "state": "SUSPECTS",
            },
            writer_confirmed=True,
        )
    )

    resolved = dispatch(
        request(
            "story_tool",
            project_root=str(root),
            tool_id="resolve_entity",
            arguments={"name": "Captain Mara"},
        )
    )
    assert resolved["matches"][0]["entity_id"] == mara_id
    knowledge = dispatch(
        request(
            "story_tool",
            project_root=str(root),
            tool_id="get_character_beliefs",
            arguments={"character": "Mara"},
        )
    )
    assert knowledge["beliefs"][0]["claim_id"] == claim_id

    assert "story_writer_mutation" not in {tool["name"] for tool in TOOLS}
    with pytest.raises(KeyError, match="unknown Story Engine tool"):
        dispatch(
            request(
                "story_tool",
                project_root=str(root),
                tool_id="put_writer_claim",
                arguments={"predicate": "bypass"},
            )
        )


def test_story_writer_mutation_fails_closed_on_kind_boolean_and_reference_errors(tmp_path):
    root = tmp_path / "story"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nMara waits.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))

    with pytest.raises(ValueError, match="JSON boolean"):
        dispatch(
            request(
                "story_writer_mutation",
                project_root=str(root),
                mutation="entity",
                payload={"canonical_name": "Mara", "entity_type": "character"},
                writer_confirmed="true",
            )
        )
    with pytest.raises(ValueError, match="unsupported writer mutation"):
        dispatch(
            request(
                "story_writer_mutation",
                project_root=str(root),
                mutation="arbitrary_sql",
                payload={},
                writer_confirmed=True,
            )
        )
    with pytest.raises(KeyError, match="entity not found"):
        dispatch(
            request(
                "story_writer_mutation",
                project_root=str(root),
                mutation="world_state",
                payload={"entity_id": "not-real", "state_type": "location", "value": "Tower"},
                writer_confirmed=True,
            )
        )


def test_story_sidecar_persists_explicit_manuscript_order(tmp_path):
    root = tmp_path / "story"
    root.mkdir()
    (root / "later.md").write_text("# Chapter Two\nLater scene.\n", encoding="utf-8")
    (root / "earlier.md").write_text("# Chapter One\nEarlier scene.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))

    result = dispatch(
        request(
            "story_set_manuscript_order",
            project_root=str(root),
            paths=["earlier.md", "later.md"],
        )
    )
    assert result["active_manuscripts"] == ["earlier.md", "later.md"]
    understanding = dispatch(request("story_project_understanding", project_root=str(root)))
    assert understanding["active_manuscripts"] == ["earlier.md", "later.md"]

    with pytest.raises(ValueError, match="array of strings"):
        dispatch(request("story_set_manuscript_order", project_root=str(root), paths="earlier.md"))


def test_story_phase26_35_desktop_operations_preserve_writer_authority(tmp_path):
    root = tmp_path / "story-phase-26-35"
    root.mkdir()
    (root / "chapter.md").write_text("# Chapter One\nMara guards the bell.\n", encoding="utf-8")
    dispatch(request("story_project_understanding", project_root=str(root)))

    legacy = root / ".thothpad" / "chapter.md.story.json"
    legacy.write_text(
        json.dumps(
            {
                "version": 2,
                "id": "legacy",
                "agents": [],
                "sessions": [],
                "memories": [],
                "markers": [],
                "scopes": [{"id": "manuscript", "title": "Whole manuscript", "level": 0, "start": 0}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PermissionError, match="writer confirmation"):
        dispatch(
            request(
                "story_legacy_bind",
                project_root=str(root),
                workspace_path=str(legacy),
                manuscript_path="chapter.md",
            )
        )

    proposal = dispatch(
        request(
            "story_proposal_submit",
            project_root=str(root),
            proposal_kind="canon_fact",
            target_mutation="claim",
            payload={"predicate": "bell_is_cursed", "literal_value": True},
        )
    )
    before = dispatch(
        request(
            "story_tool",
            project_root=str(root),
            tool_id="query_claims",
            arguments={"predicate": "bell_is_cursed"},
        )
    )
    assert before["claims"] == []
    with pytest.raises(PermissionError, match="writer confirmation"):
        dispatch(
            request(
                "story_proposal_review",
                project_root=str(root),
                proposal_id=proposal["proposal_id"],
                decision="ACCEPTED",
            )
        )
    with pytest.raises(ValueError, match="maximum_documents must be an integer"):
        dispatch(
            request(
                "story_index_batch",
                project_root=str(root),
                maximum_documents=True,
            )
        )
    with pytest.raises(ValueError, match="not writer-reviewable"):
        dispatch(
            request(
                "story_proposal_submit",
                project_root=str(root),
                proposal_kind="bad",
                target_mutation="arbitrary_sql",
                payload={},
            )
        )


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
