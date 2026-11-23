# One-command start of the whole Kalpa stack on Windows (no Docker needed).
#   powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
# Starts Redis, Postgres (embedded, data in data\pgdata_e2e), the API (2 workers), the ARQ worker, the web app and MLflow,
# then prints the URLs. Stop everything with scripts\stop_demo.ps1. Close heavy desktop apps first: it needs ~2 GB free.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$redisExe = if ($env:KALPA_REDIS) { $env:KALPA_REDIS } else { "D:\redis\redis-server.exe" }

function Wait-Url($url, $secs) {
  $end = (Get-Date).AddSeconds($secs)
  while ((Get-Date) -lt $end) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 $url | Out-Null; return $true } catch { Start-Sleep 2 } }
  return $false
}

Write-Host "1/6 Redis"
if (-not (Get-Process redis-server -ErrorAction SilentlyContinue)) {
  Start-Process -WindowStyle Hidden -FilePath $redisExe -RedirectStandardOutput "$root\data\run\redis.log" -RedirectStandardError "$root\data\run\redis.err"
  Start-Sleep 3
}

Write-Host "2/6 Postgres (first start after a crash can take a minute)"
uv run --with pgserver python scripts/pg_start.py
if ($LASTEXITCODE -ne 0) { Write-Host "  pg_start timed out; waiting for Postgres recovery..."; Start-Sleep 60 }

# environment for the API and worker (data\run\env.sh is written by pg_start.py with the current Postgres port)
$envLine = (Get-Content "$root\data\run\env.sh" | Where-Object { $_ -match "DATABASE_URL" } | Select-Object -First 1)
$env:DATABASE_URL = ([regex]::Match($envLine, "DATABASE_URL=(\S+)")).Groups[1].Value
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
$env:MODEL_DIR = "$root\data\models"
$env:SIGNUP_PER_HOUR = "100000"; $env:DEMO_LOADS_PER_HOUR = "100000"; $env:COPILOT_PER_MINUTE = "100000"; $env:UPLOADS_PER_HOUR = "100000"

Write-Host "3/6 API (2 workers) on :8000"
Start-Process -WindowStyle Hidden -FilePath "uv" -ArgumentList "run uvicorn kalpa_api.main:app --port 8000 --app-dir services/api --workers 2" -RedirectStandardOutput "$root\data\run\api.log" -RedirectStandardError "$root\data\run\api.err"
Write-Host "4/6 Worker"
Start-Process -WindowStyle Hidden -FilePath "uv" -ArgumentList "run arq kalpa_worker.main.WorkerSettings" -RedirectStandardOutput "$root\data\run\worker.log" -RedirectStandardError "$root\data\run\worker.err"

Write-Host "5/6 Web on :3000"
if (-not (Test-Path "$root\apps\web\.next")) { Push-Location "$root\apps\web"; $env:NEXT_PUBLIC_API_URL = "http://localhost:8000"; pnpm build; Pop-Location }
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
Start-Process -WindowStyle Hidden -FilePath "pnpm.cmd" -ArgumentList "start" -WorkingDirectory "$root\apps\web" -RedirectStandardOutput "$root\data\run\web.log" -RedirectStandardError "$root\data\run\web.err"

Write-Host "6/6 MLflow UI on :5000"
Start-Process -WindowStyle Hidden -FilePath "uv" -ArgumentList "run --with flask --with flask-cors --with aiohttp --with waitress mlflow server --backend-store-uri sqlite:///data/mlflow.db --host 127.0.0.1 --port 5000" -RedirectStandardOutput "$root\data\run\mlflow.log" -RedirectStandardError "$root\data\run\mlflow.err"

$api = Wait-Url "http://localhost:8000/api/v1/health" 90
$web = Wait-Url "http://localhost:3000/login" 90
Write-Host ""
Write-Host ("API   http://localhost:8000/docs   " + $(if ($api) { "UP" } else { "NOT READY (see data\run\api.log / api.err)" }))
Write-Host ("Web   http://localhost:3000        " + $(if ($web) { "UP" } else { "NOT READY (see data\run\web.log / web.err)" }))
Write-Host "MLflow http://localhost:5000       (takes ~30 s to come up)"
Write-Host "Health: " (Invoke-WebRequest -UseBasicParsing http://localhost:8000/api/v1/health).Content
Write-Host "Demo logins (password demo-password-123): freshmart-owner@example.com / freshmart-planner@example.com / freshmart-viewer@example.com"
