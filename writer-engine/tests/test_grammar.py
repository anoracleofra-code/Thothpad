import json
import urllib.request

import pytest

from backend.grammar import analyze_grammar, harper_path
from backend.grammar_policy import approved_grammar


def harper_settings(**overrides):
    values = {
        "provider": "harper",
        "dialect": "en-US",
        "include_spelling": False,
        "max_findings": 50,
        "timeout": 10,
    }
    values.update(overrides)
    return values


@pytest.mark.requires_harper
@pytest.mark.skipif(not harper_path().is_file(), reason="Harper bridge has not been built")
def test_real_harper_bridge_returns_replacement_and_codepoint_offsets():
    result = analyze_grammar("😀 This is an test.", harper_settings())
    finding = next(flag for flag in result.flags if flag.excerpt == "an")
    assert (finding.start, finding.end) == (10, 12)
    assert finding.replacements == ["a"]
    assert finding.source == "harper-local"
    assert finding.severity == "context_flag"


@pytest.mark.requires_harper
@pytest.mark.skipif(not harper_path().is_file(), reason="Harper bridge has not been built")
def test_segmented_harper_report_preserves_global_offsets():
    text = ("The sentence is correct. " * 5_000) + "This is an test."
    result = analyze_grammar(text, harper_settings(timeout=30))
    finding = next(flag for flag in result.flags if flag.excerpt == "an")
    assert finding.start == text.rfind("an test")
    assert result.metrics["segments"] == 4


@pytest.mark.requires_harper
@pytest.mark.skipif(not harper_path().is_file(), reason="Harper bridge has not been built")
def test_harper_failure_is_reported_without_raising(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("bad request")

    monkeypatch.setattr("backend.grammar._HARPER_SESSION.request", fail)
    result = analyze_grammar("Text", harper_settings())
    assert result.flags == []
    assert result.metrics["error"] == "bad request"


@pytest.mark.requires_harper
@pytest.mark.skipif(not harper_path().is_file(), reason="Harper bridge has not been built")
def test_all_harper_segments_share_the_single_persistent_session(monkeypatch):
    from backend import grammar

    calls = []

    class FakeSession:
        def request(self, payload, timeout):
            calls.append(json.loads(payload))
            return {"findings": [], "version": grammar.HARPER_VERSION}


def test_release_harper_stops_only_when_running(monkeypatch):
    from backend import grammar

    class FakeSession:
        def __init__(self, running):
            self._running = running

        def is_running(self):
            return self._running

        def stop(self):
            self._running = False

    live = FakeSession(True)
    monkeypatch.setattr(grammar, "_HARPER_SESSION", live)
    assert grammar.release_harper() is True
    assert live.is_running() is False
    monkeypatch.setattr(grammar, "_HARPER_SESSION", FakeSession(False))
    assert grammar.release_harper() is False


def test_languagetool_utf16_offsets_and_replacements(monkeypatch):
    payload = {
        "matches": [{
            "offset": 3,
            "length": 2,
            "message": "Use the correct article.",
            "rule": {"id": "EN_A_VS_AN"},
            "replacements": [{"value": "a"}],
        }]
    }
    monkeypatch.setattr("backend.grammar._read_json", lambda request, timeout: payload)
    settings = approved_grammar(
        {
            "provider": "languagetool",
            "url": "http://127.0.0.1:8081/v2/check",
            "language": "en-US",
        },
        live=False,
        consent=True,
    )
    result = analyze_grammar("😀 an test", settings)
    assert (result.flags[0].start, result.flags[0].end) == (2, 4)
    assert result.flags[0].replacements == ["a"]
    assert result.flags[0].source == "languagetool-local"


def test_prowritingaid_async_poll_and_utf16_offsets(monkeypatch):
    responses = iter([
        {"taskId": "task-1"},
        {"result": {"tags": [{
            "startPos": 3,
            "endPos": 5,
            "id": "grammar-1",
            "category": "grammar",
            "hint": "Correct the article.",
            "suggestions": ["a"],
        }]}},
    ])
    requests: list[urllib.request.Request] = []

    def response(request, timeout):
        requests.append(request)
        return next(responses)

    monkeypatch.setattr("backend.grammar._read_json", response)
    monkeypatch.setattr("backend.grammar.time.sleep", lambda value: None)
    settings = approved_grammar(
        {"provider": "prowritingaid", "api_key": "secret"},
        live=False,
        consent=True,
    )
    result = analyze_grammar("😀 an test", settings)
    assert (result.flags[0].start, result.flags[0].end) == (2, 4)
    assert result.flags[0].replacements == ["a"]
    assert result.flags[0].source == "prowritingaid-cloud"
    assert requests[0].headers["Licensecode"] == "secret"
    assert requests[1].get_method() == "GET"


def test_remote_grammar_requires_consent_and_credentials():
    with pytest.raises(ValueError, match="explicit consent"):
        approved_grammar(
            {"provider": "languagetool", "url": "https://api.languagetoolplus.com/v2/check"},
            live=False,
        )
    with pytest.raises(ValueError, match="username and API key"):
        approved_grammar(
            {"provider": "languagetool", "url": "https://api.languagetoolplus.com/v2/check"},
            live=False,
            consent=True,
        )


def test_network_grammar_is_rejected_for_live_analysis():
    with pytest.raises(ValueError, match="unavailable during live"):
        approved_grammar(
            {"provider": "prowritingaid", "api_key": "secret"},
            live=True,
            consent=True,
        )


def test_harper_config_never_retains_unrecognized_values():
    assert approved_grammar(
        {"provider": "harper", "dialect": "en-GB", "api_key": "discard-me"},
        live=True,
    ) == {
        "provider": "harper",
        "dialect": "en-GB",
        "include_spelling": False,
        "max_findings": 100000,
        "timeout": 20,
    }
