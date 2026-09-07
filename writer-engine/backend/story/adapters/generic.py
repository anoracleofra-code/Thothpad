from __future__ import annotations

import os
from pathlib import Path

from backend.story.adapters.base import SourceCandidate

DEFAULT_MAX_SOURCE_BYTES = 8 * 1024 * 1024
DEFAULT_EXTENSIONS = frozenset({".md", ".markdown", ".txt", ".rst", ".docx"})
SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".thothpad",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
    }
)


class GenericFolderAdapter:
    """Enumerate safe source candidates without assigning semantic folder meaning."""

    name = "generic"

    def __init__(
        self,
        *,
        extensions: frozenset[str] = DEFAULT_EXTENSIONS,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    ) -> None:
        self.extensions = extensions
        self.max_source_bytes = max_source_bytes

    def enumerate_sources(self, root: Path) -> list[SourceCandidate]:
        root = root.resolve(strict=True)
        candidates: list[SourceCandidate] = []
        for directory, names, filenames in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            names[:] = sorted(
                name
                for name in names
                if name.casefold() not in SKIPPED_DIRECTORIES
                and not (directory_path / name).is_symlink()
            )
            for filename in sorted(filenames):
                path = directory_path / filename
                if path.is_symlink() or path.suffix.casefold() not in self.extensions:
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(root)
                    stat = resolved.stat()
                except (OSError, RuntimeError, ValueError):
                    continue
                if not resolved.is_file() or stat.st_size > self.max_source_bytes:
                    continue
                candidates.append(
                    SourceCandidate(
                        path=resolved,
                        relative_path=resolved.relative_to(root).as_posix(),
                        size=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                    )
                )
        return candidates
