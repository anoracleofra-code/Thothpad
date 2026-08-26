@echo off
setlocal
pushd "%~dp0"
py -3.11 -m backend.cli %*
set EXITCODE=%ERRORLEVEL%
popd
exit /b %EXITCODE%
