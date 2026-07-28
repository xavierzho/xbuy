@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "TARGET_WEBUI=http://127.0.0.1:8989"
set "LOGIN_PORT=7979"
set "ORDER_PORT=6969"

set "PYEXE=python"
if exist ".venv\Scripts\python.exe" set "PYEXE=%CD%\.venv\Scripts\python.exe"

set "CLOUDFLARED=cloudflared"
if exist "%CD%\cloudflared.exe" set "CLOUDFLARED=%CD%\cloudflared.exe"

echo [public] login gate : http://127.0.0.1:%LOGIN_PORT% -^> %TARGET_WEBUI%/remote_login.html
echo [public] order gate : http://127.0.0.1:%ORDER_PORT% -^> %TARGET_WEBUI%/mobile_order.html
echo [public] console    : %TARGET_WEBUI% -^> WebUI console IP allowlist
echo [public] no token. Each port exposes only its own page and required APIs.
echo.

start "mt public login gate 7979" cmd /k "set PUBLIC_GATE_REQUIRE_TOKEN=0&& set PUBLIC_GATE_MODE=login&& set PUBLIC_GATE_PORT=%LOGIN_PORT%&& set PUBLIC_GATE_TARGET=%TARGET_WEBUI%&& "%PYEXE%" "%CD%\public_gate.py""
start "mt public order gate 6969" cmd /k "set PUBLIC_GATE_REQUIRE_TOKEN=0&& set PUBLIC_GATE_MODE=order&& set PUBLIC_GATE_PORT=%ORDER_PORT%&& set PUBLIC_GATE_TARGET=%TARGET_WEBUI%&& "%PYEXE%" "%CD%\public_gate.py""

timeout /t 1 /nobreak >nul

echo [public] starting three cloudflared tunnels...
echo [public] cloudflared quick tunnel does NOT keep the external port number.
echo [public] It will print three different https://xxxx.trycloudflare.com URLs:
echo     window "cloudflared login 7979" = 上号页
echo     window "cloudflared order 6969" = 查单页
echo.
echo [public] blocked:
echo     /  /ui.html  /api/accounts  and all non-whitelisted APIs
echo.
start "cloudflared login 7979" cmd /k ""%CLOUDFLARED%" tunnel --protocol http2 --url http://127.0.0.1:%LOGIN_PORT%"
start "cloudflared order 6969" cmd /k ""%CLOUDFLARED%" tunnel --protocol http2 --url http://127.0.0.1:%ORDER_PORT%"
start "cloudflared console 8989" cmd /k ""%CLOUDFLARED%" tunnel --protocol http2 --url %TARGET_WEBUI%"
pause
