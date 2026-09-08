from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

from backend.story.authority import AuthorityStatus
from backend.story.sources import SourceChunk, SourceDocument

SCHEMA_VERSION = 2


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class StoryStore:
    """Disposable compiled Story Engine state for one project.

    Author-approved overrides live in the project manifest, not only here. This
    database can therefore be deleted and rebuilt without losing source truth or
    writer decisions.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def __enter__(self) -> StoryStore:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        current = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise RuntimeError(f"story index schema {current} is newer than supported {SCHEMA_VERSION}")
        if current == 0:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    format TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    readability_status TEXT NOT NULL,
                    authority_default TEXT NOT NULL,
                    branch_scope TEXT NOT NULL,
                    story_scope TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    adapter_origin TEXT NOT NULL,
                    tombstoned INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project_id);
                CREATE INDEX IF NOT EXISTS idx_sources_hash ON sources(content_hash);

                CREATE TABLE IF NOT EXISTS source_roles (
                    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL,
                    user_confirmed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(source_id, role)
                );
                CREATE INDEX IF NOT EXISTS idx_source_roles_role ON source_roles(role, confidence DESC);

                CREATE TABLE IF NOT EXISTS source_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    heading TEXT NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    UNIQUE(source_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_source ON source_chunks(source_id, ordinal);

                CREATE TABLE IF NOT EXISTS source_links (
                    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
                    target TEXT NOT NULL,
                    label TEXT NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    kind TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_links_source ON source_links(source_id);

                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    branch_scope TEXT NOT NULL DEFAULT 'mainline',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    status TEXT NOT NULL DEFAULT 'PROVISIONAL',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(canonical_name COLLATE NOCASE);

                CREATE TABLE IF NOT EXISTS entity_aliases (
                    entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
                    alias TEXT NOT NULL COLLATE NOCASE,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    user_confirmed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(entity_id, alias)
                );
                CREATE INDEX IF NOT EXISTS idx_alias_value ON entity_aliases(alias COLLATE NOCASE);

                CREATE TABLE IF NOT EXISTS entity_mentions (
                    mention_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    surface TEXT NOT NULL,
                    confidence REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mentions_entity ON entity_mentions(entity_id, source_id);

                CREATE TABLE IF NOT EXISTS story_units (
                    story_unit_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    parent_id TEXT REFERENCES story_units(story_unit_id) ON DELETE SET NULL,
                    source_id TEXT REFERENCES sources(source_id) ON DELETE CASCADE,
                    display_title TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    anchor_signature TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_units_source ON story_units(source_id, ordinal);
                CREATE INDEX IF NOT EXISTS idx_units_anchor ON story_units(anchor_signature);

                CREATE TABLE IF NOT EXISTS claims (
                    claim_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    subject_entity_id TEXT REFERENCES entities(entity_id) ON DELETE SET NULL,
                    predicate TEXT NOT NULL,
                    object_entity_id TEXT REFERENCES entities(entity_id) ON DELETE SET NULL,
                    literal_value_json TEXT,
                    qualifiers_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    scope_id TEXT,
                    valid_from TEXT,
                    valid_until TEXT,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_by TEXT NOT NULL,
                    supersedes_claim_id TEXT REFERENCES claims(claim_id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject_entity_id, predicate);
                CREATE INDEX IF NOT EXISTS idx_claims_branch ON claims(branch_id, status);

                CREATE TABLE IF NOT EXISTS claim_evidence (
                    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
                    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
                    story_unit_id TEXT REFERENCES story_units(story_unit_id) ON DELETE SET NULL,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    quote_hash TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    stale INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_claim ON claim_evidence(claim_id);
                CREATE INDEX IF NOT EXISTS idx_evidence_source ON claim_evidence(source_id, stale);

                CREATE TABLE IF NOT EXISTS claim_relations (
                    left_claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
                    relation TEXT NOT NULL,
                    right_claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
                    PRIMARY KEY(left_claim_id, relation, right_claim_id)
                );

                CREATE TABLE IF NOT EXISTS story_conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolution TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS conflict_claims (
                    conflict_id TEXT NOT NULL REFERENCES story_conflicts(conflict_id) ON DELETE CASCADE,
                    claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
                    PRIMARY KEY(conflict_id, claim_id)
                );

                CREATE TABLE IF NOT EXISTS timeline_events (
                    event_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    story_unit_id TEXT REFERENCES story_units(story_unit_id) ON DELETE SET NULL,
                    time_start TEXT,
                    time_end TEXT,
                    precision TEXT NOT NULL DEFAULT 'OPEN',
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    status TEXT NOT NULL DEFAULT 'PROVISIONAL',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_timeline_branch ON timeline_events(branch_id, time_start);

                CREATE TABLE IF NOT EXISTS world_state (
                    state_id TEXT PRIMARY KEY,
                    entity_id TEXT REFERENCES entities(entity_id) ON DELETE CASCADE,
                    state_type TEXT NOT NULL,
                    value_json TEXT,
                    valid_from TEXT,
                    valid_until TEXT,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    status TEXT NOT NULL DEFAULT 'PROVISIONAL',
                    evidence_claim_id TEXT REFERENCES claims(claim_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge_state (
                    knowledge_id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
                    claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
                    state TEXT NOT NULL,
                    acquired_at TEXT,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    source_claim_id TEXT REFERENCES claims(claim_id) ON DELETE SET NULL,
                    UNIQUE(character_id, claim_id, branch_id, acquired_at)
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_character ON knowledge_state(character_id, branch_id);

                CREATE TABLE IF NOT EXISTS reader_state (
                    reader_state_id TEXT PRIMARY KEY,
                    claim_id TEXT REFERENCES claims(claim_id) ON DELETE CASCADE,
                    state TEXT NOT NULL,
                    story_unit_id TEXT REFERENCES story_units(story_unit_id) ON DELETE SET NULL,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    confidence REAL NOT NULL DEFAULT 1.0
                );

                CREATE TABLE IF NOT EXISTS relationships (
                    relationship_id TEXT PRIMARY KEY,
                    entity_a TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
                    entity_b TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
                    relationship_type TEXT NOT NULL,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    valid_from TEXT,
                    valid_until TEXT,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    evidence_claim_id TEXT REFERENCES claims(claim_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS threads (
                    thread_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL,
                    opened_at TEXT,
                    last_advanced_at TEXT,
                    resolved_at TEXT,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS promise_items (
                    item_id TEXT PRIMARY KEY,
                    item_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL,
                    opened_at TEXT,
                    resolved_at TEXT,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    evidence_claim_id TEXT REFERENCES claims(claim_id) ON DELETE SET NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    agent_entity_id TEXT REFERENCES entities(entity_id) ON DELETE SET NULL,
                    story_unit_id TEXT REFERENCES story_units(story_unit_id) ON DELETE SET NULL,
                    description TEXT NOT NULL,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    status TEXT NOT NULL DEFAULT 'PROVISIONAL',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS causal_edges (
                    edge_id TEXT PRIMARY KEY,
                    cause_kind TEXT NOT NULL,
                    cause_id TEXT NOT NULL,
                    effect_kind TEXT NOT NULL,
                    effect_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    evidence_claim_id TEXT REFERENCES claims(claim_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS opposition_state (
                    opposition_id TEXT PRIMARY KEY,
                    objective_id TEXT NOT NULL,
                    source_entity_id TEXT REFERENCES entities(entity_id) ON DELETE SET NULL,
                    description TEXT NOT NULL,
                    story_unit_id TEXT REFERENCES story_units(story_unit_id) ON DELETE SET NULL,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS scene_contracts (
                    story_unit_id TEXT PRIMARY KEY REFERENCES story_units(story_unit_id) ON DELETE CASCADE,
                    contract_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'PROVISIONAL',
                    updated_by TEXT NOT NULL DEFAULT 'writer'
                );

                CREATE TABLE IF NOT EXISTS interpretations (
                    interpretation_id TEXT PRIMARY KEY,
                    interpretation_type TEXT NOT NULL,
                    story_unit_id TEXT REFERENCES story_units(story_unit_id) ON DELETE SET NULL,
                    entity_id TEXT REFERENCES entities(entity_id) ON DELETE SET NULL,
                    statement TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    interpretive_status TEXT NOT NULL,
                    branch_id TEXT NOT NULL DEFAULT 'mainline'
                );

                CREATE TABLE IF NOT EXISTS story_lenses (
                    lens_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    definition TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_by TEXT NOT NULL DEFAULT 'writer'
                );

                CREATE TABLE IF NOT EXISTS lens_findings (
                    finding_id TEXT PRIMARY KEY,
                    lens_id TEXT NOT NULL REFERENCES story_lenses(lens_id) ON DELETE CASCADE,
                    story_unit_id TEXT REFERENCES story_units(story_unit_id) ON DELETE SET NULL,
                    source_id TEXT REFERENCES sources(source_id) ON DELETE CASCADE,
                    start_offset INTEGER,
                    end_offset INTEGER,
                    statement TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0.5
                );

                CREATE TABLE IF NOT EXISTS reader_experience (
                    experience_id TEXT PRIMARY KEY,
                    story_unit_id TEXT NOT NULL REFERENCES story_units(story_unit_id) ON DELETE CASCADE,
                    dimension TEXT NOT NULL,
                    label TEXT NOT NULL,
                    rationale TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    UNIQUE(story_unit_id, dimension, branch_id)
                );

                CREATE TABLE IF NOT EXISTS branches (
                    branch_id TEXT PRIMARY KEY,
                    parent_branch TEXT,
                    fork_story_unit TEXT REFERENCES story_units(story_unit_id) ON DELETE SET NULL,
                    base_revision TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    assumptions_json TEXT NOT NULL DEFAULT '[]'
                );
                INSERT OR IGNORE INTO branches(branch_id, parent_branch, status)
                VALUES('mainline', NULL, 'ACTIVE');

                CREATE TABLE IF NOT EXISTS branch_overlays (
                    overlay_id TEXT PRIMARY KEY,
                    branch_id TEXT NOT NULL REFERENCES branches(branch_id) ON DELETE CASCADE,
                    record_kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS branch_merge_history (
                    merge_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL REFERENCES branches(branch_id) ON DELETE CASCADE,
                    overlay_id TEXT NOT NULL REFERENCES branch_overlays(overlay_id) ON DELETE CASCADE,
                    target_record_kind TEXT NOT NULL,
                    target_record_id TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(branch_id, overlay_id)
                );
                CREATE INDEX IF NOT EXISTS idx_branch_merge_history_branch
                ON branch_merge_history(branch_id, completed_at);

                CREATE TABLE IF NOT EXISTS author_decisions (
                    author_decision_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    rationale TEXT NOT NULL DEFAULT '',
                    revisit_trigger TEXT NOT NULL DEFAULT '',
                    story_unit_id TEXT REFERENCES story_units(story_unit_id) ON DELETE SET NULL,
                    branch_id TEXT NOT NULL DEFAULT 'mainline',
                    status TEXT NOT NULL DEFAULT 'AUTHOR_LOCKED'
                );

                CREATE TABLE IF NOT EXISTS dependencies (
                    dependency_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_kind TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    dependent_kind TEXT NOT NULL,
                    dependent_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    UNIQUE(source_kind, source_id, dependent_kind, dependent_id, relation)
                );
                CREATE INDEX IF NOT EXISTS idx_dependencies_source ON dependencies(source_kind, source_id);

                CREATE TABLE IF NOT EXISTS writer_preferences (
                    preference_id TEXT PRIMARY KEY,
                    scope_kind TEXT NOT NULL,
                    scope_id TEXT NOT NULL DEFAULT '',
                    statement TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS project_rules (
                    rule_id TEXT PRIMARY KEY,
                    rule_kind TEXT NOT NULL,
                    matcher TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    user_confirmed INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS user_overrides (
                    override_id TEXT PRIMARY KEY,
                    subject_kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
            try:
                self.connection.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS source_chunks_fts
                    USING fts5(chunk_id UNINDEXED, source_id UNINDEXED, heading, text);
                    """
                )
            except sqlite3.OperationalError:
                # Some distro SQLite builds omit FTS5. Queries retain a safe
                # LIKE fallback so the Story Engine still functions.
                pass
            self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.connection.commit()
            current = SCHEMA_VERSION

        if current < 2:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS branch_merge_history (
                    merge_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL REFERENCES branches(branch_id) ON DELETE CASCADE,
                    overlay_id TEXT NOT NULL REFERENCES branch_overlays(overlay_id) ON DELETE CASCADE,
                    target_record_kind TEXT NOT NULL,
                    target_record_id TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(branch_id, overlay_id)
                );
                CREATE INDEX IF NOT EXISTS idx_branch_merge_history_branch
                ON branch_merge_history(branch_id, completed_at);
                """
            )
            self.connection.execute("PRAGMA user_version=2")
            self.connection.commit()

        # Performance-only indexes are additive and do not change the logical
        # schema. Keep them available to old caches without forcing a rebuild.
        self.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_sources_project_path
            ON sources(project_id, relative_path COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_sources_project_live
            ON sources(project_id, tombstoned, relative_path COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_mentions_source_span
            ON entity_mentions(source_id, start_offset, end_offset);
            CREATE INDEX IF NOT EXISTS idx_units_branch_source_span
            ON story_units(branch_id, source_id, start_offset, end_offset);
            CREATE INDEX IF NOT EXISTS idx_world_entity_branch_type
            ON world_state(entity_id, branch_id, state_type, valid_from);
            CREATE INDEX IF NOT EXISTS idx_reader_branch_unit
            ON reader_state(branch_id, story_unit_id);
            CREATE INDEX IF NOT EXISTS idx_relationship_pair_branch
            ON relationships(entity_a, entity_b, branch_id, relationship_type, valid_from);
            CREATE INDEX IF NOT EXISTS idx_threads_branch_state
            ON threads(branch_id, state);
            CREATE INDEX IF NOT EXISTS idx_promises_branch_state
            ON promise_items(branch_id, state);
            CREATE INDEX IF NOT EXISTS idx_decisions_branch_unit
            ON decisions(branch_id, story_unit_id);
            CREATE INDEX IF NOT EXISTS idx_causal_cause
            ON causal_edges(branch_id, cause_kind, cause_id);
            CREATE INDEX IF NOT EXISTS idx_causal_effect
            ON causal_edges(branch_id, effect_kind, effect_id);
            """
        )
        self.connection.commit()

    def transaction(self) -> sqlite3.Connection:
        return self.connection

    def upsert_source(self, source: SourceDocument) -> None:
        self.connection.execute(
            """
            INSERT INTO sources(
                source_id, project_id, relative_path, display_name, format,
                content_hash, size, mtime_ns, readability_status,
                authority_default, branch_scope, story_scope, metadata_json,
                adapter_origin, tombstoned
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
            ON CONFLICT(relative_path) DO UPDATE SET
                source_id=excluded.source_id,
                project_id=excluded.project_id,
                display_name=excluded.display_name,
                format=excluded.format,
                content_hash=excluded.content_hash,
                size=excluded.size,
                mtime_ns=excluded.mtime_ns,
                readability_status=excluded.readability_status,
                authority_default=excluded.authority_default,
                branch_scope=excluded.branch_scope,
                story_scope=excluded.story_scope,
                metadata_json=excluded.metadata_json,
                adapter_origin=excluded.adapter_origin,
                tombstoned=0
            """,
            (
                source.source_id,
                source.project_id,
                source.relative_path,
                source.display_name,
                source.format,
                source.content_hash,
                source.size,
                source.mtime_ns,
                source.readability_status,
                str(source.authority_default),
                source.branch_scope,
                source.story_scope,
                _json(source.metadata),
                source.adapter_origin,
            ),
        )
        self.connection.execute("DELETE FROM source_roles WHERE source_id=?", (source.source_id,))
        self.connection.executemany(
            "INSERT INTO source_roles(source_id, role, confidence, reason, user_confirmed) VALUES(?,?,?,?,?)",
            [
                (
                    source.source_id,
                    str(role.role),
                    float(role.confidence),
                    role.reason,
                    int(bool(source.metadata.get("role_override"))),
                )
                for role in source.roles
            ],
        )

    def replace_chunks(self, source_id: str, chunks: Sequence[SourceChunk]) -> None:
        self.connection.execute("DELETE FROM source_chunks WHERE source_id=?", (source_id,))
        if self._has_fts():
            self.connection.execute("DELETE FROM source_chunks_fts WHERE source_id=?", (source_id,))
        self.connection.executemany(
            """
            INSERT INTO source_chunks(
                chunk_id, source_id, ordinal, heading, start_offset, end_offset, text, content_hash
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            [
                (
                    chunk.chunk_id,
                    chunk.source_id,
                    chunk.ordinal,
                    chunk.heading,
                    chunk.start_offset,
                    chunk.end_offset,
                    chunk.text,
                    chunk.content_hash,
                )
                for chunk in chunks
            ],
        )
        if self._has_fts():
            self.connection.executemany(
                "INSERT INTO source_chunks_fts(chunk_id, source_id, heading, text) VALUES(?,?,?,?)",
                [(c.chunk_id, c.source_id, c.heading, c.text) for c in chunks],
            )

    def replace_links(self, source_id: str, links: Sequence[dict[str, Any]]) -> None:
        self.connection.execute("DELETE FROM source_links WHERE source_id=?", (source_id,))
        self.connection.executemany(
            """
            INSERT INTO source_links(source_id,target,label,start_offset,end_offset,kind)
            VALUES(?,?,?,?,?,?)
            """,
            [
                (
                    source_id,
                    str(link["target"]),
                    str(link.get("label", "")),
                    int(link.get("start_offset", 0)),
                    int(link.get("end_offset", 0)),
                    str(link.get("kind", "document_link")),
                )
                for link in links
            ],
        )

    def mark_missing_sources(self, project_id: str, present_paths: set[str]) -> list[str]:
        rows = self.connection.execute(
            "SELECT source_id, relative_path FROM sources WHERE project_id=? AND tombstoned=0",
            (project_id,),
        ).fetchall()
        missing = [row["source_id"] for row in rows if row["relative_path"] not in present_paths]
        if missing:
            placeholders = ",".join("?" for _ in missing)
            self.connection.execute(
                f"UPDATE sources SET tombstoned=1 WHERE source_id IN ({placeholders})",  # noqa: S608 - placeholders only
                missing,
            )
            self.connection.execute(
                f"UPDATE claim_evidence SET stale=1 WHERE source_id IN ({placeholders})",  # noqa: S608 - placeholders only
                missing,
            )
        return missing

    def source_by_path(self, relative_path: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM sources WHERE relative_path=? AND tombstoned=0", (relative_path,)
        ).fetchone()

    def source(self, source_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM sources WHERE source_id=? AND tombstoned=0", (source_id,)
        ).fetchone()

    def source_roles(self, source_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM source_roles WHERE source_id=? ORDER BY confidence DESC, role", (source_id,)
        ).fetchall()

    def sources_for_role(self, role: str, *, minimum_confidence: float = 0.0) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT s.*, r.confidence, r.reason, r.user_confirmed
            FROM sources s JOIN source_roles r ON r.source_id=s.source_id
            WHERE s.tombstoned=0 AND r.role=? AND r.confidence>=?
            ORDER BY r.user_confirmed DESC, r.confidence DESC, s.relative_path COLLATE NOCASE
            """,
            (role, minimum_confidence),
        ).fetchall()

    def list_sources(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM sources WHERE tombstoned=0 ORDER BY relative_path COLLATE NOCASE"
        ).fetchall()

    def _has_fts(self) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_chunks_fts'"
        ).fetchone()
        return row is not None

    @staticmethod
    def _fts_query(terms: Sequence[str]) -> str:
        safe = []
        for term in terms:
            cleaned = "".join(ch for ch in term if ch.isalnum() or ch in "_-")
            if cleaned:
                safe.append(f'"{cleaned}"')
        return " OR ".join(safe[:32])

    def search_chunks(self, terms: Sequence[str], *, limit: int = 24) -> list[sqlite3.Row]:
        normalized = [term.casefold().strip() for term in terms if term.strip()]
        if not normalized:
            return []
        if self._has_fts():
            query = self._fts_query(normalized)
            if query:
                try:
                    return self.connection.execute(
                        """
                        SELECT c.*, s.relative_path, s.authority_default,
                               bm25(source_chunks_fts) AS rank
                        FROM source_chunks_fts
                        JOIN source_chunks c ON c.chunk_id=source_chunks_fts.chunk_id
                        JOIN sources s ON s.source_id=c.source_id
                        WHERE source_chunks_fts MATCH ? AND s.tombstoned=0
                        ORDER BY rank, s.relative_path COLLATE NOCASE, c.ordinal
                        LIMIT ?
                        """,
                        (query, max(1, min(limit, 200))),
                    ).fetchall()
                except sqlite3.OperationalError:
                    pass

        conditions = " OR ".join("lower(c.text) LIKE ?" for _ in normalized[:12])
        params: list[Any] = [f"%{term}%" for term in normalized[:12]]
        params.append(max(1, min(limit, 200)))
        return self.connection.execute(
            f"""
            SELECT c.*, s.relative_path, s.authority_default, 0.0 AS rank
            FROM source_chunks c JOIN sources s ON s.source_id=c.source_id
            WHERE s.tombstoned=0 AND ({conditions})
            ORDER BY s.relative_path COLLATE NOCASE, c.ordinal LIMIT ?
            """,  # noqa: S608 - conditions contains fixed SQL fragments only
            params,
        ).fetchall()

    def upsert_entity(
        self,
        entity_id: str,
        canonical_name: str,
        entity_type: str,
        *,
        description: str = "",
        branch_scope: str = "mainline",
        confidence: float = 1.0,
        status: str = AuthorityStatus.PROVISIONAL,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO entities(
                entity_id,canonical_name,entity_type,description,branch_scope,confidence,status,metadata_json
            )
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(entity_id) DO UPDATE SET
                canonical_name=excluded.canonical_name,
                entity_type=excluded.entity_type,
                description=excluded.description,
                branch_scope=excluded.branch_scope,
                confidence=excluded.confidence,
                status=excluded.status,
                metadata_json=excluded.metadata_json
            """,
            (
                entity_id,
                canonical_name,
                entity_type,
                description,
                branch_scope,
                confidence,
                str(status),
                _json(metadata or {}),
            ),
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO entity_aliases(entity_id,alias,confidence,user_confirmed) VALUES(?,?,?,0)",
            (entity_id, canonical_name, confidence),
        )

    def add_entity_alias(self, entity_id: str, alias: str, *, confidence: float = 1.0, confirmed: bool = False) -> None:
        self.connection.execute(
            """
            INSERT INTO entity_aliases(entity_id,alias,confidence,user_confirmed) VALUES(?,?,?,?)
            ON CONFLICT(entity_id,alias) DO UPDATE SET
                confidence=max(entity_aliases.confidence,excluded.confidence),
                user_confirmed=max(entity_aliases.user_confirmed,excluded.user_confirmed)
            """,
            (entity_id, alias, confidence, int(confirmed)),
        )

    def resolve_entities(self, name: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT e.*, a.alias, a.confidence AS alias_confidence, a.user_confirmed
            FROM entity_aliases a JOIN entities e ON e.entity_id=a.entity_id
            WHERE a.alias=? COLLATE NOCASE
            ORDER BY a.user_confirmed DESC, a.confidence DESC, e.confidence DESC
            """,
            (name,),
        ).fetchall()

    def entity(self, entity_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()

    def list_entities(self, *, entity_type: str | None = None) -> list[sqlite3.Row]:
        if entity_type:
            return self.connection.execute(
                "SELECT * FROM entities WHERE entity_type=? ORDER BY canonical_name COLLATE NOCASE", (entity_type,)
            ).fetchall()
        return self.connection.execute("SELECT * FROM entities ORDER BY canonical_name COLLATE NOCASE").fetchall()

    def add_mention(
        self,
        entity_id: str,
        source_id: str,
        start_offset: int,
        end_offset: int,
        surface: str,
        confidence: float,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO entity_mentions(entity_id,source_id,start_offset,end_offset,surface,confidence)
            VALUES(?,?,?,?,?,?)
            """,
            (entity_id, source_id, start_offset, end_offset, surface, confidence),
        )

    def replace_mentions_for_source(self, source_id: str) -> None:
        self.connection.execute("DELETE FROM entity_mentions WHERE source_id=?", (source_id,))

    def add_story_unit(self, record: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO story_units(
                story_unit_id,kind,parent_id,source_id,display_title,ordinal,start_offset,end_offset,
                anchor_signature,content_hash,status,branch_id,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(story_unit_id) DO UPDATE SET
                kind=excluded.kind,parent_id=excluded.parent_id,source_id=excluded.source_id,
                display_title=excluded.display_title,ordinal=excluded.ordinal,start_offset=excluded.start_offset,
                end_offset=excluded.end_offset,anchor_signature=excluded.anchor_signature,
                content_hash=excluded.content_hash,status=excluded.status,branch_id=excluded.branch_id,
                metadata_json=excluded.metadata_json
            """,
            (
                record["story_unit_id"],
                record["kind"],
                record.get("parent_id"),
                record.get("source_id"),
                record.get("display_title", ""),
                int(record.get("ordinal", 0)),
                int(record.get("start_offset", 0)),
                int(record.get("end_offset", 0)),
                record.get("anchor_signature", ""),
                record.get("content_hash", ""),
                record.get("status", "PROVISIONAL"),
                record.get("branch_id", "mainline"),
                _json(record.get("metadata", {})),
            ),
        )

    def units_for_source(self, source_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM story_units WHERE source_id=? ORDER BY ordinal", (source_id,)
        ).fetchall()

    def find_unit_by_anchor(self, anchor_signature: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM story_units WHERE anchor_signature=? ORDER BY rowid LIMIT 1", (anchor_signature,)
        ).fetchone()

    def delete_units_for_source_except(self, source_id: str, unit_ids: set[str]) -> None:
        rows = self.units_for_source(source_id)
        stale = [row["story_unit_id"] for row in rows if row["story_unit_id"] not in unit_ids]
        if stale:
            placeholders = ",".join("?" for _ in stale)
            self.connection.execute(
                f"DELETE FROM story_units WHERE story_unit_id IN ({placeholders})",  # noqa: S608 - placeholders only
                stale,
            )

    def add_claim(self, record: dict[str, Any]) -> None:
        status = AuthorityStatus(record.get("status", AuthorityStatus.PROVISIONAL))
        created_by = str(record.get("created_by", "system"))
        if created_by == "model" and status not in {AuthorityStatus.INFERENCE, AuthorityStatus.SUGGESTION}:
            raise PermissionError("model-originated claims must begin as INFERENCE or SUGGESTION")
        self.connection.execute(
            """
            INSERT INTO claims(
                claim_id,project_id,subject_entity_id,predicate,object_entity_id,literal_value_json,
                qualifiers_json,status,branch_id,scope_id,valid_from,valid_until,confidence,created_by,
                supersedes_claim_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record["claim_id"],
                record["project_id"],
                record.get("subject_entity_id"),
                record["predicate"],
                record.get("object_entity_id"),
                _json(record.get("literal_value")) if "literal_value" in record else None,
                _json(record.get("qualifiers", {})),
                str(status),
                record.get("branch_id", "mainline"),
                record.get("scope_id"),
                record.get("valid_from"),
                record.get("valid_until"),
                float(record.get("confidence", 1.0)),
                created_by,
                record.get("supersedes_claim_id"),
            ),
        )

    def promote_claim(self, claim_id: str, status: AuthorityStatus | str, *, approved_by: str = "writer") -> None:
        normalized = AuthorityStatus(status)
        if approved_by != "writer" and normalized not in {AuthorityStatus.INFERENCE, AuthorityStatus.SUGGESTION}:
            raise PermissionError("only writer approval may promote a claim into authoritative state")
        self.connection.execute(
            "UPDATE claims SET status=?, created_by=? WHERE claim_id=?",
            (str(normalized), approved_by, claim_id),
        )

    def add_claim_evidence(self, record: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO claim_evidence(
                claim_id,source_id,story_unit_id,start_offset,end_offset,quote_hash,source_hash,
                evidence_type,weight,stale
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record["claim_id"],
                record["source_id"],
                record.get("story_unit_id"),
                int(record["start_offset"]),
                int(record["end_offset"]),
                record["quote_hash"],
                record["source_hash"],
                record.get("evidence_type", "source_span"),
                float(record.get("weight", 1.0)),
                int(bool(record.get("stale", False))),
            ),
        )

    def query_claims(
        self,
        *,
        subject_entity_id: str | None = None,
        predicate: str | None = None,
        branch_id: str = "mainline",
        include_noncanonical: bool = True,
    ) -> list[sqlite3.Row]:
        conditions = ["branch_id=?"]
        params: list[Any] = [branch_id]
        if subject_entity_id:
            conditions.append("subject_entity_id=?")
            params.append(subject_entity_id)
        if predicate:
            conditions.append("predicate=?")
            params.append(predicate)
        if not include_noncanonical:
            statuses = [
                AuthorityStatus.AUTHOR_LOCKED,
                AuthorityStatus.CONFIRMED_CANON,
                AuthorityStatus.MANUSCRIPT_OBSERVED,
                AuthorityStatus.COMPILED_CANON,
            ]
            conditions.append("status IN (?,?,?,?)")
            params.extend(str(status) for status in statuses)
        return self.connection.execute(
            f"SELECT * FROM claims WHERE {' AND '.join(conditions)} ORDER BY created_at, claim_id",  # noqa: S608
            params,
        ).fetchall()

    def claim_with_evidence(self, claim_id: str) -> dict[str, Any] | None:
        claim = self.connection.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
        if claim is None:
            return None
        evidence = self.connection.execute(
            """
            SELECT e.*, s.relative_path, s.content_hash AS current_source_hash
            FROM claim_evidence e JOIN sources s ON s.source_id=e.source_id
            WHERE e.claim_id=? ORDER BY e.weight DESC, e.evidence_id
            """,
            (claim_id,),
        ).fetchall()
        return {"claim": dict(claim), "evidence": [dict(row) for row in evidence]}

    def refresh_evidence_staleness(self, source_id: str) -> int:
        source = self.source(source_id)
        if source is None:
            return 0
        cursor = self.connection.execute(
            """
            UPDATE claim_evidence SET stale=CASE WHEN source_hash=? THEN stale ELSE 1 END
            WHERE source_id=?
            """,
            (source["content_hash"], source_id),
        )
        return cursor.rowcount

    def supersede_ungrounded_compiler_claims(self, project_id: str) -> int:
        """Retire compiled claims after every source that supported them went stale.

        Writer-promoted/model-authored records are deliberately untouched. A
        compiled claim can become live again if deterministic extraction later
        recreates the same stable identity with current evidence.
        """

        cursor = self.connection.execute(
            """
            UPDATE claims
            SET status=?
            WHERE project_id=?
              AND created_by='compiler'
              AND status NOT IN (?,?)
              AND EXISTS (
                  SELECT 1 FROM claim_evidence e
                  WHERE e.claim_id=claims.claim_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM claim_evidence e
                  WHERE e.claim_id=claims.claim_id AND e.stale=0
              )
            """,
            (
                str(AuthorityStatus.SUPERSEDED),
                project_id,
                str(AuthorityStatus.SUPERSEDED),
                str(AuthorityStatus.ARCHIVED),
            ),
        )
        return cursor.rowcount

    def add_dependency(
        self,
        source_kind: str,
        source_id: str,
        dependent_kind: str,
        dependent_id: str,
        relation: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO dependencies(source_kind,source_id,dependent_kind,dependent_id,relation)
            VALUES(?,?,?,?,?)
            """,
            (source_kind, source_id, dependent_kind, dependent_id, relation),
        )

    def dependents(self, source_kind: str, source_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM dependencies WHERE source_kind=? AND source_id=?",
            (source_kind, source_id),
        ).fetchall()

    def rows(self, query: str, parameters: Iterable[Any] = ()) -> Iterator[sqlite3.Row]:
        yield from self.connection.execute(query, tuple(parameters))

    def commit(self) -> None:
        self.connection.commit()

    @staticmethod
    def decode_json(value: str | None, default: Any = None) -> Any:
        return _loads(value, default)

    @staticmethod
    def encode_json(value: Any) -> str:
        return _json(value)
