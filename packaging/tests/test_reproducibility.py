from __future__ import annotations

import importlib.util
import argparse
import copy
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPRO = load("thothpad_reproducibility", ROOT / "packaging" / "reproducibility.py")
VERIFY = load("thothpad_verify_toolchain", ROOT / "packaging" / "verify_toolchain.py")


class ReproducibilityTest(unittest.TestCase):
    def test_generated_metadata_is_normalized_but_payload_changes_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "ThothPad.cdx.json").write_text(
                json.dumps({"metadata": {"timestamp": "one"}, "serialNumber": "one", "value": 7}),
                encoding="utf-8",
            )
            (second / "ThothPad.cdx.json").write_text(
                json.dumps({"metadata": {"timestamp": "two"}, "serialNumber": "two", "value": 7}),
                encoding="utf-8",
            )
            self.assertEqual(REPRO.manifest(first), REPRO.manifest(second))
            (second / "ThothPad.cdx.json").write_text(
                json.dumps({"metadata": {"timestamp": "two"}, "serialNumber": "two", "value": 8}),
                encoding="utf-8",
            )
            self.assertNotEqual(REPRO.manifest(first), REPRO.manifest(second))

    def test_arbitrary_json_timestamp_is_not_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "payload.json").write_text('{"timestamp":"one"}', encoding="utf-8")
            (second / "payload.json").write_text('{"timestamp":"two"}', encoding="utf-8")
            self.assertNotEqual(REPRO.manifest(first), REPRO.manifest(second))

    def test_comparison_receipt_binds_candidates_source_and_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = {
                "schema_version": 1,
                "normalization": "test",
                "files": {"ThothPad.exe": {"sha256": "a" * 64, "size": 1}},
            }
            left = root / "candidate-a.json"
            right = root / "candidate-b.json"
            output = root / "comparison.json"
            lock = root / "toolchain-lock.json"
            lock.write_text('{"schema_version":1}', encoding="utf-8")
            toolchain_hash = REPRO.file_sha256(lock)
            left_snapshot = copy.deepcopy(snapshot)
            left_snapshot["provenance"] = {
                "candidate": "a",
                "builder_id": "runner-a",
                "runner_pool": "repro-a",
                "source_commit": "1" * 40,
                "toolchain_lock_sha256": toolchain_hash,
            }
            right_snapshot = copy.deepcopy(snapshot)
            right_snapshot["provenance"] = {
                "candidate": "b",
                "builder_id": "runner-b",
                "runner_pool": "repro-b",
                "source_commit": "1" * 40,
                "toolchain_lock_sha256": toolchain_hash,
            }
            left.write_text(json.dumps(left_snapshot), encoding="utf-8")
            right.write_text(json.dumps(right_snapshot, indent=2), encoding="utf-8")
            result = REPRO.command_compare(argparse.Namespace(
                left=left,
                right=right,
                output=output,
                source_commit="1" * 40,
                toolchain_lock=lock,
                left_pool="repro-a",
                right_pool="repro-b",
            ))
            self.assertEqual(0, result)
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("1" * 40, receipt["source_commit"])
            self.assertEqual(REPRO.file_sha256(lock), receipt["toolchain_lock_sha256"])
            self.assertEqual(REPRO.file_sha256(left), receipt["candidate_manifests"]["left"])
            self.assertEqual(REPRO.file_sha256(right), receipt["candidate_manifests"]["right"])
            self.assertEqual(
                {"left": "runner-a", "right": "runner-b"}, receipt["builders"]
            )
            self.assertEqual(
                {"left": "repro-a", "right": "repro-b"}, receipt["runner_pools"]
            )

            right_snapshot["provenance"]["builder_id"] = "runner-a"
            right.write_text(json.dumps(right_snapshot), encoding="utf-8")
            with self.assertRaises(SystemExit):
                REPRO.command_compare(argparse.Namespace(
                    left=left,
                    right=right,
                    output=output,
                    source_commit="1" * 40,
                    toolchain_lock=lock,
                    left_pool="repro-a",
                    right_pool="repro-b",
                ))

    def test_toolchain_verifier_rejects_hash_drift_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool"
            path.write_bytes(b"locked")
            VERIFY.verify_file(
                path,
                "14493f5f5470ed48c3f103d917ec52ae9005fa3913128031d0fac2a49ac3cc41",
            )
            path.write_bytes(b"changed")
            with self.assertRaises(SystemExit):
                VERIFY.verify_file(
                    path,
                    "14493f5f5470ed48c3f103d917ec52ae9005fa3913128031d0fac2a49ac3cc41",
                )

    def test_toolchain_lock_records_immutable_release_inputs(self) -> None:
        lock = json.loads((ROOT / "packaging" / "toolchain-lock.json").read_text())
        self.assertEqual(2, lock["schema_version"])
        self.assertRegex(lock["python"]["version"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(
            lock["python"]["runtime_sha256"]["windows"], r"^[0-9a-f]{64}$"
        )
        self.assertRegex(lock["craft"]["core_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(lock["craft"]["blueprints_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(lock["linux"]["linuxdeploy"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(lock["linux"]["linuxdeploy_qt"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(
            lock["linux"]["flatpak"]["runtime_commit_x86_64"], r"^[0-9a-f]{64}$"
        )
        for platform in ("windows", "linux", "macos"):
            for tool in ("cmake", "ninja"):
                self.assertTrue(lock[platform][tool]["version"])
            self.assertTrue(lock[platform]["compiler"]["id"])
            self.assertTrue(lock[platform]["compiler"]["version"])
            for family in ("qt", "kf"):
                self.assertRegex(
                    lock[platform][family]["source_commit"], r"^[0-9a-f]{40}$"
                )
                self.assertTrue(lock[platform][family]["files"])
                for value in lock[platform][family]["files"].values():
                    self.assertRegex(value, r"^[0-9a-f]{64}$")
        self.assertRegex(lock["windows"]["compiler"]["toolset"], r"^v14\d$")
        self.assertRegex(
            lock["windows"]["compiler"]["directory_version"], r"^14\.\d+\.\d+$"
        )
        self.assertTrue(lock["linux"]["linuxdeploy"]["tag"])
        self.assertTrue(lock["linux"]["linuxdeploy_qt"]["tag"])
        self.assertTrue(lock["linux"]["flatpak"]["runtime"])
        self.assertTrue(lock["linux"]["flatpak"]["runtime_version"])
        self.assertTrue(lock["macos"]["xcode_version"])
        self.assertTrue(lock["macos"]["xcode_build"])
        self.assertTrue(lock["windows"]["nsis"]["version"])

    def test_toolchain_schema_fails_closed_when_required_identity_is_missing(self) -> None:
        lock = json.loads((ROOT / "packaging" / "toolchain-lock.json").read_text())
        broken = copy.deepcopy(lock)
        del broken["linux"]["compiler"]["id"]
        with self.assertRaises(SystemExit):
            VERIFY.validate_document(broken)

    def _compare_copies(self, root: Path, left_pool: str, right_pool: str):
        snapshot = {
            "schema_version": 1,
            "normalization": "test",
            "files": {"ThothPad.exe": {"sha256": "a" * 64, "size": 1}},
        }
        lock = root / "toolchain-lock.json"
        lock.write_text('{"schema_version":1}', encoding="utf-8")
        toolchain_hash = REPRO.file_sha256(lock)
        left = root / "candidate-a.json"
        right = root / "candidate-b.json"
        left_document = copy.deepcopy(snapshot)
        left_document["provenance"] = {
            "candidate": "a",
            "builder_id": "runner-a",
            "runner_pool": left_pool,
            "source_commit": "1" * 40,
            "toolchain_lock_sha256": toolchain_hash,
        }
        right_document = copy.deepcopy(snapshot)
        right_document["provenance"] = {
            "candidate": "b",
            "builder_id": "runner-b",
            "runner_pool": right_pool,
            "source_commit": "1" * 40,
            "toolchain_lock_sha256": toolchain_hash,
        }
        left.write_text(json.dumps(left_document), encoding="utf-8")
        right.write_text(json.dumps(right_document), encoding="utf-8")
        return argparse.Namespace(
            left=left,
            right=right,
            output=None,
            source_commit="1" * 40,
            toolchain_lock=lock,
            left_pool="repro-a",
            right_pool="repro-b",
        )

    def test_arbiter_rejects_mislabeled_runner_pool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = self._compare_copies(Path(directory), "repro-b", "repro-b")
            with self.assertRaises(SystemExit):
                REPRO.command_compare(args)

    def test_arbiter_rejects_shared_runner_pool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = self._compare_copies(Path(directory), "repro-a", "repro-a")
            with self.assertRaises(SystemExit):
                REPRO.command_compare(args)

    def test_snapshot_rejects_builder_id_not_matching_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            (stage / "ThothPad.exe").write_bytes(b"app")
            args = argparse.Namespace(
                root=stage,
                output=root / "tree.normalized.json",
                candidate="a",
                builder_id="someone-else",
                runner_pool="repro-a",
                source_commit="1" * 40,
                toolchain_lock=root / "toolchain-lock.json",
            )
            args.toolchain_lock.write_text('{"schema_version":1}', encoding="utf-8")
            with unittest.mock.patch.dict(
                os.environ, {"RUNNER_NAME": "runner-a"}
            ):
                with self.assertRaises(SystemExit):
                    REPRO.command_snapshot(args)

    def test_snapshot_embeds_runner_pool_and_runner_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            (stage / "ThothPad.exe").write_bytes(b"app")
            output = root / "tree.normalized.json"
            lock = root / "toolchain-lock.json"
            lock.write_text('{"schema_version":1}', encoding="utf-8")
            args = argparse.Namespace(
                root=stage,
                output=output,
                candidate="b",
                builder_id="runner-b",
                runner_pool="repro-b",
                source_commit="2" * 40,
                toolchain_lock=lock,
            )
            with unittest.mock.patch.dict(
                os.environ, {"RUNNER_NAME": "runner-b"}
            ):
                self.assertEqual(0, REPRO.command_snapshot(args))
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("runner-b", receipt["provenance"]["builder_id"])
            self.assertEqual("repro-b", receipt["provenance"]["runner_pool"])

    def _write_fake_build(
        self,
        root: Path,
        compiler_id: str,
        version: str,
        compiler_path: str,
    ) -> Path:
        build = root / "build"
        cache_version = ".".join(version.split(".")[:3])
        (build / "CMakeFiles" / cache_version).mkdir(parents=True)
        (build / "CMakeCache.txt").write_text(
            "".join(
                f"CMAKE_CACHE_{part}_VERSION:INTERNAL={value}\n"
                for part, value in zip(
                    ("MAJOR", "MINOR", "PATCH"), version.split(".")
                )
            ),
            encoding="utf-8",
        )
        (build / "CMakeFiles" / cache_version / "CMakeCXXCompiler.cmake").write_text(
            "".join(
                f'set({key} "{value}")\n'
                for key, value in (
                    ("CMAKE_CXX_COMPILER", compiler_path),
                    ("CMAKE_CXX_COMPILER_ID", compiler_id),
                    ("CMAKE_CXX_COMPILER_VERSION", version),
                )
            ),
            encoding="utf-8",
        )
        return build


    def test_compiler_verification_rejects_wrong_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._write_fake_build(
                root, "MSVC", "19.44.35225.0",
                r"C:\VS\VC\Tools\MSVC\14.44.35225\bin\Hostx64\x64\cl.exe",
            )
            entry = {"id": "MSVC", "version": "19.43.35217.0", "toolset": "v143"}
            with self.assertRaises(SystemExit):
                VERIFY.verify_compiler(build, entry)

    def test_compiler_verification_rejects_wrong_toolset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._write_fake_build(
                root, "MSVC", "19.44.35225.0",
                r"C:\VS\VC\Tools\MSVC\14.44.35225\bin\Hostx64\x64\cl.exe",
            )
            entry = {"id": "MSVC", "version": "19.44.35225.0", "toolset": "v142"}
            with self.assertRaises(SystemExit):
                VERIFY.verify_compiler(build, entry)

    def test_compiler_verification_rejects_wrong_toolset_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._write_fake_build(
                root, "MSVC", "19.44.35225.0",
                r"C:\VS\VC\Tools\MSVC\14.43.34808\bin\Hostx64\x64\cl.exe",
            )
            entry = {
                "id": "MSVC",
                "version": "19.44.35225.0",
                "toolset": "v143",
                "directory_version": "14.44.35207",
            }
            with self.assertRaises(SystemExit):
                VERIFY.verify_compiler(build, entry)

    def test_compiler_verification_accepts_locked_msvc_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = self._write_fake_build(
                root, "MSVC", "19.44.35225.0",
                r"C:\VS\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64\cl.exe",
            )
            entry = {
                "id": "MSVC",
                "version": "19.44.35225.0",
                "toolset": "v143",
                "directory_version": "14.44.35207",
            }
            self.assertIsNone(VERIFY.verify_compiler(build, entry))

    def test_tool_version_check_rejects_cmake_and_ninja_drift(self) -> None:
        with self.assertRaises(SystemExit):
            VERIFY.check_tool_version("cmake", "cmake version 4.2.0", "4.3.2")
        with self.assertRaises(SystemExit):
            VERIFY.check_tool_version(
                "ninja", "1.12.1", "1.13.0.git.kitware.jobserver-pipe-1"
            )
        self.assertIsNone(
            VERIFY.check_tool_version(
                "ninja", "1.13.0.git.kitware.jobserver-pipe-1",
                "1.13.0.git.kitware.jobserver-pipe-1",
            )
        )

    def test_nsis_version_check_rejects_drift(self) -> None:
        with self.assertRaises(SystemExit):
            VERIFY.check_nsis_version("v3.10", "3.09")
        self.assertIsNone(VERIFY.check_nsis_version("v3.09", "3.09"))

    def test_flatpak_runtime_check_rejects_commit_drift(self) -> None:
        document = json.loads(
            (ROOT / "packaging" / "toolchain-lock.json").read_text()
        )
        with tempfile.TemporaryDirectory() as directory:
            flatpak = Path(directory) / "flatpak"
            flatpak.write_bytes(b"#!/bin/sh\n")
            original = VERIFY.command_output
            VERIFY.command_output = lambda command, allow_failure=False: "f" * 64
            try:
                with self.assertRaises(SystemExit):
                    VERIFY.verify_flatpak_runtime(document, flatpak)
                VERIFY.command_output = (
                    lambda command, allow_failure=False:
                    document["linux"]["flatpak"]["runtime_commit_x86_64"]
                )
                self.assertIsNone(
                    VERIFY.verify_flatpak_runtime(document, flatpak)
                )
            finally:
                VERIFY.command_output = original

    def test_toolchain_schema_rejects_incomplete_framework_inputs(self) -> None:
        lock = json.loads((ROOT / "packaging" / "toolchain-lock.json").read_text())
        cases = [
            lambda document: document["linux"]["flatpak"].pop(
                "runtime_commit_x86_64"
            ),
            lambda document: document["linux"]["linuxdeploy"].pop("sha256"),
            lambda document: document["windows"]["nsis"]["files"].pop(
                "dev-utils/bin/makensis.exe"
            ),
            lambda document: document["windows"]["compiler"].pop(
                "directory_version"
            ),
            lambda document: document["windows"]["compiler"].pop("toolset"),
            lambda document: document["macos"].pop("xcode_build"),
        ]
        for breakage in cases:
            with self.subTest(breakage=breakage):
                broken = copy.deepcopy(lock)
                breakage(broken)
                with self.assertRaises(SystemExit):
                    VERIFY.validate_document(broken)


if __name__ == "__main__":
    unittest.main()
