from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
BUILTIN_PROFILES_DIR = ROOT_DIR / "profiles"

ENGINE_VERSION = "0.4.0"


def _default_data_dir() -> Path:
    override = os.getenv("THOTHPAD_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or Path.home())
        return base / "ThothPad"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ThothPad"
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "thothpad"


DATA_DIR = _default_data_dir()
PROFILES_DIR = DATA_DIR / "profiles"
RUNS_DIR = DATA_DIR / "runs"
PROJECTS_DIR = DATA_DIR / "projects"
SKILLS_DIR = ROOT_DIR / "skills"
VENDOR_DIR = ROOT_DIR / "vendor"
FRONTEND_DIST_DIR = ROOT_DIR / "frontend" / "dist"

DEFAULT_HOST = os.getenv("THOTHPAD_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("THOTHPAD_PORT", "8789"))
DEFAULT_PROFILE = os.getenv("THOTHPAD_PROFILE", "creative-default")

MAX_TEXT_CHARS = int(os.getenv("THOTHPAD_MAX_TEXT_CHARS", "8000000"))
MAX_TEXT_UTF16_UNITS = int(os.getenv("THOTHPAD_MAX_TEXT_UTF16_UNITS", "8000000"))
MAX_LIVE_TEXT_CHARS = int(os.getenv("THOTHPAD_MAX_LIVE_TEXT_CHARS", "8000"))
MAX_DOCUMENTS = int(os.getenv("THOTHPAD_MAX_DOCUMENTS", "500"))
MAX_EXCLUSION_RANGES = int(os.getenv("THOTHPAD_MAX_EXCLUSION_RANGES", "500000"))
MAX_MANUSCRIPT_CHARS = int(os.getenv("THOTHPAD_MAX_MANUSCRIPT_CHARS", "10000000"))
MAX_PASSES = int(os.getenv("THOTHPAD_MAX_PASSES", "5"))
MAX_PROFILE_BYTES = int(os.getenv("THOTHPAD_MAX_PROFILE_BYTES", "262144"))
MAX_FRAME_BYTES = int(os.getenv("THOTHPAD_MAX_FRAME_BYTES", "67108864"))
MAX_RESPONSE_BYTES = int(os.getenv("THOTHPAD_MAX_RESPONSE_BYTES", "33554432"))
MAX_LLM_RESPONSE_BYTES = int(os.getenv("THOTHPAD_MAX_LLM_RESPONSE_BYTES", "16777216"))
ANALYSIS_CACHE_DIR = DATA_DIR / "cache"
ANALYSIS_CACHE_DB = ANALYSIS_CACHE_DIR / "analysis-snapshots.sqlite3"
ANALYSIS_SNAPSHOT_TTL_SECONDS = max(
    60, int(os.getenv("THOTHPAD_ANALYSIS_SNAPSHOT_TTL_SECONDS", "3600"))
)
MAX_FINDING_PAGE_SIZE = 500
DEFAULT_FINDING_PAGE_SIZE = 500
MAX_OVERLAY_PAGE_SIZE = 4096
DEFAULT_OVERLAY_PAGE_SIZE = 4096

DEFAULT_PROVIDER_CONFIG = {
    "provider": os.getenv("THOTHPAD_PROVIDER", "openai_compatible"),
    "base_url": os.getenv("OPENAI_BASE_URL", os.getenv("THOTHPAD_BASE_URL", "http://127.0.0.1:1234/v1")),
    "api_key": os.getenv("OPENAI_API_KEY", os.getenv("THOTHPAD_API_KEY", "")),
    "model": os.getenv("THOTHPAD_MODEL", "local-model"),
    "temperature": float(os.getenv("THOTHPAD_TEMPERATURE", "0.7")),
}


def ensure_dirs() -> None:
    for path in (PROFILES_DIR, RUNS_DIR, PROJECTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def slop_score_data_dir() -> Path:
    return VENDOR_DIR / "slop-score" / "slop-score-main" / "data"
