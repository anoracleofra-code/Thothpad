param(
    [string]$BuildDirectory = "build-windows-craft",
    [ValidateSet("Debug", "Release", "RelWithDebInfo", "MinSizeRel")]
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$buildPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $BuildDirectory))

if (-not (Test-Path -LiteralPath (Join-Path $buildPath "CTestTestfile.cmake"))) {
    throw "No configured CTest build found at $buildPath"
}

$collectorConfig = $env:THOTHPAD_EFFICIENCY_CONFIG
try {
    Remove-Item Env:THOTHPAD_EFFICIENCY_CONFIG -ErrorAction SilentlyContinue
    & ctest --test-dir $buildPath -C $Configuration --output-on-failure
    $testExitCode = $LASTEXITCODE
} finally {
    if ($null -ne $collectorConfig) {
        $env:THOTHPAD_EFFICIENCY_CONFIG = $collectorConfig
    } else {
        Remove-Item Env:THOTHPAD_EFFICIENCY_CONFIG -ErrorAction SilentlyContinue
    }
}
if ($testExitCode -ne 0) {
    exit $testExitCode
}
