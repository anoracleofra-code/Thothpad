import io
import os
import subprocess
import sys
import threading
import time

import pytest

from backend.sidecar import PROTOCOL_MAJOR, ProtocolError, SidecarServer, dispatch, encode_frame, read_frame
from backend.text_utils import cancellation_checkpoint


def request(operation, **params):
    return {
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": 0,
        "request_id": "request-1",
        "document_id": "document-1",
        "document_revision": 9,
        "operation": operation,
        "params": params,
    }


def test_content_length_frame_round_trip():
    message = request("initialize")
    assert read_frame(io.BytesIO(encode_frame(message))) == message


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows process monitor")
def test_windows_supervisor_liveness_check():
    from backend.sidecar import _windows_process_is_alive

    assert _windows_process_is_alive(os.getpid()) is True
    assert _windows_process_is_alive(0x7FFFFFFF) is False


@pytest.mark.windows
@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects")
def test_windows_kill_job_terminates_worker_on_handle_close():
    from backend.sidecar import _close_windows_handle, _create_windows_kill_job

    worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        handle = _create_windows_kill_job(worker.pid)
        _close_windows_handle(handle)
        assert worker.wait(timeout=5) is not None
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)


def test_frame_rejects_oversized_content_length():
    stream = io.BytesIO(b"Content-Length: 999999999\r\n\r\n")
    with pytest.raises(ProtocolError):
        read_frame(stream)


def test_live_input_bound_is_enforced(monkeypatch):
    from backend import config

    monkeypatch.setattr(config, "MAX_LIVE_TEXT_CHARS", 4)
    with pytest.raises(ValueError, match="4-character"):
        dispatch(request("analyze_region", text="12345"))


def test_capabilities_advertise_all_slice_two_operations():
    result = dispatch(request("capabilities"))
    assert result["offset_encoding"] == "utf-16"
    assert result["analysis_persists_by_default"] is False
    assert {
        "initialize", "capabilities", "list_profiles", "get_profile", "save_profile",
        "import_profile", "export_profile", "analyze_region", "analyze_document",
        "analyze_manuscript", "rewrite", "compare", "cancel", "shutdown",
    } <= set(result["operations"])


def test_initialize_applies_cpu_only_automatic_tuning(monkeypatch):
    monkeypatch.setattr("backend.sidecar.os.cpu_count", lambda: 2)
    result = dispatch(request(
        "initialize",
        performance={
            "mode": "automatic",
            "logical_processors": 2,
            "background_threads": 8,
            "memory_limit_mb": 768,
        },
    ))
    assert result["performance"]["background_threads"] == 1
    assert result["performance"]["core_gpu_acceleration"] is False
    assert os.environ["OMP_NUM_THREADS"] == "1"
    assert os.environ["THOTHPAD_MEMORY_LIMIT_MB"] == "768"


def test_dispatch_region_preserves_revision_metadata():
    result = dispatch(request("analyze_region", text="Mara noticed it."))
    assert result["document_revision"] == 9
    assert result["diagnostics"][0]["revision"] == 9


def test_cancellable_live_path_preserves_protocol_results():
    value = request(
        "analyze_region",
        text="Mara moved quickly, but she did not simply wait.",
        analyzers=["binary_contrast", "filter_words", "possible_adverbs"],
    )
    direct = dispatch(value)
    cancellable = dispatch(value, cancelled=threading.Event())
    for result in (direct, cancellable):
        result.pop("duration_ms")
        result.pop("stage_timings_ms")
    assert cancellable == direct


def test_dispatch_manuscript_does_not_persist():
    result = dispatch(request("analyze_manuscript", documents=[{"name": "one.md", "text": "A dime a dozen."}]))
    assert result["document_count"] == 1
    assert result["persisted"] is False
    assert "run_id" not in result


def test_dispatch_compare_does_not_persist():
    result = dispatch(request("compare", before="It wasn't fear. It was memory.", after="Mara remembered."))
    assert result["persisted"] is False
    assert "run_id" not in result


def test_profile_operations_dispatch_through_sidecar(tmp_path, monkeypatch):
    from backend import config

    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    saved = dispatch(request("save_profile", name="desktop", profile={"prefer": ["plain verbs"]}))
    assert saved["name"] == "desktop"
    assert dispatch(request("get_profile", name="desktop"))["profile"]["name"] == "desktop"
    assert any(item["name"] == "desktop" for item in dispatch(request("list_profiles"))["profiles"])

    exported = dispatch(request("export_profile", name="desktop"))
    assert exported["profile"]["name"] == "desktop"
    imported = dispatch(request("import_profile", profile=exported["profile"], name="desktop-copy"))
    assert imported["name"] == "desktop-copy"


def test_rewrite_dispatch_preserves_compatibility(monkeypatch):
    monkeypatch.setattr("backend.sidecar.run_pipeline", lambda value: {"mode": value.mode, "persisted": value.persist})
    assert dispatch(request("rewrite", text="Draft", mode="line_edit")) == {
        "mode": "line_edit",
        "persisted": False,
    }


