from __future__ import annotations

import base64
import bisect
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import Counter
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend import config

_ANALYSIS_ID = re.compile(r"[0-9a-f]{32}")
_ANALYZER = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_CURSOR_VERSION = 2
_MAX_ANALYZER_FILTERS = 32
_DEFAULT_MAX_MEMORY_SNAPSHOTS = 8
_DEFAULT_MAX_MEMORY_FINDINGS = 1_000_000
_DEFAULT_MAX_MEMORY_BYTES = 512 * 1024 * 1024


@dataclass(slots=True)
class _MemoryFinding:
    ordinal: int
    analyzer: str
    rule_id: str
    level: str
    confidence: float
    source: str
    diagnostic_id: str
    finding_type: str
    severity: str
    excerpt: str
    suggestion: str
    explanation: str
    replacements_json: str
    revision: int | None
    start_utf16: int
    end_utf16: int
    extra_spans_json: str = "[]"

    @property
    def sort_key(self) -> tuple[int, int, int]:
        return self.start_utf16, self.end_utf16, self.ordinal

    def to_diagnostic(self) -> dict[str, Any]:
        value = {
            "id": self.diagnostic_id,
            "analyzer": self.analyzer,
            "rule_id": self.rule_id,
            "type": self.finding_type,
            "severity": self.severity,
            "level": self.level,
            "start_utf16": self.start_utf16,
            "end_utf16": self.end_utf16,
            "excerpt": self.excerpt,
            "suggestion": self.suggestion,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "source": self.source,
            "replacements": json.loads(self.replacements_json),
            "revision": self.revision,
        }
        if self.extra_spans_json != "[]":
            value["extra_spans_utf16"] = json.loads(self.extra_spans_json)
        return value


@dataclass(slots=True)
class _MemoryAnalysis:
    analysis_id: str
    document_id: str
    document_revision: int | None
    text_hash: str
    created_at: float
    expires_at: float
    counts: dict[str, int]
    cursor_secret: bytes
    findings: list[_MemoryFinding]
    approximate_bytes: int


