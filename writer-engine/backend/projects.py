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
            "prose_quality_timeline",
            "prose_lens_baselines",
            "story_project_understanding",
            "story_resolve_entity",
            "story_find_evidence",
            "story_query_claims",
            "story_get_context",
            "story_get_character_knowledge",
            "story_get_character_beliefs",
            "story_get_reader_state",
            "story_query_timeline",
            "story_get_world_state",
            "story_where_is_entity",
            "story_who_has_object",
            "story_list_threads",
            "story_list_reader_questions",
            "story_list_dramatic_promises",
            "story_trace_causality",
            "story_get_decision_history",
            "story_get_opposition_state",
            "story_get_scene_contract",
            "story_get_author_decisions",
            "story_audit_scene",
            "story_audit_chapter",
            "story_list_branches",
            "story_compare_branch",
            "story_get_retcon_impact",
            "story_cold_reader_at",
            "story_audit_reveal_fairness",
            "story_get_reader_expectations",
            "story_get_dramatic_irony",
            "story_get_writer_model",
            "story_explain_writer_preference",
            "story_run_editorial_council",
            "story_list_lenses",
            "story_get_lens",
            "story_run_lens",
            "story_get_reader_experience",
            "story_get_reader_experience_timeline",
        ],
    }
