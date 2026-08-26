from __future__ import annotations

import os

import pytest

from backend.analysis_store import AnalysisStore


def finding(index: int, analyzer: str = "possible_adverbs") -> dict:
    return {
        "id": f"finding-{index}",
        "analyzer": analyzer,
        "rule_id": f"{analyzer}.word",
        "start_utf16": index * 3,
        "end_utf16": index * 3 + 2,
        "excerpt": f"w{index}",
        "level": "taste_flag",
    }


def test_pages_more_than_500_without_gaps_or_duplicates(tmp_path):
    database = tmp_path / "analysis.sqlite3"
    store = AnalysisStore(database)
    created = store.create_snapshot(
        [finding(index) for index in range(1203)],
        document_id="doc",
        document_revision=7,
        text_hash="hash",
        initial_page_size=500,
    )

    pages = [created]
    while pages[-1]["has_more"]:
        pages.append(
            store.query_findings(
                created["analysis_id"], cursor=pages[-1]["next_cursor"], limit=500
            )
        )

    ids = [item["id"] for page in pages for item in page["diagnostics"]]
    assert [len(page["diagnostics"]) for page in pages] == [500, 500, 203]
    assert len(ids) == len(set(ids)) == 1203
    assert ids == [f"finding-{index}" for index in range(1203)]
    assert all(page["total_findings"] == 1203 for page in pages)
    assert all(page["counts_by_analyzer"] == {"possible_adverbs": 1203} for page in pages)
    assert all(page["persisted"] is False for page in pages)
    assert not database.exists()
    assert not (tmp_path / "analysis.sqlite3-wal").exists()
    assert not (tmp_path / "analysis.sqlite3-shm").exists()


def test_analyzer_and_utf16_range_filters_are_exact_and_cursor_bound(tmp_path):
    store = AnalysisStore(tmp_path / "analysis.sqlite3")
    diagnostics = [
        finding(0, "possible_adverbs"),
        finding(1, "possible_verbs"),
        finding(2, "possible_adverbs"),
        finding(3, "possible_verbs"),
    ]
    diagnostics[2]["start_utf16"] = 8
    diagnostics[2]["end_utf16"] = 10
    created = store.create_snapshot(
        diagnostics,
        document_id="emoji-doc",
        document_revision=3,
        text_hash="emoji-hash",
        initial_page_size=1,
    )

    page = store.query_findings(
        created["analysis_id"],
        analyzer="possible_adverbs",
        start_utf16=7,
        end_utf16=9,
        limit=1,
    )
    assert page["total_findings"] == 1
    assert page["counts_by_analyzer"] == {"possible_adverbs": 1}
    assert [(item["start_utf16"], item["end_utf16"]) for item in page["diagnostics"]] == [(8, 10)]

    with pytest.raises(ValueError, match="cursor"):
        store.query_findings(
            created["analysis_id"], analyzer="possible_verbs", cursor=created["next_cursor"]
        )


def test_multiple_analyzers_are_deduplicated_and_page_in_document_order(tmp_path):
    store = AnalysisStore(tmp_path / "analysis.sqlite3")
    created = store.create_snapshot(
        [
            finding(0, "possible_adverbs"),
            finding(1, "cliches"),
            finding(2, "possible_verbs"),
            finding(3, "filter_words"),
            finding(4, "possible_adverbs"),
        ],
        document_id="doc",
        document_revision=4,
        text_hash="hash",
        initial_page_size=5,
    )
    first = store.query_findings(
        created["analysis_id"],
        analyzers=["possible_verbs", "possible_adverbs", "possible_verbs"],
        limit=2,
    )
    assert [item["id"] for item in first["diagnostics"]] == ["finding-0", "finding-2"]
    assert first["total_findings"] == 3
    assert first["counts_by_analyzer"] == {
        "possible_adverbs": 2,
        "possible_verbs": 1,
    }
    second = store.query_findings(
        created["analysis_id"],
        analyzers=["possible_adverbs", "possible_verbs"],
        cursor=first["next_cursor"],
        limit=2,
    )
    assert [item["id"] for item in second["diagnostics"]] == ["finding-4"]
    assert second["has_more"] is False

    with pytest.raises(ValueError, match="cursor"):
        store.query_findings(
            created["analysis_id"],
            analyzers=["possible_adverbs"],
            cursor=first["next_cursor"],
        )
    with pytest.raises(ValueError, match="at most 32"):
        store.query_findings(
            created["analysis_id"], analyzers=[f"analyzer_{index}" for index in range(33)]
        )


