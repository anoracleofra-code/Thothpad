from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, BinaryIO

from backend import config
from backend.analysis_store import AnalysisStore
from backend.analyzers import run_analyzers
from backend.analyzers.dialogue import _dialogue_spans
from backend.analyzers.possible_adverbs import spacy_model_status
from backend.desktop_engine import (
    _dialogue_metrics,
    _filtered_score,
    _serialize,
    _valid_exclusions,
    analyze_text,
)
from backend.documents import DocumentRegistry, ResyncRequired
from backend.external_policy import approved_external_tools
from backend.grammar import grammar_status, warm_harper
from backend.grammar_policy import approved_grammar
from backend.llm_clients import provider_is_remote
from backend.manuscript import (
    analyze_manuscript,
    load_lens_baselines,
    manuscript_report_with_timeline,
    read_project_timeline,
)
from backend.models import RunRequest
from backend.pipeline import compare_texts, run_pipeline
from backend.profiles import (
    export_profile,
    get_profile,
    import_profile,
    list_profiles,
    load_profile,
    save_profile,
)
from backend.story.model_routing import route_story_model
from backend.story.service import (
    add_story_branch_overlay,
    apply_story_branch_merge,
    apply_story_writer_mutation,
    backup_story_project_state,
    bind_legacy_story_workspace,
    call_story_tool,
    create_story_branch,
    export_story_project,
    import_story_project,
    index_story_project_batch,
    observe_story_writer_model,
    prepare_story_branch_merge,
    rebase_story_branch,
    rebuild_story_project_index,
    recover_story_project_state,
    restore_story_project_state,
    review_story_project_proposal,
    submit_story_project_proposal,
)
from backend.story.service import (
    project_sources as story_project_sources,
)
from backend.story.service import (
    project_understanding as story_project_understanding,
)
from backend.story.service import (
    set_manuscript_order as story_set_manuscript_order,
)
from backend.story.service import (
    set_source_override as story_set_source_override,
)
from backend.text_utils import (
    AnalysisCancelled,
    cancellable_analysis,
    cancellation_checkpoint,
    document_features,
)
from backend.validation import (
    strict_bool_arg as _bool,
)
from backend.validation import (
    validate_documents,
    validate_passes,
    validate_profile,
    validate_profile_name,
    validate_text,
)

MAX_INFLIGHT = 4
_DOCUMENTS = DocumentRegistry()
_STORE_LOCK = threading.Lock()
_STORE: AnalysisStore | None = None
_STORE_PATH: str | None = None
_PERFORMANCE_LOCK = threading.Lock()
_PERFORMANCE_POLICY: dict[str, Any] = {
    "mode": "automatic",
    "logical_processors": max(1, os.cpu_count() or 1),
    "background_threads": 1,
    "memory_limit_mb": 768,
    "analysis_delay_ms": 2200,
    "overlay_budget_ms": 4,
    "preview_acceleration": True,
    "core_gpu_acceleration": False,
}


from backend.config import ENGINE_VERSION  # noqa: E402
from backend.protocol import (  # noqa: E402
    _INTERNAL_OPERATIONS,
    _STORE_ONLY_OPERATIONS,
    MAX_HEADER_BYTES,
    MAX_HEADER_COUNT,
    OPERATIONS,
    PROCESS_OPERATIONS,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    ProtocolError,
    encode_frame,
    read_frame,
)
from backend.protocol import (  # noqa: E402
    params_of as _params,
)
from backend.protocol import (  # noqa: E402
    reject_json_constant as _reject_json_constant,
)
from backend.protocol import (  # noqa: E402
    request_id as _request_id,
)
from backend.secret_hygiene import (  # noqa: E402
    clear_secret_environment as _clear_secret_environment,
)


def _configure_performance(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("performance must be an object")
    available = max(1, os.cpu_count() or 1)
    logical = value.get("logical_processors", available)
    if isinstance(logical, bool) or not isinstance(logical, int):
        raise ValueError("performance.logical_processors must be an integer")
    logical = max(1, min(logical, available))
    mode = str(value.get("mode", "automatic"))
    if mode not in {"automatic", "manual"}:
        raise ValueError("performance.mode must be automatic or manual")
    requested_threads = value.get("background_threads", 1)
    if isinstance(requested_threads, bool) or not isinstance(requested_threads, int):
        raise ValueError("performance.background_threads must be an integer")
    if mode == "automatic":
        threads = 1 if logical <= 4 else min(4, max(2, logical // 4))
    else:
        threads = max(1, min(requested_threads, logical, 8))
    memory = value.get("memory_limit_mb", 768 if logical <= 2 else 1536)
    if isinstance(memory, bool) or not isinstance(memory, int):
        raise ValueError("performance.memory_limit_mb must be an integer")
    policy = {
        "mode": mode,
        "logical_processors": logical,
        "background_threads": threads,
        "memory_limit_mb": max(256, min(memory, 8192)),
        "analysis_delay_ms": max(500, min(int(value.get("analysis_delay_ms", 2200)), 10000)),
        "overlay_budget_ms": max(1, min(int(value.get("overlay_budget_ms", 4)), 8)),
        "preview_acceleration": bool(value.get("preview_acceleration", True)),
        "core_gpu_acceleration": False,
    }
    with _PERFORMANCE_LOCK:
        _PERFORMANCE_POLICY.clear()
        _PERFORMANCE_POLICY.update(policy)
        for name in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS",
            "BLIS_NUM_THREADS",
        ):
            os.environ[name] = str(threads)
        os.environ["THOTHPAD_BACKGROUND_THREADS"] = str(threads)
        os.environ["THOTHPAD_MEMORY_LIMIT_MB"] = str(policy["memory_limit_mb"])
    return dict(policy)


def _analysis_store() -> AnalysisStore:
    global _STORE, _STORE_PATH
    path = str(config.ANALYSIS_CACHE_DB)
    with _STORE_LOCK:
        if _STORE is None or _STORE_PATH != path:
            if _STORE is not None:
                _STORE.close()
            _STORE = AnalysisStore(config.ANALYSIS_CACHE_DB)
            _STORE_PATH = path
        return _STORE


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        try:
            subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)  # type: ignore[attr-defined]
        except (OSError, ProcessLookupError):
            process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)  # type: ignore[attr-defined]
            except (OSError, ProcessLookupError):
                process.kill()
        process.wait(timeout=5)


