[CmdletBinding()]
param(
    [string]$ArtifactsDir = "",
    [string]$WorkDir = "",
    [string]$EvidencePath = "",
    [ValidateSet("Core", "Full")]
    [string]$PackageVariant = "Full",
    [ValidateRange(1, 1000)]
    [int]$LaunchTrials = 1
)

$ErrorActionPreference = "Stop"

function Get-ProcessDescendants {
    param([int]$RootProcessId)

    $allProcesses = @(Get-CimInstance Win32_Process)
    $selected = [Collections.Generic.HashSet[int]]::new()
    $pending = [Collections.Generic.Queue[int]]::new()
    $pending.Enqueue($RootProcessId)
    while ($pending.Count -gt 0) {
        $parentId = $pending.Dequeue()
        foreach ($candidate in $allProcesses) {
            if ($candidate.ParentProcessId -eq $parentId -and $selected.Add([int]$candidate.ProcessId)) {
                $pending.Enqueue([int]$candidate.ProcessId)
            }
        }
    }
    @($allProcesses | Where-Object { $selected.Contains([int]$_.ProcessId) })
}

if (-not $ArtifactsDir) { $ArtifactsDir = Join-Path $PSScriptRoot "..\..\release\windows\artifacts" }
if (-not $WorkDir) { $WorkDir = Join-Path $PSScriptRoot "..\..\release\windows\acceptance" }
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$work = [IO.Path]::GetFullPath($WorkDir)
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $repo "release"))
$releasePrefix = $releaseRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
if (-not $work.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Acceptance work directory must stay under the repository release directory."
}

$installer = Get-ChildItem -LiteralPath $ArtifactsDir -Filter "*-$PackageVariant-*-setup.exe" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw "ThothPad installer was not found in $ArtifactsDir" }

if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
$omega = [char]0x03A9
$leftQuote = [char]0x201C
$rightQuote = [char]0x201D
$emoji = [char]::ConvertFromUtf32(0x1F4DD)
$installDir = Join-Path $work ("Install {0}" -f $omega)
$sampleDir = Join-Path $work ("Unicode {0}" -f $omega)
New-Item -ItemType Directory -Path $sampleDir -Force | Out-Null
$sample = Join-Path $sampleDir ("curly-{0}quotes{1}-{2}.md" -f $leftQuote, $rightQuote, $emoji)
$content = "# Unicode`r`n`r`nCafe$([char]0x0301) ${leftQuote}curly$rightQuote $emoji`r`n"
[IO.File]::WriteAllText($sample, $content, [Text.UTF8Encoding]::new($false))
$before = (Get-FileHash -LiteralPath $sample -Algorithm SHA256).Hash

$install = Start-Process -FilePath $installer.FullName -ArgumentList @("/S", "/D=$installDir") -Wait -PassThru -WindowStyle Hidden
if ($install.ExitCode -ne 0) { throw "Silent installer failed with exit code $($install.ExitCode)." }

$app = Join-Path $installDir "thothpad.exe"
$engine = Join-Path $installDir "writer-engine\writer-engine.exe"
$harper = Join-Path $installDir "writer-engine\_internal\grammar\thothpad-harper.exe"
$wordNet = Join-Path $installDir "writer-engine\_internal\backend\data\wordnet"
$installedRequirements = @(
    $app, $engine, $harper, (Join-Path $installDir "Uninstall.exe"),
    (Join-Path $wordNet "index.adv"), (Join-Path $wordNet "index.adj"), (Join-Path $wordNet "index.verb"),
    (Join-Path $wordNet "adv.exc"), (Join-Path $wordNet "adj.exc"), (Join-Path $wordNet "verb.exc"),
    (Join-Path $wordNet "LICENSE")
)
foreach ($required in $installedRequirements) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Installed file is missing: $required" }
}
$sbomFile = Get-ChildItem -LiteralPath $installDir -Filter "*.cdx.json" -File | Select-Object -First 1
if (-not $sbomFile) {
    throw "Installed CycloneDX SBOM is missing."
}
$sbom = Get-Content -LiteralPath $sbomFile.FullName -Raw | ConvertFrom-Json
$packageComponents = @($sbom.components | Where-Object {
    $_.type -in @("library", "platform") -and $_.version -and $_.purl -and $_.licenses
})
if ($sbom.specVersion -ne "1.5" -or $packageComponents.Count -lt 8) {
    throw "Installed SBOM does not contain the required versioned and licensed dependency inventory."
}
foreach ($requiredPackage in @("Qt", "KDE Frameworks", "QtKeychain", "cmark-gfm", "Python", "spacy", "Harper", "Princeton WordNet")) {
    if (-not ($packageComponents | Where-Object { $_.name -eq $requiredPackage })) {
        throw "Installed SBOM is missing package identity: $requiredPackage"
    }
}

