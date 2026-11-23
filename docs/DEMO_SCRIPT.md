# Demo script (8 minutes)

## Before the panel enters (5 min, do once)
1. Close other heavy apps (the stack needs about 2 GB of free RAM).
2. `powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1`, wait for "ready".
3. Check `http://localhost:3000` loads. If the FreshMart tenant is missing: `uv run python scripts/prepare_demo.py` (about 10 min, do it the day before, not live).
4. Sign in as `freshmart-owner@example.com` / `demo-password-123`. Open the Command Center, leave the tab open.
5. Open `http://localhost:8000/docs` and `http://localhost:5000` (MLflow) in two other tabs.

## Timeline

| Time | Screen | Say | Show |
|---|---|---|---|
| 0:00-0:30 | Landing page | "Kalpa turns probabilistic demand forecasts into inventory decisions, for a multi-store retailer." | The public forecast chart (synthetic Bengaluru FreshMart; say "synthetic" out loud). |
| 0:30-1:30 | Command Center | "Everything here is live." | Start the simulator (speed up). Sales tick, revenue animates, stream chip shows "live". Wait for a reorder alert toast. |
| 1:30-2:30 | Forecast Explorer | "Not a single number: a P10-P90 band, calibrated." | Pick Koramangala, category Sweets, Kaju Katli or Jalebi. Open Explain: variable weights, attention strip, narrative. "Numbers in the narrative are checked against the data by a guard." |
| 2:30-4:00 | Model Arena, then Cost tab | "Accuracy is not the business metric. We replay each model's reorder policy and count rupees." | Table of WAPE/MASE/coverage. Switch to cost: TFT has the lowest replay cost (₹1,05,130 vs ₹1,31,638 LightGBM, ₹1,49,867 Seasonal Naive) and 80 stock-out days vs 112. Read the numbers from the screen. |
| 4:00-5:00 | Scenario Studio | "What if we discount or add a festival?" | Add Pongal (2026-01-14) or raise promotion depth. Show baseline vs scenario units, revenue, stock-out risk. |
| 5:00-6:30 | Alert, Inventory, Purchase order | "From forecast to action." | Click the alert, Create PO (MOQ and pack rounding visible). Switch to owner approval, then receive: stock updates. Mention audit log and PDF export. |
| 6:30-7:30 | Jobs, Models, System | "The MLOps is visible." | Jobs: progress, cancel. Models: drift (PSI and live WAPE), MLflow link. System: p50/p95, queue, cache hit rate. Swagger tab in one click. |
| 7:30-8:00 | Close | "Probabilistic forecasts, decision-focused evaluation, live operations, secured and load-tested. Honest limits: synthetic data, no M5 run." | Stop. |

## If something goes wrong
- **Live tick not visible**: refresh once; the stream reconnects. Otherwise narrate from `artifacts/perf/simulator_soak_30min.json` (30 min, zero errors).
- **A page spins**: the API keeps a Redis cache; reload. If the worker shows "down" on System, run `scripts\start_demo.ps1` again.
- **TFT row missing from the arena**: the demo tenant was not prepared; show `docs/RESULTS.md` instead and say so.
- **Never** start a new TFT backtest live (about 10 min on CPU).

## Accounts (password `demo-password-123`)
`freshmart-owner@example.com` (all rights), `freshmart-planner@example.com`, `freshmart-viewer@example.com` (read-only: show that buttons are disabled and writes return 403).
