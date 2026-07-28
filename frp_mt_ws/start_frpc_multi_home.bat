@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "LIST=%CD%\server_addrs.txt"
set "OUTDIR=%CD%\frpc_multi"
set "TOKEN=c99893c0ae57d0f9186d9b1439b627ae85cbabc1c9503d49"
set "LOCAL_PORT=8765"
set "REMOTE_PORT=18765"
set "DEFAULT_FRPS_PORT=7000"

if not exist "frpc.exe" (
  echo [frp-multi] missing frpc.exe
  echo [frp-multi] put Windows amd64 frpc.exe in: %CD%
  pause
  exit /b 1
)

if not exist "%LIST%" (
  echo [frp-multi] missing server_addrs.txt
  echo 220.197.14.204>"%LIST%"
  echo [frp-multi] created: %LIST%
  echo [frp-multi] edit it and run again.
  pause
  exit /b 1
)

if not exist "%OUTDIR%" mkdir "%OUTDIR%"

set /a COUNT=0
for /f "usebackq tokens=* delims=" %%L in ("%LIST%") do (
  set "LINE=%%L"
  for /f "tokens=* delims= " %%A in ("!LINE!") do set "LINE=%%A"
  if not "!LINE!"=="" if not "!LINE:~0,1!"=="#" (
    set "SERVER=!LINE!"
    set "FRPS_PORT=%DEFAULT_FRPS_PORT%"
    echo !SERVER! | findstr /r ":[0-9][0-9]*$" >nul
    if not errorlevel 1 (
      for /f "tokens=1,2 delims=:" %%H in ("!SERVER!") do (
        set "SERVER=%%H"
        set "FRPS_PORT=%%I"
      )
    )

    set "SAFE=!SERVER:.=_!"
    set "SAFE=!SAFE:-=_!"
    set "CFG=%OUTDIR%\frpc_!SAFE!_!FRPS_PORT!.toml"

    (
      echo serverAddr = "!SERVER!"
      echo serverPort = !FRPS_PORT!
      echo.
      echo auth.method = "token"
      echo auth.token = "%TOKEN%"
      echo.
      echo [[proxies]]
      echo name = "mt-master-ws"
      echo type = "tcp"
      echo localIP = "127.0.0.1"
      echo localPort = %LOCAL_PORT%
      echo remotePort = %REMOTE_PORT%
    ) > "!CFG!"

    set /a COUNT+=1
    echo [frp-multi] start !SERVER!:!FRPS_PORT!  127.0.0.1:%LOCAL_PORT% -^> !SERVER!:%REMOTE_PORT%
    start "frpc !SERVER!" cmd /k ""%CD%\frpc.exe" -c "!CFG!""
  )
)

echo.
echo [frp-multi] started %COUNT% frpc client(s).
echo [frp-multi] server worker uses: ws://127.0.0.1:%REMOTE_PORT%
echo.
pause
