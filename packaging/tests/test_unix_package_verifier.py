#!/usr/bin/env python3
"""Unit tests for the Linux/macOS packaged acceptance verifier."""

from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("verify_unix_package.py")
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("verify_unix_package", SCRIPT)
assert SPEC and SPEC.loader
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class UnixPackageVerifierTest(unittest.TestCase):
    def test_release_workflow_wires_target_artifact_gates(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "native-release.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(1, workflow.count("--format appimage"))
        self.assertEqual(1, workflow.count("--format flatpak"))
        self.assertEqual(1, workflow.count("--format dmg"))
        self.assertEqual(2, workflow.count("--launch-trials 100"))
        self.assertEqual(1, workflow.count("--launch-trials 1 "))
        self.assertIn("acceptance-$slug-appimage.json", workflow)
        self.assertIn("acceptance-$slug-flatpak-smoke.json", workflow)
        self.assertIn("acceptance-$slug-macos.json", workflow)
        for script in (
            "packaging/linux/package-appimage.sh",
            "packaging/linux/package-flatpak.sh",
            "packaging/macos/package-dmg.sh",
        ):
            self.assertIn(f"bash -n {script}", workflow)
        verifier = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("QT_QPA_PLATFORM", verifier)
        self.assertIn("writer-engine", verifier)
        self.assertIn("tcp_sockets", verifier)

    def make_linux_stage(self, root: Path, variant: str, webengine: bool) -> None:
        for relative in ("usr/bin/thothpad", "usr/bin/writer-engine/writer-engine"):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test", encoding="utf-8")
            path.chmod(0o755)
        profile = root / "usr/share/thothpad/ThothPad-0.1.2.package.json"
        profile.parent.mkdir(parents=True)
        profile.write_text(json.dumps({"variant": variant}), encoding="utf-8")
        if webengine:
            marker = root / "usr/lib/libQt6WebEngineCore.so.6"
            marker.parent.mkdir(parents=True)
            marker.write_bytes(b"test")

    def test_core_and_full_layouts_are_distinguished(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            core = Path(directory) / "core"
            full = Path(directory) / "full"
            self.make_linux_stage(core, "Core", False)
            self.make_linux_stage(full, "Full", True)
            self.assertEqual("Core", VERIFIER.validate_layout(core, "Core", "linux")["profile_variant"])
            self.assertEqual(1, VERIFIER.validate_layout(full, "Full", "linux")["webengine_file_count"])

    def test_core_rejects_webengine_and_variant_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            self.make_linux_stage(stage, "Core", True)
            with self.assertRaisesRegex(VERIFIER.AcceptanceError, "contains Qt WebEngine"):
                VERIFIER.validate_layout(stage, "Core", "linux")
            with self.assertRaisesRegex(VERIFIER.AcceptanceError, "expected 'Full'"):
                VERIFIER.validate_layout(stage, "Full", "linux")

    def test_flatpak_full_accepts_declared_composed_webengine_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            self.make_linux_stage(stage, "Full", False)
            profile = next(stage.rglob("*.package.json"))
            profile.write_text(
                json.dumps({"variant": "Full", "webengine_policy": "included"}),
                encoding="utf-8",
            )
            result = VERIFIER.validate_layout(
                stage, "Full", "linux", composed_runtime=True
            )
            self.assertTrue(result["webengine_from_composed_runtime"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc fixture")
    def test_linux_tcp_scan_ignores_processes_without_tcp_sockets(self) -> None:
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
        try:
            self.assertEqual([], VERIFIER.linux_tcp_sockets({process.pid}))
        finally:
            process.terminate()
            process.wait(timeout=2)

    def test_launch_trial_bounds_are_enforced(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            VERIFIER.parse_args([
                "--format", "appimage", "--artifact", "x", "--variant", "Core",
                "--launch-trials", "0", "--evidence", "evidence.json",
            ])


if __name__ == "__main__":
    unittest.main()
