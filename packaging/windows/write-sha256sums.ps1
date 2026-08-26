# SPDX-License-Identifier: GPL-3.0-or-later

# Writes SHA256SUMS.txt with LF-only line endings so `sha256sum -c` works
# identically on POSIX and Windows text tools.
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ArtifactsDirectory
)

$ErrorActionPreference = "Stop"

$lines = Get-ChildItem -LiteralPath $ArtifactsDirectory -File |
    Where-Object { $_.Name -ne "SHA256SUMS.txt" } |
    Sort-Object Name |
    ForEach-Object {
        "{0}  {1}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name
    }

$sumsPath = Join-Path $ArtifactsDirectory "SHA256SUMS.txt"
[IO.File]::WriteAllText($sumsPath, ($lines -join "`n") + "`n", [Text.UTF8Encoding]::new($false))
