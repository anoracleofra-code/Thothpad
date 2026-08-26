"""Direct coverage for the extracted windows process supervision module."""

from __future__ import annotations

import os

import pytest

from backend.process_supervisor import (
    _close_windows_handle,
    _create_windows_kill_job,
    _windows_process_is_alive,
)

pytestmark = pytest.mark.windows


@pytest.mark.skipif(os.name != "nt", reason="windows-only")
def test_current_process_is_alive():
    assert _windows_process_is_alive(os.getpid()) is True


@pytest.mark.skipif(os.name != "nt", reason="windows-only")
def test_bogus_pid_is_not_alive():
    assert _windows_process_is_alive(4_000_000_000) is False


def test_closing_none_handle_is_a_noop():
    _close_windows_handle(None)


@pytest.mark.skipif(os.name != "nt", reason="windows-only")
def test_kill_job_terminates_assigned_child_on_close():
    import subprocess
    import sys
    import time

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
    )
    try:
        assert _windows_process_is_alive(child.pid) is True
        job = _create_windows_kill_job(child.pid)
        _close_windows_handle(job)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not _windows_process_is_alive(child.pid):
                break
            time.sleep(0.05)
        assert _windows_process_is_alive(child.pid) is False
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
