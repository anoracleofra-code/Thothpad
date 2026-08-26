from __future__ import annotations

import uuid

import pytest

from backend import config
from backend.documents import _adjust_exclusions
from backend.sidecar import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    dispatch,
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


def _range(start: int, end: int, kind: str | None = None) -> dict[str, object]:
    item: dict[str, object] = {"start_utf16": start, "end_utf16": end}
    if kind is not None:
        item["kind"] = kind
    return item


def test_insertion_inside_fenced_code_span_extends_without_staleness():
    adjusted, stale = _adjust_exclusions(
        (_range(10, 20, "fenced_code"),),
        [(14, 14, "xy", 14, 14)],
    )
    assert stale is False
    assert adjusted == (_range(10, 22, "fenced_code"),)


def test_insertion_inside_inline_code_span_extends_without_staleness():
    adjusted, stale = _adjust_exclusions(
        (_range(2, 8, "inline_code"),),
        [(5, 5, "\U0001f600", 5, 6)],
    )
    assert stale is False
    assert adjusted == (_range(2, 9, "inline_code"),)


def test_deletion_fully_inside_span_shrinks_without_staleness():
    adjusted, stale = _adjust_exclusions(
        (_range(10, 20),),
        [(12, 17, "", 12, 17)],
    )
    assert stale is False
    assert adjusted == (_range(10, 15),)


def test_replacement_fully_inside_span_resizes_without_staleness():
    adjusted, stale = _adjust_exclusions(
        (_range(10, 20),),
        [(12, 17, "abc", 12, 17)],
    )
    assert stale is False
    assert adjusted == (_range(10, 18),)


@pytest.mark.parametrize(
    "patch",
    [
        (16, 26, "", 16, 26),
        (6, 14, "", 6, 14),
        (10, 20, "", 10, 20),
    ],
)
def test_edit_crossing_span_boundary_marks_stale(patch: tuple[int, int, str, int, int]):
    adjusted, stale = _adjust_exclusions((_range(10, 20),), [patch])
    assert stale is True
    assert adjusted == (_range(10, 20),)


def test_patch_spanning_two_ranges_marks_stale_and_keeps_coordinates():
    adjusted, stale = _adjust_exclusions(
        (_range(10, 20), _range(30, 40)),
        [(15, 35, "", 15, 35)],
    )
    assert stale is True
    assert adjusted == (_range(10, 20), _range(30, 40))


def test_zero_width_insertion_at_start_shifts_span_and_at_end_is_noop():
    shifted, stale = _adjust_exclusions((_range(10, 20),), [(10, 10, "Q", 10, 10)])
    assert stale is False
    assert shifted == (_range(11, 21),)
    untouched, stale = _adjust_exclusions((_range(10, 20),), [(20, 20, "Q", 20, 20)])
    assert stale is False
    assert untouched == (_range(10, 20),)


def test_multi_patch_frame_applies_outside_shift_and_inside_growth_together():
    ranges = (_range(5, 9), _range(20, 30), _range(40, 44))
    patches = [
        (0, 2, "ABC", 0, 2),
        (7, 7, "q", 7, 7),
        (24, 24, "zz", 24, 24),
    ]
    adjusted, stale = _adjust_exclusions(ranges, patches)
    assert stale is False
    assert adjusted == (_range(6, 11), _range(22, 34), _range(44, 48))


def test_multi_patch_frame_reports_stale_once_for_any_ambiguous_edit():
    ranges = (_range(5, 9), _range(40, 44))
    patches = [
        (7, 7, "q", 7, 7),
        (42, 50, "", 42, 50),
    ]
    adjusted, stale = _adjust_exclusions(ranges, patches)
    assert stale is True
    assert adjusted == (_range(5, 10), _range(41, 45))


def test_interior_edits_never_collapse_a_range_below_one_unit():
    adjusted, stale = _adjust_exclusions((_range(10, 13),), [(11, 12, "", 11, 12)])
    assert stale is False
    assert adjusted == (_range(10, 12),)


def test_inside_code_insertion_keeps_exclusions_fresh_and_analyzable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ANALYSIS_CACHE_DB", tmp_path / "analysis.sqlite3")
    document_id = f"doc-{uuid.uuid4().hex}"
    opened = dispatch(request(
        "open_document",
        document_id=document_id,
        text="xx`code`yy",
        exclusion_ranges=[{"start_utf16": 2, "end_utf16": 8, "kind": "inline_code"}],
    ))
    assert opened["exclusions_stale"] is False

    patched = dispatch(request(
        "patch_document",
        document_id=document_id,
        revision=2,
        base_revision=1,
        changes=[{"start_utf16": 4, "end_utf16": 4, "replacement": "Z"}],
    ))
    assert patched["exclusions_stale"] is False

    captured: dict[str, object] = {}

    def fake_analyze(value, **kwargs):
        captured.update(text=value, **kwargs)
        return {"diagnostics": [], "analysis": [], "text_hash": "hash"}

    monkeypatch.setattr("backend.sidecar.analyze_text", fake_analyze)
    result = dispatch(request(
        "analyze_document", document_id=document_id, revision=2, initial_page_size=0
    ))
    assert result["total_findings"] == 0
    assert captured["text"] == "xx`cZode`yy"
    assert captured["exclusion_ranges"] == [
        {"start_utf16": 2, "end_utf16": 9, "kind": "inline_code"}
    ]