def test_disposal_expiry_and_invalid_tokens(tmp_path):
    store = AnalysisStore(tmp_path / "analysis.sqlite3", ttl_seconds=1)
    created = store.create_snapshot(
        [finding(0)],
        document_id="doc",
        document_revision=1,
        text_hash="hash",
    )
    analysis_id = created["analysis_id"]
    assert store.dispose_analysis(analysis_id) is True
    assert store.dispose_analysis(analysis_id) is False
    with pytest.raises(ValueError, match="invalid or expired"):
        store.query_findings(analysis_id)
    with pytest.raises(ValueError, match="analysis_id is invalid"):
        store.query_findings("../not-an-analysis")
    expiring = store.create_snapshot(
        [finding(1)],
        document_id="doc",
        document_revision=2,
        text_hash="hash-2",
    )
    with pytest.raises(ValueError, match="cursor"):
        store.query_findings(expiring["analysis_id"], cursor="not-a-valid-cursor")
    assert store.cleanup_expired(now=10**20) == 1
    with pytest.raises(ValueError, match="invalid or expired"):
        store.query_findings(expiring["analysis_id"])


def test_compact_overlay_pages_are_complete_and_normalized(tmp_path):
    store = AnalysisStore(tmp_path / "analysis.sqlite3")
    created = store.create_snapshot(
        [
            finding(index, "possible_adverbs" if index % 2 else "filter_words")
            for index in range(5003)
        ],
        document_id="doc",
        document_revision=8,
        text_hash="hash",
        initial_page_size=0,
    )
    first = store.query_overlay_spans(created["analysis_id"], limit=4096)
    second = store.query_overlay_spans(
        created["analysis_id"], cursor=first["next_cursor"], limit=4096
    )
    assert [len(first["spans"]), len(second["spans"])] == [4096, 907]
    assert first["total_spans"] == second["total_spans"] == 5003
    assert first["counts_by_category"] == {
        "filter_words": 2502,
        "possible_adverbs": 2501,
    }
    assert all(len(span) == 3 for span in first["spans"])
    assert all(
        set(rule) == {"category", "rule_id", "level", "confidence", "source"}
        for rule in first["rules"]
    )
    with pytest.raises(ValueError, match="cursor does not match"):
        store.query_overlay_spans(
            created["analysis_id"],
            categories=["filter_words"],
            cursor=first["next_cursor"],
        )
    store.close()


def test_unfiltered_counts_use_snapshot_metadata(tmp_path):
    store = AnalysisStore(tmp_path / "analysis.sqlite3")
    created = store.create_snapshot(
        [finding(index) for index in range(20)],
        document_id="doc",
        document_revision=1,
        text_hash="hash",
        initial_page_size=0,
        persist=True,
    )
    statements = []
    store._connect().set_trace_callback(statements.append)
    page = store.query_findings(created["analysis_id"], limit=5)
    assert page["total_findings"] == 20
    assert not any("GROUP BY" in statement.upper() for statement in statements)
    store.close()


def test_explicit_persistence_is_required_for_sqlite(tmp_path):
    database = tmp_path / "private" / "analysis.sqlite3"
    store = AnalysisStore(database)
    transient = store.create_snapshot(
        [finding(0)],
        document_id="transient",
        document_revision=1,
        text_hash="transient-hash",
    )
    assert transient["persisted"] is False
    assert not database.exists()

    persisted = store.create_snapshot(
        [finding(1)],
        document_id="saved",
        document_revision=2,
        text_hash="saved-hash",
        persist=True,
    )
    assert persisted["persisted"] is True
    assert database.exists()
    if os.name != "nt":
        assert database.stat().st_mode & 0o777 == 0o600
        assert database.parent.stat().st_mode & 0o777 == 0o700
    store.close()


def test_memory_bounds_reject_single_oversized_snapshot_without_disk(tmp_path):
    database = tmp_path / "analysis.sqlite3"
    store = AnalysisStore(database, max_memory_findings=2)
    with pytest.raises(ValueError, match="in-memory finding limit"):
        store.create_snapshot(
            [finding(0), finding(1), finding(2)],
            document_id="doc",
            document_revision=1,
            text_hash="hash",
        )
    assert not database.exists()


def test_document_disposal_of_memory_snapshot_does_not_create_database(tmp_path):
    database = tmp_path / "analysis.sqlite3"
    store = AnalysisStore(database)
    store.create_snapshot(
        [finding(0)],
        document_id="doc",
        document_revision=1,
        text_hash="hash",
    )
    assert store.dispose_document("doc") == 1
    assert not database.exists()
