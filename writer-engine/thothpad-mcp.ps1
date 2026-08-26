$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root
try {
  py -3.11 -m backend.mcp_server
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}
