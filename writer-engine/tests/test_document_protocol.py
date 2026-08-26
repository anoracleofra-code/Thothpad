from __future__ import annotations

import hashlib
import io
import threading
import time
import uuid

import pytest

from backend import config
from backend.documents import ResyncRequired
from backend.sidecar import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    PersistentWorker,
    SidecarServer,
    dispatch,
    encode_frame,
    read_frame,
)


def request(operation: str, *, document_id: str | None = None, revision: int = 1, **params):
    return {
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": PROTOCOL_MINOR,
        "request_id": uuid.uuid4().hex,
        "document_id": document_id or f"doc-{uuid.uuid4().hex}",
        "document_revision": revision,
        "operation": operation,
        "params": params,
    }


def test_open_patch_analyze_and_dispose_document_reference(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ANALYSIS_CACHE_DB", tmp_path / "analysis.sqlite3")
    document_id = f"doc-{uuid.uuid4().hex}"
    text = "A\U0001f600B"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    opened = dispatch(request(
        "open_document", document_id=document_id, text=text, hash=digest
    ))
    assert opened["text_utf16_units"] == 4

    patched = dispatch(request(
        "patch_document",
        document_id=document_id,
        revision=2,
        base_revision=1,
        changes=[{"start_utf16": 1, "end_utf16": 3, "replacement": "X"}],
    ))
    assert patched["document_revision"] == 2
    captured = {}

    def fake_analyze(value, **kwargs):
        captured.update(text=value, **kwargs)
        return {"diagnostics": [], "analysis": [], "text_hash": "hash"}

    monkeypatch.setattr("backend.sidecar.analyze_text", fake_analyze)
    result = dispatch(request(
        "analyze_document", document_id=document_id, revision=2, initial_page_size=0
    ))
    assert captured["text"] == "AXB"
    assert result["total_findings"] == 0

    disposed = dispatch(request("dispose_document", document_id=document_id, revision=2))
    assert disposed["disposed"] is True
    with pytest.raises(ResyncRequired, match="not open"):
        dispatch(request("analyze_document", document_id=document_id, revision=2))


def test_patch_revision_hash_and_surrogate_failures_require_resync():
    document_id = f"doc-{uuid.uuid4().hex}"
    dispatch(request("open_document", document_id=document_id, text="A\U0001f600B"))
    with pytest.raises(ResyncRequired, match="base_revision"):
        dispatch(request(
            "patch_document", document_id=document_id, revision=3,
            base_revision=2, changes=[]
        ))
    with pytest.raises(ResyncRequired, match="surrogate pair"):
        dispatch(request(
            "patch_document", document_id=document_id, revision=2,
            base_revision=1,
            changes=[{"start_utf16": 2, "end_utf16": 3, "replacement": "X"}],
        ))
    with pytest.raises(ResyncRequired, match="hash"):
        dispatch(request(
            "patch_document", document_id=document_id, revision=2,
            base_revision=1, changes=[], hash="0" * 64
        ))


def test_patch_indexes_only_bounded_chunks_and_defers_whole_document_hash(monkeypatch):
    from backend import documents

    document_id = f"doc-{uuid.uuid4().hex}"
    text = ("alpha \U0001f600 beta " * 80_000) + "tail"
    dispatch(request("open_document", document_id=document_id, text=text))

    indexed_lengths = []
    original_index = documents.Utf16Index

    def bounded_index(value):
        indexed_lengths.append(len(value))
        return original_index(value)

    monkeypatch.setattr(documents, "Utf16Index", bounded_index)
    monkeypatch.setattr(
        documents,
        "_text_hash",
        lambda _value: pytest.fail("ordinary patch computed a whole-document hash"),
    )
    prefix = "alpha \U0001f600 beta " * 40_000
    position = len(prefix) + prefix.count("\U0001f600")
    started = time.perf_counter()
    patched = dispatch(request(
        "patch_document",
        document_id=document_id,
        revision=2,
        base_revision=1,
        changes=[{
            "start_utf16": position,
            "end_utf16": position,
            "replacement": "X",
        }],
    ))
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert patched["hash_deferred"] is True
    assert patched["text_hash"] == ""
    assert indexed_lengths and max(indexed_lengths) <= 4_096
    assert elapsed_ms < 250
    from backend import sidecar
    document = sidecar._DOCUMENTS.get_document(document_id, 2)
    assert document.buffer.slice_utf16(position, position + 1) == "X"
    dispatch(request("dispose_document", document_id=document_id, revision=2))


def test_resync_required_has_distinct_protocol_error_code():
    server = SidecarServer(io.BytesIO(), io.BytesIO())
    envelope = server._envelope(
        request("patch_document"), error=ResyncRequired("revision mismatch")
    )
    assert envelope["error"] == {
        "code": "resync_required",
        "message": "revision mismatch",
    }


def test_read_frame_reads_partial_body_chunks_exactly():
    class ChunkedStream(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 3) if size >= 0 else 3)

    message = request("capabilities")
    assert read_frame(ChunkedStream(encode_frame(message))) == message


def test_persistent_report_worker_is_reused(monkeypatch, tmp_path):
    monkeypatch.setenv("THOTHPAD_DATA_DIR", str(tmp_path))
    worker = PersistentWorker()
    try:
        for index in range(2):
            value = request(
                "analyze_document",
                document_id=f"legacy-{index}",
                text="Mara noticed the clock.",
                analyzers=["filter_words"],
                confirm_adverbs=False,
                initial_page_size=0,
            )
            response = worker.execute(value, threading.Event())
            assert response["ok"] is True
        assert worker.starts == 1
    finally:
        worker.stop()


def test_report_worker_pages_transient_analysis_without_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("THOTHPAD_DATA_DIR", str(tmp_path))
    worker = PersistentWorker()
    try:
        analysis_request = request(
            "analyze_document",
            document_id="private-report",
            text="Mara moved quickly. She answered softly.",
            analyzers=["possible_adverbs"],
            confirm_adverbs=False,
            initial_page_size=1,
            persist=False,
        )
        analyzed = worker.execute(analysis_request, threading.Event())
        assert analyzed["ok"] is True
        result = analyzed["result"]
        assert result["persisted"] is False

        page_request = request(
            "query_findings",
            analysis_id=result["analysis_id"],
            limit=500,
        )
        paged = worker.execute(page_request, threading.Event())
        assert paged["ok"] is True
        assert paged["result"]["persisted"] is False
        assert not list(tmp_path.rglob("*.sqlite3"))
        assert worker.starts == 1
    finally:
        worker.stop()
