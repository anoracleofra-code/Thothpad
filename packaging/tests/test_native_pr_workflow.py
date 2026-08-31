#!/usr/bin/env python3
"""Static regression checks for the hosted native PR workflow."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class NativePrWorkflowTest(unittest.TestCase):
    def test_windows_gettext_patch_restores_printf_n_crash_guard(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "native-pr.yml").read_text(
            encoding="utf-8"
        )

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


if __name__ == "__main__":
    unittest.main()
