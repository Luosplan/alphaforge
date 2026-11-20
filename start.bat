@echo off
setlocal enabledelayedexpansion
title AlphaForge Launcher
cd /d "%~dp0"

set "AF_PORT=8000"
set "WEB_PORT=5173"
set "PYEXE="
set "RELOAD=--reload --reload-dir packages/core --reload-dir packages/data --reload-dir packages/engine --reload-dir packages/api"

echo.
echo  ==========================================================
echo    AlphaForge  -  one click launcher
echo  ==========================================================
echo.

REM ---------------------------------------------------------------
REM  1. pick a python that actually has fastapi + uvicorn
REM     The default `python` on this machine often does not.
REM     Override with:  set AF_PY=C:\path\to\python.exe
REM ---------------------------------------------------------------
call :try_py "%AF_PY%"
call :try_py "%USERPROFILE%\miniconda3\python.exe"
call :try_py "%LOCALAPPDATA%\miniconda3\python.exe"
call :try_py "C:\ProgramData\miniconda3\python.exe"
call :try_py "C:\ProgramData\anaconda3\python.exe"
call :try_py "python"
call :try_py "python3"
call :try_py "py"

if not defined PYEXE (
    echo  [X] No Python found with fastapi + uvicorn installed.
    echo.
    echo      Install once:
    echo        python -m pip install "fastapi^>=0.115" "uvicorn[standard]^>=0.32" "pydantic^>=2"
    echo.
    echo      Or point this script at another interpreter:
    echo        set AF_PY=C:\path\to\python.exe
    echo.
    pause
    exit /b 1
)
echo  [OK] python   : %PYEXE%

REM ---------------------------------------------------------------
REM  2. node
REM ---------------------------------------------------------------
where node >nul 2>&1
if errorlevel 1 (
    echo  [X] Node.js not found in PATH. Install Node 20+ first.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('node -v') do set "NODEV=%%v"
echo  [OK] node     : !NODEV!

REM ---------------------------------------------------------------
REM  3. dependency / data sanity
REM ---------------------------------------------------------------
if not exist "packages\web\node_modules" (
    echo  [!]  packages\web\node_modules missing, running npm install ...
    pushd "packages\web"
    call npm install
    popd
)

if not exist ".env" (
    echo  [!]  .env missing. Copy .env.example to .env.
    echo       Without AF_LLM_BASE_URL / AF_LLM_API_KEY the AI analysis falls back to rule mode.
)

if not exist "data\alphaforge.db" (
    echo  [!]  data\alphaforge.db missing. Run the first sync:
    echo        python -m alphaforge_data sync --only all --workers 12
)

REM ---------------------------------------------------------------
REM  4. launch
REM     PYTHONPATH must be absolute: the child window inherits it.
REM     --reload-dir is listed one by one on purpose - watching the
REM     whole cwd includes data\, and autopilot writes there every
REM     30s, which would reload the server into a loop.
REM ---------------------------------------------------------------
if defined AF_DRY_RUN (
    echo.
    echo  [DRY RUN] checks passed, nothing launched.
    echo    python : %PYEXE%
    echo    ports  : %AF_PORT% / %WEB_PORT%
    echo.
    exit /b 0
)

set "PYTHONPATH=%CD%\packages\core;%CD%\packages\data;%CD%\packages\engine"

call :port_free %AF_PORT%
if errorlevel 1 (
    echo  [!]  port %AF_PORT% busy - backend already running, skipping.
) else (
    start "AlphaForge API" cmd /k ""%PYEXE%" -m uvicorn alphaforge_api.main:app --port %AF_PORT% --app-dir packages/api %RELOAD%"
    echo  [OK] backend  : http://127.0.0.1:%AF_PORT%/docs
)

call :port_free %WEB_PORT%
if errorlevel 1 (
    echo  [!]  port %WEB_PORT% busy - frontend already running, skipping.
) else (
    start "AlphaForge Web" /D "%CD%\packages\web" cmd /k "npm run dev"
    echo  [OK] frontend : http://localhost:%WEB_PORT%
)

echo.
echo  Two windows opened. Close them to stop the servers.
echo  Frontend opens in ~6s ...
timeout /t 6 /nobreak >nul
start "" "http://localhost:%WEB_PORT%"

echo.
exit /b 0

REM ===============================================================
REM  helpers
REM ===============================================================
:try_py
if defined PYEXE goto :eof
if "%~1"=="" goto :eof
"%~1" -c "import fastapi, uvicorn" >nul 2>&1
if not errorlevel 1 set "PYEXE=%~1"
goto :eof

:port_free
netstat -ano | findstr ":%~1 " | findstr "LISTENING" >nul 2>&1
if errorlevel 1 (exit /b 0) else (exit /b 1)
