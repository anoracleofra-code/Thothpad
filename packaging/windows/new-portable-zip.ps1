# SPDX-License-Identifier: GPL-3.0-or-later

# Packs a portable ZIP with forward-slash entry separators via the Windows
# bsdtar (tar.exe, Windows 10+). Compress-Archive stores backslash-separated
# entry names that POSIX unzip tools extract as flat files.
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$SourceDirectory,
    [Parameter(Mandatory)]
    [string]$DestinationPath
)

$ErrorActionPreference = "Stop"

$tar = (Get-Command tar.exe -ErrorAction Stop).Source
if (Test-Path -LiteralPath $DestinationPath) {
    Remove-Item -LiteralPath $DestinationPath -Force
}

Push-Location $SourceDirectory
try {
    & $tar -a -c -f $DestinationPath -- '*'
    if ($LASTEXITCODE -ne 0) { throw "tar.exe portable archive creation failed." }
} finally {
    Pop-Location
}
