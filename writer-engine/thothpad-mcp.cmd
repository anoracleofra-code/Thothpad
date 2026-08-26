@echo off
setlocal
pushd "%~dp0"
py -3.11 -m backend.mcp_server
set EXITCODE=%ERRORLEVEL%
popd
exit /b %EXITCODE%
