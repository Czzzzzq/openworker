@echo off
REM OpenWorker dev stack launcher (Windows)
REM
REM WHY THIS FILE MUST USE A NON-AppData STATE DIR:
REM This repo's venv is built on the Microsoft Store Python (MSIX-packaged). MSIX
REM virtualizes file writes under known folders (AppData\Roaming etc.) into the
REM package's LocalCache, INVISIBLE to non-packaged processes. So a plain
REM `openworker-server` run writes sidecar-8765.token into
REM ...\Packages\PythonSoftwareFoundation...\LocalCache\Roaming\coworker, while
REM Vite (Node) looks in %APPDATA%\coworker -> empty token -> every API call 401.
REM Pinning COWORKER_STATE_DIR to a path OUTSIDE known folders (repo-local
REM .dev-state) makes both processes see the same physical files.

setlocal
cd /d "%~dp0"
set "COWORKER_STATE_DIR=%~dp0.dev-state"

REM A stale sidecar token breaks the wait below (the file already exists, so Vite can
REM bake the OLD token while the server writes a new one). Always boot with a fresh file.
if exist "%COWORKER_STATE_DIR%\sidecar-8765.token" del "%COWORKER_STATE_DIR%\sidecar-8765.token" >nul 2>nul

REM 1) Server needs the model keys to fetch the official balance. Copy the
REM    desktop app's secret store once (sessions stay separate - dev uses a
REM    fresh store so it can't clobber the desktop app's data).
if not exist "%COWORKER_STATE_DIR%\secrets.json" (
    if exist "%USERPROFILE%\AppData\Roaming\coworker\secrets.json" (
        mkdir "%COWORKER_STATE_DIR%" 2>nul
        copy /y "%USERPROFILE%\AppData\Roaming\coworker\secrets.json" "%COWORKER_STATE_DIR%\secrets.json" >nul
        echo Copied existing model keys into .dev-state ^(balance will work^).
    )
)

REM 2) Start the backend server (writes .dev-state\sidecar-8765.token on boot).
start "openworker-server" cmd /k "set COWORKER_STATE_DIR=%COWORKER_STATE_DIR% && .venv\Scripts\openworker-server.exe --cwd "%~dp0" --port 8765"

REM 3) Wait for the token file so Vite embeds the right token.
:wait_token
if not exist "%COWORKER_STATE_DIR%\sidecar-8765.token" (
    timeout /t 1 /nobreak >nul
    goto wait_token
)

REM 4) Start the GUI dev server (reads the token file at startup).
start "openworker-gui" cmd /k "set COWORKER_STATE_DIR=%COWORKER_STATE_DIR% && cd /d "%~dp0surfaces\gui" && npm run dev"

echo.
echo OpenWorker dev stack is starting...
echo   state dir : %COWORKER_STATE_DIR%
echo   server    : http://127.0.0.1:8765
echo   GUI       : http://localhost:1420  (open in your browser)
echo Stop: close the two new console windows (or Ctrl+C in each).
endlocal
