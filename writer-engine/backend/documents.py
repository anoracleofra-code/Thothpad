from __future__ import annotations

import hashlib
import re
import threading
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from backend import config
from backend.text_utils import Utf16Index
from backend.validation import validate_text

_DOCUMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")
_MAX_PATCHES = 10_000
_CHUNK_CODEPOINTS = 4_096


class ResyncRequired(ValueError):
    pass


def _utf16_units(text: str) -> int:
    return len(text) + sum(ord(char) > 0xFFFF for char in text)


@dataclass(frozen=True)
class _TextChunk:
    text: str
    utf16_units: int

    @classmethod
    def create(cls, text: str) -> _TextChunk:
        return cls(text, _utf16_units(text))


class ChunkedText:
    """Revision-local text storage with bounded UTF-16 indexing work per edit."""

    def __init__(self, chunks: Iterable[str | _TextChunk]):
        normalized: list[_TextChunk] = []
        for value in chunks:
            if isinstance(value, _TextChunk):
                if value.text:
                    normalized.append(value)
                continue
            if not value:
                continue
            for start in range(0, len(value), _CHUNK_CODEPOINTS):
                chunk = value[start:start + _CHUNK_CODEPOINTS]
                if (
                    normalized
                    and len(normalized[-1].text) + len(chunk) <= _CHUNK_CODEPOINTS
                ):
                    normalized[-1] = _TextChunk.create(normalized[-1].text + chunk)
                else:
                    normalized.append(_TextChunk.create(chunk))
        self._chunks = tuple(normalized)
        char_prefix = [0]
        utf16_prefix = [0]
        for entry in self._chunks:
            char_prefix.append(char_prefix[-1] + len(entry.text))
            utf16_prefix.append(utf16_prefix[-1] + entry.utf16_units)
        self._char_prefix = tuple(char_prefix)
        self._utf16_prefix = tuple(utf16_prefix)

    @classmethod
    def from_text(cls, text: str) -> ChunkedText:
        return cls((text,))

    @property
    def char_count(self) -> int:
        return self._char_prefix[-1]

    @property
    def utf16_units(self) -> int:
        return self._utf16_prefix[-1]

    def materialize(self) -> str:
        return "".join(chunk.text for chunk in self._chunks)

    def codepoint_offset(self, utf16_offset: int) -> int:
        if utf16_offset < 0 or utf16_offset > self.utf16_units:
            raise ValueError("UTF-16 offset is outside the text")
        if utf16_offset == self.utf16_units:
            return self.char_count
        chunk_index = bisect_right(self._utf16_prefix, utf16_offset) - 1
        local_utf16 = utf16_offset - self._utf16_prefix[chunk_index]
        local_codepoint = Utf16Index(
            self._chunks[chunk_index].text
        ).codepoint_offset(local_utf16)
        return self._char_prefix[chunk_index] + local_codepoint

    def slice_utf16(self, start_utf16: int, end_utf16: int) -> str:
        return self.slice_codepoints(
            self.codepoint_offset(start_utf16), self.codepoint_offset(end_utf16)
        )

    def slice_codepoints(self, start: int, end: int) -> str:
        if start < 0 or end < start or end > self.char_count:
            raise ValueError("codepoint range is outside the text")
        return "".join(
            value.text if isinstance(value, _TextChunk) else value
            for value in self._range_chunks(start, end)
        )

    def _range_chunks(self, start: int, end: int) -> list[str | _TextChunk]:
        if start == end:
            return []
        first = bisect_right(self._char_prefix, start) - 1
        last = bisect_right(self._char_prefix, end - 1) - 1
        if first == last:
            return [self._chunks[first].text[
                start - self._char_prefix[first]:end - self._char_prefix[first]
            ]]
        values: list[str | _TextChunk] = [
            self._chunks[first].text[start - self._char_prefix[first]:]
        ]
        values.extend(self._chunks[first + 1:last])
        values.append(self._chunks[last].text[:end - self._char_prefix[last]])
        return values

    def replace_many(self, patches: list[tuple[int, int, str]]) -> ChunkedText:
        values: list[str | _TextChunk] = []
        cursor = 0
        for start, end, replacement in patches:
            values.extend(self._range_chunks(cursor, start))
            if replacement:
                values.append(replacement)
            cursor = end
        values.extend(self._range_chunks(cursor, self.char_count))
        return ChunkedText(values)


@dataclass
class OpenDocument:
    document_id: str
    revision: int
    buffer: ChunkedText
    text_hash: str | None
    language: str
    exclusion_ranges: tuple[dict[str, Any], ...] = ()
    exclusions_stale: bool = False

    @property
    def text(self) -> str:
        return self.buffer.materialize()

    @property
    def char_count(self) -> int:
        return self.buffer.char_count

    @property
    def utf16_units(self) -> int:
        return self.buffer.utf16_units

    def exact_hash(self) -> str:
        if self.text_hash is None:
            self.text_hash = _text_hash(self.text)
        return self.text_hash


