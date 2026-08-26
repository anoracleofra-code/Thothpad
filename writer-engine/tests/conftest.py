import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "windows: test exercises Windows-only behavior (job objects, process handles)",
    )
    config.addinivalue_line(
        "markers",
        "requires_harper: test needs the built Harper grammar bridge binary",
    )
    config.addinivalue_line(
        "markers",
        "requires_spacy_model: test needs the en_core_web_sm model from the desktop extra",
    )
