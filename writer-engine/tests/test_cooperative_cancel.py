"""Cooperative cancellation keeps the persistent report worker warm.

The supervisor signals an in-flight analyze_document with a per-request
cancel file that the worker polls at analysis checkpoints. Process-tree
termination remains only as the bounded last resort.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from backend.grammar import harper_path
from backend.sidecar import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    PersistentWorker,
    dispatch,
)


def _worker_request(request_id: str, text: str):
    return {
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": PROTOCOL_MINOR,
        "request_id": request_id,
        "document_id": f"doc-{request_id}",
        "document_revision": 1,
        "operation": "analyze_document",
        "params": {
            "text": text,
            "confirm_adverbs": False,
            "initial_page_size": 0,
        },
    }


def _wait_until_active(worker: PersistentWorker, request_id: str, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with worker._state_lock:
            if worker._active_request_id == request_id:
                return True
        time.sleep(0.005)
    return False


def _large_model_free_text() -> str:
    # Full preset, confirm_adverbs off: lexical analyzers only, no spaCy model.
    return "She moved quickly through the crowded market before the rain arrived. " * 20_000


def test_cooperative_cancel_preserves_the_warm_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("THOTHPAD_DATA_DIR", str(tmp_path))
    worker = PersistentWorker()
    try:
        slow_request = _worker_request("cancel-me", _large_model_free_text())
        outcome: dict[str, object] = {}

        def run_slow():
            outcome["response"] = worker.execute(slow_request, threading.Event())

        executor = threading.Thread(target=run_slow)
        executor.start()
        assert _wait_until_active(worker, "cancel-me")
        pid_before = worker._process.pid
        starts_before = worker.starts

        assert worker.cancel("cancel-me") is True
        executor.join(timeout=10)
        assert not executor.is_alive()

        # The worker itself observed the cancel flag and aborted cooperatively;
        # the supervisor suppresses this result because the entry was cancelled.
        assert worker.is_running()
        assert worker.starts == starts_before
        assert worker._process is not None
        assert worker._process.pid == pid_before
        response = outcome["response"]
        assert isinstance(response, dict)
        assert response["ok"] is False
        assert response["error_type"] == "AnalysisCancelled"

        followup = worker.execute(
            _worker_request("followup", "Mara noticed the clock."), threading.Event()
        )
        assert followup["ok"] is True
        assert worker.starts == starts_before
        assert worker._process is not None
        assert worker._process.pid == pid_before
    finally:
        worker.stop()


def test_cancel_ack_is_bounded_well_under_five_seconds(monkeypatch, tmp_path):
    monkeypatch.setenv("THOTHPAD_DATA_DIR", str(tmp_path / "resp"))
    worker = PersistentWorker()
    try:
        slow_request = _worker_request("cancel-timing", _large_model_free_text())
        done = threading.Event()

        def run_slow():
            try:
                worker.execute(slow_request, threading.Event())
            finally:
                done.set()

        executor = threading.Thread(target=run_slow)
        executor.start()
        assert _wait_until_active(worker, "cancel-timing")

        started = time.perf_counter()
        assert worker.cancel("cancel-timing") is True
        assert done.wait(timeout=10)
        elapsed = time.perf_counter() - started
        assert elapsed < 5.0
        assert worker.is_running()
    finally:
        worker.stop()


def test_noncooperative_worker_still_terminates_after_grace_period():
    worker = PersistentWorker()
    worker._cancel_grace_seconds = 0.3
    creation_flags = (
        subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        if os.name == "nt" else 0
    )
    # A stand-in "worker" that never observes cancel flags (no cancel dir is
    # installed), forcing the bounded fallback onto the real terminate path.
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )
    worker._process = sleeper
    worker._active_request_id = "stuck-request"
    try:
        started = time.perf_counter()
        assert worker.cancel("stuck-request") is True
        assert sleeper.poll() is not None or sleeper.wait(timeout=10) is not None
        elapsed = time.perf_counter() - started
        assert elapsed < 5.0
        assert worker._process is None
        assert worker.is_running() is False
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
            sleeper.wait(timeout=5)
        worker.stop()


def test_initialize_calls_the_harper_warm_hook_exactly_once(monkeypatch):
    from backend import sidecar

    calls: list[int] = []
    monkeypatch.setattr(sidecar, "warm_harper", lambda: calls.append(1))
    dispatch({
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": PROTOCOL_MINOR,
        "request_id": "warm-init",
        "operation": "initialize",
        "params": {},
    })
    assert len(calls) == 1


def test_report_worker_startup_warms_harper_exactly_once(monkeypatch):
    from backend import sidecar

    calls: list[int] = []
    monkeypatch.setattr(sidecar, "warm_harper", lambda: calls.append(1))
    monkeypatch.setattr(sidecar, "read_frame", lambda stream: None)
    assert sidecar._report_worker_main() == 0
    assert len(calls) == 1


@pytest.mark.requires_harper
@pytest.mark.skipif(not harper_path().is_file(), reason="Harper bridge has not been built")
def test_harper_session_persists_across_two_documents():
    from backend import grammar

    settings = {
        "provider": "harper",
        "dialect": "en-US",
        "include_spelling": False,
        "max_findings": 50,
        "timeout": 30,
    }
    try:
        first = grammar.analyze_grammar(
            "Mara noticed the rain gathering over the harbor.", settings
        )
        assert first.metrics.get("available") is True
        session = grammar._HARPER_SESSION
        assert session.is_running()
        pid_first = session._process.pid

        second = grammar.analyze_grammar(
            "It was not merely weather; it was an omen she could not name.", settings
        )
        assert second.metrics.get("available") is True
        assert session.is_running()
        assert session._process.pid == pid_first
    finally:
        grammar.release_harper()