def _document_id(value: Any) -> str:
    if not isinstance(value, str) or not _DOCUMENT_ID.fullmatch(value):
        raise ValueError("document_id is invalid")
    return value


def _revision(value: Any, name: str = "document_revision") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validated_exclusions(values: Any, text_units: int) -> tuple[dict[str, Any], ...]:
    if values is None:
        return ()
    if not isinstance(values, list) or len(values) > config.MAX_EXCLUSION_RANGES:
        raise ValueError(
            f"exclusion_ranges must be an array with at most {config.MAX_EXCLUSION_RANGES} entries"
        )
    ranges: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("each exclusion range must be an object")
        start = value.get("start_utf16")
        end = value.get("end_utf16")
        if (
            isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, int) or not isinstance(end, int)
            or start < 0 or end <= start or end > text_units
        ):
            raise ValueError("exclusion range is outside the document")
        item: dict[str, Any] = {"start_utf16": start, "end_utf16": end}
        if value.get("kind") is not None:
            kind = value["kind"]
            if not isinstance(kind, str) or len(kind) > 64:
                raise ValueError("exclusion range kind must be a string of at most 64 characters")
            item["kind"] = kind
        ranges.append(item)
    merged: list[dict[str, Any]] = []
    for item in sorted(ranges, key=lambda value: (value["start_utf16"], value["end_utf16"])):
        if merged and item["start_utf16"] <= merged[-1]["end_utf16"]:
            merged[-1]["end_utf16"] = max(merged[-1]["end_utf16"], item["end_utf16"])
            if merged[-1].get("kind") != item.get("kind"):
                merged[-1].pop("kind", None)
        else:
            merged.append(item)
    return tuple(merged)


def _adjust_exclusions(
    ranges: tuple[dict[str, Any], ...],
    patches: list[tuple[int, int, str, int, int]],
) -> tuple[tuple[dict[str, Any], ...], bool]:
    """Map exclusion ranges onto patched coordinates; only boundary-crossing edits go stale."""
    adjusted: list[dict[str, Any]] = []
    stale = False
    prefix_delta = [0]
    for _, _, replacement, patch_start, patch_end in patches:
        prefix_delta.append(prefix_delta[-1] + _utf16_units(replacement) - (patch_end - patch_start))
    consumed = 0
    for value in ranges:
        start = int(value["start_utf16"])
        end = int(value["end_utf16"])
        while consumed < len(patches) and patches[consumed][4] <= start:
            consumed += 1
        shift = prefix_delta[consumed]
        growth = 0
        for index in range(consumed, len(patches)):
            _, _, replacement, patch_start, patch_end = patches[index]
            if patch_start >= end:
                continue
            if patch_start > start and patch_end < end:
                growth += _utf16_units(replacement) - (patch_end - patch_start)
            else:
                stale = True
        new_start = start + shift
        new_end = end + shift + growth
        if new_end > new_start:
            item = dict(value)
            item["start_utf16"] = new_start
            item["end_utf16"] = new_end
            adjusted.append(item)
    return tuple(adjusted), stale


