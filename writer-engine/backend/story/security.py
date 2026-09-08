from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.story.adapters.generic import DEFAULT_EXTENSIONS, DEFAULT_MAX_SOURCE_BYTES, SKIPPED_DIRECTORIES
from backend.story.project import StoryProject

ARCHIVE_EXTENSIONS = frozenset({".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"})
EXECUTABLE_EXTENSIONS = frozenset({".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".ps1", ".sh", ".py", ".js"})


@dataclass(slots=True)
class ProjectSecurityAudit:
    project: StoryProject

    def report(self) -> dict[str, Any]:
        root = self.project.root.resolve(strict=True)
        counts = {
            "safe_source_candidates": 0,
            "symlink_entries_ignored": 0,
            "oversized_sources_ignored": 0,
            "archives_ignored": 0,
            "executables_ignored": 0,
            "unsupported_files_ignored": 0,
        }
        for directory, names, filenames in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            kept_names: list[str] = []
            for name in names:
                path = directory_path / name
                if path.is_symlink():
                    counts["symlink_entries_ignored"] += 1
                    continue
                if name.casefold() in SKIPPED_DIRECTORIES:
                    continue
                kept_names.append(name)
            names[:] = kept_names
            for filename in filenames:
                path = directory_path / filename
                if path.is_symlink():
                    counts["symlink_entries_ignored"] += 1
                    continue
                suffix = path.suffix.casefold()
                if suffix in ARCHIVE_EXTENSIONS:
                    counts["archives_ignored"] += 1
                    continue
                if suffix in EXECUTABLE_EXTENSIONS:
                    counts["executables_ignored"] += 1
                    continue
                if suffix not in DEFAULT_EXTENSIONS:
                    counts["unsupported_files_ignored"] += 1
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(root)
                    stat = resolved.stat()
                except (OSError, RuntimeError, ValueError):
                    counts["symlink_entries_ignored"] += 1
                    continue
                if stat.st_size > DEFAULT_MAX_SOURCE_BYTES:
                    counts["oversized_sources_ignored"] += 1
                    continue
                counts["safe_source_candidates"] += 1
        return {
            "project_id": self.project.project_id,
            "counts": counts,
            "project_root_containment": True,
            "symlinks_followed": False,
            "archives_extracted": False,
            "executables_loaded": False,
            "maximum_source_bytes": DEFAULT_MAX_SOURCE_BYTES,
            "docx_archive_limits": {
                "entry_count_limited": True,
                "document_xml_size_limited": True,
                "compression_ratio_limited": True,
            },
            "project_content_authority": "DATA_ONLY",
        }
