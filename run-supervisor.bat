@echo off
setlocal
chcp 65001 > nul
set "SCRIPT_DIR=%~dp0"
set "SUPERVISOR_PY=%SCRIPT_DIR%supervisor.py"

where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    python "%SUPERVISOR_PY%" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    py -3 "%SUPERVISOR_PY%" %*
    exit /b %ERRORLEVEL%
)

echo [Error] Python 3 was not found in PATH. Please install Python 3.9+. >&2
exit /b 1
