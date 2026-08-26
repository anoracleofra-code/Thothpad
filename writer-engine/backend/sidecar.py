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
from backend.models import AnalyzerResult, RunRequest
from backend.pipeline import compare_texts, run_pipeline
from backend.profiles import (
    export_profile,
    get_profile,
    import_profile,
    list_profiles,
    load_profile,
    save_profile,
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

MAX_HEADER_COUNT = 16
MAX_HEADER_BYTES = 16_384
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
            "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
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
        taskkill = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe"
        )
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


def _create_windows_kill_job(process_id: int) -> int:
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_extended_limit_information = 9
    job_object_limit_kill_on_job_close = 0x00002000
    process_set_quota = 0x0100
    process_terminate = 0x0001
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = job_object_limit_kill_on_job_close
    process_handle = None
    try:
        if not kernel32.SetInformationJobObject(
            job,
            job_object_extended_limit_information,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        process_handle = kernel32.OpenProcess(
            process_set_quota | process_terminate, False, process_id
        )
        if not process_handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        if not kernel32.AssignProcessToJobObject(job, process_handle):
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        return int(ctypes.cast(job, ctypes.c_void_p).value)  # type: ignore[arg-type]
    except BaseException:
        kernel32.CloseHandle(job)
        raise
    finally:
        if process_handle:
            kernel32.CloseHandle(process_handle)


def _close_windows_handle(handle: int | None) -> None:
    if handle and os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle(wintypes.HANDLE(handle))


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
        raise ResyncRequired(
            "document exclusion_ranges are stale; resend them with patch_document"
        )
    exclusions = params.get("exclusion_ranges", list(document.exclusion_ranges))
    if operation != "analyze_region":
        return document.text, document.language, 0, exclusions
    start = params.get("start_utf16")
    end = params.get("end_utf16")
    if (
        isinstance(start, bool) or isinstance(end, bool)
        or not isinstance(start, int) or not isinstance(end, int)
        or start < 0 or end <= start
    ):
        raise ValueError(
            "document-reference region requires 0 <= start_utf16 < end_utf16"
        )
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
        _analyzers,
        _apply_thresholds,
    )
    from backend.analyzers.dialogue import dialogue_spans, inside_dialogue
    from backend.analyzers.pattern_helpers import with_profile_patterns

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
        registry = _analyzers()
        unknown = sorted(set(selected) - set(registry))
        if unknown:
            raise ValueError(f"unknown analyzers: {', '.join(unknown)}")
        results = []
        if lexical_rules_enabled:
            for name in selected:
                cancellation_checkpoint()
                results.append(registry[name].analyze(text, profile))
                cancellation_checkpoint()

            spans: list[tuple[int, int]] | None = None
            dialogue_settings = profile.get("dialogue_exclusions", {}) or {}
            for result in results:
                cancellation_checkpoint()
                settings = profile.get(result.name, {}) or {}
                ignore_dialogue = settings.get(
                    "ignore_dialogue",
                    dialogue_settings.get(result.name, dialogue_settings.get("all", False)),
                )
                if not ignore_dialogue:
                    continue
                if spans is None:
                    spans = dialogue_spans(text)
                original_count = len(result.flags)
                result.flags = [
                    flag for flag in result.flags
                    if not inside_dialogue(flag.start, flag.end, spans)
                ]
                removed = original_count - len(result.flags)
                if removed:
                    result.score = max(0.0, result.score - removed)
                result.metrics["ignored_dialogue"] = True
                result.metrics["dialogue_findings_removed"] = removed

        cancellation_checkpoint()
        results.append(with_profile_patterns(
            AnalyzerResult(name="profile_patterns", score=0.0), text, profile
        ))
        _apply_thresholds(results, profile)
        for result in results:
            result.metrics.setdefault("total_findings", len(result.flags))
            result.metrics.setdefault("findings_truncated", False)
            for flag in result.flags:
                flag.analyzer = result.name

        grammar_allowed = bool(
            grammar
            and (grammar.get("provider") != "harper" or lexical_rules_enabled)
        )
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
        serialization_ms = round(
            (time.perf_counter() - serialization_started) * 1000, 3
        )
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
    message: dict[str, Any], *, cancelled: threading.Event | None = None
) -> dict[str, Any]:
    operation = str(message.get("operation", ""))
    if operation not in OPERATIONS and operation not in _INTERNAL_OPERATIONS:
        raise ValueError(f"unsupported operation: {operation}")
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
        confirm_adverbs = _bool(
            params, "confirm_adverbs", operation == "analyze_document"
        )
        document_revision = (
            int(message["document_revision"])
            if message.get("document_revision") is not None else None
        )
        language = (
            str(params.get("language"))
            if params.get("language") is not None else document_language
        )
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
                preset=(
                    "live" if operation == "analyze_region"
                    else str(params.get("preset", "full"))
                ),
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
                int(message["document_revision"])
                if message.get("document_revision") is not None
                else None
            ),
            text_hash=str(result.get("text_hash", "")),
            initial_page_size=params.get(
                "initial_page_size", config.DEFAULT_FINDING_PAGE_SIZE
            ),
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
        passes = validate_passes(int(params.get("passes", 1)))
        mode = str(params.get("mode", "rewrite"))
        if mode not in {"rewrite", "deslop", "line_edit", "write_from_brief"}:
            raise ValueError("unsupported rewrite mode")
        profile_name = validate_profile_name(
            str(params.get("profile", config.DEFAULT_PROFILE))
        )
        snapshot = params.get("profile_snapshot")  # type: ignore[assignment]
        if snapshot is not None:
            snapshot = validate_profile(snapshot)
            if snapshot.get("name") != profile_name:
                raise ValueError("profile_snapshot name must match profile")
        return run_pipeline(RunRequest(
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
        ))
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
            creation_flags = (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
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

    def execute(
        self, request: dict[str, Any], cancelled: threading.Event
    ) -> dict[str, Any]:
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
                        f"[write] op={payload.get('operation')} "
                        f"declared={len(frame)} "
                        f"head={frame[:48]!r}\n".encode("utf-8", errors="replace")
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
                    "resync_required" if isinstance(error, ResyncRequired)
                    else "cancelled" if isinstance(error, AnalysisCancelled)
                    else "invalid_request" if isinstance(error, ValueError)
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
            if (
                entry.request.get("operation") == "dispose_document"
                and not entry.cancelled.is_set()
            ):
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
                    cleanup = self._report_worker.execute(
                        cleanup_request, entry.cancelled
                    )
                    if cleanup.get("ok") is not True:
                        raise RuntimeError(
                            f"snapshot disposal failed: {cleanup.get('message', '')}"
                        )
                    cleanup_result = cleanup.get("result", {})
                    result["disposed_analyses"] = int(
                        result.get("disposed_analyses", 0)
                    ) + int(cleanup_result.get("disposed_analyses", 0))
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
            worker_cancelled = (
                entry.cancelled.is_set()
                or str(message.get("error_type", "")) == "AnalysisCancelled"
            )
            if worker_cancelled:
                self._finish(request_id, error=ProtocolError("request cancelled"))
            elif message.get("ok") is True:
                self._finish(request_id, result=message.get("result"))
            else:
                error_name = str(message.get("error_type", "RuntimeError"))
                code = str(message.get("code", ""))
                error_type = (
                    ResyncRequired if code == "resync_required"
                    else ValueError if error_name in {"ValueError", "ProtocolError"}
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
                raise ResyncRequired(
                    "document exclusion_ranges are stale; resend them with patch_document"
                )
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
                request["request_id"] = _request_id(
                    request.get("request_id", str(uuid.uuid4()))
                )
            except Exception as exc:
                self._write(self._envelope(request, error=exc))
                continue
            operation = request.get("operation")
            if operation == "cancel":
                try:
                    target = _request_id(
                        _params(request).get("target_request_id"), "target_request_id"
                    )
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
            parent_exited = (
                not _windows_process_is_alive(parent_pid)
                if os.name == "nt"
                else os.getppid() != parent_pid
            )
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
        serialized = json.dumps({
            "ok": False,
            "error_type": "ProtocolError",
            "message": f"worker response exceeds the {config.MAX_RESPONSE_BYTES}-byte limit",
        }, separators=(",", ":")).encode("utf-8")
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
            parent_exited = (
                not _windows_process_is_alive(parent_pid)
                if os.name == "nt" else os.getppid() != parent_pid
            )
            if parent_exited:
                os._exit(1)

    threading.Thread(target=stop_if_supervisor_exits, daemon=True).start()
    while True:
        try:
            request = read_frame(sys.stdin.buffer)
            if request is None:
                break
            try:
                with cancellable_analysis(
                    _worker_cancelled_check(cancel_dir, str(request.get("request_id", "")))
                ):
                    result = dispatch(request)
                response = {"ok": True, "result": result}
            except BaseException as exc:
                response = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "code": (
                        "resync_required" if isinstance(exc, ResyncRequired)
                        else "cancelled" if isinstance(exc, AnalysisCancelled)
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