CANCEL_GRACE_SECONDS = 3.0
_CANCEL_POLL_SECONDS = 0.02


def _cancel_flag_path(cancel_dir: str | None, request_id: str) -> str | None:
    if not cancel_dir:
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", request_id)
    return os.path.join(cancel_dir, f"{safe}.cancel")


def _signal_cancel(cancel_dir: str | None, request_id: str) -> bool:
    path = _cancel_flag_path(cancel_dir, request_id)
    if path is None:
        return False
    try:
        with open(path, "w"):
            pass
    except OSError:
        return False
    return True


def _clear_cancel_flag(cancel_dir: str | None, request_id: str) -> None:
    path = _cancel_flag_path(cancel_dir, request_id)
    if path is None:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _worker_cancelled_check(cancel_dir: str | None, request_id: str) -> Callable[[], bool]:
    path = _cancel_flag_path(cancel_dir, request_id)

    def check() -> bool:
        return path is not None and os.path.exists(path)

    return check


from backend.process_supervisor import (  # noqa: E402
    _close_windows_handle,
    _create_windows_kill_job,
    _windows_process_is_alive,
)


def _capabilities() -> dict[str, Any]:
    return {
        "engine": {"name": "thothpad-engine", "version": ENGINE_VERSION},
        "protocol": {"major": PROTOCOL_MAJOR, "minor": PROTOCOL_MINOR},
        "operations": list(OPERATIONS),
        "presets": ["live", "full"],
        "offset_encoding": "utf-16",
        "analysis_persists_by_default": False,
        "performance": dict(_PERFORMANCE_POLICY),
        "document_references": True,
        "compact_overlay_paging": True,
        "limits": {
            "text_chars": config.MAX_TEXT_CHARS,
            "text_utf16_units": config.MAX_TEXT_UTF16_UNITS,
            "live_text_chars": config.MAX_LIVE_TEXT_CHARS,
            "documents": config.MAX_DOCUMENTS,
            "exclusion_ranges": config.MAX_EXCLUSION_RANGES,
            "manuscript_chars": config.MAX_MANUSCRIPT_CHARS,
            "passes": config.MAX_PASSES,
            "frame_bytes": config.MAX_FRAME_BYTES,
            "header_bytes": MAX_HEADER_BYTES,
            "header_count": MAX_HEADER_COUNT,
            "finding_page_size": config.MAX_FINDING_PAGE_SIZE,
            "overlay_page_size": config.MAX_OVERLAY_PAGE_SIZE,
            "analysis_snapshot_ttl_seconds": config.ANALYSIS_SNAPSHOT_TTL_SECONDS,
        },
        "optional_features": {
            "spacy_pos_confirmation": spacy_model_status(),
            "grammar": grammar_status(),
            "external_tools": {
                "supported": ["proselint", "vale", "harper", "languagetool"],
                "requires_explicit_consent": True,
                "trusted_absolute_paths_required": True,
                "approved_endpoints_required": True,
            },
        },
    }


def _message_document_id(message: dict[str, Any], params: dict[str, Any]) -> Any:
    return params.get("document_id", message.get("document_id"))


def _message_revision(message: dict[str, Any], params: dict[str, Any]) -> Any:
    return params.get("revision", message.get("document_revision"))


def _resolved_document_text(
    message: dict[str, Any], params: dict[str, Any], operation: str
) -> tuple[str, str | None, int, Any]:
    if "text" in params:
        return (
            params.get("text", ""),
            str(params["language"]) if params.get("language") is not None else None,
            int(params.get("base_offset_utf16", 0)),
            params.get("exclusion_ranges"),
        )
    document = _DOCUMENTS.get_document(
        _message_document_id(message, params),
        _message_revision(message, params),
    )
    if params.get("exclusion_ranges") is None and document.exclusions_stale:
        raise ResyncRequired("document exclusion_ranges are stale; resend them with patch_document")
    exclusions = params.get("exclusion_ranges", list(document.exclusion_ranges))
    if operation != "analyze_region":
        return document.text, document.language, 0, exclusions
    start = params.get("start_utf16")
    end = params.get("end_utf16")
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 0
        or end <= start
    ):
        raise ValueError("document-reference region requires 0 <= start_utf16 < end_utf16")
    try:
        region = document.buffer.slice_utf16(start, end)
    except ValueError as exc:
        raise ValueError(f"invalid UTF-16 region: {exc}") from exc
    return (
        region,
        document.language,
        start,
        exclusions,
    )


def _desktop_provider(params: dict[str, Any]) -> dict[str, Any]:
    value = params.get("provider") or {}
    if not isinstance(value, dict):
        raise ValueError("provider must be an object")
    provider = dict(value)
    provider["_desktop_no_environment"] = True
    consent = _bool(params, "consent", False)
    if provider_is_remote(provider) and not consent:
        raise ValueError("remote AI requests require explicit consent")
    return provider