def test_rewrite_rejects_mismatched_profile_snapshot():
    with pytest.raises(ValueError, match="must match"):
        dispatch(request(
            "rewrite",
            text="Draft",
            mode="line_edit",
            profile="creative-default",
            profile_snapshot={"name": "different"},
        ))


def test_cancel_completes_accepted_request_and_releases_slot():
    slow = request("analyze_document", text=("It was really quickly a dime a dozen. " * 20_000))
    slow["request_id"] = "slow"
    cancel = request("cancel", target_request_id="slow")
    cancel["request_id"] = "cancel"
    shutdown = request("shutdown")
    shutdown["request_id"] = "shutdown"
    reader = io.BytesIO(encode_frame(slow) + encode_frame(cancel) + encode_frame(shutdown))
    writer = io.BytesIO()
    server = SidecarServer(reader, writer)
    assert server.serve() == 0
    assert server._inflight == {}
    writer.seek(0)
    responses = []
    while response := read_frame(writer):
        responses.append(response)
    by_id = {response["request_id"]: response for response in responses}
    assert by_id["cancel"]["result"]["cancelled"] is True
    assert by_id["slow"]["ok"] is False
    assert "cancelled" in by_id["slow"]["error"]["message"]


def test_live_cancellation_stops_cooperatively_within_250ms_and_emits_no_result(monkeypatch):
    from backend.analyzers import base

    started = threading.Event()
    stopped = threading.Event()

    class SlowAnalyzer:
        name = "binary_contrast"

        def analyze(self, _text, _profile=None):
            started.set()
            try:
                while True:
                    cancellation_checkpoint()
                    time.sleep(0.005)
            finally:
                stopped.set()

    monkeypatch.setattr(base, "_analyzers", lambda: {"binary_contrast": SlowAnalyzer()})
    value = request(
        "analyze_region", text="A bounded live region.", analyzers=["binary_contrast"]
    )
    value["request_id"] = "live-cancellable"
    writer = io.BytesIO()
    server = SidecarServer(io.BytesIO(), writer)
    server._accept(value)
    assert started.wait(1)
    entry = server._inflight["live-cancellable"]

    cancelled_at = time.perf_counter()
    assert server._cancel_request("live-cancellable") is True
    assert entry.thread is not None
    entry.thread.join(timeout=0.25)
    elapsed_ms = (time.perf_counter() - cancelled_at) * 1000

    assert not entry.thread.is_alive()
    assert stopped.is_set()
    assert elapsed_ms <= 250
    writer.seek(0)
    response = read_frame(writer)
    assert response is not None
    assert response["ok"] is False
    assert response["error"]["code"] == "cancelled"
    assert read_frame(writer) is None
    server._report_worker.stop()


def test_dispose_document_keeps_report_worker_and_harper_warm(monkeypatch, tmp_path):
    from backend import config

    monkeypatch.setattr(config, "ANALYSIS_CACHE_DB", tmp_path / "analysis.sqlite3")

    class FakeWorker:
        def __init__(self):
            self.stopped = False

        def is_running(self):
            return True

        def execute(self, worker_request, cancelled):
            assert worker_request["operation"] == "dispose_document_snapshots"
            return {"ok": True, "result": {"disposed_analyses": 2}}

        def stop(self):
            self.stopped = True

    worker = FakeWorker()
    value = request("dispose_document", document_id="doc-gone")
    value["request_id"] = "dispose-release"
    writer = io.BytesIO()
    server = SidecarServer(io.BytesIO(), writer)
    server._report_worker = worker
    server._accept(value)
    deadline = time.monotonic() + 5
    while not writer.getvalue() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert writer.getvalue()
    response = read_frame(io.BytesIO(writer.getvalue()))
    assert response is not None
    assert response["ok"] is True
    assert response["result"]["disposed_analyses"] == 2
    assert worker.stopped is False


def test_dispose_analysis_with_released_worker_does_not_respawn(monkeypatch, tmp_path):
    from backend import config

    monkeypatch.setattr(config, "ANALYSIS_CACHE_DB", tmp_path / "analysis.sqlite3")

    class IdleWorker:
        def __init__(self):
            self.stopped = False

        def is_running(self):
            return False

        def stop(self):
            self.stopped = True

    worker = IdleWorker()
    value = request("dispose_analysis", analysis_id="0f8d4c2e" * 4)
    value["request_id"] = "dispose-idle-store"
    writer = io.BytesIO()
    server = SidecarServer(io.BytesIO(), writer)
    server._report_worker = worker
    server._accept(value)
    deadline = time.monotonic() + 5
    while not writer.getvalue() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert writer.getvalue()
    response = read_frame(io.BytesIO(writer.getvalue()))
    assert response is not None
    assert response["ok"] is True
    assert response["result"] == {
        "analysis_id": "0f8d4c2e" * 4,
        "disposed": False,
    }
    assert worker.stopped is False
