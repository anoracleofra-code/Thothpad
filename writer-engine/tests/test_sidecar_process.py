import subprocess
import sys

from backend.sidecar import PROTOCOL_MAJOR, encode_frame, read_frame


def test_sidecar_process_initializes_and_shuts_down():
    process = subprocess.Popen(
        [sys.executable, "-m", "backend.sidecar"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    initialize = {
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": 0,
        "request_id": "init-1",
        "operation": "initialize",
    }
    process.stdin.write(encode_frame(initialize))
    process.stdin.flush()
    response = read_frame(process.stdout)
    assert response is not None
    assert response["ok"] is True
    assert response["request_id"] == "init-1"
    assert response["result"]["offset_encoding"] == "utf-16"

    shutdown = {
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": 0,
        "request_id": "shutdown-1",
        "operation": "shutdown",
    }
    process.stdin.write(encode_frame(shutdown))
    process.stdin.flush()
    response = read_frame(process.stdout)
    assert response is not None
    assert response["result"]["shutting_down"] is True
    assert process.wait(timeout=5) == 0
    assert process.stderr.read() == b""
