from __future__ import annotations

import pytest

from backend import config
from backend.documents import ResyncRequired
from backend.sidecar import PROTOCOL_MAJOR, dispatch


def request(operation: str, **params):
    return {
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": 1,
        "request_id": "request-1",
        "document_id": "document-1",
        "document_revision": 9,
        "operation": operation,
        "params": params,
    }


def diagnostic(index: int, analyzer: str) -> dict:
    return {
        "id": f"d-{index}",
        "analyzer": analyzer,
        "rule_id": f"{analyzer}.test",
        "start_utf16": index * 2,
        "end_utf16": index * 2 + 1,
        "excerpt": "x",
        "revision": 9,
    }


def test_document_analysis_snapshots_and_protocol_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ANALYSIS_CACHE_DB", tmp_path / "snapshots.sqlite3")
    findings = [
        diagnostic(index, "possible_adverbs" if index % 2 == 0 else "possible_verbs")
        for index in range(1101)
    ]

    def fake_analyze_text(*_args, **_kwargs):
        return {
            "diagnostics": findings,
            "analysis": [
                {"name": "possible_adverbs", "flags": findings[::2], "metrics": {}},
                {"name": "possible_verbs", "flags": findings[1::2], "metrics": {}},
            ],
            "text_hash": "document-hash",
            "document_revision": 9,
            "truncated": False,
        }

    monkeypatch.setattr("backend.sidecar.analyze_text", fake_analyze_text)
    first = dispatch(request("analyze_document", text="text", initial_page_size=137))
    assert first["persisted"] is False
    assert not config.ANALYSIS_CACHE_DB.exists()
    assert first["total_findings"] == 1101
    assert first["counts_by_analyzer"] == {
        "possible_adverbs": 551,
        "possible_verbs": 550,
    }
    assert len(first["diagnostics"]) == 137
    assert first["has_more"] is True
    assert all(row["flags"] == [] for row in first["analysis"])

    ids = [item["id"] for item in first["diagnostics"]]
    cursor = first["next_cursor"]
    while cursor:
        page = dispatch(
            request(
                "query_findings",
                analysis_id=first["analysis_id"],
                cursor=cursor,
                limit=500,
            )
        )
        ids.extend(item["id"] for item in page["diagnostics"])
        cursor = page["next_cursor"]
    assert ids == [f"d-{index}" for index in range(1101)]

    verbs = dispatch(
        request(
            "query_findings",
            analysis_id=first["analysis_id"],
            analyzer="possible_verbs",
            start_utf16=100,
            end_utf16=120,
            limit=500,
        )
    )
    assert verbs["total_findings"] == 5
    assert all(item["analyzer"] == "possible_verbs" for item in verbs["diagnostics"])
    assert all(item["end_utf16"] > 100 and item["start_utf16"] < 120 for item in verbs["diagnostics"])

    combined = dispatch(
        request(
            "query_findings",
            analysis_id=first["analysis_id"],
            analyzers=["possible_verbs", "possible_adverbs", "possible_verbs"],
            start_utf16=100,
            end_utf16=120,
            limit=500,
        )
    )
    assert combined["total_findings"] == 10
    assert [item["id"] for item in combined["diagnostics"]] == [
        f"d-{index}" for index in range(50, 60)
    ]

    disposed = dispatch(request("dispose_analysis", analysis_id=first["analysis_id"]))
    assert disposed["disposed"] is True
    with pytest.raises(ValueError, match="invalid or expired"):
        dispatch(request("query_findings", analysis_id=first["analysis_id"]))
    assert not config.ANALYSIS_CACHE_DB.exists()


