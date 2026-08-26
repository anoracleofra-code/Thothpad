from __future__ import annotations

import importlib.util
import hashlib
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "thothpad_generate_sbom", ROOT / "packaging" / "generate_sbom.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SbomLockTest(unittest.TestCase):
    def test_uv_lock_supplies_packages_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "uv.lock"
            lock.write_text(
                """
version = 1

[[package]]
name = "example-package"
version = "1.2.3"
sdist = { url = "https://example.invalid/example.tar.gz", hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }
""",
                encoding="utf-8",
            )
            components = MODULE.locked_python_components(lock)

        self.assertEqual(1, len(components))
        self.assertEqual("pkg:pypi/example-package@1.2.3", components[0]["purl"])
        self.assertEqual("uv-lock", components[0]["properties"][0]["value"])
        self.assertEqual(64, len(components[0]["hashes"][0]["content"]))

    def test_release_lock_is_complete_enough_for_desktop_runtime(self) -> None:
        components = MODULE.locked_python_components(ROOT / "writer-engine" / "uv.lock")
        names = {component["name"] for component in components}
        self.assertGreaterEqual(len(components), 50)
        self.assertTrue({"spacy", "numpy", "pydantic", "pyinstaller"}.issubset(names))

    def test_cargo_lock_supplies_transitive_packages_and_checksums(self) -> None:
        components = MODULE.locked_cargo_components(
            ROOT / "writer-engine" / "harper-bridge" / "Cargo.lock"
        )
        by_purl = {component["purl"]: component for component in components}
        harper = by_purl["pkg:cargo/harper-core@2.5.0"]

        self.assertGreaterEqual(len(components), 500)
        self.assertEqual("cargo-lock", harper["properties"][0]["value"])
        self.assertEqual(
            "acab57352d953e0ab227d4a0a7a8f990e28053f47b8d633711677a83180f51fb",
            harper["hashes"][0]["content"],
        )

    def test_native_libraries_are_individual_hashed_file_components(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "plugins" / "example.dll"
            library.parent.mkdir()
            library.write_bytes(b"native-library")
            output = root / "sbom.json"
            components = MODULE.file_components(root, output)

        self.assertEqual(1, len(components))
        self.assertEqual("file:plugins/example.dll", components[0]["bom-ref"])
        self.assertEqual(
            hashlib.sha256(b"native-library").hexdigest(),
            components[0]["hashes"][0]["content"],
        )
        properties = {item["name"]: item["value"] for item in components[0]["properties"]}
        self.assertEqual("true", properties["thothpad:native-library"])

    def test_duplicate_declared_and_locked_components_are_merged(self) -> None:
        declared = MODULE.declared_component(
            "Harper|2.5.0|Apache-2.0|pkg:cargo/harper-core@2.5.0"
        )
        locked = {
            "type": "library",
            "bom-ref": "pkg:cargo/harper-core@2.5.0",
            "name": "harper-core",
            "version": "2.5.0",
            "licenses": [MODULE.license_entry("NOASSERTION")],
            "hashes": [{"alg": "SHA-256", "content": "a" * 64}],
        }
        components = MODULE.merge_components([[declared], [locked]])

        self.assertEqual(1, len(components))
        self.assertEqual("Apache-2.0", components[0]["licenses"][0]["license"]["id"])
        self.assertEqual("a" * 64, components[0]["hashes"][0]["content"])


if __name__ == "__main__":
    unittest.main()
