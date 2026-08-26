#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

variant="${PACKAGE_VARIANT:-Full}"
case "$variant" in Core|Full) ;; *) echo "PACKAGE_VARIANT must be Core or Full" >&2; exit 1 ;; esac
variant_slug="$(printf '%s' "$variant" | tr '[:upper:]' '[:lower:]')"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "${PUBLIC_RELEASE:-0}" == "1" && -n "$(git -C "$repo" status --porcelain)" ]]; then
    echo "Public release packaging requires a clean Git worktree." >&2
    exit 1
fi
build="${BUILD_DIR:-$repo/build-linux-$variant_slug-release}"
engine="${ENGINE_DIR:-$repo/writer-engine/dist/writer-engine}"
output="${OUTPUT_DIR:-$repo/release/linux/$variant_slug}"
version="${VERSION:-0.1.2}"
benchmark_suite="${BENCHMARK_SUITE:-smoke}"
appdir="$output/ThothPad.AppDir"
linuxdeploy="${LINUXDEPLOY:-linuxdeploy-x86_64.AppImage}"
linuxdeploy_qt="${LINUXDEPLOY_QT:-linuxdeploy-plugin-qt-x86_64.AppImage}"
toolchain_lock="$repo/packaging/toolchain-lock.json"
release_root="$repo/release"
mkdir -p "$output"
output="$(cd "$output" && pwd -P)"
case "$output/" in
    "$release_root/"*) ;;
    *) echo "OUTPUT_DIR must stay under $release_root" >&2; exit 1 ;;
esac
appdir="$output/ThothPad.AppDir"

case "$benchmark_suite" in smoke|certification) ;; *) echo "BENCHMARK_SUITE must be smoke or certification" >&2; exit 1 ;; esac

test -x "$engine/writer-engine" || { echo "Linux ThothPad Engine is missing: $engine" >&2; exit 1; }
linuxdeploy="$(command -v "$linuxdeploy")"
linuxdeploy_qt="$(command -v "$linuxdeploy_qt")"
python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" \
    asset --key linux.linuxdeploy --path "$linuxdeploy"
python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" \
    asset --key linux.linuxdeploy_qt --path "$linuxdeploy_qt"
python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" linux \
    --build-dir "$build" --python "$(command -v python3)" \
    --cmake "$(command -v cmake)" --ninja "$(command -v ninja)" \
    --qtpaths "$(command -v qtpaths6)"
export PATH="$(dirname "$linuxdeploy_qt"):$PATH"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$repo" show -s --format=%ct HEAD)}"
rm -rf -- "$appdir"
mkdir -p "$appdir/usr/bin/writer-engine" "$output"
DESTDIR="$appdir" cmake --install "$build" --prefix /usr
cp -a "$engine/." "$appdir/usr/bin/writer-engine/"
chmod 0755 "$appdir/usr/bin/writer-engine/writer-engine"

qt_version="$(python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" value linux.qt.version)"
kf_version="$(python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" value linux.kf.version)"
export QML_SOURCES_PATHS="$repo/src"
export OUTPUT="ThothPad-$version-$variant-x86_64.AppImage"
"$linuxdeploy" --appdir "$appdir" \
    --desktop-file "$appdir/usr/share/applications/org.thothpad.ThothPad.desktop" \
    --executable "$appdir/usr/bin/thothpad" \
    --icon-file "$appdir/usr/share/icons/hicolor/256x256/apps/thothpad.png" \
    --plugin qt
python3 "$repo/packaging/package_profile.py" write \
    --root "$appdir/usr" \
    --output "$appdir/usr/share/thothpad/ThothPad-$version.package.json" \
    --repo "$repo" --version "$version" --variant "$variant"
sbom="$appdir/usr/share/thothpad/ThothPad-$version.cdx.json"
python3 "$repo/packaging/generate_sbom.py" \
    --root "$appdir/usr" --output "$sbom" --app-version "$version" \
    --lock "$repo/writer-engine/uv.lock" \
    --cargo-lock "$repo/writer-engine/harper-bridge/Cargo.lock" \
    --toolchain-lock "$toolchain_lock" \
    --component "Qt|$qt_version|LGPL-3.0-only|pkg:generic/qt@$qt_version" \
    --component "KDE Frameworks|$kf_version|LGPL-2.1-or-later|pkg:generic/kde-frameworks@$kf_version" \
    --component "QtKeychain|0.15.0|BSD-2-Clause|pkg:github/frankosterfeld/qtkeychain@0.15.0" \
    --component "Harper|2.5.0|Apache-2.0|pkg:cargo/harper-core@2.5.0" \
    --component "cmark-gfm|0.29.0.gfm.6|BSD-2-Clause|pkg:github/github/cmark-gfm@0.29.0.gfm.6"
if [[ "${STAGE_ONLY:-0}" == "1" ]]; then
    echo "Reproducibility stage: $appdir/usr"
    exit 0
fi
"$linuxdeploy" --appdir "$appdir" --output appimage
mv -f "$OUTPUT" "$output/$OUTPUT"
artifact_sbom="$output/ThothPad-$version-$variant-AppImage.cdx.json"
cp "$sbom" "$artifact_sbom"
sha256sum "$output/$OUTPUT" "$artifact_sbom" \
    > "$output/SHA256SUMS-$variant-AppImage.txt"
commit="$(git -C "$repo" rev-parse HEAD)"
dirty=()
[[ -n "$(git -C "$repo" status --porcelain)" ]] && dirty+=(--dirty)
python3 "$repo/packaging/package_profile.py" measure \
    --root "$appdir/usr" --archive "$output/$OUTPUT" \
    --output "$output/benchmark-package-${variant,,}-linux-appimage.json" \
    --variant "$variant" --suite "$benchmark_suite" --commit "$commit" "${dirty[@]}"
echo "AppImage: $output/$OUTPUT"
