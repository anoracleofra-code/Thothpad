from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "thothpad_release_attestation", ROOT / "packaging" / "release_attestation.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleaseAttestationTest(unittest.TestCase):
    def test_attestation_binds_source_toolchain_sbom_candidate_and_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "toolchain.json"
            sbom = root / "sbom.json"
            candidate = root / "candidate.json"
            lock.write_text('{"lock":1}', encoding="utf-8")
            sbom.write_text('{"bomFormat":"CycloneDX"}', encoding="utf-8")
            candidate.write_text('{"files":{"ThothPad.exe":"abc"}}', encoding="utf-8")
            result = MODULE.build_attestation(
                source_commit="a" * 40,
                toolchain_lock=lock,
                sbom=sbom,
                candidate_manifest=candidate,
                test_evidence={"pytest": "PASS", "ctest": "PASS"},
            )
            self.assertEqual("a" * 40, result["source_commit"])
            self.assertEqual(64, len(result["toolchain_lock_sha256"]))
            self.assertEqual(64, len(result["sbom_sha256"]))
            self.assertEqual(64, len(result["candidate_manifest_sha256"]))
            self.assertEqual(64, len(result["attestation_sha256"]))

            changed = dict(result)
            changed["test_evidence"] = {"pytest": "FAIL", "ctest": "PASS"}
            changed.pop("attestation_sha256")
            self.assertNotEqual(result["attestation_sha256"], MODULE.canonical_sha256(changed))

    def test_invalid_source_commit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("lock", "sbom", "candidate"):
                (root / name).write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                MODULE.build_attestation(
                    source_commit="not-a-commit",
                    toolchain_lock=root / "lock",
                    sbom=root / "sbom",
                    candidate_manifest=root / "candidate",
                    test_evidence={},
                )


if __name__ == "__main__":
    unittest.main()