def _analyze_live_cancellable(
    text: str,
    *,
    cancelled: threading.Event,
    profile_name: str,
    overrides: Any,
    base_offset_utf16: int,
    exclusion_ranges: Any,
    confirm_adverbs: bool,
    document_revision: int | None,
    grammar: dict[str, Any],
    language: str | None,
    analyzers: Any,
) -> dict[str, Any]:
    from backend.analyzers.base import (
        LIVE_ANALYZERS,
        validate_analyzer_names,
    )

    validate_text(text, live=True)
    if base_offset_utf16 < 0:
        raise ValueError("base_offset_utf16 must be non-negative")
    if analyzers is not None and (
        not isinstance(analyzers, (list, tuple))
        or not analyzers
        or len(analyzers) > 32
        or any(not isinstance(name, str) or not name for name in analyzers)
    ):
        raise ValueError("analyzers must contain 1-32 analyzer names")

    profile = load_profile(profile_name, overrides)
    if confirm_adverbs:
        for name in ("possible_adverbs", "possible_adjectives", "possible_verbs"):
            profile.setdefault(name, {})["confirm_pos"] = True
    profile["_live_lexical_only"] = True
    exclusions = _valid_exclusions(exclusion_ranges)
    language_code = (language or "en").replace("_", "-").casefold()
    lexical_rules_enabled = language_code in {"", "und"} or language_code.startswith("en")
    selected = tuple(dict.fromkeys(analyzers or LIVE_ANALYZERS))
    started = time.perf_counter()

    with cancellable_analysis(cancelled.is_set), document_features(text, exclusions) as features:
        analyzer_started = time.perf_counter()
        # Unknown names raise before any analyzer work, matching the sidecar's
        # validate-first contract; the shared orchestration owns the registry
        # loop, dialogue-exclusion post-pass, profile_patterns appending, and
        # threshold application from here on.
        validate_analyzer_names(selected)
        results = run_analyzers(text, profile, selected if lexical_rules_enabled else ())

        grammar_allowed = bool(grammar and (grammar.get("provider") != "harper" or lexical_rules_enabled))
        if grammar_allowed:
            cancellation_checkpoint()
            from backend.grammar import analyze_grammar

            results.append(analyze_grammar(text, grammar))
            cancellation_checkpoint()
        analyzer_ms = round((time.perf_counter() - analyzer_started) * 1000, 3)

        serialization_started = time.perf_counter()
        cancellation_checkpoint()
        analysis, diagnostics = _serialize(
            results,
            text,
            base_offset_utf16=base_offset_utf16,
            exclusions=exclusions,
            features=features,
        )
        cancellation_checkpoint()
        serialization_ms = round((time.perf_counter() - serialization_started) * 1000, 3)
        # Dialogue balance block, mirroring the desktop envelope so the
        # cancelled and direct live paths stay byte-identical.
        spans_cached = features.cached("dialogue_spans", lambda: _dialogue_spans(text))
        dialogue_metrics = _dialogue_metrics(text, spans_cached)

    for diagnostic in diagnostics:
        diagnostic["revision"] = document_revision
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    return {
        "mode": "diagnose",
        "profile": profile.get("name", profile_name),
        "preset": "live",
        "score": _filtered_score(analysis, profile),
        "analysis": analysis,
        "dialogue": dialogue_metrics,
        "diagnostics": diagnostics,
        "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "duration_ms": duration_ms,
        "stage_timings_ms": {
            "analysis": analyzer_ms,
            "serialization": serialization_ms,
        },
        "truncated": False,
        "persisted": False,
        "document_revision": document_revision,
        "language": language_code or "und",
        "lexical_rules_enabled": lexical_rules_enabled,
        "excluded_count": sum(len(result.flags) for result in results) - len(diagnostics),
    }


