# Stop the Kalpa stack (API, worker, web, MLflow). Add -All to also stop Redis and Postgres.
param([switch]$All)
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'kalpa_a[p]i.main:app|arq kalpa_worker|kalpa_worker.main|mlflow server|next.dist.bin.next' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
if ($All) { Get-Process redis-server, postgres -ErrorAction SilentlyContinue | Stop-Process -Force }
Write-Host "stopped"
