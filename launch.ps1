# PowerShell launcher for Advance Clipboard
Set-Location -Path $PSScriptRoot

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "[Advance Clipboard] Launching with uv run..." -ForegroundColor Cyan
    uv run main.py
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    Write-Host "[Advance Clipboard] Launching with python..." -ForegroundColor Cyan
    python main.py
    exit $LASTEXITCODE
}

Write-Error "Neither 'uv' nor 'python' was found in PATH. Please install uv or Python 3.11+."
Read-Host "Press Enter to exit..."