def dispatch(
    message: dict[str, Any],
    *,
    cancelled: threading.Event | None = None,
    internal: bool = False,
) -> dict[str, Any]:
    operation = str(message.get("operation", ""))
    if operation not in OPERATIONS and operation not in _INTERNAL_OPERATIONS:
        raise ValueError(f"unsupported operation: {operation}")
    if operation in _INTERNAL_OPERATIONS and not internal:
        # Internal operations exist for the sidecar's own worker supervision
        # (e.g. snapshot disposal after dispose_document); they are never part
        # of the client-facing operation set and must not be callable over
        # the wire.
        raise ValueError(f"operation is reserved for internal use: {operation}")
    params = _params(message)
    for boolean_name in ("persist", "overwrite", "consent", "grammar_consent"):
        if boolean_name in params:
            _bool(params, boolean_name)
    if operation == "initialize":
        _configure_performance(params.get("performance"))
        warm_harper()
        return _capabilities()
    if operation == "capabilities":
        return _capabilities()
    if operation == "provider_access":
        from backend.provider_access import provider_access

        return provider_access(params)
    if operation == "story_project_understanding":
        root = params.get("project_root")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        return story_project_understanding(root)
    if operation == "story_project_sources":
        root = params.get("project_root")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        role = params.get("role")
        if role is not None and not isinstance(role, str):
            raise ValueError("role must be a string")
        return story_project_sources(
            root,
            offset=params.get("offset", 0),
            limit=params.get("limit", 100),
            role=role,
        )
    if operation == "story_set_source_override":
        root = params.get("project_root")
        path = params.get("path")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")
        roles = params.get("roles")
        if roles is not None and (not isinstance(roles, list) or any(not isinstance(role, str) for role in roles)):
            raise ValueError("roles must be an array of strings")
        authority = params.get("authority")
        if authority is not None and not isinstance(authority, str):
            raise ValueError("authority must be a string")
        pattern = params.get("pattern")
        if pattern is not None and not isinstance(pattern, str):
            raise ValueError("pattern must be a string")
        return story_set_source_override(
            root,
            path,
            roles=roles,
            authority=authority,
            pattern=pattern if isinstance(pattern, str) and pattern.strip() else None,
        )
    if operation == "story_set_manuscript_order":
        root = params.get("project_root")
        paths = params.get("paths")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise ValueError("paths must be an array of strings")
        return story_set_manuscript_order(root, paths)
    if operation == "story_branch_create":
        root = params.get("project_root")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        parent_branch = params.get("parent_branch", "mainline")
        fork_story_unit = params.get("fork_story_unit")
        assumptions = params.get("assumptions", [])
        branch_id = params.get("branch_id")
        if not isinstance(parent_branch, str) or not parent_branch.strip():
            raise ValueError("parent_branch must be a non-empty string")
        if fork_story_unit is not None and not isinstance(fork_story_unit, str):
            raise ValueError("fork_story_unit must be a string")
        if branch_id is not None and not isinstance(branch_id, str):
            raise ValueError("branch_id must be a string")
        if not isinstance(assumptions, list) or not all(isinstance(item, str) for item in assumptions):
            raise ValueError("assumptions must be an array of strings")
        return create_story_branch(
            root,
            parent_branch=parent_branch,
            fork_story_unit=fork_story_unit,
            assumptions=assumptions,
            branch_id=branch_id,
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_branch_add_overlay":
        root = params.get("project_root")
        branch_id = params.get("branch_id")
        record_kind = params.get("record_kind")
        record_id = params.get("record_id")
        change = params.get("operation")
        payload = params.get("payload", {})
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise ValueError("branch_id must be a non-empty string")
        if not isinstance(record_kind, str) or not record_kind.strip():
            raise ValueError("record_kind must be a non-empty string")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError("record_id must be a non-empty string")
        if not isinstance(change, str) or not change.strip():
            raise ValueError("operation must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        return add_story_branch_overlay(
            root,
            branch_id=branch_id,
            record_kind=record_kind,
            record_id=record_id,
            operation=change,
            payload=payload,
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_branch_rebase":
        root = params.get("project_root")
        branch_id = params.get("branch_id")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise ValueError("branch_id must be a non-empty string")
        return rebase_story_branch(
            root,
            branch_id,
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_branch_prepare_merge":
        root = params.get("project_root")
        branch_id = params.get("branch_id")
        overlay_ids = params.get("overlay_ids")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise ValueError("branch_id must be a non-empty string")
        if not isinstance(overlay_ids, list) or not all(isinstance(item, str) for item in overlay_ids):
            raise ValueError("overlay_ids must be an array of strings")
        return prepare_story_branch_merge(root, branch_id, overlay_ids)
    if operation == "story_branch_apply_merge":
        root = params.get("project_root")
        branch_id = params.get("branch_id")
        overlay_ids = params.get("overlay_ids")
        expected = params.get("expected_parent_revision")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise ValueError("branch_id must be a non-empty string")
        if not isinstance(overlay_ids, list) or not all(isinstance(item, str) for item in overlay_ids):
            raise ValueError("overlay_ids must be an array of strings")
        if not isinstance(expected, str) or not expected.strip():
            raise ValueError("expected_parent_revision must be a non-empty string")
        return apply_story_branch_merge(
            root,
            branch_id,
            overlay_ids,
            expected_parent_revision=expected,
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_writer_mutation":
        root = params.get("project_root")
        mutation = params.get("mutation")
        payload = params.get("payload", {})
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(mutation, str) or not mutation.strip():
            raise ValueError("mutation must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        return apply_story_writer_mutation(
            root,
            mutation,
            payload,
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_writer_model_observe":
        root = params.get("project_root")
        events = params.get("events", [])
        scope_kind = params.get("scope_kind", "project")
        scope_id = params.get("scope_id", "")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(events, list) or not all(isinstance(item, dict) for item in events):
            raise ValueError("events must be an array of objects")
        if not isinstance(scope_kind, str) or not isinstance(scope_id, str):
            raise ValueError("writer-model scope must use strings")
        return observe_story_writer_model(
            root,
            events,
            scope_kind=scope_kind,
            scope_id=scope_id,
        )
    if operation == "story_model_route":
        prompt = params.get("prompt", "")
        candidates = params.get("candidates", [])
        fallback = params.get("fallback")
        task = params.get("task")
        quality = params.get("quality", "balanced")
        privacy = params.get("privacy", "prefer_local")
        if not isinstance(prompt, str):
            raise ValueError("prompt must be a string")
        if not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates):
            raise ValueError("candidates must be an array of objects")
        if fallback is not None and not isinstance(fallback, dict):
            raise ValueError("fallback must be an object")
        if task is not None and not isinstance(task, str):
            raise ValueError("task must be a string")
        if not isinstance(quality, str) or not isinstance(privacy, str):
            raise ValueError("quality and privacy must be strings")
        return route_story_model(
            prompt=prompt,
            candidates=candidates,
            fallback=fallback,
            task=task,
            quality=quality,
            privacy=privacy,
        )
    if operation == "story_index_rebuild":
        root = params.get("project_root")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        return rebuild_story_project_index(
            root,
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_index_batch":
        root = params.get("project_root")
        maximum_documents = params.get("maximum_documents", 100)
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if isinstance(maximum_documents, bool) or not isinstance(maximum_documents, int):
            raise ValueError("maximum_documents must be an integer")
        return index_story_project_batch(
            root,
            maximum_documents=maximum_documents,
            reset=_bool(params, "reset", False),
        )
    if operation == "story_legacy_bind":
        root = params.get("project_root")
        workspace_path = params.get("workspace_path")
        manuscript_path = params.get("manuscript_path")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(workspace_path, str) or not workspace_path.strip():
            raise ValueError("workspace_path must be a non-empty string")
        if not isinstance(manuscript_path, str) or not manuscript_path.strip():
            raise ValueError("manuscript_path must be a non-empty string")
        return bind_legacy_story_workspace(
            root,
            workspace_path=workspace_path,
            manuscript_path=manuscript_path,
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_project_export":
        root = params.get("project_root")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        return export_story_project(root)
    if operation == "story_project_import":
        root = params.get("project_root")
        bundle = params.get("bundle")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(bundle, dict):
            raise ValueError("bundle must be an object")
        return import_story_project(
            root,
            bundle,
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_proposal_submit":
        root = params.get("project_root")
        proposal_kind = params.get("proposal_kind")
        target_mutation = params.get("target_mutation")
        payload = params.get("payload")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(proposal_kind, str) or not proposal_kind.strip():
            raise ValueError("proposal_kind must be a non-empty string")
        if not isinstance(target_mutation, str) or not target_mutation.strip():
            raise ValueError("target_mutation must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        evidence = params.get("evidence", [])
        if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
            raise ValueError("evidence must be an array of objects")
        return submit_story_project_proposal(
            root,
            proposal_kind=proposal_kind,
            target_mutation=target_mutation,
            payload=payload,
            branch_id=str(params.get("branch_id", "mainline")),
            story_unit_id=str(params.get("story_unit_id")) if params.get("story_unit_id") else None,
            evidence=evidence,
            created_by=str(params.get("created_by", "model")),
        )
    if operation == "story_proposal_review":
        root = params.get("project_root")
        proposal_id = params.get("proposal_id")
        decision = params.get("decision")
        payload_override = params.get("payload_override")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise ValueError("proposal_id must be a non-empty string")
        if not isinstance(decision, str) or not decision.strip():
            raise ValueError("decision must be a non-empty string")
        if payload_override is not None and not isinstance(payload_override, dict):
            raise ValueError("payload_override must be an object")
        return review_story_project_proposal(
            root,
            proposal_id=proposal_id,
            decision=decision,
            payload_override=payload_override,
            note=str(params.get("note", "")),
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_recover":
        root = params.get("project_root")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        return recover_story_project_state(
            root,
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_state_backup":
        root = params.get("project_root")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        return backup_story_project_state(root)
    if operation == "story_state_restore":
        root = params.get("project_root")
        backup_name = params.get("backup_name")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(backup_name, str) or not backup_name.strip():
            raise ValueError("backup_name must be a non-empty string")
        return restore_story_project_state(
            root,
            backup_name,
            writer_confirmed=_bool(params, "writer_confirmed", False),
        )
    if operation == "story_tool":
        root = params.get("project_root")
        tool_id = params.get("tool_id")
        arguments = params.get("arguments", {})
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if not isinstance(tool_id, str) or not tool_id.strip():
            raise ValueError("tool_id must be a non-empty string")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        return call_story_tool(root, tool_id, arguments)
    if operation == "list_profiles":
        return {"profiles": list_profiles()}
    if operation == "get_profile":
        return {"profile": get_profile(validate_profile_name(str(params.get("name", ""))))}
    if operation == "save_profile":
        return save_profile(str(params.get("name", "")), params.get("profile"))  # type: ignore[arg-type]
    if operation == "import_profile":
        if params.get("path") is not None:
            raise ValueError("desktop profile import accepts JSON only; the native app owns file paths")
        return import_profile(profile=params.get("profile"), name=params.get("name"))
    if operation == "export_profile":
        _bool(params, "overwrite", False)
        if params.get("path") is not None:
            raise ValueError("desktop profile export returns JSON only; the native app owns file paths")
        return export_profile(str(params.get("name", "")))
    if operation == "open_document":
        return _DOCUMENTS.open_document(
            _message_document_id(message, params),
            _message_revision(message, params),
            params.get("text"),
            language=params.get("language", "en"),
            expected_hash=params.get("hash", params.get("text_hash")),
            exclusion_ranges=params.get("exclusion_ranges"),
        )
    if operation == "patch_document":
        return _DOCUMENTS.patch_document(
            _message_document_id(message, params),
            params.get("base_revision"),
            _message_revision(message, params),
            params.get("changes"),
            expected_hash=params.get("hash", params.get("text_hash")),
            exclusion_ranges=params.get("exclusion_ranges"),
        )
    if operation == "dispose_document":
        document_id = _message_document_id(message, params)
        disposed = _DOCUMENTS.dispose_document(document_id)
        snapshots = _analysis_store().dispose_document(str(document_id))
        return {
            "document_id": document_id,
            "disposed": disposed,
            "disposed_analyses": snapshots,
        }
    if operation == "dispose_document_snapshots":
        document_id = _message_document_id(message, params)
        return {
            "document_id": document_id,
            "disposed_analyses": _analysis_store().dispose_document(str(document_id)),
        }
    if operation == "query_findings":
        return _analysis_store().query_findings(
            params.get("analysis_id"),
            analyzer=params.get("analyzer"),
            analyzers=params.get("analyzers"),
            start_utf16=params.get("start_utf16"),
            end_utf16=params.get("end_utf16"),
            cursor=params.get("cursor"),
            limit=params.get("limit", config.DEFAULT_FINDING_PAGE_SIZE),
        )
    if operation == "query_overlay_spans":
        return _analysis_store().query_overlay_spans(
            params.get("analysis_id"),
            categories=params.get("categories"),
            cursor=params.get("cursor"),
            limit=params.get("limit", config.DEFAULT_OVERLAY_PAGE_SIZE),
        )
    if operation == "dispose_analysis":
        analysis_id = params.get("analysis_id")
        return {
            "analysis_id": AnalysisStore.validate_analysis_id(analysis_id),
            "disposed": _analysis_store().dispose_analysis(analysis_id),
        }
    if operation in {"analyze_region", "analyze_document"}:
        text, document_language, resolved_base_offset, resolved_exclusions = _resolved_document_text(
            message, params, operation
        )
        external = approved_external_tools(params.get("external_tools"))
        grammar = approved_grammar(
            params.get("grammar"),
            live=operation == "analyze_region",
            consent=_bool(params, "grammar_consent", False),
        )
        profile_name = str(params.get("profile", config.DEFAULT_PROFILE))
        confirm_adverbs = _bool(params, "confirm_adverbs", operation == "analyze_document")
        document_revision = int(message["document_revision"]) if message.get("document_revision") is not None else None
        language = str(params.get("language")) if params.get("language") is not None else document_language
        if operation == "analyze_region" and cancelled is not None:
            if external:
                raise ValueError("external tools are unavailable in the live preset")
            result = _analyze_live_cancellable(
                text,
                cancelled=cancelled,
                profile_name=profile_name,
                overrides=params.get("overrides"),
                base_offset_utf16=resolved_base_offset,
                exclusion_ranges=resolved_exclusions,
                confirm_adverbs=confirm_adverbs,
                document_revision=document_revision,
                grammar=grammar,  # type: ignore[arg-type]
                language=language,
                analyzers=params.get("analyzers"),
            )
        else:
            result = analyze_text(
                text,
                profile_name=profile_name,
                overrides=params.get("overrides"),
                preset=("live" if operation == "analyze_region" else str(params.get("preset", "full"))),
                base_offset_utf16=resolved_base_offset,
                exclusion_ranges=resolved_exclusions,
                confirm_adverbs=confirm_adverbs,
                document_revision=document_revision,
                external_tools=external,
                grammar=grammar,
                language=language,
                analyzers=params.get("analyzers"),
            )
        if operation == "analyze_region":
            return result

        diagnostics = result.get("diagnostics", [])
        if not isinstance(diagnostics, list):
            raise ValueError("document analysis diagnostics must be an array")
        snapshot_started = time.perf_counter()
        snapshot = _analysis_store().create_snapshot(
            diagnostics,
            document_id=str(message.get("document_id", "")),
            document_revision=(
                int(message["document_revision"]) if message.get("document_revision") is not None else None
            ),
            text_hash=str(result.get("text_hash", "")),
            initial_page_size=params.get("initial_page_size", config.DEFAULT_FINDING_PAGE_SIZE),
            persist=_bool(params, "persist", False),
        )
        for row in result.get("analysis", []):
            if isinstance(row, dict):
                row["flags"] = []
        result.update(snapshot)
        result.setdefault("stage_timings_ms", {})["snapshot"] = round(
            (time.perf_counter() - snapshot_started) * 1000, 3
        )
        return result
    if operation == "analyze_manuscript":
        documents = params.get("documents", [])
        if not isinstance(documents, list):
            raise ValueError("documents must be an array")
        validate_documents(documents)
        grammar = approved_grammar(
            params.get("grammar"),
            live=False,
            consent=_bool(params, "grammar_consent", False),
        )
        return manuscript_report_with_timeline(
            analyze_manuscript(
                documents,
                str(params.get("profile", config.DEFAULT_PROFILE)),
                overrides=params.get("overrides"),
                project=params.get("project"),
                persist=_bool(params, "persist", False),
                grammar=grammar,
            ),
            params.get("project") if isinstance(params.get("project"), str) else None,
        )
    if operation == "quality_timeline":
        project = params.get("project")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project must be a non-empty string")
        return read_project_timeline(project)
    if operation == "lens_baselines":
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        return {"name": name, "baselines": load_lens_baselines(name)}
    if operation == "rewrite":
        passes = validate_passes(params.get("passes", 1))
        mode = str(params.get("mode", "rewrite"))
        if mode not in {"rewrite", "deslop", "line_edit", "write_from_brief"}:
            raise ValueError("unsupported rewrite mode")
        profile_name = validate_profile_name(str(params.get("profile", config.DEFAULT_PROFILE)))
        snapshot = params.get("profile_snapshot")  # type: ignore[assignment]
        if snapshot is not None:
            snapshot = validate_profile(snapshot)
            if snapshot.get("name") != profile_name:
                raise ValueError("profile_snapshot name must match profile")
        return run_pipeline(
            RunRequest(
                text=params.get("text", ""),
                profile=profile_name,
                profile_snapshot=snapshot,
                mode=mode,
                passes=passes,
                provider=_desktop_provider(params),
                overrides=params.get("overrides"),
                preserve=params.get("preserve"),
                aggressiveness=str(params.get("aggressiveness", "medium")),
                persist=_bool(params, "persist", False),
            )
        )
    if operation == "compare":
        return compare_texts(
            params.get("before", ""),
            params.get("after", ""),
            str(params.get("profile", config.DEFAULT_PROFILE)),
            persist=_bool(params, "persist", False),
        )
    raise ValueError(f"operation is handled by the sidecar loop: {operation}")


class PersistentWorker:
    def __init__(self) -> None:
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._job_handle: int | None = None
        self._active_request_id: str | None = None
        self._cancel_dir: str | None = None
        self._cancel_grace_seconds = CANCEL_GRACE_SECONDS
        self.starts = 0

    def is_running(self) -> bool:
        with self._state_lock:
            return self._process is not None and self._process.poll() is None

    @staticmethod
    def _command() -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--thothpad-report-worker"]
        return [sys.executable, "-m", "backend.sidecar", "--thothpad-report-worker"]

    def _start_locked(self) -> subprocess.Popen[bytes]:
        process = self._process
        if process is not None and process.poll() is None:
            return process
        _close_windows_handle(self._job_handle)
        self._job_handle = None
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        if self._cancel_dir is None or not os.path.isdir(self._cancel_dir):
            self._cancel_dir = tempfile.mkdtemp(prefix="thothpad-cancel-")
        env = dict(os.environ)
        env["THOTHPAD_CANCEL_DIR"] = self._cancel_dir
        process = subprocess.Popen(
            self._command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=creation_flags,
            start_new_session=os.name != "nt",
        )
        if os.name == "nt":
            self._job_handle = _create_windows_kill_job(process.pid)
        self._process = process
        self.starts += 1
        return process

    def execute(self, request: dict[str, Any], cancelled: threading.Event) -> dict[str, Any]:
        request_id = str(request["request_id"])
        with self._operation_lock:
            if cancelled.is_set():
                raise ProtocolError("request cancelled")
            with self._state_lock:
                process = self._start_locked()
                self._active_request_id = request_id
            try:
                if process.stdin is None or process.stdout is None:
                    raise RuntimeError("report worker pipes are unavailable")
                process.stdin.write(encode_frame(request))
                process.stdin.flush()
                response = read_frame(process.stdout)  # type: ignore[arg-type]
                if response is None:
                    raise RuntimeError("report worker exited without a response")
                return response
            finally:
                with self._state_lock:
                    if self._active_request_id == request_id:
                        self._active_request_id = None
                    if self._process is process and process.poll() is not None:
                        self._process = None
                        _close_windows_handle(self._job_handle)
                        self._job_handle = None
                _clear_cancel_flag(self._cancel_dir, request_id)

    def cancel(self, request_id: str) -> bool:
        with self._state_lock:
            if self._active_request_id != request_id:
                return False
            process = self._process
            cancel_dir = self._cancel_dir
        if process is None or process.poll() is not None:
            with self._state_lock:
                if self._active_request_id == request_id:
                    self._active_request_id = None
            return True
        # Cooperative first: the worker polls this flag at analysis checkpoints
        # and aborts without losing its warm state.
        _signal_cancel(cancel_dir, request_id)
        deadline = time.monotonic() + self._cancel_grace_seconds
        while time.monotonic() < deadline:
            with self._state_lock:
                if self._active_request_id != request_id:
                    return True
                process = self._process
            if process is None or process.poll() is not None:
                return True
            time.sleep(_CANCEL_POLL_SECONDS)
        # Last resort only: the worker missed every checkpoint within the grace
        # period, so its warm state is forfeit and the tree must go.
        with self._state_lock:
            if self._active_request_id != request_id:
                return True
            self._process = None
            self._active_request_id = None
            handle = self._job_handle
            self._job_handle = None
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)
        _close_windows_handle(handle)
        _clear_cancel_flag(cancel_dir, request_id)
        return True

    def stop(self) -> None:
        with self._state_lock:
            process = self._process
            self._process = None
            self._active_request_id = None
            handle = self._job_handle
            self._job_handle = None
            cancel_dir = self._cancel_dir
            self._cancel_dir = None
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)
        _close_windows_handle(handle)
        if cancel_dir is not None:
            shutil.rmtree(cancel_dir, ignore_errors=True)


@dataclass
class InFlight:
    request: dict[str, Any]
    cancelled: threading.Event
    thread: threading.Thread | None = None


class SidecarServer:
    def __init__(self, reader: BinaryIO, writer: BinaryIO):
        self.reader = reader
        self.writer = writer
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._inflight: dict[str, InFlight] = {}
        self._stopping = False
        self._report_worker = PersistentWorker()
        # Warm the analysis worker immediately: its first spawn imports the
        # full analyzer stack, which can take minutes under antivirus
        # real-time scanning. Warming here absorbs that cost into engine
        # startup instead of stalling the user's first scan behind a
        # lazy-spawn timeout.
        threading.Thread(target=self._warm_worker, daemon=True).start()

    def _warm_worker(self) -> None:
        try:
            with self._report_worker._operation_lock:
                self._report_worker._start_locked()
        except Exception:
            # A failed warm-up degrades to the existing lazy-spawn path.
            pass

    def _write(self, payload: dict[str, Any]) -> None:
        try:
            frame = encode_frame(payload)
        except ProtocolError as exc:
            minimal = {
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": PROTOCOL_MINOR,
                "request_id": payload.get("request_id"),
                "document_id": payload.get("document_id"),
                "document_revision": payload.get("document_revision"),
                "ok": False,
                "error": {"code": "response_too_large", "message": str(exc)},
            }
            frame = encode_frame(minimal)
        with self._write_lock:
            if os.environ.get("THOTHPAD_SIDECAR_TRACE"):
                trace_path = os.environ["THOTHPAD_SIDECAR_TRACE"]
                with open(trace_path, "ab") as trace:
                    trace.write(
                        f"[write] op={payload.get('operation')} declared={len(frame)} head={frame[:48]!r}\n".encode(
                            "utf-8", errors="replace"
                        )
                    )
            self.writer.write(frame)
            self.writer.flush()

    def _envelope(
        self,
        request: dict[str, Any],
        *,
        result: Any = None,
        error: Exception | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol_major": PROTOCOL_MAJOR,
            "protocol_minor": PROTOCOL_MINOR,
            "request_id": request.get("request_id"),
            "document_id": request.get("document_id"),
            "document_revision": request.get("document_revision"),
            "ok": error is None,
        }
        if error is None:
            payload["result"] = result
        else:
            payload["error"] = {
                "code": (
                    "resync_required"
                    if isinstance(error, ResyncRequired)
                    else "cancelled"
                    if isinstance(error, AnalysisCancelled)
                    else "invalid_request"
                    if isinstance(error, ValueError)
                    else "internal_error"
                ),
                "message": str(error),
            }
        return payload

    def _finish(self, request_id: str, result: Any = None, error: Exception | None = None) -> None:
        with self._state_lock:
            entry = self._inflight.pop(request_id, None)
            if entry is not None and entry.cancelled.is_set() and error is None:
                error = AnalysisCancelled("request cancelled")
        if entry is not None:
            self._write(self._envelope(entry.request, result=result, error=error))

    def _run_thread(self, request_id: str) -> None:
        entry = self._inflight[request_id]
        try:
            result = dispatch(entry.request, cancelled=entry.cancelled)
            if entry.request.get("operation") == "dispose_document" and not entry.cancelled.is_set():
                if self._report_worker.is_running():
                    cleanup_request: dict[str, Any] = {
                        "protocol_major": PROTOCOL_MAJOR,
                        "protocol_minor": PROTOCOL_MINOR,
                        "request_id": f"{request_id}:snapshot-disposal",
                        "document_id": entry.request.get("document_id"),
                        "document_revision": entry.request.get("document_revision"),
                        "operation": "dispose_document_snapshots",
                        "params": {},
                    }
                    cleanup = self._report_worker.execute(cleanup_request, entry.cancelled)
                    if cleanup.get("ok") is not True:
                        raise RuntimeError(f"snapshot disposal failed: {cleanup.get('message', '')}")
                    cleanup_result = cleanup.get("result", {})
                    result["disposed_analyses"] = int(result.get("disposed_analyses", 0)) + int(
                        cleanup_result.get("disposed_analyses", 0)
                    )
                # The report worker and its Harper session deliberately stay
                # warm across documents: releasing them on last-document dispose
                # forced a cold process restart (and cold Harper start) on the
                # next analysis, which dominated real editing latency.
            error = AnalysisCancelled("request cancelled") if entry.cancelled.is_set() else None
            self._finish(request_id, result=result if error is None else None, error=error)
        except Exception as exc:
            self._finish(request_id, error=exc)

    def _run_persistent_process(self, request_id: str) -> None:
        entry = self._inflight[request_id]
        try:
            request = self._prepare_worker_request(entry.request)
            message = self._report_worker.execute(request, entry.cancelled)
            worker_cancelled = entry.cancelled.is_set() or str(message.get("error_type", "")) == "AnalysisCancelled"
            if worker_cancelled:
                self._finish(request_id, error=ProtocolError("request cancelled"))
            elif message.get("ok") is True:
                self._finish(request_id, result=message.get("result"))
            else:
                error_name = str(message.get("error_type", "RuntimeError"))
                code = str(message.get("code", ""))
                error_type = (
                    ResyncRequired
                    if code == "resync_required"
                    else ValueError
                    if error_name in {"ValueError", "ProtocolError"}
                    else RuntimeError
                )
                self._finish(
                    request_id,
                    error=error_type(f"{error_name}: {message.get('message', '')}"),
                )
        except Exception as exc:
            error = ProtocolError("request cancelled") if entry.cancelled.is_set() else exc
            self._finish(request_id, error=error)

    @staticmethod
    def _prepare_worker_request(request: dict[str, Any]) -> dict[str, Any]:
        params = dict(_params(request))
        if request.get("operation") == "analyze_document" and "text" not in params:
            document = _DOCUMENTS.get_document(
                _message_document_id(request, params),
                _message_revision(request, params),
            )
            if document.exclusions_stale:
                raise ResyncRequired("document exclusion_ranges are stale; resend them with patch_document")
            params["text"] = document.text
            params.setdefault("language", document.language)
            params.setdefault("exclusion_ranges", list(document.exclusion_ranges))
        prepared = dict(request)
        prepared["params"] = params
        return prepared

    def _accept(self, request: dict[str, Any]) -> None:
        request_id = _request_id(request["request_id"])
        with self._state_lock:
            if request_id in self._inflight:
                raise ProtocolError("duplicate request_id")
            if len(self._inflight) >= MAX_INFLIGHT:
                raise ProtocolError("too many in-flight requests")
            entry = InFlight(request=request, cancelled=threading.Event())
            self._inflight[request_id] = entry
        try:
            operation = request.get("operation")
            # Store-only operations never need the worker's in-memory state, so
            # when the worker has been released (idle) they run in-process
            # against the shared snapshot store instead of respawning it.
            needs_worker = operation in PROCESS_OPERATIONS and (
                operation not in _STORE_ONLY_OPERATIONS or self._report_worker.is_running()
            )
            if needs_worker:
                thread = threading.Thread(
                    target=self._run_persistent_process,
                    args=(request_id,),
                    daemon=False,
                )
            else:
                thread = threading.Thread(target=self._run_thread, args=(request_id,), daemon=False)
            entry.thread = thread
            thread.start()
        except BaseException:
            with self._state_lock:
                self._inflight.pop(request_id, None)
            raise

    def _cancel_request(self, request_id: str) -> bool:
        with self._state_lock:
            entry = self._inflight.get(request_id)
        if entry is None:
            return False
        entry.cancelled.set()
        if entry.request.get("operation") in PROCESS_OPERATIONS:
            self._report_worker.cancel(request_id)
        return True

    def _drain(self) -> None:
        with self._state_lock:
            entries = list(self._inflight.values())
        for entry in entries:
            entry.cancelled.set()
            if entry.request.get("operation") in PROCESS_OPERATIONS:
                self._report_worker.cancel(str(entry.request["request_id"]))
        for entry in entries:
            if entry.thread and entry.thread is not threading.current_thread():
                entry.thread.join()
        with self._state_lock:
            if self._inflight:
                raise RuntimeError("sidecar failed to drain accepted requests")
        self._report_worker.stop()

    def serve(self) -> int:
        while not self._stopping:
            try:
                request = read_frame(self.reader)
            except Exception as exc:
                self._write(self._envelope({}, error=exc))
                self._drain()
                return 1
            if request is None:
                self._drain()
                break
            if request.get("protocol_major") != PROTOCOL_MAJOR:
                self._write(self._envelope(request, error=ProtocolError("unsupported protocol_major")))
                continue
            try:
                request["request_id"] = _request_id(request.get("request_id", str(uuid.uuid4())))
            except Exception as exc:
                self._write(self._envelope(request, error=exc))
                continue
            operation = request.get("operation")
            if operation == "cancel":
                try:
                    target = _request_id(_params(request).get("target_request_id"), "target_request_id")
                except Exception as exc:
                    self._write(self._envelope(request, error=exc))
                    continue
                cancelled = self._cancel_request(target)
                self._write(self._envelope(request, result={"cancelled": cancelled, "target_request_id": target}))
                continue
            if operation == "shutdown":
                self._drain()
                self._write(self._envelope(request, result={"shutting_down": True}))
                self._stopping = True
                continue
            try:
                self._accept(request)
            except Exception as exc:
                self._write(self._envelope(request, error=exc))
        return 0


def _worker_main() -> int:
    _clear_secret_environment()
    parent_pid = os.getppid()

    def stop_if_supervisor_exits() -> None:
        while True:
            time.sleep(0.25)
            parent_exited = not _windows_process_is_alive(parent_pid) if os.name == "nt" else os.getppid() != parent_pid
            if parent_exited:
                if os.name != "nt":
                    try:
                        os.killpg(os.getpgrp(), signal.SIGKILL)  # type: ignore[attr-defined]
                    finally:
                        os._exit(1)
                os._exit(1)

    threading.Thread(target=stop_if_supervisor_exits, daemon=True).start()
    body = sys.stdin.buffer.read(config.MAX_FRAME_BYTES + 1)
    if len(body) > config.MAX_FRAME_BYTES:
        return 2
    try:
        request = json.loads(body.decode("utf-8"), parse_constant=_reject_json_constant)
        if not isinstance(request, dict):
            raise ProtocolError("worker request must be an object")
        response = {"ok": True, "result": dispatch(request)}
    except BaseException as exc:
        response = {
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    serialized = json.dumps(
        response,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(serialized) > config.MAX_RESPONSE_BYTES:
        serialized = json.dumps(
            {
                "ok": False,
                "error_type": "ProtocolError",
                "message": f"worker response exceeds the {config.MAX_RESPONSE_BYTES}-byte limit",
            },
            separators=(",", ":"),
        ).encode("utf-8")
    sys.stdout.buffer.write(serialized)
    sys.stdout.buffer.flush()
    return 0


def _report_worker_main() -> int:
    _clear_secret_environment()
    parent_pid = os.getppid()
    cancel_dir = os.environ.get("THOTHPAD_CANCEL_DIR") or None
    # Warm the private Harper session immediately so the first grammar-bearing
    # analysis does not pay the process start; a missing binary degrades
    # silently and every failure path inside warm_harper is already guarded.
    warm_harper()

    def stop_if_supervisor_exits() -> None:
        while True:
            time.sleep(0.25)
            parent_exited = not _windows_process_is_alive(parent_pid) if os.name == "nt" else os.getppid() != parent_pid
            if parent_exited:
                os._exit(1)

    threading.Thread(target=stop_if_supervisor_exits, daemon=True).start()
    while True:
        try:
            request = read_frame(sys.stdin.buffer)
            if request is None:
                break
            try:
                with cancellable_analysis(_worker_cancelled_check(cancel_dir, str(request.get("request_id", "")))):
                    # The worker pipe is private to PersistentWorker.execute: it
                    # only carries client PROCESS operations and the server's
                    # own internal cleanup requests, so internal operations are
                    # dispatchable here and nowhere else on the client surface.
                    result = dispatch(request, internal=True)
                response = {"ok": True, "result": result}
            except BaseException as exc:
                response = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "code": (
                        "resync_required"
                        if isinstance(exc, ResyncRequired)
                        else "cancelled"
                        if isinstance(exc, AnalysisCancelled)
                        else ""
                    ),
                    "message": str(exc),
                }
            sys.stdout.buffer.write(encode_frame(response))
            sys.stdout.buffer.flush()
            del request, response
        except (BrokenPipeError, EOFError):
            break
        except ProtocolError:
            return 2
    if _STORE is not None:
        _STORE.close()
    return 0


def main() -> int:
    if "--thothpad-report-worker" in sys.argv:
        return _report_worker_main()
    if "--thothpad-worker" in sys.argv:
        return _worker_main()
    _clear_secret_environment()
    # The framed protocol needs exclusive ownership of stdout: analyzers and
    # their C extensions can emit progress noise to fd 1 mid-dispatch, which
    # would interleave with response frames and desynchronize the client.
    # Bind the protocol to a private duplicate of the real stdout, then point
    # fd 1 at the bit bucket so stray writes cannot corrupt the stream.
    real_stdout = os.fdopen(os.dup(1), "wb", buffering=0)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, 1)
    finally:
        os.close(devnull_fd)
    return SidecarServer(sys.stdin.buffer, real_stdout).serve()


if __name__ == "__main__":
    raise SystemExit(main())
