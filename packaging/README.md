# Release Packaging

ThothPad packages the Qt application and a platform-local
PyInstaller one-directory ThothPad Engine. End users do not install Python or
run a localhost service. The editor supervises the sidecar over framed JSON on
standard input/output.

## Package profiles

- **Core** includes the editor, deterministic engine, and grammar/POS assets,
  but omits the optional HTML preview and every Qt WebEngine dependency. Its
  package-profile check rejects any staged WebEngine marker file.
- **Full** adds the Qt WebEngine preview runtime to the same editor and engine.

Select the profile with `-PackageVariant Core|Full` on Windows or
`PACKAGE_VARIANT=Core|Full` on macOS/Linux. CMake uses the corresponding
`-DTHOTHPAD_PACKAGE_VARIANT=Core|Full` value. Every stage receives a machine-readable
package profile and every archive receives measured size evidence.

## Windows

Prerequisites are MSVC 2022, Ninja, KDE Craft with Qt 6.5+/KF6, NSIS, and a
frozen Windows engine at `writer-engine/dist/writer-engine`.

```powershell
. C:\CraftRoot\craft\craftenv.ps1
cmake -S . -B build-windows-core-release -G Ninja `
    -DCMAKE_BUILD_TYPE=Release -DTHOTHPAD_PACKAGE_VARIANT=Core
.\packaging\windows\package.ps1 -BuildDirectory build-windows-core-release `
    -OutputDirectory release/windows/core -PackageVariant Core
.\packaging\windows\verify-package.ps1 -PackageVariant Core `
    -ArtifactsDir release/windows/core/artifacts `
    -WorkDir release/windows/core/acceptance
```

Repeat with variant-specific `full` directories and `-PackageVariant Full` for
the preview-enabled package. The two stages and artifact sets are independent.

The package script builds and tests the application, stages Qt/KF6/Sonnet,
bundles the engine and English Hunspell dictionary, validates imported DLLs,
generates a CycloneDX SBOM with versioned native and Python package identities,
licenses, purls, and staged-file hashes, and emits a portable ZIP and per-user NSIS
installer. The verification script silently installs to a Unicode path,
launches a Unicode document without modifying it, verifies the engine child
and zero deterministic TCP connections, and uninstalls.

## Linux

Build the Linux engine and native application first. Both Flatpak manifests use
KDE Platform 6.10; only the Full manifest uses QtWebEngine BaseApp 6.10. The
Core manifest has no BaseApp or WebEngine runtime environment entries. The
AppImage path requires
`linuxdeploy-x86_64.AppImage` and `linuxdeploy-plugin-qt` on `PATH`.

```bash
PACKAGE_VARIANT=Core bash packaging/linux/package-flatpak.sh
PACKAGE_VARIANT=Core bash packaging/linux/package-appimage.sh
PACKAGE_VARIANT=Full bash packaging/linux/package-flatpak.sh
PACKAGE_VARIANT=Full bash packaging/linux/package-appimage.sh
```

Defaults are variant-specific: native builds use
`build-linux-{core,full}-release`, and artifacts are written under
`release/linux/{core,full}`. Core packaging fails if deployment introduces a
WebEngine marker file.

The Flatpak permits network access because the user can explicitly invoke a
remote AI operation; live and report-only deterministic analysis remains
local. Secret Service access is used for provider credentials.

## macOS

Build with a Qt 6/KF6 Craft environment and freeze the engine on macOS, then:

```bash
PACKAGE_VARIANT=Full \
CODESIGN_IDENTITY="Developer ID Application: ..." \
APPLE_NOTARY_PROFILE="thothpad-notary" \
bash packaging/macos/package-dmg.sh
```

Without those variables the script produces an ad-hoc signed development DMG.
Set `PUBLIC_RELEASE=1` for public artifacts; the script then refuses to run
without a Developer ID and notarization profile. The DMG includes legal and
provenance files inside the application bundle. Core and Full default to
`build-macos-{core,full}-release` and `release/macos/{core,full}`, so their app
stages, SBOMs, checksums, measurements, and DMGs cannot collide.

## CI and Release Inputs

`.github/workflows/native-release.yml` is a manual six-entry platform/Core/Full
release matrix. Candidate A and B run as separate jobs in separate checkout
paths on runner pools labeled `repro-a` and `repro-b`; no runner may carry both
labels. Each job uploads its staged tree and provenance-bound normalized
manifest. A hosted arbiter downloads both artifacts, rejects a shared runner
identity, and compares them before release attestation. The regular
`thothpad-engine.yml` matrix tests and freezes the Python engine on hosted
runners. KDE CI continues to build the native Linux, Windows, and FreeBSD
targets.

Every platform script emits SHA-256 manifests and a CycloneDX 1.5 SBOM. Every
public release includes source, dependency locks, GPL and third-party notices,
and those generated artifacts. Build the sidecar independently
on each target OS; PyInstaller outputs are not portable between platforms.

`packaging/toolchain-lock.json` is the reviewed native build-input contract.
All platforms enforce Python 3.11.9, CMake 4.3.2, the locked Ninja build, and
the compiler identity/version recorded by CMake. Windows additionally hashes
the Python runtime, CMake, Ninja, compiler, Qt/KF/NSIS files, and checks Craft
commits. Linux and macOS hash Qt/KF version artifacts and pin their upstream
source commits; Linux also verifies linuxdeploy assets and its Flatpak runtime
commit, while macOS verifies the exact Xcode build. Missing lock fields or any
version, identity, commit, or artifact mismatch fails packaging closed.

SBOM generation consumes `writer-engine/uv.lock`,
`writer-engine/harper-bridge/Cargo.lock`, and the native toolchain lock. It
records every locked Python and Rust package, registry checksum, build-lock
hash, and every staged file. DLL, dylib, framework, and shared-object files are
also marked as individual native-library components with their own SHA-256.

Separate native release jobs produce independently cleaned engine freezes and
native builds, stage each unsigned candidate, and upload them for comparison by
a non-building arbiter job. Generated
timestamps, SBOM UUIDs, and macOS signature metadata are normalized; payload
bytes and all meaningful JSON fields remain part of the comparison. Signed
archives are intentionally not required to be byte-identical because signing,
notarization, and container metadata are nondeterministic.

`.github/workflows/s-efficiency-gate.yml` validates the immutable benchmark
contract on hosted Windows, macOS, and Linux runners. S certification runs only
on self-hosted runners labeled `thothpad-benchmark`, `2vcpu-8gb`, and their OS.
Hosted smoke timings are evidence that collectors work, not S-grade evidence.
