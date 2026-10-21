@echo off
title Advance Clipboard
cd /d "%~dp0"

where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [Advance Clipboard] Starting via uv run...
    uv run main.py
    goto :eof
)

where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [Advance Clipboard] Starting via python...
    python main.py
    goto :eof
)

echo [ERROR] Neither 'uv' nor 'python' was found in PATH.
echo Please install uv (https://docs.astral.sh/uv/) or Python 3.11+.
pause