$tcpSamplesTotal = 0
$peakTreeProcesses = 0
$monitoredSeconds = 0.0
for ($trial = 1; $trial -le $LaunchTrials; $trial++) {
    $process = Start-Process -FilePath $app -ArgumentList @("--disable-gpu", $sample) -PassThru -WindowStyle Hidden
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 100
            $process.Refresh()
            if ($process.HasExited) { throw "Installed application exited during startup trial $trial." }
        } while ($process.MainWindowHandle -eq 0 -and [DateTime]::UtcNow -lt $deadline)

        if ($process.MainWindowHandle -eq 0) {
            throw "Installed application did not create its main window during trial $trial."
        }
        $process.Refresh()
        if (-not $process.Responding) {
            throw "Installed application stopped responding during startup trial $trial."
        }

        $engineDeadline = [DateTime]::UtcNow.AddSeconds(5)
        do {
            $descendants = @(Get-ProcessDescendants -RootProcessId $process.Id)
            if ($descendants | Where-Object { $_.Name -eq "writer-engine.exe" }) { break }
            Start-Sleep -Milliseconds 50
        } while ([DateTime]::UtcNow -lt $engineDeadline)
        if (-not ($descendants | Where-Object { $_.Name -eq "writer-engine.exe" })) {
            throw "Bundled ThothPad Engine did not start during trial $trial."
        }

        # Continuous whole-descendant-tree monitoring across the full
        # acceptance scenario: launch -> engine start -> document open ->
        # analysis -> idle window. A background worker that opens a TCP
        # connection at any point in the tree must be caught, so the union of
        # every observed descendant PID is re-checked against
        # Get-NetTCPConnection on every sample until the tree is idle.
        $observed = [Collections.Generic.HashSet[int]]::new()
        [void]$observed.Add([int]$process.Id)
        $previousTreeCount = -1
        $stableSamples = 0
        $requiredStableSamples = 12   # 3 s of no new processes at 250 ms sampling
        $monitorDeadline = [DateTime]::UtcNow.AddSeconds(120)
        $monitorStart = [DateTime]::UtcNow
        $tcpSamples = 0
        while ($true) {
            $descendants = @(Get-ProcessDescendants -RootProcessId $process.Id)
            foreach ($descendant in $descendants) { [void]$observed.Add([int]$descendant.ProcessId) }
            if ($process.HasExited) { throw "Installed application exited before the idle window during trial $trial." }
            if ($observed.Count -gt $peakTreeProcesses) { $peakTreeProcesses = $observed.Count }
            $connections = Get-NetTCPConnection |
                Where-Object { $observed.Contains([int]$_.OwningProcess) }
            $tcpSamples++
            if ($connections) {
                throw "Process tree opened an unexpected TCP connection during trial $trial."
            }
            if ($observed.Count -eq $previousTreeCount) { $stableSamples++ } else { $stableSamples = 0 }
            $previousTreeCount = $observed.Count
            if ($stableSamples -ge $requiredStableSamples) { break }
            if ([DateTime]::UtcNow -gt $monitorDeadline) {
                throw "Process tree never reached an idle window during trial $trial."
            }
            Start-Sleep -Milliseconds 250
        }
        $monitoredSeconds += ([DateTime]::UtcNow - $monitorStart).TotalSeconds
        $tcpSamplesTotal += $tcpSamples
    }
    finally {
        if (-not $process.HasExited) {
            & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
            $process.WaitForExit(10000) | Out-Null
        }
    }
}

$after = (Get-FileHash -LiteralPath $sample -Algorithm SHA256).Hash
if ($before -ne $after) { throw "Opening the Unicode sample modified its contents." }

$uninstaller = Join-Path $installDir "Uninstall.exe"
$uninstall = Start-Process -FilePath $uninstaller -ArgumentList "/S" -Wait -PassThru -WindowStyle Hidden
if ($uninstall.ExitCode -ne 0) { throw "Silent uninstaller failed with exit code $($uninstall.ExitCode)." }
Start-Sleep -Milliseconds 500
if (Test-Path -LiteralPath $app) { throw "Uninstaller left the application executable behind." }

$evidence = [pscustomobject]@{
    installer = $installer.FullName
    launch_trials = $LaunchTrials
    installed_and_launched = $true
    main_window_responding = $true
    bundled_engine_started = $true
    deterministic_tcp_connections = 0
    process_tree_monitored = $true
    tcp_samples = $tcpSamplesTotal
    peak_tree_processes = $peakTreeProcesses
    tree_monitor_seconds = [math]::Round($monitoredSeconds, 1)
    unicode_sample_preserved = $true
    uninstall_completed = $true
}
$evidenceJson = $evidence | ConvertTo-Json
if ($EvidencePath) {
    $evidenceFile = [IO.Path]::GetFullPath($EvidencePath)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $evidenceFile) | Out-Null
    Set-Content -LiteralPath $evidenceFile -Value $evidenceJson -Encoding utf8
}
$evidenceJson
