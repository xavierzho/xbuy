@echo off
setlocal
cd /d "%~dp0"

set "CONFIG=%CD%\frpc.toml"
set "SERVER_ADDR=220.197.14.204"

if not exist "frpc.exe" (
  echo [frp] missing frpc.exe
  echo [frp] put Windows amd64 frpc.exe in: %CD%
  pause
  exit /b 1
)

(
  echo serverAddr = "%SERVER_ADDR%"
  echo serverPort = 7000
  echo.
  echo auth.method = "token"
  echo auth.token = "c99893c0ae57d0f9186d9b1439b627ae85cbabc1c9503d49"
  echo.
  echo [[proxies]]
  echo name = "mt-master-ws"
  echo type = "tcp"
  echo localIP = "127.0.0.1"
  echo localPort = 8765
  echo remotePort = 18765
) > "%CONFIG%"

echo.
echo [frp] home master tunnel starting:
echo [frp] 127.0.0.1:8765 -^> %SERVER_ADDR%:18765
echo [frp] server worker ws: ws://127.0.0.1:18765
echo.
frpc.exe -c frpc.toml
pause