class AnalysisStore:
    def __init__(
        self,
        path: Path | str | None = None,
        *,
        ttl_seconds: int | None = None,
        max_page_size: int = config.MAX_FINDING_PAGE_SIZE,
        max_memory_snapshots: int = _DEFAULT_MAX_MEMORY_SNAPSHOTS,
        max_memory_findings: int = _DEFAULT_MAX_MEMORY_FINDINGS,
        max_memory_bytes: int = _DEFAULT_MAX_MEMORY_BYTES,
    ) -> None:
        self.path = Path(path or config.ANALYSIS_CACHE_DB)
        self.ttl_seconds = int(
            config.ANALYSIS_SNAPSHOT_TTL_SECONDS
            if ttl_seconds is None
            else ttl_seconds
        )
        self.max_page_size = int(max_page_size)
        self.max_memory_snapshots = int(max_memory_snapshots)
        self.max_memory_findings = int(max_memory_findings)
        self.max_memory_bytes = int(max_memory_bytes)
        if self.ttl_seconds <= 0:
            raise ValueError("analysis snapshot TTL must be positive")
        if self.max_page_size <= 0:
            raise ValueError("maximum finding page size must be positive")
        if min(
            self.max_memory_snapshots,
            self.max_memory_findings,
            self.max_memory_bytes,
        ) <= 0:
            raise ValueError("in-memory analysis limits must be positive")
        self._connection: sqlite3.Connection | None = None
        self._memory: dict[str, _MemoryAnalysis] = {}
        self._memory_findings = 0
        self._memory_bytes = 0
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self.path.parent, 0o700)
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analyses (
              analysis_id TEXT PRIMARY KEY,
              document_id TEXT NOT NULL,
              document_revision INTEGER,
              text_hash TEXT NOT NULL,
              created_at REAL NOT NULL,
              expires_at REAL NOT NULL,
              total_findings INTEGER NOT NULL,
              counts_json TEXT NOT NULL,
              cursor_secret BLOB NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS findings (
              analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
              ordinal INTEGER NOT NULL,
              analyzer TEXT NOT NULL,
              rule_id TEXT NOT NULL DEFAULT '',
              level TEXT NOT NULL DEFAULT '',
              confidence REAL NOT NULL DEFAULT 1.0,
              source TEXT NOT NULL DEFAULT 'deterministic',
              diagnostic_id TEXT NOT NULL DEFAULT '',
              finding_type TEXT NOT NULL DEFAULT '',
              severity TEXT NOT NULL DEFAULT '',
              excerpt TEXT NOT NULL DEFAULT '',
              suggestion TEXT NOT NULL DEFAULT '',
              explanation TEXT NOT NULL DEFAULT '',
              replacements_json TEXT NOT NULL DEFAULT '[]',
              revision INTEGER,
              start_utf16 INTEGER NOT NULL,
              end_utf16 INTEGER NOT NULL,
              payload_json TEXT NOT NULL,
              PRIMARY KEY (analysis_id, ordinal)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS findings_keyset
            ON findings(analysis_id, start_utf16, end_utf16, ordinal)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS findings_analyzer_keyset
            ON findings(analysis_id, analyzer, start_utf16, end_utf16, ordinal)
            """
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(findings)")
        }
        migrations = {
            "rule_id": "TEXT NOT NULL DEFAULT ''",
            "level": "TEXT NOT NULL DEFAULT ''",
            "confidence": "REAL NOT NULL DEFAULT 1.0",
            "source": "TEXT NOT NULL DEFAULT 'deterministic'",
            "diagnostic_id": "TEXT NOT NULL DEFAULT ''",
            "finding_type": "TEXT NOT NULL DEFAULT ''",
            "severity": "TEXT NOT NULL DEFAULT ''",
            "excerpt": "TEXT NOT NULL DEFAULT ''",
            "suggestion": "TEXT NOT NULL DEFAULT ''",
            "explanation": "TEXT NOT NULL DEFAULT ''",
            "replacements_json": "TEXT NOT NULL DEFAULT '[]'",
            "revision": "INTEGER",
        }
        for name, definition in migrations.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE findings ADD COLUMN {name} {definition}"
                )
        connection.commit()
        self._connection = connection
        self._secure_disk_paths()
        return connection

    def _secure_disk_paths(self) -> None:
        if os.name == "nt":
            return
        for path in (
            self.path,
            Path(f"{self.path}-wal"),
            Path(f"{self.path}-shm"),
        ):
            if path.exists():
                os.chmod(path, 0o600)

    def _disk_exists(self) -> bool:
        return self._connection is not None or self.path.is_file()

    def _drop_memory_locked(self, analysis_id: str) -> bool:
        analysis = self._memory.pop(analysis_id, None)
        if analysis is None:
            return False
        self._memory_findings -= len(analysis.findings)
        self._memory_bytes -= analysis.approximate_bytes
        return True

    def _cleanup_memory_locked(self, cutoff: float) -> int:
        expired = [
            analysis_id
            for analysis_id, analysis in self._memory.items()
            if analysis.expires_at <= cutoff
        ]
        for analysis_id in expired:
            self._drop_memory_locked(analysis_id)
        return len(expired)

    @staticmethod
    def _normalize_memory_finding(
        diagnostic: dict[str, Any], ordinal: int, analyzer: str
    ) -> tuple[_MemoryFinding, int]:
        start = diagnostic.get("start_utf16")
        end = diagnostic.get("end_utf16")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
        ):
            raise ValueError("diagnostic UTF-16 offsets are invalid")
        replacements = diagnostic.get("replacements", [])
        if not isinstance(replacements, list):
            replacements = []
        replacements_json = json.dumps(
            replacements, ensure_ascii=False, separators=(",", ":")
        )
        raw_extra = diagnostic.get("extra_spans_utf16", [])
        if not isinstance(raw_extra, list):
            raw_extra = []
        extra_spans: list[list[int]] = []
        for span in raw_extra:
            if (
                isinstance(span, list)
                and len(span) == 2
                and all(isinstance(value, int) and not isinstance(value, bool) for value in span)
                and 0 <= span[0] < span[1]
            ):
                extra_spans.append([span[0], span[1]])
        extra_spans_json = json.dumps(extra_spans, separators=(",", ":"))
        finding = _MemoryFinding(
            ordinal=ordinal,
            analyzer=analyzer,
            rule_id=str(diagnostic.get("rule_id", ""))[:256],
            level=str(diagnostic.get("level", diagnostic.get("severity", "")))[:32],
            confidence=float(diagnostic.get("confidence", 1.0)),
            source=str(diagnostic.get("source", "deterministic"))[:64],
            diagnostic_id=str(diagnostic.get("id", ""))[:128],
            finding_type=str(diagnostic.get("type", ""))[:128],
            severity=str(diagnostic.get("severity", diagnostic.get("level", "")))[:32],
            excerpt=str(diagnostic.get("excerpt", "")),
            suggestion=str(diagnostic.get("suggestion", "")),
            explanation=str(diagnostic.get("explanation", "")),
            replacements_json=replacements_json,
            revision=(
                diagnostic.get("revision")
                if isinstance(diagnostic.get("revision"), int)
                else None
            ),
            start_utf16=start,
            end_utf16=end,
            extra_spans_json=extra_spans_json,
        )
        text_bytes = sum(
            len(value.encode("utf-8"))
            for value in (
                finding.analyzer,
                finding.rule_id,
                finding.level,
                finding.source,
                finding.diagnostic_id,
                finding.finding_type,
                finding.severity,
                finding.excerpt,
                finding.suggestion,
                finding.explanation,
                finding.replacements_json,
                finding.extra_spans_json,
            )
        )
        return finding, 192 + text_bytes

    @contextmanager
    def _transaction(self):
        with self._lock:
            connection = self._connect()
            with connection:
                yield connection
            self._secure_disk_paths()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self._memory.clear()
            self._memory_findings = 0
            self._memory_bytes = 0

    def __enter__(self) -> AnalysisStore:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def validate_analysis_id(value: Any) -> str:
        if not isinstance(value, str) or not _ANALYSIS_ID.fullmatch(value):
            raise ValueError("analysis_id is invalid")
        return value

    @staticmethod
    def _validate_analyzer(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not _ANALYZER.fullmatch(value):
            raise ValueError("analyzer filter is invalid")
        return value

    @classmethod
    def _validate_analyzers(
        cls, analyzer: Any, analyzers: Any
    ) -> tuple[str, ...]:
        values: list[Any] = []
        if analyzer is not None:
            values.append(analyzer)
        if analyzers is not None:
            if not isinstance(analyzers, list):
                raise ValueError("analyzers filter must be an array")
            if not analyzers:
                raise ValueError("analyzers filter must not be empty")
            if len(analyzers) > _MAX_ANALYZER_FILTERS:
                raise ValueError(
                    f"analyzers filter must contain at most {_MAX_ANALYZER_FILTERS} items"
                )
            values.extend(analyzers)

        validated: set[str] = set()
        for value in values:
            item = cls._validate_analyzer(value)
            if item is not None:
                validated.add(item)
        if len(validated) > _MAX_ANALYZER_FILTERS:
            raise ValueError(
                f"analyzers filter must contain at most {_MAX_ANALYZER_FILTERS} unique items"
            )
        return tuple(sorted(validated))

    def _validate_page_size(
        self,
        value: Any,
        *,
        allow_zero: bool = False,
        maximum: int | None = None,
    ) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("page size must be an integer")
        minimum = 0 if allow_zero else 1
        upper = self.max_page_size if maximum is None else maximum
        if value < minimum or value > upper:
            raise ValueError(
                f"page size must be between {minimum} and {upper}"
            )
        return value

    @staticmethod
    def _validate_range(start: Any, end: Any) -> tuple[int | None, int | None]:
        if start is None and end is None:
            return None, None
        if start is None or end is None:
            raise ValueError("start_utf16 and end_utf16 must be provided together")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("UTF-16 range offsets must be integers")
        if start < 0 or end <= start:
            raise ValueError("UTF-16 range must satisfy 0 <= start_utf16 < end_utf16")
        return start, end

    def cleanup_expired(self, *, now: float | None = None) -> int:
        cutoff = time.time() if now is None else float(now)
        with self._lock:
            removed = self._cleanup_memory_locked(cutoff)
            if not self._disk_exists():
                return removed
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM analyses WHERE expires_at <= ?", (cutoff,)
                )
            self._secure_disk_paths()
            return removed + cursor.rowcount

    def _create_memory_snapshot(
        self,
        diagnostics: Iterable[dict[str, Any]],
        *,
        document_id: str,
        document_revision: int | None,
        text_hash: str,
        page_size: int,
    ) -> dict[str, Any]:
        counts: Counter[str] = Counter()
        findings: list[_MemoryFinding] = []
        approximate_bytes = 512
        for ordinal, diagnostic in enumerate(diagnostics):
            if not isinstance(diagnostic, dict):
                raise ValueError("diagnostics must contain JSON objects")
            analyzer = self._validate_analyzer(diagnostic.get("analyzer"))
            if analyzer is None:
                raise ValueError("diagnostic analyzer is required")
            finding, finding_bytes = self._normalize_memory_finding(
                diagnostic, ordinal, analyzer
            )
            findings.append(finding)
            counts[analyzer] += 1
            approximate_bytes += finding_bytes
            if len(findings) > self.max_memory_findings:
                raise ValueError("analysis snapshot exceeds the in-memory finding limit")
            if approximate_bytes > self.max_memory_bytes:
                raise ValueError("analysis snapshot exceeds the in-memory byte limit")

        findings.sort(key=lambda item: item.sort_key)
        analysis_id = uuid.uuid4().hex
        now = time.time()
        analysis = _MemoryAnalysis(
            analysis_id=analysis_id,
            document_id=str(document_id),
            document_revision=document_revision,
            text_hash=str(text_hash),
            created_at=now,
            expires_at=now + self.ttl_seconds,
            counts=dict(sorted(counts.items())),
            cursor_secret=secrets.token_bytes(32),
            findings=findings,
            approximate_bytes=approximate_bytes,
        )
        with self._lock:
            self._cleanup_memory_locked(now)
            while self._memory and (
                len(self._memory) >= self.max_memory_snapshots
                or self._memory_findings + len(findings) > self.max_memory_findings
                or self._memory_bytes + approximate_bytes > self.max_memory_bytes
            ):
                oldest = min(
                    self._memory.values(), key=lambda item: item.created_at
                )
                self._drop_memory_locked(oldest.analysis_id)
            self._memory[analysis_id] = analysis
            self._memory_findings += len(findings)
            self._memory_bytes += approximate_bytes

        if page_size == 0:
            return {
                "analysis_id": analysis_id,
                "total_findings": len(findings),
                "counts_by_analyzer": analysis.counts,
                "diagnostics": [],
                "has_more": bool(findings),
                "next_cursor": None,
                "page_size": 0,
                "persisted": False,
            }
        return self.query_findings(analysis_id, limit=page_size)

    def create_snapshot(
        self,
        diagnostics: Iterable[dict[str, Any]],
        *,
        document_id: str,
        document_revision: int | None,
        text_hash: str,
        initial_page_size: int = config.DEFAULT_FINDING_PAGE_SIZE,
        persist: bool = False,
    ) -> dict[str, Any]:
        page_size = self._validate_page_size(initial_page_size, allow_zero=True)
        if not isinstance(persist, bool):
            raise ValueError("persist must be a boolean")
        if not persist:
            return self._create_memory_snapshot(
                diagnostics,
                document_id=document_id,
                document_revision=document_revision,
                text_hash=text_hash,
                page_size=page_size,
            )
        analysis_id = uuid.uuid4().hex
        now = time.time()
        counts: Counter[str] = Counter()
        total = 0
        with self._transaction() as connection:
            connection.execute("DELETE FROM analyses WHERE expires_at <= ?", (now,))
            connection.execute(
                """
                INSERT INTO analyses(
                  analysis_id, document_id, document_revision, text_hash,
                  created_at, expires_at, total_findings, counts_json, cursor_secret
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    str(document_id),
                    document_revision,
                    str(text_hash),
                    now,
                    now + self.ttl_seconds,
                    0,
                    "{}",
                    secrets.token_bytes(32),
                ),
            )
            insert_sql = """
                INSERT INTO findings(
                  analysis_id, ordinal, analyzer, rule_id, level, confidence, source,
                  diagnostic_id, finding_type, severity, excerpt, suggestion,
                  explanation, replacements_json, revision, start_utf16, end_utf16,
                  payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            batch: list[tuple[Any, ...]] = []
            for ordinal, diagnostic in enumerate(diagnostics):
                if not isinstance(diagnostic, dict):
                    raise ValueError("diagnostics must contain JSON objects")
                analyzer = self._validate_analyzer(diagnostic.get("analyzer"))
                if analyzer is None:
                    raise ValueError("diagnostic analyzer is required")
                start = diagnostic.get("start_utf16")
                end = diagnostic.get("end_utf16")
                if (
                    isinstance(start, bool)
                    or isinstance(end, bool)
                    or not isinstance(start, int)
                    or not isinstance(end, int)
                    or start < 0
                    or end <= start
                ):
                    raise ValueError("diagnostic UTF-16 offsets are invalid")
                replacements = diagnostic.get("replacements", [])
                if not isinstance(replacements, list):
                    replacements = []
                raw_extra = diagnostic.get("extra_spans_utf16", [])
                extra_spans: list[list[int]] = []
                if isinstance(raw_extra, list):
                    for span in raw_extra:
                        if (
                            isinstance(span, list)
                            and len(span) == 2
                            and all(
                                isinstance(value, int) and not isinstance(value, bool)
                                for value in span
                            )
                            and 0 <= span[0] < span[1]
                        ):
                            extra_spans.append([span[0], span[1]])
                payload_json = (
                    json.dumps(
                        {"extra_spans_utf16": extra_spans},
                        separators=(",", ":"),
                    )
                    if extra_spans
                    else "{}"
                )
                counts[analyzer] += 1
                total += 1
                batch.append((
                    analysis_id,
                    ordinal,
                    analyzer,
                    str(diagnostic.get("rule_id", ""))[:256],
                    str(diagnostic.get("level", diagnostic.get("severity", "")))[:32],
                    float(diagnostic.get("confidence", 1.0)),
                    str(diagnostic.get("source", "deterministic"))[:64],
                    str(diagnostic.get("id", ""))[:128],
                    str(diagnostic.get("type", ""))[:128],
                    str(diagnostic.get("severity", diagnostic.get("level", "")))[:32],
                    str(diagnostic.get("excerpt", "")),
                    str(diagnostic.get("suggestion", "")),
                    str(diagnostic.get("explanation", "")),
                    json.dumps(replacements, ensure_ascii=False, separators=(",", ":")),
                    diagnostic.get("revision") if isinstance(diagnostic.get("revision"), int) else None,
                    start,
                    end,
                    payload_json,
                ))
                if len(batch) == self.max_page_size:
                    connection.executemany(insert_sql, batch)
                    batch.clear()
            if batch:
                connection.executemany(insert_sql, batch)
            connection.execute(
                "UPDATE analyses SET total_findings = ?, counts_json = ? WHERE analysis_id = ?",
                (
                    total,
                    json.dumps(dict(sorted(counts.items())), separators=(",", ":")),
                    analysis_id,
                ),
            )

        if page_size == 0:
            return {
                "analysis_id": analysis_id,
                "total_findings": total,
                "counts_by_analyzer": dict(sorted(counts.items())),
                "diagnostics": [],
                "has_more": total > 0,
                "next_cursor": None,
                "page_size": 0,
                "persisted": True,
            }
        page = self.query_findings(analysis_id, limit=page_size)
        page["persisted"] = True
        return page

    @staticmethod
    def _encode_cursor(secret: bytes, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(secret, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw + signature).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_cursor(secret: bytes, token: Any) -> dict[str, Any]:
        if not isinstance(token, str) or not token or len(token) > 2048:
            raise ValueError("cursor is invalid")
        try:
            padding = "=" * (-len(token) % 4)
            combined = base64.b64decode(
                token + padding, altchars=b"-_", validate=True
            )
            raw, signature = combined[:-32], combined[-32:]
            if len(signature) != 32 or not hmac.compare_digest(
                signature, hmac.new(secret, raw, hashlib.sha256).digest()
            ):
                raise ValueError
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("cursor is invalid") from exc
        if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
            raise ValueError("cursor is invalid")
        return payload

    def _memory_analysis_locked(
        self, analysis_id: str, now: float
    ) -> _MemoryAnalysis | None:
        self._cleanup_memory_locked(now)
        analysis = self._memory.get(analysis_id)
        if analysis is not None and analysis.expires_at - now < self.ttl_seconds / 2:
            analysis.expires_at = now + self.ttl_seconds
        return analysis

    def _query_memory_findings_locked(
        self,
        analysis: _MemoryAnalysis,
        *,
        analyzer_filters: tuple[str, ...],
        range_start: int | None,
        range_end: int | None,
        cursor: Any,
        page_size: int,
    ) -> dict[str, Any]:
        after: tuple[int, int, int] | None = None
        if cursor is not None:
            payload = self._decode_cursor(analysis.cursor_secret, cursor)
            expected = {
                "v": _CURSOR_VERSION,
                "analysis_id": analysis.analysis_id,
                "analyzers": list(analyzer_filters),
                "start_utf16": range_start,
                "end_utf16": range_end,
            }
            if any(payload.get(key) != value for key, value in expected.items()):
                raise ValueError("cursor does not match the requested findings")
            key = payload.get("after")
            if (
                not isinstance(key, list)
                or len(key) != 3
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in key
                )
            ):
                raise ValueError("cursor is invalid")
            after = key[0], key[1], key[2]

        allowed = set(analyzer_filters)

        def matches(item: _MemoryFinding) -> bool:
            return (
                (not allowed or item.analyzer in allowed)
                and (
                    range_start is None
                    or (item.end_utf16 > range_start and item.start_utf16 < range_end)  # type: ignore[operator]
                )
            )

        if range_start is None:
            counts = {
                name: count
                for name, count in analysis.counts.items()
                if not allowed or name in allowed
            }
        else:
            range_counts = Counter(
                item.analyzer for item in analysis.findings if matches(item)
            )
            counts = dict(sorted(range_counts.items()))
        total = sum(counts.values())
        start_index = 0
        if after is not None:
            start_index = bisect.bisect_right(
                analysis.findings, after, key=lambda item: item.sort_key
            )
        selected: list[_MemoryFinding] = []
        for item in analysis.findings[start_index:]:
            if matches(item):
                selected.append(item)
                if len(selected) > page_size:
                    break
        has_more = len(selected) > page_size
        selected = selected[:page_size]
        next_cursor = None
        if has_more and selected:
            next_cursor = self._encode_cursor(
                analysis.cursor_secret,
                {
                    "v": _CURSOR_VERSION,
                    "analysis_id": analysis.analysis_id,
                    "analyzers": list(analyzer_filters),
                    "start_utf16": range_start,
                    "end_utf16": range_end,
                    "after": list(selected[-1].sort_key),
                },
            )
        return {
            "analysis_id": analysis.analysis_id,
            "document_id": analysis.document_id,
            "document_revision": analysis.document_revision,
            "text_hash": analysis.text_hash,
            "total_findings": total,
            "counts_by_analyzer": counts,
            "diagnostics": [item.to_diagnostic() for item in selected],
            "has_more": has_more,
            "next_cursor": next_cursor,
            "page_size": len(selected),
            "persisted": False,
        }

    def query_findings(
        self,
        analysis_id: Any,
        *,
        analyzer: Any = None,
        analyzers: Any = None,
        start_utf16: Any = None,
        end_utf16: Any = None,
        cursor: Any = None,
        limit: Any = config.DEFAULT_FINDING_PAGE_SIZE,
    ) -> dict[str, Any]:
        analysis_id = self.validate_analysis_id(analysis_id)
        analyzer_filters = self._validate_analyzers(analyzer, analyzers)
        range_start, range_end = self._validate_range(start_utf16, end_utf16)
        page_size = self._validate_page_size(limit)
        now = time.time()

        with self._lock:
            memory_analysis = self._memory_analysis_locked(analysis_id, now)
            if memory_analysis is not None:
                return self._query_memory_findings_locked(
                    memory_analysis,
                    analyzer_filters=analyzer_filters,
                    range_start=range_start,
                    range_end=range_end,
                    cursor=cursor,
                    page_size=page_size,
                )
            if not self._disk_exists():
                raise ValueError("analysis_id is invalid or expired")

        with self._transaction() as connection:
            connection.execute("DELETE FROM analyses WHERE expires_at <= ?", (now,))
            analysis = connection.execute(
                "SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
            if analysis is None:
                raise ValueError("analysis_id is invalid or expired")
            if float(analysis["expires_at"]) - now < self.ttl_seconds / 2:
                connection.execute(
                    "UPDATE analyses SET expires_at = ? WHERE analysis_id = ?",
                    (now + self.ttl_seconds, analysis_id),
                )

            after: tuple[int, int, int] | None = None
            if cursor is not None:
                payload = self._decode_cursor(analysis["cursor_secret"], cursor)
                expected = {
                    "v": _CURSOR_VERSION,
                    "analysis_id": analysis_id,
                    "analyzers": list(analyzer_filters),
                    "start_utf16": range_start,
                    "end_utf16": range_end,
                }
                if any(payload.get(key) != value for key, value in expected.items()):
                    raise ValueError("cursor does not match the requested findings")
                key = payload.get("after")
                if (
                    not isinstance(key, list)
                    or len(key) != 3
                    or any(isinstance(value, bool) or not isinstance(value, int) for value in key)
                ):
                    raise ValueError("cursor is invalid")
                after = (key[0], key[1], key[2])

            conditions = ["analysis_id = ?"]
            parameters: list[Any] = [analysis_id]
            if analyzer_filters:
                placeholders = ",".join("?" for _ in analyzer_filters)
                conditions.append(f"analyzer IN ({placeholders})")
                parameters.extend(analyzer_filters)
            if range_start is not None:
                conditions.extend(["end_utf16 > ?", "start_utf16 < ?"])
                parameters.extend([range_start, range_end])
            filter_sql = " AND ".join(conditions)

            if range_start is None:
                stored_counts = json.loads(analysis["counts_json"])
                counts = {
                    name: int(count)
                    for name, count in stored_counts.items()
                    if not analyzer_filters or name in analyzer_filters
                }
            else:
                count_rows = connection.execute(
                    f"SELECT analyzer, COUNT(*) AS count FROM findings WHERE {filter_sql} GROUP BY analyzer ORDER BY analyzer",  # noqa: E501
                    parameters,
                ).fetchall()
                counts = {row["analyzer"]: row["count"] for row in count_rows}
            total = sum(counts.values())

            page_conditions = list(conditions)
            page_parameters = list(parameters)
            if after is not None:
                page_conditions.append(
                    "(start_utf16 > ? OR "
                    "(start_utf16 = ? AND end_utf16 > ?) OR "
                    "(start_utf16 = ? AND end_utf16 = ? AND ordinal > ?))"
                )
                page_parameters.extend(
                    [after[0], after[0], after[1], after[0], after[1], after[2]]
                )
            page_parameters.append(page_size + 1)
            finding_rows = connection.execute(
                f"""
                SELECT ordinal, analyzer, rule_id, level, confidence, source,
                       diagnostic_id, finding_type, severity, excerpt, suggestion,
                       explanation, replacements_json, revision,
                       start_utf16, end_utf16, payload_json
                FROM findings
                WHERE {' AND '.join(page_conditions)}
                ORDER BY start_utf16, end_utf16, ordinal
                LIMIT ?
                """,
                page_parameters,
            ).fetchall()

            has_more = len(finding_rows) > page_size
            finding_rows = finding_rows[:page_size]
            diagnostics = [self._diagnostic_from_row(row) for row in finding_rows]
            next_cursor = None
            if has_more and finding_rows:
                last = finding_rows[-1]
                next_cursor = self._encode_cursor(
                    analysis["cursor_secret"],
                    {
                        "v": _CURSOR_VERSION,
                        "analysis_id": analysis_id,
                        "analyzers": list(analyzer_filters),
                        "start_utf16": range_start,
                        "end_utf16": range_end,
                        "after": [
                            last["start_utf16"],
                            last["end_utf16"],
                            last["ordinal"],
                        ],
                    },
                )
            return {
                "analysis_id": analysis_id,
                "document_id": analysis["document_id"],
                "document_revision": analysis["document_revision"],
                "text_hash": analysis["text_hash"],
                "total_findings": total,
                "counts_by_analyzer": counts,
                "diagnostics": diagnostics,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "page_size": len(diagnostics),
                "persisted": True,
            }

    @staticmethod
    def _diagnostic_from_row(row: sqlite3.Row) -> dict[str, Any]:
        legacy = json.loads(row["payload_json"])
        if legacy:
            return legacy
        value: dict[str, Any] = {
            "id": row["diagnostic_id"],
            "analyzer": row["analyzer"],
            "rule_id": row["rule_id"],
            "type": row["finding_type"],
            "severity": row["severity"],
            "level": row["level"],
            "start_utf16": row["start_utf16"],
            "end_utf16": row["end_utf16"],
            "excerpt": row["excerpt"],
            "suggestion": row["suggestion"],
            "explanation": row["explanation"],
            "confidence": float(row["confidence"]),
            "source": row["source"],
            "replacements": json.loads(row["replacements_json"]),
            "revision": row["revision"],
        }
        return value

    def dispose_analysis(self, analysis_id: Any) -> bool:
        analysis_id = self.validate_analysis_id(analysis_id)
        with self._lock:
            if self._drop_memory_locked(analysis_id):
                return True
            if not self._disk_exists():
                return False
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM analyses WHERE analysis_id = ?", (analysis_id,)
            )
            return cursor.rowcount > 0

    def dispose_document(self, document_id: str) -> int:
        with self._lock:
            memory_ids = [
                analysis_id
                for analysis_id, analysis in self._memory.items()
                if analysis.document_id == document_id
            ]
            for analysis_id in memory_ids:
                self._drop_memory_locked(analysis_id)
            if not self._disk_exists():
                return len(memory_ids)
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM analyses WHERE document_id = ?", (document_id,)
            )
            return len(memory_ids) + cursor.rowcount

    def _query_memory_overlay_locked(
        self,
        analysis: _MemoryAnalysis,
        *,
        category_filters: tuple[str, ...],
        cursor: Any,
        page_size: int,
    ) -> dict[str, Any]:
        after: tuple[int, int, int] | None = None
        if cursor is not None:
            payload = self._decode_cursor(analysis.cursor_secret, cursor)
            if (
                payload.get("kind") != "overlay"
                or payload.get("analysis_id") != analysis.analysis_id
                or payload.get("categories") != list(category_filters)
            ):
                raise ValueError("cursor does not match the requested overlay spans")
            key = payload.get("after")
            if (
                not isinstance(key, list)
                or len(key) != 3
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in key
                )
            ):
                raise ValueError("cursor is invalid")
            after = key[0], key[1], key[2]

        allowed = set(category_filters)
        start_index = 0
        if after is not None:
            start_index = bisect.bisect_right(
                analysis.findings, after, key=lambda item: item.sort_key
            )
        selected: list[_MemoryFinding] = []
        for item in analysis.findings[start_index:]:
            if not allowed or item.analyzer in allowed:
                selected.append(item)
                if len(selected) > page_size:
                    break
        has_more = len(selected) > page_size
        selected = selected[:page_size]
        category_names = sorted({item.analyzer for item in selected})
        category_index = {name: index for index, name in enumerate(category_names)}
        rules: list[dict[str, Any]] = []
        rule_index: dict[tuple[Any, ...], int] = {}
        spans: list[list[Any]] = []
        for item in selected:
            key = (
                item.analyzer,
                item.rule_id,
                item.level,
                item.confidence,
                item.source,
            )
            index = rule_index.get(key)
            if index is None:
                index = len(rules)
                rule_index[key] = index
                rules.append(
                    {
                        "category": category_index[item.analyzer],
                        "rule_id": item.rule_id,
                        "level": item.level,
                        "confidence": item.confidence,
                        "source": item.source,
                    }
                )
            spans.append([item.start_utf16, item.end_utf16, index])
            # Related occurrences (e.g., the earlier half of a repetition
            # pair) paint as additional spans of the same rule; the overlay
            # renderer is span-agnostic.
            for extra_start, extra_end in json.loads(item.extra_spans_json):
                spans.append([extra_start, extra_end, index])
        next_cursor = None
        if has_more and selected:
            next_cursor = self._encode_cursor(
                analysis.cursor_secret,
                {
                    "v": _CURSOR_VERSION,
                    "kind": "overlay",
                    "analysis_id": analysis.analysis_id,
                    "categories": list(category_filters),
                    "after": list(selected[-1].sort_key),
                },
            )
        counts = {
            name: count
            for name, count in analysis.counts.items()
            if not allowed or name in allowed
        }
        return {
            "analysis_id": analysis.analysis_id,
            "document_id": analysis.document_id,
            "document_revision": analysis.document_revision,
            "text_hash": analysis.text_hash,
            "total_spans": sum(counts.values()),
            "counts_by_category": counts,
            "categories": category_names,
            "rules": rules,
            "spans": spans,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "page_size": len(spans),
            "persisted": False,
        }

    def query_overlay_spans(
        self,
        analysis_id: Any,
        *,
        categories: Any = None,
        cursor: Any = None,
        limit: Any = config.DEFAULT_OVERLAY_PAGE_SIZE,
    ) -> dict[str, Any]:
        analysis_id = self.validate_analysis_id(analysis_id)
        category_filters = self._validate_analyzers(None, categories)
        page_size = self._validate_page_size(
            limit, maximum=config.MAX_OVERLAY_PAGE_SIZE
        )
        now = time.time()
        with self._lock:
            memory_analysis = self._memory_analysis_locked(analysis_id, now)
            if memory_analysis is not None:
                return self._query_memory_overlay_locked(
                    memory_analysis,
                    category_filters=category_filters,
                    cursor=cursor,
                    page_size=page_size,
                )
            if not self._disk_exists():
                raise ValueError("analysis_id is invalid or expired")
        with self._transaction() as connection:
            connection.execute("DELETE FROM analyses WHERE expires_at <= ?", (now,))
            analysis = connection.execute(
                "SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
            if analysis is None:
                raise ValueError("analysis_id is invalid or expired")
            if float(analysis["expires_at"]) - now < self.ttl_seconds / 2:
                connection.execute(
                    "UPDATE analyses SET expires_at = ? WHERE analysis_id = ?",
                    (now + self.ttl_seconds, analysis_id),
                )

            after: tuple[int, int, int] | None = None
            if cursor is not None:
                payload = self._decode_cursor(analysis["cursor_secret"], cursor)
                if (
                    payload.get("kind") != "overlay"
                    or payload.get("analysis_id") != analysis_id
                    or payload.get("categories") != list(category_filters)
                ):
                    raise ValueError("cursor does not match the requested overlay spans")
                key = payload.get("after")
                if (
                    not isinstance(key, list)
                    or len(key) != 3
                    or any(isinstance(value, bool) or not isinstance(value, int) for value in key)
                ):
                    raise ValueError("cursor is invalid")
                after = (key[0], key[1], key[2])

            conditions = ["analysis_id = ?"]
            parameters: list[Any] = [analysis_id]
            if category_filters:
                placeholders = ",".join("?" for _ in category_filters)
                conditions.append(f"analyzer IN ({placeholders})")
                parameters.extend(category_filters)
            if after is not None:
                conditions.append(
                    "(start_utf16 > ? OR "
                    "(start_utf16 = ? AND end_utf16 > ?) OR "
                    "(start_utf16 = ? AND end_utf16 = ? AND ordinal > ?))"
                )
                parameters.extend(
                    [after[0], after[0], after[1], after[0], after[1], after[2]]
                )
            parameters.append(page_size + 1)
            rows = connection.execute(
                f"""
                SELECT ordinal, analyzer, rule_id, level, confidence, source,
                       start_utf16, end_utf16, payload_json
                FROM findings
                WHERE {' AND '.join(conditions)}
                ORDER BY start_utf16, end_utf16, ordinal
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            has_more = len(rows) > page_size
            rows = rows[:page_size]
            category_names = sorted({row["analyzer"] for row in rows})
            category_index = {name: index for index, name in enumerate(category_names)}
            rules: list[dict[str, Any]] = []
            rule_index: dict[tuple[Any, ...], int] = {}
            spans: list[list[Any]] = []
            for row in rows:
                key = (
                    row["analyzer"], row["rule_id"], row["level"],
                    float(row["confidence"]), row["source"],
                )
                index = rule_index.get(key)
                if index is None:
                    index = len(rules)
                    rule_index[key] = index
                    rules.append({
                        "category": category_index[row["analyzer"]],
                        "rule_id": row["rule_id"],
                        "level": row["level"],
                        "confidence": float(row["confidence"]),
                        "source": row["source"],
                    })
                spans.append([row["start_utf16"], row["end_utf16"], index])
                # Related occurrences (e.g., the earlier half of a repetition
                # pair) paint as additional spans of the same rule; persisted
                # in payload_json at snapshot time.
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, ValueError):
                    payload = {}
                for extra_span in payload.get("extra_spans_utf16", []) or []:
                    if (
                        isinstance(extra_span, list)
                        and len(extra_span) == 2
                        and all(
                            isinstance(value, int) and not isinstance(value, bool)
                            for value in extra_span
                        )
                    ):
                        spans.append([extra_span[0], extra_span[1], index])

            next_cursor = None
            if has_more and rows:
                last = rows[-1]
                next_cursor = self._encode_cursor(
                    analysis["cursor_secret"],
                    {
                        "v": _CURSOR_VERSION,
                        "kind": "overlay",
                        "analysis_id": analysis_id,
                        "categories": list(category_filters),
                        "after": [
                            last["start_utf16"], last["end_utf16"], last["ordinal"]
                        ],
                    },
                )
            stored_counts = json.loads(analysis["counts_json"])
            counts = {
                name: int(count)
                for name, count in stored_counts.items()
                if not category_filters or name in category_filters
            }
            return {
                "analysis_id": analysis_id,
                "document_id": analysis["document_id"],
                "document_revision": analysis["document_revision"],
                "text_hash": analysis["text_hash"],
                "total_spans": sum(counts.values()),
                "counts_by_category": counts,
                "categories": category_names,
                "rules": rules,
                "spans": spans,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "page_size": len(spans),
                "persisted": True,
            }
