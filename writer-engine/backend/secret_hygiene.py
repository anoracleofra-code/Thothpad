"""Secret-environment hygiene for the engine worker process.

Extracted from sidecar.py (phase 5): the child engine process must not
inherit provider credentials from the desktop app.
"""

from __future__ import annotations

import os

from backend import config

SECRET_ENV_VARS = (
    "OPENAI_API_KEY", "THOTHPAD_API_KEY", "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY", "AZURE_OPENAI_API_KEY",
)


def clear_secret_environment() -> None:
    for name in SECRET_ENV_VARS:
        os.environ.pop(name, None)
    config.DEFAULT_PROVIDER_CONFIG["api_key"] = ""
