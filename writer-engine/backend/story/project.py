from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from backend import config
from backend.atomic_io import atomic_write_text

PROJECT_SCHEMA_VERSION = 1
STORY_STATE_SCHEMA_VERSION = 2


def _resolved_directory(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("story project root must be a directory")
    return root


def _fallback_dir(root: Path) -> Path:
    digest = hashlib.sha256(os.fsencode(str(root))).hexdigest()[:24]
    return config.DATA_DIR / "story-projects" / digest


@dataclass(slots=True)
class StoryProject:
    project_id: str
    root: Path
    metadata_dir: Path
    manifest_path: Path
    cache_path: Path
    state_path: Path
    manifest: dict[str, Any]
    state: dict[str, Any]
    metadata_is_external: bool = False
    state_was_existing: bool = False

    @classmethod
    def open(cls, root_value: str | Path) -> StoryProject:
        root = _resolved_directory(root_value)
        local_metadata = root / ".thothpad"
        local_manifest = local_metadata / "project.json"
        external = False

        if local_manifest.exists():
            metadata_dir = local_metadata
            manifest_path = local_manifest
        else:
            try:
                local_metadata.mkdir(parents=True, exist_ok=True)
                probe = local_metadata / ".story-engine-write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                metadata_dir = local_metadata
                manifest_path = local_manifest
            except OSError:
                metadata_dir = _fallback_dir(root)
                metadata_dir.mkdir(parents=True, exist_ok=True)
                manifest_path = metadata_dir / "project.json"
                external = True

        manifest = cls._load_manifest(manifest_path)
        manifest_version = manifest.get("version", PROJECT_SCHEMA_VERSION) if manifest else PROJECT_SCHEMA_VERSION
        if isinstance(manifest_version, int) and manifest_version > PROJECT_SCHEMA_VERSION:
            raise ValueError("Story Project manifest uses a newer unsupported schema version")
        if not manifest:
            manifest = {
                "version": PROJECT_SCHEMA_VERSION,
                "project_id": str(uuid.uuid4()),
                "source_rules": [],
                "authority_rules": [],
                "source_overrides": {},
                "entity_overrides": {},
                "active_manuscripts": [],
            }
            atomic_write_text(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False))

        project_id = manifest.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            project_id = str(uuid.uuid4())
            manifest["project_id"] = project_id
            atomic_write_text(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False))

        state_path = metadata_dir / "story-state.json"
        state_was_existing = state_path.exists()
        state = cls._load_manifest(state_path)
        state_version = state.get("version", STORY_STATE_SCHEMA_VERSION) if state else STORY_STATE_SCHEMA_VERSION
        if isinstance(state_version, int) and state_version > STORY_STATE_SCHEMA_VERSION:
            raise ValueError("Story State uses a newer unsupported schema version")
        if not state:
            state = {
                "version": STORY_STATE_SCHEMA_VERSION,
                "project_id": project_id,
                "writer_entities": [],
                "writer_entity_aliases": [],
                "writer_claims": [],
                "writer_claim_relations": [],
                "timeline_events": [],
                "world_state": [],
                "knowledge_state": [],
                "reader_state": [],
                "relationships": [],
                "threads": [],
                "promise_items": [],
                "decisions": [],
                "causal_edges": [],
                "opposition_state": [],
                "branches": [],
                "branch_overlays": [],
                "branch_merge_history": [],
                "scene_contracts": [],
                "author_decisions": [],
                "writer_preferences": [],
                "story_proposals": [],
            }
            atomic_write_text(state_path, json.dumps(state, indent=2, ensure_ascii=False))
        for key in (
            "writer_entities",
            "writer_entity_aliases",
            "writer_claims",
            "writer_claim_relations",
            "timeline_events",
            "world_state",
            "knowledge_state",
            "reader_state",
            "relationships",
            "threads",
            "promise_items",
            "decisions",
            "causal_edges",
            "opposition_state",
            "branches",
            "branch_overlays",
            "branch_merge_history",
            "scene_contracts",
            "author_decisions",
            "writer_preferences",
            "story_proposals",
        ):
            if not isinstance(state.get(key), list):
                state[key] = []
        state["version"] = STORY_STATE_SCHEMA_VERSION
        state["project_id"] = project_id

        cache_dir = metadata_dir / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            project_id=project_id,
            root=root,
            metadata_dir=metadata_dir,
            manifest_path=manifest_path,
            cache_path=cache_dir / "story-index.sqlite",
            state_path=state_path,
            manifest=manifest,
            state=state,
            metadata_is_external=external,
            state_was_existing=state_was_existing,
        )

    @classmethod
    def is_initialized(cls, root_value: str | Path) -> bool:
        """Return whether ThothPad already owns metadata for this project root.

        MCP uses this as a capability boundary: an external agent may inspect a
        project the writer has already opened in ThothPad, but it cannot turn an
        arbitrary machine directory into a new Story Project merely by naming it.
        """

        try:
            root = _resolved_directory(root_value)
        except (OSError, RuntimeError, ValueError):
            return False
        if (root / ".thothpad" / "project.json").is_file():
            return True
        return (_fallback_dir(root) / "project.json").is_file()

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def save_manifest(self) -> None:
        self.manifest["version"] = PROJECT_SCHEMA_VERSION
        self.manifest["project_id"] = self.project_id
        atomic_write_text(self.manifest_path, json.dumps(self.manifest, indent=2, ensure_ascii=False))

    def save_state(self) -> None:
        self.state["version"] = STORY_STATE_SCHEMA_VERSION
        self.state["project_id"] = self.project_id
        atomic_write_text(self.state_path, json.dumps(self.state, indent=2, ensure_ascii=False))
        self.state_was_existing = True

    def source_rule_override(self, relative_path: str) -> dict[str, Any]:
        """Return merged writer-confirmed rules matching one project-relative source.

        Rules are convenience metadata only: they never make folder names semantic
        in core Story Engine logic. The writer must explicitly create them after
        reviewing Project Understanding. Later rules override earlier ones.
        """

        normalized = self._relative_manifest_path(relative_path)
        if normalized is None:
            return {}
        rules = self.manifest.get("source_rules", [])
        if not isinstance(rules, list):
            return {}
        merged: dict[str, Any] = {}
        folded = normalized.casefold()
        for rule in rules[:500]:
            if not isinstance(rule, dict) or rule.get("user_confirmed") is not True:
                continue
            pattern = self._relative_rule_pattern(rule.get("pattern"))
            if pattern is None or not fnmatch.fnmatchcase(folded, pattern.casefold()):
                continue
            roles = rule.get("roles")
            if isinstance(roles, list):
                merged["roles"] = [str(item) for item in roles if isinstance(item, str)][:20]
            authority = rule.get("authority")
            if isinstance(authority, str) and authority.strip():
                merged["authority"] = authority.strip()
            merged["matched_rule"] = pattern
        return merged

    @staticmethod
    def _relative_rule_pattern(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.replace("\\", "/").strip()
        if not normalized or normalized.startswith("/"):
            return None
        parts = PurePosixPath(normalized).parts
        if ".." in parts:
            return None
        return normalized

    def add_source_rule(self, pattern: str, override: dict[str, Any]) -> None:
        normalized = self._relative_rule_pattern(pattern)
        if normalized is None:
            raise ValueError("source rule pattern must be project-relative")
        rules = self.manifest.setdefault("source_rules", [])
        if not isinstance(rules, list):
            rules = []
            self.manifest["source_rules"] = rules
        record = {"pattern": normalized, "user_confirmed": True}
        if isinstance(override.get("roles"), list):
            record["roles"] = list(override["roles"])[:20]
        if isinstance(override.get("authority"), str) and override["authority"].strip():
            record["authority"] = override["authority"].strip()
        replaced = False
        for index, existing in enumerate(rules):
            if isinstance(existing, dict) and str(existing.get("pattern", "")).casefold() == normalized.casefold():
                rules[index] = record
                replaced = True
                break
        if not replaced:
            rules.append(record)
        self.save_manifest()

    def source_override(self, relative_path: str) -> dict[str, Any]:
        overrides = self.manifest.get("source_overrides")
        if not isinstance(overrides, dict):
            return {}
        value = overrides.get(relative_path)
        return dict(value) if isinstance(value, dict) else {}

    def set_source_override(self, relative_path: str, override: dict[str, Any]) -> None:
        overrides = self.manifest.setdefault("source_overrides", {})
        if not isinstance(overrides, dict):
            overrides = {}
            self.manifest["source_overrides"] = overrides
        overrides[relative_path] = dict(override)
        self.save_manifest()

    @staticmethod
    def _relative_manifest_path(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.replace("\\", "/").strip()
        if not normalized:
            return None
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts:
            return None
        return path.as_posix()

    def active_manuscripts(self) -> list[str]:
        """Return the writer-owned cross-file manuscript order.

        The Story Engine deliberately does not infer a global chapter order from
        folder names. If a novel is split across files, reader/character cutoffs
        may use only this explicit order. With no order, restricted epistemic
        modes fail closed across files rather than guessing which chapter came
        first.
        """

        raw = self.manifest.get("active_manuscripts", [])
        if not isinstance(raw, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for item in raw:
            path = self._relative_manifest_path(item)
            if path is None:
                continue
            folded = path.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            result.append(path)
        return result

    def set_active_manuscripts(self, paths: list[str]) -> None:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            path = self._relative_manifest_path(raw)
            if path is None:
                raise ValueError("active manuscript paths must be project-relative")
            folded = path.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            normalized.append(path)
        self.manifest["active_manuscripts"] = normalized
        self.save_manifest()

    def contains(self, path: Path) -> bool:
        try:
            path.resolve(strict=True).relative_to(self.root)
        except (OSError, RuntimeError, ValueError):
            return False
        return True
