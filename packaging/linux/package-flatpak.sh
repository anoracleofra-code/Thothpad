#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
toolchain_lock="$repo/packaging/toolchain-lock.json"
if [[ "${PUBLIC_RELEASE:-0}" == "1" && -n "$(git -C "$repo" status --porcelain)" ]]; then
    echo "Public release packaging requires a clean Git worktree." >&2
    exit 1
fi
version="${VERSION:-0.1.2}"
variant="${PACKAGE_VARIANT:-Full}"
case "$variant" in Core|Full) ;; *) echo "PACKAGE_VARIANT must be Core or Full" >&2; exit 1 ;; esac
variant_slug="$(printf '%s' "$variant" | tr '[:upper:]' '[:lower:]')"
manifest="$repo/packaging/linux/org.thothpad.ThothPad.$variant.yml"
benchmark_suite="${BENCHMARK_SUITE:-smoke}"
output="${OUTPUT_DIR:-$repo/release/linux/$variant_slug}"
build_dir="${1:-$output/flatpak-build}"
repo_dir="${2:-$output/flatpak-repo}"
bundle="${3:-$output/ThothPad-$version-$variant.flatpak}"
release_root="$repo/release"
case "$benchmark_suite" in smoke|certification) ;; *) echo "BENCHMARK_SUITE must be smoke or certification" >&2; exit 1 ;; esac
test -f "$manifest" || { echo "Flatpak manifest is missing: $manifest" >&2; exit 1; }
build_dir="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$build_dir")"
repo_dir="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$repo_dir")"
bundle="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$bundle")"
for path in "$build_dir" "$repo_dir" "$bundle"; do
    case "$path" in
        "$release_root"/*) ;;
        *) echo "Flatpak output paths must stay under $release_root" >&2; exit 1 ;;
    esac
done
mkdir -p "$(dirname "$bundle")"

test -x "$repo/writer-engine/dist/writer-engine/writer-engine" || {
    echo "Build the Linux ThothPad Engine sidecar first." >&2
    exit 1
}
runtime="$(python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" value linux.flatpak.runtime)"
runtime_version="$(python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" value linux.flatpak.runtime_version)"
runtime_commit="$(python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" value linux.flatpak.runtime_commit_x86_64)"
if ! flatpak info --user "$runtime/x86_64/$runtime_version" >/dev/null 2>&1; then
    flatpak install --user --noninteractive flathub "$runtime/x86_64/$runtime_version"
fi
flatpak update --user --noninteractive --commit="$runtime_commit" \
    "$runtime/x86_64/$runtime_version"
actual_runtime_commit="$(flatpak info --user --show-commit "$runtime/x86_64/$runtime_version")"
[[ "$actual_runtime_commit" == "$runtime_commit" ]] || {
    echo "Flatpak runtime commit does not match toolchain lock." >&2
    exit 1
}
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$repo" show -s --format=%ct HEAD)}"
flatpak-builder --force-clean --user --install-deps-from=flathub \
    --repo="$repo_dir" "$build_dir" "$manifest"
actual_runtime_commit="$(flatpak info --user --show-commit "$runtime/x86_64/$runtime_version")"
[[ "$actual_runtime_commit" == "$runtime_commit" ]] || {
    echo "flatpak-builder changed the locked runtime commit." >&2
    exit 1
}
python3 "$repo/packaging/package_profile.py" write \
    --root "$build_dir/files" \
    --output "$(dirname "$bundle")/ThothPad-$version-$variant.package.json" \
    --repo "$repo" --version "$version" --variant "$variant"
qt_version="$(python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" value linux.qt.version)"
kf_version="$(python3 "$repo/packaging/verify_toolchain.py" --lock "$toolchain_lock" value linux.kf.version)"
sbom="${bundle%.flatpak}.cdx.json"
python3 "$repo/packaging/generate_sbom.py" \
    --root "$build_dir/files" --output "$sbom" --app-version "$version" \
    --lock "$repo/writer-engine/uv.lock" \
    --cargo-lock "$repo/writer-engine/harper-bridge/Cargo.lock" \
    --toolchain-lock "$toolchain_lock" \
    --component "Qt|$qt_version|LGPL-3.0-only|pkg:generic/qt@$qt_version" \
    --component "KDE Frameworks|$kf_version|LGPL-2.1-or-later|pkg:generic/kde-frameworks@$kf_version" \
    --component "QtKeychain|0.15.0|BSD-2-Clause|pkg:github/frankosterfeld/qtkeychain@0.15.0" \
    --component "Harper|2.5.0|Apache-2.0|pkg:cargo/harper-core@2.5.0" \
    --component "cmark-gfm|0.29.0.gfm.6|BSD-2-Clause|pkg:github/github/cmark-gfm@0.29.0.gfm.6"
if [[ "${STAGE_ONLY:-0}" == "1" ]]; then
    echo "Reproducibility stage: $build_dir/files"
    exit 0
fi
flatpak build-bundle "$repo_dir" "$bundle" org.thothpad.ThothPad
sha256sum "$bundle" "$sbom" > "$(dirname "$bundle")/SHA256SUMS-$variant-Flatpak.txt"
commit="$(git -C "$repo" rev-parse HEAD)"
dirty=()
[[ -n "$(git -C "$repo" status --porcelain)" ]] && dirty+=(--dirty)
python3 "$repo/packaging/package_profile.py" measure \
    --root "$build_dir/files" --archive "$bundle" \
    --output "$(dirname "$bundle")/benchmark-package-${variant,,}-linux-flatpak.json" \
    --variant "$variant" --suite "$benchmark_suite" --commit "$commit" "${dirty[@]}"
echo "Flatpak bundle: $bundle"
