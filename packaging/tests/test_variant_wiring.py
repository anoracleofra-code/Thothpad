#!/usr/bin/env python3
"""Static regression checks for Core/Full release wiring."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LINUX = ROOT / "packaging" / "linux"


class VariantWiringTest(unittest.TestCase):
    def test_flatpak_manifests_are_explicit_and_webengine_safe(self) -> None:
        core = (LINUX / "org.thothpad.ThothPad.Core.yml").read_text(encoding="utf-8")
        full = (LINUX / "org.thothpad.ThothPad.Full.yml").read_text(encoding="utf-8")

        self.assertNotIn("base:", core)
        self.assertNotIn("qtwebengine", core.lower())
        self.assertIn("-DTHOTHPAD_PACKAGE_VARIANT=Core", core)

        self.assertIn("base: io.qt.qtwebengine.BaseApp", full)
        self.assertIn("QTWEBENGINEPROCESS_PATH", full)
        self.assertIn("-DTHOTHPAD_PACKAGE_VARIANT=Full", full)
        self.assertFalse((LINUX / "org.thothpad.ThothPad.yml").exists())

    def test_packaging_defaults_are_variant_specific(self) -> None:
        appimage = (LINUX / "package-appimage.sh").read_text(encoding="utf-8")
        flatpak = (LINUX / "package-flatpak.sh").read_text(encoding="utf-8")
        macos = (ROOT / "packaging" / "macos" / "package-dmg.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("build-linux-$variant_slug-release", appimage)
        self.assertIn("release/linux/$variant_slug", appimage)
        self.assertIn("org.thothpad.ThothPad.$variant.yml", flatpak)
        self.assertIn("release/linux/$variant_slug", flatpak)
        self.assertIn("build-macos-$variant_slug-release", macos)
        self.assertIn("release/macos/$variant_slug", macos)

        for script in (appimage, flatpak, macos):
            self.assertIn("PACKAGE_VARIANT must be Core or Full", script)
            self.assertRegex(script, r"SHA256SUMS-\$variant")
            self.assertIn("Public release packaging requires a clean Git worktree.", script)

    def test_release_workflow_has_six_noncolliding_matrix_entries(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "native-release.yml").read_text(
            encoding="utf-8"
        )

        arbiter = workflow.split("  reproducibility-arbiter:", 1)[1].split(
            "  attest-release:", 1
        )[0]
        self.assertEqual(6, len(re.findall(r"^          - platform:", arbiter, re.M)))
        self.assertEqual(3, len(re.findall(r"^            variant: Core$", arbiter, re.M)))
        self.assertEqual(3, len(re.findall(r"^            variant: Full$", arbiter, re.M)))
        self.assertEqual(3, workflow.count("variant: [Core, Full]"))
        self.assertEqual(3, workflow.count("candidate: [a, b]"))
        self.assertEqual(
            3,
            len(
                re.findall(
                    r"^\s*runs-on:.*\"repro-\$\{\{ matrix.candidate \}\}\"",
                    workflow,
                    re.M,
                )
            ),
        )
        self.assertEqual(
            4, workflow.count('--runner-pool "repro-${{ matrix.candidate }}"')
        )
        self.assertEqual(2, workflow.count("--left-pool repro-a"))
        self.assertEqual(2, workflow.count("--right-pool repro-b"))
        self.assertEqual(4, workflow.count('--builder-id "${{ runner.name }}"'))
        self.assertEqual(4, workflow.count('--candidate "${{ matrix.candidate }}"'))
        for platform in ("windows", "linux", "macos"):
            self.assertIn(
                f"thothpad-repro-{platform}-${{{{ matrix.variant }}}}", workflow
            )
            self.assertIn(f"release/{platform}/$slug", workflow)
        self.assertIn("-PublicRelease", workflow)
        self.assertIn("-LaunchTrials 100", workflow)
        self.assertIn('PUBLIC_RELEASE: "1"', workflow)
        self.assertIn("reproducibility", workflow)
        self.assertIn("packaging/reproducibility.py compare", workflow)
        self.assertEqual(6, workflow.count('--source-commit "${{ github.sha }}"'))
        self.assertGreaterEqual(
            workflow.count("--toolchain-lock packaging/toolchain-lock.json"), 2
        )
        self.assertEqual(3, workflow.count("cargo clean --manifest-path harper-bridge/Cargo.toml"))
        candidate_jobs = workflow.split("  reproducibility-arbiter:", 1)[0]
        self.assertNotIn("packaging/reproducibility.py compare", candidate_jobs)
        self.assertEqual(2, arbiter.count("actions/download-artifact@"))
        self.assertIn("candidate-a", arbiter)
        self.assertIn("candidate-b", arbiter)

    def test_packaging_docs_do_not_claim_core_is_unbuildable(self) -> None:
        readme = (ROOT / "packaging" / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("uncertified/unbuildable", readme)
        self.assertIn("Core manifest has no BaseApp", readme)

    def test_windows_public_release_rejects_dirty_source(self) -> None:
        script = (ROOT / "packaging" / "windows" / "package.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("[switch]$PublicRelease", script)
        self.assertIn("Public release packaging requires a clean Git worktree.", script)

    def test_windows_packaging_uses_guarded_qt_test_launcher(self) -> None:
        script = (ROOT / "packaging" / "windows" / "package.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('autotest\\run-tests.ps1', script)
        self.assertNotRegex(script, r"(?m)^\s*ctest\s")

    def test_windows_acceptance_supports_certification_launch_count(self) -> None:
        script = (ROOT / "packaging" / "windows" / "verify-package.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("[int]$LaunchTrials = 1", script)
        self.assertIn("$trial -le $LaunchTrials", script)
        self.assertIn("launch_trials = $LaunchTrials", script)

    def test_windows_network_gate_walks_the_full_process_tree(self) -> None:
        script = (ROOT / "packaging" / "windows" / "verify-package.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("function Get-ProcessDescendants", script)
        self.assertIn("[Collections.Generic.Queue[int]]", script)
        self.assertIn("$pending.Enqueue([int]$candidate.ProcessId)", script)
        self.assertIn("$observed.Contains([int]$_.OwningProcess)", script)
        self.assertIn("Get-NetTCPConnection", script)
        self.assertIn("$requiredStableSamples", script)
        self.assertNotIn("$treeIds", script)
        self.assertNotIn("$children.ProcessId", script)

    def test_every_packager_passes_python_and_cargo_locks_to_sbom(self) -> None:
        scripts = (
            ROOT / "packaging" / "windows" / "package.ps1",
            LINUX / "package-appimage.sh",
            LINUX / "package-flatpak.sh",
            ROOT / "packaging" / "macos" / "package-dmg.sh",
        )
        for path in scripts:
            script = path.read_text(encoding="utf-8")
            self.assertIn("uv.lock", script, path.name)
            self.assertIn("Cargo.lock", script, path.name)
            self.assertIn("toolchain-lock.json", script, path.name)
            self.assertIn("--toolchain-lock", script, path.name)


if __name__ == "__main__":
    unittest.main()
