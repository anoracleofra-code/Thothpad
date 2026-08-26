"""Direct coverage for the extracted wire-protocol module."""

from __future__ import annotations

import io

import pytest

from backend.protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    ProtocolError,
    encode_frame,
    params_of,
    read_frame,
    request_id,
)


def test_frame_round_trip_preserves_message():
    message = {"operation": "analyze_region", "params": {"text": "héllo → utf16"}}
    stream = io.BytesIO(encode_frame(message))
    assert read_frame(stream) == message


def test_read_frame_returns_none_on_clean_eof():
    assert read_frame(io.BytesIO(b"")) is None


def test_encode_frame_rejects_non_finite_values():
    with pytest.raises(ProtocolError):
        encode_frame({"value": float("nan")})


def test_read_frame_rejects_missing_content_length():
    stream = io.BytesIO(b"X-Foo: bar\r\n\r\n{}")
    with pytest.raises(ProtocolError):
        read_frame(stream)


def test_request_id_enforces_charset_and_length():
    assert request_id("abc_123") == "abc_123"
    with pytest.raises(ProtocolError):
        request_id("bad id!")
    with pytest.raises(ProtocolError):
        request_id("x" * 200)
    with pytest.raises(ProtocolError):
        request_id(42)


def test_params_of_unwraps_params_object():
    assert params_of({"operation": "x", "params": {"a": 1}}) == {"a": 1}
    bare = {"operation": "x"}
    assert params_of(bare) is bare
    with pytest.raises(ValueError):
        params_of({"params": [1, 2]})


def test_protocol_version_constants_are_integers():
    assert isinstance(PROTOCOL_MAJOR, int)
    assert isinstance(PROTOCOL_MINOR, int)
