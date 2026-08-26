"""Direct coverage for the extracted secret-hygiene module."""

from __future__ import annotations

from backend import config
from backend.secret_hygiene import SECRET_ENV_VARS, clear_secret_environment


def test_clear_secret_environment_strips_provider_keys(monkeypatch):
    for name in SECRET_ENV_VARS:
        monkeypatch.setenv(name, "leak")
    config.DEFAULT_PROVIDER_CONFIG["api_key"] = "leak"
    clear_secret_environment()
    for name in SECRET_ENV_VARS:
        import os

        assert name not in os.environ
    assert config.DEFAULT_PROVIDER_CONFIG["api_key"] == ""


def test_clear_secret_environment_is_idempotent():
    clear_secret_environment()
    clear_secret_environment()
