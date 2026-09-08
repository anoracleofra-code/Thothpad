from __future__ import annotations

import hashlib
import importlib
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from backend import config

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCAL = threading.local()
_DEFAULT_TIMEOUT_SECONDS = 30.0


def _resolved_key(root: str | Path) -> tuple[str, Path]:
    resolved = Path(root).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("story project root must be a directory")
    # normcase keeps differently-cased Windows spellings from getting separate
    # locks for the same project while remaining a no-op on case-sensitive POSIX.
    key = os.path.normcase(str(resolved))
    return key, resolved


def _thread_depths() -> dict[str, int]:
    depths = getattr(_THREAD_LOCAL, "depths", None)
    if not isinstance(depths, dict):
        depths = {}
        _THREAD_LOCAL.depths = depths
    return depths


def _lock_path(key: str) -> Path:
    digest = hashlib.sha256(os.fsencode(key)).hexdigest()
    return config.DATA_DIR / "story-locks" / f"{digest}.lock"


@contextmanager
def _cross_process_lock(path: Path, *, timeout_seconds: float) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())

        deadline = time.monotonic() + max(0.1, timeout_seconds)
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")
            locking = msvcrt.locking
            lock_nonblocking = int(msvcrt.LK_NBLCK)
            lock_unlock = int(msvcrt.LK_UNLCK)

            acquired = False
            while not acquired:
                handle.seek(0)
                try:
                    locking(handle.fileno(), lock_nonblocking, 1)
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for the Story Project operation lock") from None
                    time.sleep(0.025)
                else:
                    acquired = True
            try:
                yield
            finally:
                handle.seek(0)
                locking(handle.fileno(), lock_unlock, 1)
            return

        fcntl = importlib.import_module("fcntl")

        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for the Story Project operation lock") from None
                time.sleep(0.025)
            else:
                break
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def story_project_lock(
    root: str | Path,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> Iterator[Path]:
    """Serialize Story Engine operations for one project across threads/processes.

    The sidecar intentionally serves multiple requests concurrently. Story State
    and Project Understanding, however, contain JSON read-modify-write sections in
    addition to SQLite transactions. SQLite alone cannot serialize those files.
    This lock therefore covers the complete project operation boundary.

    The per-process ``RLock`` makes nested workflows re-entrant (for example,
    proposal review calling the writer mutation API), while the OS file lock
    prevents another local ThothPad/MCP process from racing the same project.
    """

    key, resolved = _resolved_key(root)
    with _LOCKS_GUARD:
        local_lock = _LOCKS.setdefault(key, threading.RLock())

    with local_lock:
        depths = _thread_depths()
        depth = int(depths.get(key, 0))
        if depth:
            depths[key] = depth + 1
            try:
                yield resolved
            finally:
                remaining = int(depths.get(key, 1)) - 1
                if remaining:
                    depths[key] = remaining
                else:
                    depths.pop(key, None)
            return

        depths[key] = 1
        try:
            with _cross_process_lock(_lock_path(key), timeout_seconds=timeout_seconds):
                yield resolved
        finally:
            depths.pop(key, None)
