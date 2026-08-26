# SPDX-License-Identifier: GPL-3.0-or-later

[CmdletBinding()]
param(
    [string]$BuildDirectory = "build-windows-craft",
    [string]$EngineDirectory = "writer-engine/dist/writer-engine",
    [string]$OutputDirectory = "release/windows",
    [string]$Version = "0.1.2",
    [string]$CraftRoot = "C:\CraftRoot",
    [ValidateSet("Core", "Full")]
    [string]$PackageVariant = "Full",
    [ValidateSet("smoke", "certification")]
    [string]$BenchmarkSuite = "smoke",
    [switch]$PublicRelease,
    [switch]$StageOnly,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repo "writer-engine\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python.exe -ErrorAction Stop).Source }
$toolchainLock = Join-Path $repo "packaging\toolchain-lock.json"
$build = [IO.Path]::GetFullPath((Join-Path $repo $BuildDirectory))
$cmake = (Get-Command cmake.exe -ErrorAction Stop).Source
$ninja = (Get-Command ninja.exe -ErrorAction Stop).Source
& $python (Join-Path $repo "packaging\verify_toolchain.py") --lock $toolchainLock `
    windows --craft-root $CraftRoot --build-dir $build --python $python `
    --cmake $cmake --ninja $ninja
if ($LASTEXITCODE -ne 0) { throw "Locked Windows toolchain verification failed." }
if (-not $env:SOURCE_DATE_EPOCH) {
    $env:SOURCE_DATE_EPOCH = (& git -C $repo show -s --format=%ct HEAD).Trim()
}
if ($PublicRelease -and (& git -C $repo status --porcelain)) {
    throw "Public release packaging requires a clean Git worktree."
}
$engine = [IO.Path]::GetFullPath((Join-Path $repo $EngineDirectory))
$output = [IO.Path]::GetFullPath((Join-Path $repo $OutputDirectory))
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $repo "release"))
$releasePrefix = $releaseRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
$stage = Join-Path $output "stage"
$artifacts = Join-Path $output "artifacts"

