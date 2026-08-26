from backend.analyzers.dialogue import MAX_SPAN_CHARS, dialogue_spans
from backend.desktop_engine import analyze_text


def _straight(interior: str) -> str:
    return 'He began, "' + interior + '" and closed it.'


def test_normal_size_span_is_kept():
    text = 'He said, "Hello there," and left.'
    assert dialogue_spans(text) == [(9, 23)]


def test_unclosed_quote_giant_span_is_discarded():
    # An unclosed quote pairs with a quote hundreds of thousands of chars
    # later; the resulting artifact span must not be reported.
    text = 'He shouted, "' + "narration " * 1200
    text += '" much later, then said "a short line" properly.'
    assert dialogue_spans(text) == []


def test_curly_pairing_unaffected():
    text = "\u201cCurly pair,\u201d she said."
    assert dialogue_spans(text) == [(0, 13)]
    nested = "\u201cFirst \u2018inner\u2019 second,\u201d he said."
    spans = dialogue_spans(nested)
    assert len(spans) >= 1


def test_span_exactly_at_limit_is_kept():
    interior = "a" * (MAX_SPAN_CHARS - 2)
    text = _straight(interior)
    spans = dialogue_spans(text)
    assert len(spans) == 1
    assert spans[0][1] - spans[0][0] == MAX_SPAN_CHARS


def test_span_one_over_limit_is_discarded():
    interior = "a" * (MAX_SPAN_CHARS - 1)
    assert dialogue_spans(_straight(interior)) == []


def test_analyze_text_reports_dialogue_balance():
    text = (
        '"Hello there," she said. "Fine," he answered, turning away. '
        "The long winding narration sentence traveled over the quiet hills "
        "and past the mill before settling into the pines for the night."
    )
    result = analyze_text(text, preset="full")
    block = result["dialogue"]
    assert block["span_count"] == 2
    assert 0.05 < block["dialogue_word_ratio"] < 0.25
    assert "said" in block["tag_verb_histogram"]
    assert "answered" in block["tag_verb_histogram"]


def test_analyze_text_dialogue_block_handles_plain_narration():
    result = analyze_text("No dialogue at all in this passage, just narration.", preset="live")
    block = result["dialogue"]
    assert block["span_count"] == 0
    assert block["dialogue_word_ratio"] == 0.0
    assert block["tag_verb_histogram"] == {}
