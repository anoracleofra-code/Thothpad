$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = "py"
$pythonArgs = @("-3.11")

if (!(Test-Path ".venv")) {
  & $python @pythonArgs -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"

Start-Process "http://127.0.0.1:8789"
& ".\.venv\Scripts\python.exe" -m backend.cli serve --host 127.0.0.1 --port 8789
