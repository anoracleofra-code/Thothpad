from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NETWORK = _module("thothpad_network_evidence", "packaging/network_evidence.py")
PACKAGE = _module("thothpad_package_contract", "packaging/package_contract.py")
UPDATE = _module("thothpad_update_manifest", "packaging/update_manifest.py")


class ReleasePhases4655Test(unittest.TestCase):
    def test_phase49_network_capture_requires_real_monitored_zero_connection_evidence(self) -> None:
        report = NETWORK.validate_network_capture_evidence(
            {
                "process_tree_monitored": True,
                "deterministic_tcp_connections": 0,
                "tcp_samples": 24,
                "tree_monitor_seconds": 6.0,
                "installed_and_launched": True,
                "bundled_engine_started": True,
            }
        )
        self.assertTrue(report["all_green"])
        bad = NETWORK.validate_network_capture_evidence(
            {
                "process_tree_monitored": True,
                "deterministic_tcp_connections": 1,
                "tcp_samples": 24,
                "tree_monitor_seconds": 6.0,
                "installed_and_launched": True,
                "bundled_engine_started": True,
            }
        )
        self.assertFalse(bad["all_green"])
        runtime = NETWORK.validate_network_capture_evidence(
            {
                "process_tree_monitored": True,
                "deterministic_tcp_connections": 0,
                "tcp_samples": 12,
                "tree_monitor_seconds": 3.0,
                "application_launched": True,
                "bundled_engine_started": True,
            }
        )
        self.assertTrue(runtime["all_green"])

    def test_phase53_package_contract_binds_candidate_commit_and_sbom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate.json"
            sbom = root / "sbom.json"
            commit = "a" * 40
            candidate.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "provenance": {"source_commit": commit, "toolchain_lock_sha256": "d" * 64},
                        "files": {"writer-engine.exe": {"sha256": "b" * 64, "size": 10}},
                    }
                ),
                encoding="utf-8",
            )
            sbom.write_text(
                json.dumps(
                    {
                        "bomFormat": "CycloneDX",
                        "specVersion": "1.5",
                        "metadata": {"component": {"name": "ThothPad"}},
                        "components": [
                            {
                                "type": "file",
                                "name": "writer-engine.exe",
                                "hashes": [{"alg": "SHA-256", "content": "b" * 64}],
                            },
                            *[{"type": "library", "name": f"c{i}"} for i in range(7)],
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = PACKAGE.validate_candidate_contract(
                candidate_manifest=candidate,
                sbom=sbom,
                expected_commit=commit,
            )
            self.assertTrue(report["all_green"])
            with self.assertRaisesRegex(ValueError, "hexadecimal"):
                PACKAGE.validate_candidate_contract(
                    candidate_manifest=candidate,
                    sbom=sbom,
                    expected_commit="z" * 40,
                )

    def test_phase54_update_manifest_is_https_checksum_bound_and_tamper_detecting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "candidate.zip"
            artifact.write_bytes(b"release-candidate")
            manifest = UPDATE.build_update_manifest(
                version="0.1.2",
                channel="beta",
                artifact_url="https://updates.example.invalid/ThothPad-0.1.2.zip",
                artifact=artifact,
                source_commit="c" * 40,
            )
            self.assertTrue(UPDATE.verify_update_artifact(manifest, artifact)["all_green"])
            manually_broken = dict(manifest)
            manually_broken["artifact_url"] = "http://updates.example.invalid/ThothPad-0.1.2.zip"
            self.assertFalse(UPDATE.verify_update_artifact(manually_broken, artifact)["all_green"])
            artifact.write_bytes(b"tampered")
            self.assertFalse(UPDATE.verify_update_artifact(manifest, artifact)["all_green"])


if __name__ == "__main__":
    unittest.main()
