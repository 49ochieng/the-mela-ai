Set-Location "$PSScriptRoot\backend"
$env:PYTHONPATH = "$PSScriptRoot\backend"
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
