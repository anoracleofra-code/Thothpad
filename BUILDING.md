# Building ThothPad

ThothPad has two buildable pieces:

1. the **native Qt/KF6 desktop application**; and
2. the **ThothPad Engine** Python sidecar, whose production desktop build also compiles the Rust/Harper grammar bridge.

You can build the native editor without freezing the Python engine by setting `THOTHPAD_BUNDLE_ENGINE=OFF`. A release-style desktop package normally builds both and bundles the frozen engine beside the application.

For dependency lineage and licenses, see [`PROVENANCE.md`](PROVENANCE.md), [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), and [`writer-engine/THIRD_PARTY.md`](writer-engine/THIRD_PARTY.md).

## What you need to compile

### Native editor — Core

The supported native build uses:

- a **C++20 compiler**;
- **CMake** (the project minimum is 3.16; release tooling currently locks CMake 4.3.2);
- **Ninja** for the supported/release build path;
- **Qt 6.5+** with `Concurrent`, `Core`, `Gui`, `Svg`, and `Widgets`;
- **ECM 6 / KDE Frameworks 6**;
- KF6 `ConfigWidgets`, `CoreAddons`, `Sonnet`, `WidgetsAddons`, and `XmlGui`;
- **QtKeychain** (`Qt6Keychain`); and
- the repository's vendored **cmark-gfm** source under `3rdparty/cmark-gfm`.

You do **not** need to download cmark-gfm separately; CMake builds the copy in this repository.

### Native editor — Full

The **Full** package includes everything above plus:

- Qt `WebChannel`; and
- Qt `WebEngineWidgets`.

If you only want to compile and use the editor without the HTML/WebEngine preview runtime, build the **Core** variant.

### ThothPad Engine

To run or build the engine from source:

- **Python 3.11+** is required;
- the production desktop sidecar build uses the dependencies pinned in `writer-engine/pyproject.toml`;
- **Rust/Cargo 1.95** is required to compile the bundled Harper grammar bridge; and
- the production frozen sidecar requires the spaCy `en_core_web_sm` model declared by the `desktop` extra.

The engine source already contains the bundled WordNet and Slopless-derived data that the production sidecar expects. Harper itself is resolved through the locked Rust dependency graph in `writer-engine/harper-bridge/Cargo.lock`.

## What is optional

The following are **not required to compile the native editor**:

- Proselint;
- LanguageTool;
- ProWritingAid;
- Vale;
- LexicalRichness;
- a remote LLM provider; or
- the browser/development frontend toolchain.

Those features are integrations, development surfaces, or optional analysis helpers. The native editor and deterministic local analysis path do not require a cloud account.

The engine frontend under `writer-engine/frontend/` has its own Node/Vite/React/TypeScript dependencies. They are needed only when developing or rebuilding that browser interface; they are not native CMake dependencies.

## What is packaging-only

You can compile ThothPad without the release packaging tools below. They are needed only when producing distributable platform artifacts:

- **Windows:** NSIS and the locked KDE Craft/MSVC release environment;
- **Linux:** Flatpak tooling and/or `linuxdeploy` plus `linuxdeploy-plugin-qt` for AppImage builds;
- **macOS:** signing/notarization credentials and Apple packaging tools for public DMGs.

Release packaging additionally generates an SBOM and performs clean-install/acceptance checks.

---

## Build the ThothPad Engine

From `writer-engine/`, create an isolated Python environment and install the desktop/development dependencies.

### Windows PowerShell

```powershell
cd writer-engine
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,desktop]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m backend.build_sidecar --smoke
.\.venv\Scripts\python.exe -m backend.build_sidecar
```

### Linux / macOS shell

```bash
cd writer-engine
python3 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev,desktop]'
./.venv/bin/python -m pytest -q
./.venv/bin/python -m backend.build_sidecar --smoke
./.venv/bin/python -m backend.build_sidecar
```

The final command:

1. validates the sidecar build inputs;
2. builds the locked Rust Harper bridge;
3. freezes the Python engine with PyInstaller; and
4. runs a frozen-sidecar smoke test.

PyInstaller artifacts are platform-specific. Build the sidecar independently on Windows, Linux, and macOS rather than copying a frozen engine from another operating system.

On Windows the output is `writer-engine/dist/writer-engine/writer-engine.exe` plus its `_internal` directory. Other platforms produce the corresponding native executable in the same one-directory layout.

