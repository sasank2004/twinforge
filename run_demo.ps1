# run_demo.ps1 - launch TwinForge (single FastAPI process serves API + UI).
# Usage:  ./run_demo.ps1        then open http://localhost:8000
$ErrorActionPreference = "Stop"
$py = "E:\Code\AIC\.venv\Scripts\python.exe"
Set-Location $PSScriptRoot

Write-Host "Freeing port 8000..."
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

$env:PYTHONPATH = $PSScriptRoot
Write-Host "Starting TwinForge on http://localhost:8000 ..."
& $py -m uvicorn src.api.main:app --port 8000 --host 127.0.0.1
