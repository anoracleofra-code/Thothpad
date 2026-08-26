from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend import config
from backend.validation import validate_run_id

DB_PATH = config.RUNS_DIR / "runs.sqlite3"


def _restrict_permissions(path: Path, mode: int) -> None:
    if os.name != "nt":
        path.chmod(mode)


def _write_private_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")
    _restrict_permissions(path, 0o600)


def init_db() -> None:
    config.ensure_dirs()
    _restrict_permissions(config.RUNS_DIR, 0o700)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              mode TEXT NOT NULL,
              profile TEXT NOT NULL,
              provider_model TEXT,
              score_before REAL,
              score_after REAL,
              run_dir TEXT NOT NULL
            )
            """
        )
    _restrict_permissions(DB_PATH, 0o600)


def save_run(
    *,
    mode: str,
    profile_name: str,
    input_text: str,
    output_text: str,
    report: dict[str, Any],
    derivation: dict[str, Any],
    run_config: dict[str, Any],
) -> dict[str, Any]:
    init_db()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = config.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(run_dir, 0o700)

    _write_private_text(run_dir / "input.md", input_text)
    _write_private_text(run_dir / "output.md", output_text)
    _write_private_text(
        run_dir / "report.json", json.dumps(report, indent=2, ensure_ascii=False)
    )
    _write_private_text(run_dir / "report.md", report_to_markdown(report))
    _write_private_text(
        run_dir / "derivation.json", json.dumps(derivation, indent=2, ensure_ascii=False)
    )
    _write_private_text(
        run_dir / "config.json", json.dumps(run_config, indent=2, ensure_ascii=False)
    )

    provider = run_config.get("provider", {})
    provider_model = provider.get("model", "")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                datetime.now(UTC).isoformat(),
                mode,
                profile_name,
                provider_model,
                report.get("score_before"),
                report.get("score_after"),
                str(run_dir),
            ),
        )

    return {"run_id": run_id, "run_dir": str(run_dir)}


def load_run(run_id: str) -> dict[str, Any]:
    validate_run_id(run_id)
    run_dir = config.RUNS_DIR / run_id
    if not run_dir.exists():
        raise FileNotFoundError(run_id)
    report_path = run_dir / "report.json"
    return json.loads(report_path.read_text(encoding="utf-8"))


def report_to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ThothPad Report",
        "",
        f"- Mode: `{report.get('mode')}`",
        f"- Profile: `{report.get('profile')}`",
        f"- Score before: `{report.get('score_before')}`",
        f"- Score after: `{report.get('score_after')}`",
        "",
        "## Flags",
    ]
    if report.get("mode") == "manuscript":
        repetition = report.get("repetition", {})
        lines.extend(
            [
                "",
                "## Manuscript Repetition",
                "",
                f"- Documents: `{report.get('document_count')}`",
                f"- Words: `{report.get('manuscript_stats', {}).get('word_count')}`",
                "",
                "### Repeated words",
            ]
        )
        chapter_names = repetition.get("chapter_names", [])
        for item in repetition.get("repeated_words", [])[:30]:
            line = (
                f"- `{item.get('lemma')}`: {item.get('count')} uses across {item.get('affected_files')} files"
            )
            per_chapter = item.get("per_chapter") or []
            if per_chapter and chapter_names:
                curve = ", ".join(
                    f"{name}: {count}" for name, count in zip(chapter_names, per_chapter, strict=False)
                )
                line += f" ({curve})"
            lines.append(line)
        lines.append("")
        lines.append("### Repeated phrases")
        for item in repetition.get("repeated_phrases", [])[:30]:
            lines.append(
                f"- `{item.get('phrase')}`: {item.get('count')} uses across {item.get('affected_files')} files"
            )
        lines.append("")
        lines.append("### Pattern hotspots")
        for item in report.get("pattern_hotspots", [])[:30]:
            lines.append(
                f"- `{item.get('analyzer')}:{item.get('type')}`: {item.get('total_matches')} matches across {item.get('affected_files')} files"  # noqa: E501
            )
        return "\n".join(lines) + "\n"
    for result in report.get("analysis_after") or report.get("analysis_before") or []:
        flags = result.get("flags", [])
        if not flags:
            continue
        lines.append(f"### {result.get('name')}")
        for flag in flags:
            lines.append(f"- `{flag.get('severity')}` `{flag.get('type')}`: {flag.get('excerpt')}")
            if flag.get("suggestion"):
                lines.append(f"  - {flag.get('suggestion')}")
    if len(lines) == 9:
        lines.append("No flags.")
    return "\n".join(lines) + "\n"
