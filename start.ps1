# Meituan Hackathon - one-command launcher
# Usage: powershell -ExecutionPolicy Bypass -File .\start.ps1 [ANTHROPIC_API_KEY]

param(
    [string]$ApiKey = $env:ANTHROPIC_API_KEY
)

$ROOT = $PSScriptRoot
$PYTHON = "C:\Users\AIR\.conda\envs\MeiTuan\python.exe"

Write-Host "=== Meituan Local-Life Agent - starting ===" -ForegroundColor Cyan

if ($ApiKey) {
    $env:ANTHROPIC_API_KEY = $ApiKey
    Write-Host "Anthropic API Key: configured" -ForegroundColor Green
} else {
    Write-Host "Anthropic API Key: not configured; using .env or fallback planner" -ForegroundColor Yellow
}

Write-Host "`n[1/3] Starting Mock API (port 8000)..." -ForegroundColor Yellow
$mockJob = Start-Job -ScriptBlock {
    param($root, $python)
    Set-Location "$root\mock_api"
    $env:PYTHONIOENCODING = "utf-8"
    & $python app.py
} -ArgumentList $ROOT, $PYTHON

Start-Sleep -Seconds 2

Write-Host "[2/3] Starting Agent backend (port 8002)..." -ForegroundColor Yellow
$backendJob = Start-Job -ScriptBlock {
    param($root, $python)
    Set-Location $root
    $env:PYTHONIOENCODING = "utf-8"
    & $python -m uvicorn backend.main:app --host 0.0.0.0 --port 8002 --reload
} -ArgumentList $ROOT, $PYTHON

Start-Sleep -Seconds 2

Write-Host "[3/3] Starting frontend (port 5173)..." -ForegroundColor Yellow
$frontendJob = Start-Job -ScriptBlock {
    param($root)
    Set-Location "$root\frontend"
    npm run dev
} -ArgumentList $ROOT

Start-Sleep -Seconds 3

Write-Host "`n=== Services started ===" -ForegroundColor Green
Write-Host "Frontend: http://localhost:5173" -ForegroundColor Cyan
Write-Host "Agent:    http://localhost:8002" -ForegroundColor Cyan
Write-Host "Mock API: http://localhost:8000" -ForegroundColor Cyan
Write-Host "`nPress Ctrl+C to stop all services." -ForegroundColor Gray

try {
    while ($true) {
        $mockOut = Receive-Job $mockJob -ErrorAction SilentlyContinue
        $mockErr = Receive-Job $mockJob -ErrorAction SilentlyContinue -ErrorVariable mockReceiveErr
        $backOut = Receive-Job $backendJob -ErrorAction SilentlyContinue
        $frontOut = Receive-Job $frontendJob -ErrorAction SilentlyContinue
        if ($mockOut) { Write-Host "[Mock] $mockOut" -ForegroundColor DarkGray }
        if ($backOut) { Write-Host "[Agent] $backOut" -ForegroundColor DarkGray }
        if ($frontOut) { Write-Host "[Frontend] $frontOut" -ForegroundColor DarkGray }
        Start-Sleep -Seconds 2
    }
} finally {
    Write-Host "`nStopping services..." -ForegroundColor Yellow
    Stop-Job $mockJob, $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Remove-Job $mockJob, $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Write-Host "Stopped." -ForegroundColor Green
}