$cmakeSource = Get-Content -LiteralPath (Join-Path $repo "CMakeLists.txt") -Raw
$versionParts = foreach ($name in @("MAJOR", "MINOR", "MICRO")) {
    $match = [regex]::Match($cmakeSource, "RELEASE_SERVICE_VERSION_$name\s+`"(\d+)`"")
    if (-not $match.Success) { throw "Could not read application $name version from CMakeLists.txt." }
    $match.Groups[1].Value
}
$applicationVersion = $versionParts -join "."
if ($Version -ne $applicationVersion) {
    throw "Package version $Version does not match application version $applicationVersion."
}

if (-not $output.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must remain under the repository release directory."
}
if (-not (Test-Path (Join-Path $engine "writer-engine.exe"))) {
    throw "Packaged ThothPad Engine not found at $engine"
}

if (-not $SkipBuild) {
    cmake --build $build --config Release
    if ($LASTEXITCODE -ne 0) { throw "Native build failed." }
    & (Join-Path $repo "autotest\run-tests.ps1") `
        -BuildDirectory $BuildDirectory -Configuration Release
    if ($LASTEXITCODE -ne 0) { throw "Native tests failed." }
}

foreach ($path in @($stage, $artifacts)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $path | Out-Null
}

cmake --install $build --config Release --prefix $stage
if ($LASTEXITCODE -ne 0) { throw "CMake install failed." }

$bin = Join-Path $stage "bin"
$exe = Join-Path $bin "thothpad.exe"
if (-not (Test-Path $exe)) { throw "Installed application is missing: $exe" }

# The engine is a release input, not a native CMake build product. Stage it
# explicitly so packaging does not depend on a pre-existing CMake cache option.
$engineTarget = Join-Path $bin "writer-engine"
New-Item -ItemType Directory -Path $engineTarget -Force | Out-Null
Copy-Item -Path (Join-Path $engine "*") -Destination $engineTarget -Recurse -Force

# Current Craft QtWebEngine packages place these resources in bin while
# windeployqt resolves them from the prefix resource directory.
$craftResources = Join-Path $CraftRoot "resources"
New-Item -ItemType Directory -Force -Path $craftResources | Out-Null
foreach ($resource in @("icudtl.dat", "qtwebengine_devtools_resources.pak", "qtwebengine_resources.pak", "qtwebengine_resources_100p.pak", "qtwebengine_resources_200p.pak")) {
    $source = Join-Path (Join-Path $CraftRoot "bin") $resource
    if (Test-Path $source) { Copy-Item -LiteralPath $source -Destination $craftResources -Force }
}

$windeployqt = Join-Path $CraftRoot "bin\windeployqt.exe"
& $windeployqt --release --no-translations --no-system-d3d-compiler --no-compiler-runtime $exe
if ($LASTEXITCODE -ne 0) { throw "windeployqt failed." }

# Qt 6.5+ windeployqt copies dxcompiler.dll/dxil.dll unconditionally, but they
# are runtime LoadLibrary targets of Qt6Gui's D3D12 RHI shader compiler only.
# ThothPad is widgets-only (no Quick/QML/RHI modules in any variant deploy
# list) and dumpbin shows zero import-table references in any staged binary,
# so both are dead weight (~15.9 MB raw / ~7.2 MB compressed).
foreach ($shaderDll in @("dxcompiler.dll", "dxil.dll")) {
    $shaderPath = Join-Path $bin $shaderDll
    if (Test-Path $shaderPath) { Remove-Item -LiteralPath $shaderPath -Force }
}

foreach ($library in @("cmark-gfm.dll", "cmark-gfm-extensions.dll")) {
    $source = Join-Path $build "bin\$library"
    if (-not (Test-Path $source)) { throw "Required application library is missing: $source" }
    Copy-Item -LiteralPath $source -Destination $bin -Force
}

$sonnetTarget = Join-Path $bin "plugins\kf6\sonnet"
New-Item -ItemType Directory -Force -Path $sonnetTarget | Out-Null
foreach ($plugin in @("sonnet_hunspell.dll", "sonnet_ispellchecker.dll")) {
    $source = Join-Path $CraftRoot "plugins\kf6\sonnet\$plugin"
    if (-not (Test-Path $source)) {
        throw "Sonnet spell backend is missing. Build Craft Sonnet with useHunspell=True."
    }
    Copy-Item -LiteralPath $source -Destination $sonnetTarget -Force
}
$dictionaryTarget = Join-Path $bin "data\hunspell"
New-Item -ItemType Directory -Force -Path $dictionaryTarget | Out-Null
foreach ($dictionary in @("en_US.aff", "en_US.dic", "LICENSE_en_US.txt", "README_en_US.txt")) {
    $source = Join-Path $CraftRoot "bin\data\hunspell\$dictionary"
    if (-not (Test-Path $source)) { throw "Required Hunspell data is missing: $source" }
    Copy-Item -LiteralPath $source -Destination $dictionaryTarget -Force
}

$systemLibraries = @{
    "ADVAPI32.DLL"=$true; "AUTHZ.DLL"=$true; "BCRYPT.DLL"=$true; "COMDLG32.DLL"=$true
    "CRYPT32.DLL"=$true; "D3D11.DLL"=$true; "DWMAPI.DLL"=$true; "DXGI.DLL"=$true
    "GDI32.DLL"=$true; "IPHLPAPI.DLL"=$true; "KERNEL32.DLL"=$true; "MPR.DLL"=$true
    "NETAPI32.DLL"=$true; "NORMALIZ.DLL"=$true; "OLE32.DLL"=$true; "OLEAUT32.DLL"=$true
    "OPENGL32.DLL"=$true; "PSAPI.DLL"=$true; "RPCRT4.DLL"=$true; "SHELL32.DLL"=$true
    "SHLWAPI.DLL"=$true; "USER32.DLL"=$true; "USERENV.DLL"=$true; "UXTHEME.DLL"=$true
    "VERSION.DLL"=$true; "WINHTTP.DLL"=$true; "WINMM.DLL"=$true; "WINSPOOL.DRV"=$true
    "WS2_32.DLL"=$true; "WTSAPI32.DLL"=$true; "ZLIB.DLL"=$true; "NTDLL.DLL"=$true
}
$searchRoots = @((Join-Path $build "bin"), (Join-Path $CraftRoot "bin"))
$dumpbinCommand = Get-Command dumpbin.exe -ErrorAction SilentlyContinue
$dumpbin = if ($dumpbinCommand) { $dumpbinCommand.Source } else {
    $visualStudioRoot = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio"
    Get-ChildItem -LiteralPath $visualStudioRoot -Filter dumpbin.exe -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\bin\\Hostx64\\x64\\dumpbin\.exe$' } |
        Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $dumpbin -or -not (Test-Path -LiteralPath $dumpbin)) {
    throw "MSVC dumpbin.exe was not found. Install the Visual C++ x64 build tools."
}
$queue = [Collections.Generic.Queue[string]]::new()
Get-ChildItem -LiteralPath $bin -Recurse -File | Where-Object { $_.Extension -in ".exe", ".dll" } | ForEach-Object { $queue.Enqueue($_.FullName) }
$scanned = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$missing = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
while ($queue.Count) {
    $binary = $queue.Dequeue()
    if (-not $scanned.Add($binary)) { continue }
    $dependencies = & $dumpbin /nologo /dependents $binary 2>$null |
        Select-String '^\s+([A-Za-z0-9_.+-]+\.(?:dll|DLL))\s*$' |
        ForEach-Object { $_.Matches[0].Groups[1].Value }
    foreach ($dependency in $dependencies) {
        $name = $dependency.ToUpperInvariant()
        $systemPath = Join-Path $env:SystemRoot "System32\$dependency"
        if ($systemLibraries.ContainsKey($name) -or (Test-Path $systemPath) -or $name.StartsWith("API-MS-WIN-") -or $name.StartsWith("EXT-MS-WIN-")) { continue }
        $existing = Get-ChildItem -LiteralPath $bin -Recurse -Filter $dependency -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($existing) { $queue.Enqueue($existing.FullName); continue }
        $source = $null
        foreach ($root in $searchRoots) {
            $candidate = Join-Path $root $dependency
            if (Test-Path $candidate) { $source = $candidate; break }
        }
        if ($source) {
            $destination = Join-Path $bin $dependency
            Copy-Item -LiteralPath $source -Destination $destination -Force
            $queue.Enqueue($destination)
        } else {
            [void]$missing.Add($dependency)
        }
    }
}
$unresolved = @($missing | Where-Object {
    -not (Get-ChildItem -LiteralPath $bin -Recurse -Filter $_ -File -ErrorAction SilentlyContinue)
})
if ($unresolved.Count) { throw "Unresolved runtime libraries: $($unresolved -join ', ')" }

$required = @(
    "thothpad.exe", "Qt6Core.dll",
    "KF6SonnetCore.dll", "qt6keychain.dll", "writer-engine\writer-engine.exe",
    "writer-engine\_internal\grammar\thothpad-harper.exe",
    "plugins\kf6\sonnet\sonnet_hunspell.dll", "data\hunspell\en_US.dic",
    "writer-engine\_internal\backend\data\wordnet\index.adv",
    "writer-engine\_internal\backend\data\wordnet\index.adj",
    "writer-engine\_internal\backend\data\wordnet\index.verb",
    "writer-engine\_internal\backend\data\wordnet\adv.exc",
    "writer-engine\_internal\backend\data\wordnet\adj.exc",
    "writer-engine\_internal\backend\data\wordnet\verb.exc",
    "writer-engine\_internal\backend\data\wordnet\LICENSE"
)
if ($PackageVariant -eq "Full") { $required += "Qt6WebEngineCore.dll" }
foreach ($item in $required) {
    if (-not (Test-Path (Join-Path $bin $item))) { throw "Release validation failed; missing $item" }
}

$packageManifest = Join-Path $bin "ThothPad-$Version.package.json"
& $python (Join-Path $repo "packaging\package_profile.py") write `
    --root $bin --output $packageManifest --repo $repo `
    --version $Version --variant $PackageVariant
if ($LASTEXITCODE -ne 0) { throw "$PackageVariant package profile validation failed." }

$sbomPath = Join-Path $bin "ThothPad-$Version.cdx.json"
$qtVersion = (Get-Item (Join-Path $bin "Qt6Core.dll")).VersionInfo.ProductVersion.TrimEnd('.0')
$kfVersionFile = Join-Path $CraftRoot "lib\cmake\KF6CoreAddons\KF6CoreAddonsConfigVersion.cmake"
$kfVersion = "unknown"
if (Test-Path $kfVersionFile) {
    $match = Select-String -LiteralPath $kfVersionFile -Pattern 'PACKAGE_VERSION\s+"([^"]+)' | Select-Object -First 1
    if ($match) { $kfVersion = $match.Matches[0].Groups[1].Value }
}
& $python (Join-Path $repo "packaging\generate_sbom.py") `
    --root $bin --output $sbomPath --app-version $Version `
    --lock (Join-Path $repo "writer-engine\uv.lock") `
    --cargo-lock (Join-Path $repo "writer-engine\harper-bridge\Cargo.lock") `
    --toolchain-lock $toolchainLock `
    --component "Qt|$qtVersion|LGPL-3.0-only|pkg:generic/qt@$qtVersion" `
    --component "KDE Frameworks|$kfVersion|LGPL-2.1-or-later|pkg:generic/kde-frameworks@$kfVersion" `
    --component "QtKeychain|0.15.0|BSD-2-Clause|pkg:github/frankosterfeld/qtkeychain@0.15.0" `
    --component "cmark-gfm|0.29.0.gfm.6|BSD-2-Clause|pkg:github/github/cmark-gfm@0.29.0.gfm.6" `
    --component "Harper|2.5.0|Apache-2.0|pkg:cargo/harper-core@2.5.0" `
    --component "Hunspell|1.7|LGPL-2.1-or-later|pkg:generic/hunspell@1.7" `
    --component "Princeton WordNet|3.1|LicenseRef-Princeton-WordNet|pkg:generic/princeton-wordnet@3.1"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $sbomPath)) { throw "SBOM generation failed." }

if ($StageOnly) {
    Write-Output "Reproducibility stage: $bin"
    return
}

$zipPath = Join-Path $artifacts "ThothPad-$Version-$PackageVariant-Windows-x64-portable.zip"
& (Join-Path $PSScriptRoot "new-portable-zip.ps1") `
    -SourceDirectory $bin -DestinationPath $zipPath

$makensis = Join-Path $CraftRoot "dev-utils\bin\makensis.exe"
if (-not (Test-Path $makensis)) { $makensis = Join-Path $CraftRoot "bin\makensis.exe" }
$installer = Join-Path $artifacts "ThothPad-$Version-$PackageVariant-Windows-x64-setup.exe"
& $makensis "/DVERSION=$Version" "/DSTAGE_DIR=$bin" "/DOUTPUT_FILE=$installer" (Join-Path $PSScriptRoot "thothpad.nsi")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $installer)) { throw "NSIS installer creation failed." }

$commit = (& git -C $repo rev-parse HEAD).Trim()
$dirtyArgs = @()
if (& git -C $repo status --porcelain) { $dirtyArgs += "--dirty" }
& $python (Join-Path $repo "packaging\package_profile.py") measure `
    --root $bin --archive $zipPath `
    --output (Join-Path $artifacts "benchmark-package-$($PackageVariant.ToLowerInvariant())-windows.json") `
    --variant $PackageVariant --suite $BenchmarkSuite --commit $commit @dirtyArgs
if ($LASTEXITCODE -ne 0) { throw "$PackageVariant package measurement failed." }

& (Join-Path $PSScriptRoot "write-sha256sums.ps1") -ArtifactsDirectory $artifacts
Write-Output "Release artifacts: $artifacts"
