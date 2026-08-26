from __future__ import annotations

import re
import statistics
from array import array
from bisect import bisect_left
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?", re.S)
_T = TypeVar("_T")


class AnalysisCancelled(RuntimeError):
    pass


_CANCELLATION_CHECK: ContextVar[Callable[[], bool] | None] = ContextVar(
    "thothpad_cancellation_check", default=None
)


def cancellation_checkpoint() -> None:
    check = _CANCELLATION_CHECK.get()
    if check is not None and check():
        raise AnalysisCancelled("request cancelled")


@contextmanager
def cancellable_analysis(check: Callable[[], bool]) -> Iterator[None]:
    token = _CANCELLATION_CHECK.set(check)
    try:
        cancellation_checkpoint()
        yield
        cancellation_checkpoint()
    finally:
        _CANCELLATION_CHECK.reset(token)


class Utf16Index:
    def __init__(self, text: str):
        offsets = array("I", [0])
        total = 0
        for char in text:
            total += 2 if ord(char) > 0xFFFF else 1
            offsets.append(total)
        self._offsets = offsets

    def __getitem__(self, codepoint_offset: int) -> int:
        return self._offsets[codepoint_offset]

    def codepoint_offset(self, utf16_offset: int) -> int:
        if utf16_offset < 0 or utf16_offset > self._offsets[-1]:
            raise ValueError("UTF-16 offset is outside the text")
        index = bisect_left(self._offsets, utf16_offset)
        if self._offsets[index] != utf16_offset:
            raise ValueError("UTF-16 offset splits a surrogate pair")
        return index


@dataclass
class DocumentFeatures:
    """Request-scoped derived data; never retains superseded document text."""

    text: str
    exclusion_ranges: tuple[tuple[int, int], ...] = ()
    _values: dict[str, Any] = field(default_factory=dict)

    def cached(self, name: str, factory: Callable[[], _T]) -> _T:
        cancellation_checkpoint()
        if name not in self._values:
            self._values[name] = factory()
        cancellation_checkpoint()
        return self._values[name]

    @property
    def utf16_index(self) -> Utf16Index:
        return self.cached("utf16_index", lambda: Utf16Index(self.text))


_ACTIVE_FEATURES: ContextVar[DocumentFeatures | None] = ContextVar(
    "thothpad_document_features", default=None
)


def active_document_features(text: str | None = None) -> DocumentFeatures | None:
    features = _ACTIVE_FEATURES.get()
    if features is None or (text is not None and features.text is not text):
        return None
    return features


@contextmanager
def document_features(
    text: str,
    exclusion_ranges: Iterable[tuple[int, int]] = (),
) -> Iterator[DocumentFeatures]:
    current = active_document_features(text)
    if current is not None:
        yield current
        return
    features = DocumentFeatures(text, tuple(exclusion_ranges))
    token = _ACTIVE_FEATURES.set(features)
    try:
        yield features
    finally:
        _ACTIVE_FEATURES.reset(token)


def normalize_quotes(text: str) -> str:
    features = active_document_features(text)
    def build() -> str:
        return (
            text.replace("\u2018", "'")
            .replace("\u2019", "'")
            .replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2014", "-")
            .replace("\u2013", "-")
        )
    return features.cached("normalized_quotes", build) if features else build()


def words(text: str) -> tuple[str, ...]:
    features = active_document_features(text)
    def build() -> tuple[str, ...]:
        values: list[str] = []
        for index, match in enumerate(WORD_RE.finditer(normalize_quotes(text))):
            if index % 256 == 0:
                cancellation_checkpoint()
            values.append(match.group(0).lower().strip("'"))
        return tuple(values)
    return features.cached("words", build) if features else build()


def sentences(text: str) -> tuple[tuple[int, int, str], ...]:
    features = active_document_features(text)
    def build() -> tuple[tuple[int, int, str], ...]:
        out: list[tuple[int, int, str]] = []
        for index, match in enumerate(SENTENCE_RE.finditer(text)):
            if index % 64 == 0:
                cancellation_checkpoint()
            chunk = match.group(0).strip()
            if chunk:
                start = match.start() + (len(match.group(0)) - len(match.group(0).lstrip()))
                out.append((start, match.end(), chunk))
        return tuple(out)
    return features.cached("sentences", build) if features else build()


def paragraphs(text: str) -> tuple[tuple[int, int, str], ...]:
    features = active_document_features(text)
    def build() -> tuple[tuple[int, int, str], ...]:
        out: list[tuple[int, int, str]] = []
        pos = 0
        for index, part in enumerate(re.split(r"(\n\s*\n)", text)):
            if index % 64 == 0:
                cancellation_checkpoint()
            if not part:
                continue
            start = pos
            pos += len(part)
            if part.strip() and not re.fullmatch(r"\n\s*\n", part):
                stripped = part.strip()
                left_trim = len(part) - len(part.lstrip())
                out.append((start + left_trim, start + left_trim + len(stripped), stripped))
        if not out and text.strip():
            stripped = text.strip()
            left_trim = len(text) - len(text.lstrip())
            out.append((left_trim, left_trim + len(stripped), stripped))
        return tuple(out)
    return features.cached("paragraphs", build) if features else build()


def sentence_word_lengths(text: str) -> list[int]:
    return [len(words(sentence)) for _, _, sentence in sentences(text) if words(sentence)]


def safe_stdev(values: Iterable[int]) -> float:
    vals = list(values)
    if len(vals) < 2:
        return 0.0
    return statistics.pstdev(vals)


def excerpt(text: str, start: int, end: int, max_len: int = 220) -> str:
    value = re.sub(r"\s+", " ", text[start:end].strip())
    if len(value) <= max_len:
        return value
    return value[: max_len - 1].rstrip() + "..."