### Engine dependency files

The main dependency declarations are:

- `writer-engine/pyproject.toml` — direct Python dependencies and development/desktop extras;
- `writer-engine/uv.lock` — locked Python dependency closure used by release/SBOM tooling;
- `writer-engine/harper-bridge/Cargo.toml` — direct Rust dependencies;
- `writer-engine/harper-bridge/Cargo.lock` — locked Rust dependency closure; and
- `writer-engine/harper-bridge/rust-toolchain.toml` — required Rust toolchain.

Do not replace those with floating package versions for a release build.

---

## Build the native application

A normal release-style configure that bundles an already-frozen engine is:

```powershell
cmake --preset release `
  -DTHOTHPAD_BUNDLE_ENGINE=ON `
  -DTHOTHPAD_ENGINE_DIR="$PWD/writer-engine/dist/writer-engine"
cmake --build --preset release
$env:QT_QPA_PLATFORM = "offscreen"
ctest --test-dir build-release --output-on-failure
cmake --install build-release --prefix stage
```

For a source/development build of only the native application, disable engine bundling:

```bash
cmake -S . -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DTHOTHPAD_PACKAGE_VARIANT=Core \
  -DTHOTHPAD_BUNDLE_ENGINE=OFF
cmake --build build
ctest --test-dir build --output-on-failure
```

Use `-DTHOTHPAD_PACKAGE_VARIANT=Full` when Qt WebEngine/WebChannel are installed and you want the full preview-enabled build.

## Platform notes

### Windows

KDE Craft with **MSVC 2022** is the supported release environment. The reviewed Windows baseline uses:

- Craft ABI `windows-cl-msvc2022-x86_64`;
- Qt 6.11.1;
- KF6 6.28;
- MSVC 2022; and
- Ninja.

The Craft source revision used for the 0.1.0 development package is recorded in release evidence as `a7c3f639b3a84d2033c76857ad151e34df6133c9`.

### Linux

Use a distribution/environment that provides Qt 6.5+ and KF6, or the KDE Flatpak build environment used by the packaging scripts. Distribution package names differ, so CMake's `find_package` output is the authoritative list of missing native components on a particular system.

### macOS

Use a Qt 6/KF6-capable Craft environment. Ordinary local builds do not need a Developer ID. Public distributable DMGs require signing and notarization as described in `packaging/README.md`.

---

## Platform packages

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

See `packaging/README.md` for runner prerequisites, signing variables, package profiles, and artifact contents. Release scripts refuse to delete staging paths outside the repository's `release` directory.

All platform packages run `packaging/generate_sbom.py` against their staged tree. The resulting CycloneDX 1.5 document records versions, licenses, purls where available, lockfile identities, and staged-file hashes. Windows acceptance additionally rejects a package whose SBOM lacks expected Qt, KF6, QtKeychain, cmark-gfm, Python, and spaCy identities.

## Licensing and provenance for builders

A source checkout intentionally contains third-party code/data with different compatible licenses. Building ThothPad does not make those notices optional.

Relevant files include:

- `COPYING` and `LICENSES/` — project and reusable license texts;
- `THIRD_PARTY_NOTICES.md` — major linked/bundled application dependencies;
- `writer-engine/THIRD_PARTY.md` — engine data, integrations, and analysis references; and
- `PROVENANCE.md` — source lineage, upstream links, version basis, and redistribution guidance.

CMake installs those legal/provenance files with the application. Public release packages should also include the generated SBOM because it is more precise than a hand-maintained dependency list for transitive/platform-specific libraries.

## Release gates

- Build and test the native editor on all three platforms.
- Run `writer-engine/benchmarks/benchmark_analysis.py` on the Windows reference machine.
- Verify the frozen sidecar with `initialize`, `analyze_document`, `cancel`, and `shutdown` frames.
- Generate an SBOM from the staged application and retain all `LICENSES/`, provenance, and third-party notices.
- Confirm with network capture that typing and deterministic reports produce no traffic.
- Sign/notarize only after unsigned artifacts pass clean-machine installation and Unicode-path tests.
- Run independent architecture, analyzer-quality, UX/accessibility, security/privacy, performance/reliability, and packaging reviews; record PASS/FAIL evidence under `quality-gates/` and rerun every failed review after corrections.
