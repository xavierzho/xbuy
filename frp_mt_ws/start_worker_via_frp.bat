@echo off
setlocal
cd /d "%~dp0\.."

set "MASTER_WS=%~1"
if "%MASTER_WS%"=="" set "MASTER_WS=ws://127.0.0.1:18765"

call start_distributed_worker.bat "%MASTER_WS%"