def test_document_analysis_writes_sqlite_only_with_explicit_persist(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ANALYSIS_CACHE_DB", tmp_path / "snapshots.sqlite3")
    monkeypatch.setattr(
        "backend.sidecar.analyze_text",
        lambda *_args, **_kwargs: {
            "diagnostics": [diagnostic(0, "possible_adverbs")],
            "analysis": [],
            "text_hash": "saved-hash",
        },
    )
    result = dispatch(
        request(
            "analyze_document",
            text="text",
            persist=True,
            initial_page_size=1,
        )
    )
    assert result["persisted"] is True
    assert config.ANALYSIS_CACHE_DB.exists()


def test_region_analysis_remains_direct(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ANALYSIS_CACHE_DB", tmp_path / "unused.sqlite3")
    direct = {"diagnostics": [diagnostic(0, "possible_adverbs")], "text_hash": "hash"}
    monkeypatch.setattr("backend.sidecar.analyze_text", lambda *_args, **_kwargs: direct)
    assert dispatch(request("analyze_region", text="text")) is direct
    assert not config.ANALYSIS_CACHE_DB.exists()


def test_document_reference_region_slices_utf16_and_sets_base_offset(monkeypatch):
    text = "A\U0001f600 quickly."
    opened = request("open_document", text=text, language="en")
    dispatch(opened)
    captured = {}

    def fake_analyze(value, **kwargs):
        captured.update(text=value, **kwargs)
        return {"diagnostics": [], "analysis": [], "text_hash": "slice"}

    monkeypatch.setattr("backend.sidecar.analyze_text", fake_analyze)
    result = dispatch(request(
        "analyze_region",
        start_utf16=4,
        end_utf16=11,
        analyzers=["possible_adverbs"],
    ))
    assert result["text_hash"] == "slice"
    assert captured["text"] == "quickly"
    assert captured["base_offset_utf16"] == 4
    assert captured["analyzers"] == ["possible_adverbs"]


@pytest.mark.parametrize(
    "start,end,error",
    [
        (2, 3, "splits a surrogate pair"),
        (3, 3, "start_utf16 < end_utf16"),
        (3, 99, "outside the text"),
        (-1, 3, "start_utf16 < end_utf16"),
    ],
)
def test_document_reference_region_rejects_invalid_utf16_ranges(start, end, error):
    dispatch(request("open_document", text="A\U0001f600 quickly."))
    with pytest.raises(ValueError, match=error):
        dispatch(request("analyze_region", start_utf16=start, end_utf16=end))


def test_document_reference_reuses_and_adjusts_stored_exclusions(monkeypatch):
    document_id = "stored-exclusions"
    opened = request(
        "open_document",
        text="xxCODEyy",
        exclusion_ranges=[{"start_utf16": 2, "end_utf16": 6, "kind": "code"}],
    )
    opened["document_id"] = document_id
    dispatch(opened)
    patched = request(
        "patch_document",
        base_revision=9,
        revision=10,
        changes=[{"start_utf16": 0, "end_utf16": 0, "replacement": "ABC"}],
    )
    patched["document_revision"] = 10
    patched["document_id"] = document_id
    metadata = dispatch(patched)
    assert metadata["exclusions_stale"] is False

    captured = {}
    monkeypatch.setattr(
        "backend.sidecar.analyze_text",
        lambda text, **kwargs: captured.update(text=text, **kwargs)
        or {"diagnostics": [], "analysis": [], "text_hash": "hash"},
    )
    analyzed = request("analyze_document", revision=10, initial_page_size=0)
    analyzed["document_revision"] = 10
    analyzed["document_id"] = document_id
    dispatch(analyzed)
    assert captured["text"] == "ABCxxCODEyy"
    assert captured["exclusion_ranges"] == [
        {"start_utf16": 5, "end_utf16": 9, "kind": "code"}
    ]


def test_overlapping_patch_marks_exclusions_stale_until_replaced():
    document_id = "stale-exclusions"
    opened = request(
        "open_document",
        text="xxCODEyy",
        exclusion_ranges=[{"start_utf16": 2, "end_utf16": 6, "kind": "code"}],
    )
    opened["document_id"] = document_id
    dispatch(opened)
    patch = request(
        "patch_document",
        base_revision=9,
        revision=10,
        changes=[{"start_utf16": 1, "end_utf16": 4, "replacement": "X"}],
    )
    patch["document_revision"] = 10
    patch["document_id"] = document_id
    assert dispatch(patch)["exclusions_stale"] is True

    analysis = request("analyze_document", revision=10)
    analysis["document_revision"] = 10
    analysis["document_id"] = document_id
    with pytest.raises(ResyncRequired, match="exclusion_ranges are stale"):
        dispatch(analysis)

    replacement = request(
        "patch_document",
        base_revision=10,
        revision=11,
        changes=[],
        exclusion_ranges=[{"start_utf16": 2, "end_utf16": 6, "kind": "code"}],
    )
    replacement["document_revision"] = 11
    replacement["document_id"] = document_id
    assert dispatch(replacement)["exclusions_stale"] is False


def test_protocol_rejects_oversized_pages_and_invalid_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ANALYSIS_CACHE_DB", tmp_path / "snapshots.sqlite3")
    with pytest.raises(ValueError, match="page size"):
        dispatch(request("query_findings", analysis_id="0" * 32, limit=501))
    with pytest.raises(ValueError, match="analysis_id"):
        dispatch(request("dispose_analysis", analysis_id="../../runs"))
