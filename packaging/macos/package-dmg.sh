#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
toolchain_lock="$repo/packaging/toolchain-lock.json"
if [[ "${PUBLIC_RELEASE:-0}" == "1" && -n "$(git -C "$repo" status --porcelain)" ]]; then
    echo "Public release packaging requires a clean Git worktree." >&2
    exit 1
fi
variant="${PACKAGE_VARIANT:-Full}"
case "$variant" in Core|Full) ;; *) echo "PACKAGE_VARIANT must be Core or Full" >&2; exit 1 ;; esac
variant_slug="$(printf '%s' "$variant" | tr '[:upper:]' '[:lower:]')"
build="${BUILD_DIR:-$repo/build-macos-$variant_slug-release}"
engine="${ENGINE_DIR:-$repo/writer-engine/dist/writer-engine}"
output="${OUTPUT_DIR:-$repo/release/macos/$variant_slug}"
stage="$output/stage"
version="${VERSION:-0.1.2}"
benchmark_suite="${BENCHMARK_SUITE:-smoke}"
identity="${CODESIGN_IDENTITY:--}"
release_root="$repo/release"
mkdir -p "$output"
output="$(cd "$output" && pwd -P)"
case "$output/" in
    "$release_root/"*) ;;
    *) echo "OUTPUT_DIR must stay under $release_root" >&2; exit 1 ;;
esac
stage="$output/stage"

case "$benchmark_suite" in smoke|certification) ;; *) echo "BENCHMARK_SUITE must be smoke or certification" >&2; exit 1 ;; esac

if [[ "${PUBLIC_RELEASE:-0}" == "1" ]]; then
    [[ "$identity" != "-" ]] || { echo "PUBLIC_RELEASE requires CODESIGN_IDENTITY." >&2; exit 1; }
    [[ -n "${APPLE_NOTARY_PROFILE:-}" ]] || { echo "PUBLIC_RELEASE requires APPLE_NOTARY_PROFILE." >&2; exit 1; }
fi

test -x "$engine/writer-engine" || { echo "macOS ThothPad Engine is missing: $engine" >&2; exit 1; }
python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" macos \
    --build-dir "$build" --python "$(command -v python3)" \
    --cmake "$(command -v cmake)" --ninja "$(command -v ninja)" \
    --qtpaths "$(command -v qtpaths6)" \
    --macdeployqt "$(command -v macdeployqt)" \
    --xcodebuild "$(command -v xcodebuild)"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$repo" show -s --format=%ct HEAD)}"
rm -rf -- "$stage"
mkdir -p "$stage" "$output"
cmake --install "$build" --prefix "$stage"
app="$(find "$stage" -maxdepth 3 -name 'thothpad.app' -type d -print -quit)"
test -n "$app" || { echo "Installed application bundle was not found." >&2; exit 1; }

mkdir -p "$app/Contents/MacOS/writer-engine"
cp -a "$engine/." "$app/Contents/MacOS/writer-engine/"
chmod 0755 "$app/Contents/MacOS/writer-engine/writer-engine"
macdeployqt "$app" -always-overwrite

package_manifest="$app/Contents/Resources/ThothPad-$version.package.json"
python3 "$repo/packaging/package_profile.py" write \
    --root "$app/Contents" --output "$package_manifest" --repo "$repo" \
    --version "$version" --variant "$variant"

legal="$app/Contents/Resources/legal"
mkdir -p "$legal"
cp "$repo/COPYING" "$repo/PROVENANCE.md" "$repo/THIRD_PARTY_NOTICES.md" "$repo/writer-engine/THIRD_PARTY.md" "$legal/"
cp -a "$repo/LICENSES" "$legal/"
qt_version="$(python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" value macos.qt.version)"
kf_version="$(python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" value macos.kf.version)"
sbom="$output/ThothPad-$version-$variant-macOS.cdx.json"
generate_sbom() {
    python3 "$repo/packaging/generate_sbom.py" \
        --root "$app/Contents" --output "$sbom" --app-version "$version" \
        --lock "$repo/writer-engine/uv.lock" \
        --cargo-lock "$repo/writer-engine/harper-bridge/Cargo.lock" \
        --toolchain-lock "$toolchain_lock" \
        --component "Qt|$qt_version|LGPL-3.0-only|pkg:generic/qt@$qt_version" \
        --component "KDE Frameworks|$kf_version|LGPL-2.1-or-later|pkg:generic/kde-frameworks@$kf_version" \
        --component "QtKeychain|0.15.0|BSD-2-Clause|pkg:github/frankosterfeld/qtkeychain@0.15.0" \
        --component "Harper|2.5.0|Apache-2.0|pkg:cargo/harper-core@2.5.0" \
        --component "cmark-gfm|0.29.0.gfm.6|BSD-2-Clause|pkg:github/github/cmark-gfm@0.29.0.gfm.6"
}
if [[ "${STAGE_ONLY:-0}" == "1" ]]; then
    generate_sbom
    echo "Reproducibility stage: $app/Contents"
    exit 0
fi
# Sign nested executable code before the application bundle. A dash produces
# an ad-hoc development signature; public releases provide a Developer ID.
timestamp="--timestamp=none"
[[ "${PUBLIC_RELEASE:-0}" == "1" ]] && timestamp="--timestamp"
while IFS= read -r -d '' binary; do
    codesign --force --options runtime "$timestamp" --sign "$identity" "$binary"
done < <(find "$app/Contents" -type f \( -perm -111 -o -name '*.dylib' -o -name '*.so' \) -print0)
codesign --force --deep --options runtime "$timestamp" --sign "$identity" "$app"
codesign --verify --deep --strict --verbose=2 "$app"
generate_sbom

dmg_root="$output/dmg-root"
rm -rf -- "$dmg_root"
mkdir -p "$dmg_root"
cp -a "$app" "$dmg_root/"
cp "$sbom" "$dmg_root/"
dmg="$output/ThothPad-$version-$variant-macOS.dmg"
rm -f -- "$dmg"
hdiutil create -volname "ThothPad" -srcfolder "$dmg_root" -ov -format UDZO "$dmg"

if [[ -n "${APPLE_NOTARY_PROFILE:-}" ]]; then
    xcrun notarytool submit "$dmg" --keychain-profile "$APPLE_NOTARY_PROFILE" --wait
    xcrun stapler staple "$dmg"
    spctl --assess --type open --context context:primary-signature --verbose=2 "$dmg"
fi
shasum -a 256 "$dmg" "$sbom" \
    > "$output/SHA256SUMS-$variant-macOS.txt"
commit="$(git -C "$repo" rev-parse HEAD)"
dirty=()
[[ -n "$(git -C "$repo" status --porcelain)" ]] && dirty+=(--dirty)
python3 "$repo/packaging/package_profile.py" measure \
    --root "$app/Contents" --archive "$dmg" \
    --output "$output/benchmark-package-${variant,,}-macos.json" \
    --variant "$variant" --suite "$benchmark_suite" --commit "$commit" "${dirty[@]}"
echo "DMG: $dmg"
