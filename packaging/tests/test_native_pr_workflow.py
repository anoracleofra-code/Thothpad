#!/usr/bin/env python3
"""Static regression checks for the hosted native PR workflow."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class NativePrWorkflowTest(unittest.TestCase):
    def workflow(self) -> str:
        return (ROOT / ".github" / "workflows" / "native-pr.yml").read_text(
            encoding="utf-8"
        )

    def test_windows_craft_dependencies_are_cached(self) -> None:
        workflow = self.workflow()

        self.assertIn("uses: actions/cache/restore@", workflow)
        self.assertIn("id: windows-craft-deps-cache", workflow)
        self.assertIn("id: windows-craft-glib-cache", workflow)
        self.assertIn("id: windows-craft-core-cache", workflow)
        self.assertIn("uses: actions/cache/save@", workflow)
        self.assertIn(
            "if: steps.windows-craft-deps-cache.outputs.cache-hit != 'true'",
            workflow,
        )
        self.assertIn(
            "if: steps.windows-craft-deps-cache.outputs.cache-matched-key == ''",
            workflow,
        )
        self.assertIn(
            "steps.windows-craft-glib-cache.outputs.cache-matched-key == ''",
            workflow,
        )
        self.assertIn(
            "steps.windows-craft-core-cache.outputs.cache-matched-key == ''",
            workflow,
        )
        self.assertIn("hashFiles('packaging/toolchain-lock.json')", workflow)
        self.assertNotIn(
            "hashFiles('packaging/toolchain-lock.json', '.github/workflows/native-pr.yml')",
            workflow,
        )
        self.assertIn("windows-craft-deps-v3-relwithdebinfo-", workflow)
        self.assertIn("windows-craft-glib-v2-relwithdebinfo-", workflow)
        self.assertIn("windows-craft-core-v1-", workflow)

    def test_windows_craft_uses_pinned_relwithdebinfo_binary_cache(self) -> None:
        workflow = self.workflow()

        self.assertIn("$blueprintsCommit = $lock.craft.blueprints_commit", workflow)
        self.assertIn('checkout --detach $blueprintsCommit', workflow)
        self.assertIn("DriveLetter = Z:/", workflow)
        self.assertIn("BuildType = RelWithDebInfo", workflow)
        self.assertIn("FailOnCacheMiss = True", workflow)
        self.assertIn("from Package.VirtualPackageBase import VirtualPackageBase", workflow)
        self.assertIn("if not isinstance(package.instance, VirtualPackageBase):", workflow)
        self.assertIn("is unavailable in the configured binary cache", workflow)
        self.assertIn("$craftPy --use-cache libs/glib", workflow)
        self.assertNotIn("--resolve-deps all --fetch-binary", workflow)
        self.assertNotIn('"-Dnls=disabled"', workflow)
        self.assertLess(
            workflow.index('Invoke-Logged "Craft cached GLib graph"'),
            workflow.index("Save Windows Craft GLib checkpoint"),
        )
        self.assertLess(
            workflow.index('Invoke-Logged "Craft cached GLib graph"'),
            workflow.index('Invoke-Logged "Craft cached extra-cmake-modules graph"'),
        )

    def test_windows_gettext_patch_restores_printf_n_guard(self) -> None:
        workflow = self.workflow()

        self.assertIn(
            "$printfGuardNeedle = '                \"ac_cv_func_memmove=yes\"'",
            workflow,
        )
        self.assertIn(
            "$printfGuardReplacement = '                \"ac_cv_func_memmove=yes\",'",
            workflow,
        )
        self.assertIn(
            "Pinned Craft gettext recipe still concatenates the MSVC printf guard.",
            workflow,
        )

    def test_correctness_suite_does_not_enforce_wall_clock_microbenchmark(self) -> None:
        source = (
            ROOT
            / "autotest"
            / "textformatoverlaycontroller"
            / "textformatoverlaycontrollertest.cpp"
        ).read_text(encoding="utf-8")

        self.assertNotIn("QVERIFY2(p95 <=", source)


if __name__ == "__main__":
    unittest.main()
