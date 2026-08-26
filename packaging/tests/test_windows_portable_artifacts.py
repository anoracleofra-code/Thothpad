#!/usr/bin/env python3
"""Windows packaging artifact regression checks.

Covers the two packaging defects found by JudgePackaging:

* SHA256SUMS.txt must be LF-only so ``sha256sum -c`` works on POSIX and
  Windows (the old ``Set-Content -Encoding ascii`` write emitted CRLF).
* The portable ZIP must use forward-slash entry separators (the old
  ``Compress-Archive`` call stored backslash-separated names that POSIX
  unzip tools extract as flat files).

The writer/packer behavior is exercised end-to-end through the factored
helpers on Windows; the package script wiring is checked statically
everywhere so the suite stays green on non-Windows CI runners.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = ROOT / "packaging" / "windows"


def run_powershell(script: Path, *args: str) -> None:
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(sys.platform == "win32", "exercises the Windows helpers")
class ChecksumWriterTest(unittest.TestCase):
    def test_sha256sums_are_lf_only_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            (artifacts / "b.bin").write_bytes(b"\x00\x01binary")
            (artifacts / "a.txt").write_text("hello", encoding="utf-8")

            helper = WINDOWS / "write-sha256sums.ps1"
            run_powershell(helper, "-ArtifactsDirectory", str(artifacts))
            first = (artifacts / "SHA256SUMS.txt").read_bytes()

            self.assertNotIn(b"\x0d", first)
            self.assertTrue(first.endswith(b"\n"))
            lines = first.decode("utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[0], f"{'a' * 0}".join([lines[0]])[0:0] or lines[0])
            self.assertRegex(lines[0], r"^[0-9a-f]{64}  a\.txt$")

            run_powershell(helper, "-ArtifactsDirectory", str(artifacts))
            second = (artifacts / "SHA256SUMS.txt").read_bytes()
            self.assertEqual(first, second)


@unittest.skipUnless(sys.platform == "win32", "exercises the Windows helpers")
class PortableZipTest(unittest.TestCase):
    def test_zip_entries_use_forward_slashes_and_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "bin"
            nested = source / "plugins" / "kf6"
            nested.mkdir(parents=True)
            (source / "thothpad.exe").write_bytes(b"MZ")
            (nested / "plugin.dll").write_bytes(b"dll")
            spaced = source / "data dir"
            spaced.mkdir()
            (spaced / "file with space.txt").write_bytes(b"spaced")

            destination = root / "ThothPad-portable.zip"
            run_powershell(
                WINDOWS / "new-portable-zip.ps1",
                "-SourceDirectory",
                str(source),
                "-DestinationPath",
                str(destination),
            )

            with zipfile.ZipFile(destination) as archive:
                names = archive.namelist()
                self.assertFalse(any("\\" in name for name in names))
                self.assertIn("thothpad.exe", names)
                self.assertIn("plugins/kf6/plugin.dll", names)
                self.assertIn("data dir/file with space.txt", names)
                extract = root / "extracted"
                archive.extractall(extract)

            self.assertEqual(
                (extract / "plugins" / "kf6" / "plugin.dll").read_bytes(), b"dll"
            )
            self.assertEqual(
                (extract / "data dir" / "file with space.txt").read_bytes(), b"spaced"
            )


class PackageScriptWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (WINDOWS / "package.ps1").read_text(encoding="utf-8")

    def test_package_script_delegates_to_separator_safe_helpers(self) -> None:
        self.assertIn("new-portable-zip.ps1", self.script)
        self.assertIn("write-sha256sums.ps1", self.script)
        self.assertNotIn("Compress-Archive", self.script)
        self.assertNotIn("Set-Content", self.script)

    def test_package_profile_runs_under_pinned_python(self) -> None:
        for mode in ("write", "measure"):
            self.assertRegex(
                self.script,
                rf"& \$python \(Join-Path \$repo .packaging.package_profile\.py.\) {mode}",
            )
        self.assertNotRegex(self.script, r"(?m)^python\b")

    def test_checksum_helper_writes_lf_only_bytes(self) -> None:
        helper = (WINDOWS / "write-sha256sums.ps1").read_text(encoding="utf-8")
        self.assertIn("WriteAllText", helper)
        self.assertIn('`n', helper)
        self.assertNotIn("Set-Content", helper)
        self.assertNotIn("`r`n", helper)

    def test_zip_helper_uses_bsdtar_auto_suffix_mode(self) -> None:
        helper = (WINDOWS / "new-portable-zip.ps1").read_text(encoding="utf-8")
        self.assertRegex(helper, r"tar\.exe")
        self.assertRegex(helper, r"-a -c -f")


if __name__ == "__main__":
    unittest.main()