class DocumentRegistry:
    def __init__(self) -> None:
        self._documents: dict[str, OpenDocument] = {}
        self._lock = threading.RLock()

    def open_document(
        self,
        document_id: Any,
        revision: Any,
        text: Any,
        *,
        language: Any = "en",
        expected_hash: Any = None,
        exclusion_ranges: Any = None,
    ) -> dict[str, Any]:
        identifier = _document_id(document_id)
        version = _revision(revision)
        value = validate_text(text)
        language_code = str(language or "und")[:32]
        digest = _text_hash(value)
        if expected_hash is not None and expected_hash != digest:
            raise ValueError("document hash does not match text")
        buffer = ChunkedText.from_text(value)
        exclusions = _validated_exclusions(exclusion_ranges, buffer.utf16_units)
        document = OpenDocument(
            identifier, version, buffer, digest, language_code, exclusions, False
        )
        with self._lock:
            current = self._documents.get(identifier)
            if current is not None and version < current.revision:
                raise ResyncRequired(
                    f"document revision {version} is older than current revision {current.revision}"
                )
            if current is None and len(self._documents) >= config.MAX_DOCUMENTS:
                raise ValueError(
                    f"open documents exceed the {config.MAX_DOCUMENTS}-document limit"
                )
            existing_chars = sum(
                item.char_count for key, item in self._documents.items()
                if key != identifier
            )
            if existing_chars + buffer.char_count > config.MAX_MANUSCRIPT_CHARS:
                raise ValueError(
                    f"open document text exceeds the {config.MAX_MANUSCRIPT_CHARS}-character limit"
                )
            self._documents[identifier] = document
        return self._metadata(document)

    def patch_document(
        self,
        document_id: Any,
        base_revision: Any,
        revision: Any,
        changes: Any,
        *,
        expected_hash: Any = None,
        exclusion_ranges: Any = None,
    ) -> dict[str, Any]:
        identifier = _document_id(document_id)
        base = _revision(base_revision, "base_revision")
        version = _revision(revision)
        if version <= base:
            raise ValueError("revision must be greater than base_revision")
        if not isinstance(changes, list) or len(changes) > _MAX_PATCHES:
            raise ValueError(f"changes must be an array with at most {_MAX_PATCHES} entries")
        with self._lock:
            current = self._documents.get(identifier)
            if current is None or current.revision != base:
                actual = "closed" if current is None else str(current.revision)
                raise ResyncRequired(
                    f"base_revision {base} does not match current revision {actual}"
                )
            patches: list[tuple[int, int, str, int, int]] = []
            for change in changes:
                if not isinstance(change, dict):
                    raise ValueError("each change must be an object")
                start = change.get("start_utf16")
                end = change.get("end_utf16")
                replacement = change.get("replacement")
                if (
                    isinstance(start, bool) or isinstance(end, bool)
                    or not isinstance(start, int) or not isinstance(end, int)
                    or start < 0 or end < start
                ):
                    raise ValueError("patch offsets must satisfy 0 <= start_utf16 <= end_utf16")
                if not isinstance(replacement, str):
                    raise ValueError("patch replacement must be a string")
                try:
                    patches.append((
                        current.buffer.codepoint_offset(start),
                        current.buffer.codepoint_offset(end),
                        replacement,
                        start,
                        end,
                    ))
                except ValueError as exc:
                    raise ResyncRequired(str(exc)) from exc
            patches.sort(key=lambda item: (item[0], item[1]))
            for previous, following in zip(patches, patches[1:], strict=False):
                if previous[1] > following[0]:
                    raise ValueError("patch ranges must not overlap")
            buffer = current.buffer.replace_many([
                (start, end, replacement) for start, end, replacement, _, _ in patches
            ])
            if buffer.char_count > config.MAX_TEXT_CHARS:
                raise ValueError(f"text exceeds the {config.MAX_TEXT_CHARS}-character limit")
            if buffer.utf16_units > config.MAX_TEXT_UTF16_UNITS:
                raise ValueError(
                    f"text exceeds the {config.MAX_TEXT_UTF16_UNITS}-UTF-16-unit limit"
                )
            other_chars = sum(
                item.char_count for key, item in self._documents.items()
                if key != identifier
            )
            if other_chars + buffer.char_count > config.MAX_MANUSCRIPT_CHARS:
                raise ValueError(
                    f"open document text exceeds the {config.MAX_MANUSCRIPT_CHARS}-character limit"
                )
            digest = None
            if expected_hash is not None:
                digest = _text_hash(buffer.materialize())
                if expected_hash != digest:
                    raise ResyncRequired("patched document hash does not match")
            if exclusion_ranges is not None:
                exclusions = _validated_exclusions(exclusion_ranges, buffer.utf16_units)
                exclusions_stale = False
            else:
                exclusions, newly_stale = _adjust_exclusions(
                    current.exclusion_ranges, patches
                )
                exclusions_stale = current.exclusions_stale or newly_stale
            document = OpenDocument(
                identifier,
                version,
                buffer,
                digest,
                current.language,
                exclusions,
                exclusions_stale,
            )
            self._documents[identifier] = document
            return self._metadata(document)

    def get_document(self, document_id: Any, revision: Any = None) -> OpenDocument:
        identifier = _document_id(document_id)
        with self._lock:
            document = self._documents.get(identifier)
            if document is None:
                raise ResyncRequired("document is not open")
            if revision is not None and _revision(revision) != document.revision:
                raise ResyncRequired(
                    f"requested revision {revision} does not match current revision {document.revision}"
                )
            return document

    def dispose_document(self, document_id: Any) -> bool:
        identifier = _document_id(document_id)
        with self._lock:
            return self._documents.pop(identifier, None) is not None

    @staticmethod
    def _metadata(document: OpenDocument) -> dict[str, Any]:
        return {
            "document_id": document.document_id,
            "document_revision": document.revision,
            "text_hash": document.text_hash or "",
            "hash_deferred": document.text_hash is None,
            "language": document.language,
            "text_chars": document.char_count,
            "text_utf16_units": document.utf16_units,
            "exclusion_count": len(document.exclusion_ranges),
            "exclusions_stale": document.exclusions_stale,
        }
