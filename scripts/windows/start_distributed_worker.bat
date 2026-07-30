@echo off
setlocal
cd /d "%~dp0"

set "MASTER_WS=%~1"
if "%MASTER_WS%"=="" set "MASTER_WS=%MT_DIST_MASTER_WS%"
if "%MASTER_WS%"=="" set "MASTER_WS=ws://192.168.31.105:8765"

echo [dist-worker] master=%MASTER_WS%
echo [dist-worker] cwd=%CD%

rem Worker only sends rush packets and returns priorityRecordId.
rem Compose/verify/submit/pay stay on master. Force clean env on node.
set "MT_DIST_MEMORY_ONLY=1"
set "MT_DIST_RUSH_ONLY=1"
set "MT_USE_LEGACY_BB=0"
set "MT_RUSH_HTTP_NODE=1"
set "MT_BATCH_RUSH_NODE=1"
set "MT_GO_TLS_CLIENT=1"
set "MT_GO_TLS_CLIENT_PROFILE=chrome_138"
set "MT_TLS_ENGINE=reqrio"
if "%MT_HTTP_BACKEND%"=="" set "MT_HTTP_BACKEND=node"
if "%MT_CURL_WORKER_POOL%"=="" set "MT_CURL_WORKER_POOL=1"
if "%MT_GO_BRIDGE_PORT%"=="" set "MT_GO_BRIDGE_PORT=19091"

if "%MT_DIST_SKIP_DEP_CHECK%"=="" set "MT_DIST_SKIP_DEP_CHECK=0"
for /f "usebackq delims=" %%V in (`py -3 -c "from project_versions import BB_SIGN_WASM_VERSION; print(BB_SIGN_WASM_VERSION)"`) do set "BB_SIGN_WASM_VERSION=%%V"
if "%BB_SIGN_WASM_VERSION%"=="" set "BB_SIGN_WASM_VERSION=unknown"
echo [dist-worker] dep_check=%MT_DIST_SKIP_DEP_CHECK%
echo [dist-worker] memory_only=%MT_DIST_MEMORY_ONLY% rush_only=%MT_DIST_RUSH_ONLY% legacy_bb=%MT_USE_LEGACY_BB%
echo [dist-worker] go_bridge=%MT_GO_BRIDGE_PORT% tls_engine=%MT_TLS_ENGINE% tls_client=%MT_GO_TLS_CLIENT% tls_profile=%MT_GO_TLS_CLIENT_PROFILE% bb=stable_py_%BB_SIGN_WASM_VERSION%

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" distributed_worker.py "%MASTER_WS%"
) else (
  py -3 distributed_worker.py "%MASTER_WS%"
  if %ERRORLEVEL% NEQ 0 (
    echo [dist-worker] py -3 failed, try python...
    python distributed_worker.py "%MASTER_WS%"
  )
)

pause
