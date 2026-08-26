from __future__ import annotations

import json

from backend.analyzers.concrete_anchor import ConcreteAnchorAnalyzer
from backend.analyzers.external_tools import ExternalToolsAnalyzer
from backend.analyzers.rhythm import RhythmAnalyzer


def test_rhythm_does_not_match_abstract_tokens_by_substring():
    result = RhythmAnalyzer().analyze("Nothing.")
    assert not any(flag.type == "abstract_mic_drop" for flag in result.flags)

    control = RhythmAnalyzer().analyze("Truth.")
    assert any(flag.type == "abstract_mic_drop" for flag in control.flags)


def test_concrete_anchor_does_not_treat_sentence_initial_capital_as_a_name():
    text = (
        "The concept remained uncertain while every argument drifted through theory, "
        "assumption, possibility, meaning, consequence, intention, uncertainty, reason, "
        "belief, expectation, interpretation, principle, judgment, concern, purpose, "
        "question, answer, doubt, and speculation without settling anywhere specific."
    )
    result = ConcreteAnchorAnalyzer().analyze(text)
    assert any(flag.type == "missing_concrete_anchor" for flag in result.flags)


def test_concrete_anchor_keeps_mid_sentence_proper_name_signal():
    text = (
        "The concept remained uncertain while every argument drifted through theory, "
        "assumption, possibility, meaning, consequence, intention, uncertainty, reason, "
        "belief, expectation, interpretation, principle, judgment, concern, purpose, "
        "question, and doubt until Mara finally interrupted the speculation."
    )
    result = ConcreteAnchorAnalyzer().analyze(text)
    assert not any(flag.type == "missing_concrete_anchor" for flag in result.flags)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self._payload


class _FakeOpener:
    def __init__(self, payload: dict):
        self._payload = payload

    def open(self, _request, timeout: int):
        assert timeout == 20
        return _FakeResponse(self._payload)


def test_languagetool_utf16_offsets_map_back_to_python_codepoints(monkeypatch):
    # "teh" starts after an astral-plane emoji. LanguageTool reports UTF-16
    # code-unit offsets, while Flag expects Python codepoint offsets.
    text = "A 📝 teh word."
    payload = {
        "matches": [
            {
                "offset": 5,
                "length": 3,
                "message": "Possible typo.",
                "rule": {"id": "AUDIT_UTF16"},
            }
        ]
    }
    monkeypatch.setattr(
        "backend.analyzers.external_tools.urllib.request.build_opener",
        lambda *_handlers: _FakeOpener(payload),
    )

    result = ExternalToolsAnalyzer().analyze(
        text,
        {
            "_approved_external_tools": {
                "languagetool": {
                    "url": "http://127.0.0.1:18081/v2/check",
                    "approved_endpoints": ["http://127.0.0.1:18081/v2/check"],
                    "language": "en-US",
                }
            }
        },
    )

    finding = next(flag for flag in result.flags if flag.type == "languagetool:AUDIT_UTF16")
    assert (finding.start, finding.end) == (4, 7)
    assert finding.excerpt == "teh"
