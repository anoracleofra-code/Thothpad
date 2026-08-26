from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from backend import config
from backend.atomic_io import atomic_write_text


def safe_project_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_ ." else "-" for ch in name).strip(" .-") or "Project"


def list_projects() -> list[dict[str, Any]]:
    config.ensure_dirs()
    projects: list[dict[str, Any]] = []
    for path in sorted(config.PROJECTS_DIR.iterdir()):
        if not path.is_dir():
            continue
        meta_path = path / "project.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt project.json must not break the whole listing;
                # fall back to the directory-derived metadata.
                meta = {"name": path.name, "profile": config.DEFAULT_PROFILE}
        else:
            meta = {"name": path.name, "profile": config.DEFAULT_PROFILE}
        meta["path"] = str(path)
        projects.append(meta)
    return projects


def create_project(name: str, profile: str = config.DEFAULT_PROFILE) -> dict[str, Any]:
    config.ensure_dirs()
    safe_name = safe_project_name(name)
    project_dir = config.PROJECTS_DIR / safe_name
    for sub in ("drafts", "voice-samples", "runs", "style-rules"):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)
    meta = {
        "name": safe_name,
        "profile": profile,
        "created_at": datetime.now(UTC).isoformat(),
        "path": str(project_dir),
        "folders": {
            "drafts": str(project_dir / "drafts"),
            "voice_samples": str(project_dir / "voice-samples"),
            "runs": str(project_dir / "runs"),
            "style_rules": str(project_dir / "style-rules"),
        },
    }
    atomic_write_text(project_dir / "project.json", json.dumps(meta, indent=2))
    defaults = {
        "canon.json": {
            "characters": {},
            "locations": {},
            "terms": {},
            "chronology": [],
            "locked_facts": [],
        },
        "voice-fingerprint.json": {
            "profile": profile,
            "sample_files": [],
            "metrics": {},
        },
        "quality-ledger.json": {"runs": []},
    }
    for filename, value in defaults.items():
        path = project_dir / filename
        if not path.exists():
            atomic_write_text(path, json.dumps(value, indent=2))
    return meta


def agent_setup() -> dict[str, Any]:
    mcp_cmd = str(config.ROOT_DIR / "thothpad-mcp.cmd")
    cli_cmd = str(config.ROOT_DIR / "thothpad.cmd")
    cwd = str(config.ROOT_DIR)
    return {
        "mcp_command": mcp_cmd,
        "cli_command": cli_cmd,
        "cwd": cwd,
        "codex_mcp": {
            "mcpServers": {
                "thothpad": {
                    "command": mcp_cmd
                }
            }
        },
        "claude_code_mcp": {
            "thothpad": {
                "command": mcp_cmd
            }
        },
        "zed_cli_examples": [
            f'"{cli_cmd}" diagnose ".\\chapter.md" --profile fiction-gritty',
            f'"{cli_cmd}" rewrite ".\\chapter.md" --profile fiction-gritty --passes 2',
            f'"{cli_cmd}" deslop ".\\chapter.md" --profile creative-default',
            f'"{cli_cmd}" compare ".\\draft_ai.md" ".\\draft_clean.md"',
        ],
        "tools": [
            "prose_diagnose",
            "prose_rewrite",
            "prose_deslop",
            "prose_compare",
            "prose_build_voice_profile",
            "prose_list_profiles",
            "prose_get_run",
            "prose_analyze_manuscript",
            "prose_calibrate_corpus",
        ],
    }
