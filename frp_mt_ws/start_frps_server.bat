@echo off
setlocal
cd /d "%~dp0"

if not exist "frps.exe" (
  echo [frp] missing frps.exe
  echo [frp] put Windows amd64 frps.exe in this folder.
  pause
  exit /b 1
)

echo [frp] start frps: bind 7000, local worker ws 127.0.0.1:18765
frps.exe -c frps.toml
pause
