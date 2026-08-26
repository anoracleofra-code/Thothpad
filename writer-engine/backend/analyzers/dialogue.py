from __future__ import annotations

from bisect import bisect_right

from backend.text_utils import active_document_features

# Spans longer than this are almost always pairing artifacts — an unclosed
# quote swallowing tens of thousands of characters of narration — rather than
# real dialogue, so they are discarded instead of suppressing analysis.
MAX_SPAN_CHARS = 5000


def dialogue_spans(text: str) -> list[tuple[int, int]]:
    features = active_document_features(text)
    if features is not None:
        return features.cached("dialogue_spans", lambda: _dialogue_spans(text))
    return _dialogue_spans(text)


def _dialogue_spans(text: str) -> list[tuple[int, int]]:
    pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
    closing_to_opening = {closing: opening for opening, closing in pairs.items()}
    starts: dict[str, int] = {}
    spans: list[tuple[int, int]] = []
    for index, char in enumerate(text):
        if char not in pairs and char not in closing_to_opening:
            continue
        if index and text[index - 1] == "\\":
            continue
        previous = text[index - 1] if index else ""
        following = text[index + 1] if index + 1 < len(text) else ""
        if char in {"'", "’"} and previous.isalnum() and following.isalnum():
            continue
        opening = closing_to_opening.get(char, char)
        if opening in starts and char == pairs[opening]:
            if previous and not previous.isspace() and not following.isalnum():
                start = starts.pop(opening)
                if index + 1 - start <= MAX_SPAN_CHARS:
                    spans.append((start, index + 1))
        elif char in pairs and not previous.isalnum() and following and not following.isspace():
            starts[char] = index
    return sorted(spans)


def inside_dialogue(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    index = bisect_right(spans, (start, 1 << 62)) - 1
    return index >= 0 and start >= spans[index][0] and end <= spans[index][1]
