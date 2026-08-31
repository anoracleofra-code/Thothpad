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
        self.assertIn("id: windows-craft-core-cache", workflow)
        self.assertIn("uses: actions/cache/save@", workflow)
        self.assertIn(
            "if: steps.windows-craft-deps-cache.outputs.cache-hit != 'true'",
            workflow,
        )

    def test_windows_craft_blueprints_are_pinned_and_glib_nls_is_disabled(self) -> None:
        workflow = self.workflow()

        self.assertIn("$blueprintsCommit = $lock.craft.blueprints_commit", workflow)
        self.assertIn('checkout --detach $blueprintsCommit', workflow)
        self.assertIn('"-Dnls=disabled"', workflow)
        self.assertIn("Pinned Craft GLib recipe changed", workflow)

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
