# Building ThothPad

## ThothPad Engine

Python 3.11 or newer and Rust 1.95 are required to build the sidecar and its bundled Harper bridge. The application release bundles the resulting one-directory artifact; end users need neither runtime.

```powershell
cd writer-engine
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,desktop]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m backend.build_sidecar
```

The Windows output is `writer-engine/dist/writer-engine/writer-engine.exe` plus its `_internal` directory. Build the sidecar independently on each target operating system; PyInstaller artifacts are not cross-platform.

## Native Application

Required dependencies are CMake, Ninja, Qt 6.5+, ECM 6, KF6 ConfigWidgets/CoreAddons/Sonnet/WidgetsAddons/XmlGui, Qt WebEngine, and QtKeychain.

```powershell
cmake --preset release `
  -DTHOTHPAD_BUNDLE_ENGINE=ON `
  -DTHOTHPAD_ENGINE_DIR="$PWD/writer-engine/dist/writer-engine"
cmake --build --preset release
$env:QT_QPA_PLATFORM = "offscreen"
ctest --test-dir build-release --output-on-failure
cmake --install build-release --prefix stage
```

KDE Craft with MSVC 2022 is the supported Windows release environment. Linux release jobs should use the KDE Flatpak runtime or a distribution containing KF6. macOS releases require a KF6-capable Craft environment, application signing, and notarization.

The verified Windows baseline uses Craft ABI `windows-cl-msvc2022-x86_64`,
Qt 6.11.1, KF6 6.28, MSVC 2022, and Ninja. The Craft source revision used for
the 0.1.0 development package is recorded in the release evidence as
`a7c3f639b3a84d2033c76857ad151e34df6133c9`.

## Platform Packages

```powershell
# Windows portable ZIP, per-user NSIS installer, SBOM, and acceptance smoke
.\packaging\windows\package.ps1 -BuildDir build-release
.\packaging\windows\verify-package.ps1
```

```bash
# Linux, on a Linux host with a platform-local frozen engine
bash packaging/linux/package-flatpak.sh
bash packaging/linux/package-appimage.sh

# macOS, on a macOS host with a platform-local frozen engine
bash packaging/macos/package-dmg.sh
```

See `packaging/README.md` for runner prerequisites, signing variables, and
artifact contents. The release scripts refuse to delete staging paths outside
the repository's `release` directory.

All platform packages run `packaging/generate_sbom.py` against their staged
tree. The resulting CycloneDX 1.5 document includes purls, versions, licenses,
Python `dist-info` metadata, and file hashes. Windows acceptance rejects a
package whose SBOM lacks the expected Qt, KF6, QtKeychain, cmark-gfm, Python,
and spaCy identities.

## Release Gates

- Build and test the native editor on all three platforms.
- Run `writer-engine/benchmarks/benchmark_analysis.py` on the Windows reference machine.
- Verify the frozen sidecar with `initialize`, `analyze_document`, `cancel`, and `shutdown` frames.
- Generate an SBOM from the staged application and retain all `LICENSES/` files and third-party notices.
- Confirm with network capture that typing and deterministic reports produce no traffic.
- Sign/notarize only after the unsigned artifacts pass clean-machine installation and Unicode-path tests.
- Run independent architecture, analyzer-quality, UX/accessibility,
  security/privacy, performance/reliability, and packaging reviews. Record
  PASS/FAIL evidence under `quality-gates/` and rerun every failed review after
  corrections.
