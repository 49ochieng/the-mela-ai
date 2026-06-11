$ErrorActionPreference = "SilentlyContinue"

# Kill anything on port 8010
$pids = (netstat -ano | Select-String ":8010 ") | ForEach-Object { ($_ -split '\s+')[-1] }
$pids | Where-Object { $_ -match '^\d+$' } | ForEach-Object {
    Stop-Process -Id $_ -Force
    Write-Host "Killed PID $_ on 8010"
}

Start-Sleep -Milliseconds 500

# Confirm env
$fe = (Get-Content "c:\copilot\Mela Task Radar\apps\api\.env") | Select-String "FRONTEND_URL"
Write-Host "ENV CHECK: $fe"

# Start API
Set-Location "c:\copilot\Mela Task Radar\apps\api"
Write-Host "Starting API on :8010 ..." -ForegroundColor Cyan
.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8010 --reload
