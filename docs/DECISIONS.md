# Decisions

- **uv workspace at repo root** (members: api, worker, ml): one lockfile, one `uv sync --all-packages`.
- **TypeScript pinned to 6.0.3**: TS 7.0 breaks openapi-typescript and typescript-eslint (both crash on import).
- **OpenAPI export via Python script, not a running server**: `pnpm gen:api` works in CI with no services up.
- **Health returns 200 + `degraded`** when a dependency is down, so the /status page can render red pills instead of failing.
- **Worker liveness = Redis heartbeat key (30 s TTL)** refreshed every 10 s by an ARQ cron; API reads the key.
- **Next `output: standalone`** for a small web Docker image.
- **Portable column types (Uuid, JSON)** instead of Postgres-only (JSONB, citext): the same models run on SQLite for fast tests. Revisit if JSONB indexing is needed. `users.email` is lower-cased in the service rather than citext.
- **All ORM models in one module** (`db/models/__init__.py`) rather than one file per table - 24 small tables.
- **Festival calendar**: library-derived where the `holidays` package has the festival, hand-curated (flagged) otherwise - no dates invented silently.
- **Intermittent SKUs are an exact share**, not Bernoulli, so the 15-25 % requirement always holds.
- **Seeds wipe-and-reload** a tenant's data for idempotency; uploads replace only the affected (location, sku, date-range).
- **Role is looked up per request, not stored in the JWT**: revocation/role changes take effect immediately (costs one indexed query).
- **Stock decrement = conditional `UPDATE ... WHERE on_hand >= qty`** (+ rowcount check) instead of SELECT FOR UPDATE: atomic on Postgres and SQLite, no lock-ordering bugs. Lines are processed in sorted product order.
- **Idempotency key inserted in the same transaction as the sale**: a failed sale rolls the key back so the client can retry with the same key.
- **Audit written in the repo layer** (before/after available) rather than middleware.
- **Test SQLite engine uses explicit `BEGIN IMMEDIATE`**: pysqlite otherwise commits on SAVEPOINT release, which silently broke rollback semantics in tests.
- **`tzdata` is a dependency**: Windows has no system tz database for `zoneinfo`.
- **Phase 4 baselines**: SeasonalNaive(7), AutoETS, CrostonOptimized (statsforecast); LightGBM = Tweedie point forecast (P50 proxy) + two quantile models (P10/P90) via mlforecast with lags 7/14/28 + rolling means + known-future covariates. Raw bands are clipped to p10 <= p50 <= p90; conformal calibration comes in Phase 5.
- **Croston has no interval** (statsforecast limitation): it is scored with median pinball only and `coverage80 = null`, never a fake band.
- **Backtest stores the last fold's forecasts** (28 d, compared with real actuals) - true future forecasts need planned promo/price inputs and arrive with Phase 5/6.
- **Jobs run in the ARQ worker; `INLINE_JOBS=true` runs them inside the API** for development without a worker. Both call the same service function.
- **M5 not available** here, so `artifacts/benchmarks/baselines.json` is on the synthetic FreshMart data and says so in its `dataset` block.
- **Leakage guard is tested by corrupting the held-out targets** and requiring byte-identical forecasts, rather than by trusting the features.

## Phase 7 - Explainability & Copilot
- **SHAP for LightGBM uses LightGBM's built-in TreeSHAP** (`booster.predict(pred_contrib=True)`), not the `shap` package: it is exact for trees, adds no dependency, and is the same algorithm. Values are on the model's log-demand scale (Tweedie link) and the UI says so.
- **SHAP is computed on the exact feature frames mlforecast builds** at each recursive step (captured with `before_predict_callback`), and only the target model's block of `h` frames is used (mlforecast recurses model by model; later steps depend on that model's own earlier predictions - verified against the installed mlforecast 1.1.0).
- **TFT explanations are stored per series** in `forecast_explanations` (static / encoder / decoder weights time-averaged; attention = decoder rows over the lookback columns, averaged over the horizon). The full [H, L] attention matrix is not stored: it would be ~3,000 numbers per series.
- **Narrative = deterministic template first.** Facts JSON -> sentence; an optional LLM may reword it, but every number in its output must occur in the facts JSON (`is_grounded`), else the template is used. Ids (`run_id` etc.) are excluded from the allowed-number set so their digits cannot whitelist figures.
- **No `anthropic` SDK**: the optional LLM calls the Messages API with `httpx` (already needed) so the app has no extra dependency and tests can inject a fake transport. Nothing is sent unless `ANTHROPIC_API_KEY` is set, and only structured facts / tool results are sent.
- **Copilot works without a key**: a rule-based router picks one of the five read-only tools by keywords and renders the answer from the tool result. With a key, the LLM does tool-calling; its answer is accepted only if it called a tool and every number is in the tool results, otherwise the rule-based answer is returned. Out-of-scope questions are refused. Prompts and answers are logged (structlog), never keys.
- **Copilot never sums quantiles**: `get_forecast` returns P50 totals for 7/14/28 days only (a P90 of a week is not the sum of daily P90s).
- **Replay cost needs >= 2 folds** (the policy uses the previous fold's residuals for safety stock), so the copilot says "no replay cost is available" for 1-fold runs instead of guessing.

## Phase 8 - Real-time operations & visible MLOps
- **One Redis pub/sub channel per org, relayed as SSE** (`GET /stream/events`, cookie auth). Publishers: POS checkout (sale, reorder alert), simulator (sim), jobs (progress), drift check (alert). Short capped Redis lists (feed 60, alerts 50) let a page catch up; `GET /live/state` is read on load and after every reconnect, so events missed during an outage cost nothing. A failed publish never undoes a committed sale.
- **Reorder alert = exact crossing inside the stock `UPDATE ... RETURNING`**: alert only when this sale moved on-hand from above the reorder point to at or below it, so concurrent sales cannot double-fire. Reorder point = forecast demand over the supplier lead time + safety stock (the same maths as the recommendations), stored on `inventory_positions` after every backtest.
- **The simulator runs on a simulated business calendar** starting the day after the data ends, not on the real date. Writing to the real date would leave rows a year after the data, corrupt "latest data" for backtests and fake drift. It sells through the REAL `checkout()` (audited `simulated: true`), follows the forecast P50 x a demand multiplier with Poisson noise and an hourly traffic profile, and only exists on the demo tenant (`orgs.is_demo`). Refused sales (empty shelf) are counted as lost units: stock is never negative.
- **The business day shown on the Command Center follows the simulator** even when its calendar is earlier than the real date (a reducer bug found in the browser; unit-tested).
- **Drift = PSI on scaled units/price (training window vs new complete days) + live WAPE vs backtest WAPE**; alert at PSI > 0.2 or WAPE > 20 % worse. PSI needs >= 500 new rows: on 75 rows identical-distribution data scored 0.217 (PSI over 10 bins is biased upward by ~9/n), which would be a false alarm. An alert fires once per model version, then starts a retrain.
- **The drift query reads forecasts and sales as two indexed ranges and merges in pandas** (a SQL join took 72 s on a large, freshly bulk-loaded multi-tenant table: bad plan from stale statistics; now 0.8 s). `ANALYZE forecasts` runs after each backtest's bulk insert.
- **Training data is zero-filled** (`frames.zero_fill`): checkout and the simulator only write a row when something sold, which made statsforecast fail with "missing or duplicate timestamps" on the first retrain after POS data (found end to end). Fills up to the last day on which >= 50 % of series have a row, never into today.
- **Cancel = status change + ARQ abort + checkpoints**: the status flips at once; a running backtest raises `JobCancelled` at its next checkpoint, so a late success can never overwrite `cancelled`. The CPU-bound thread itself cannot be interrupted and finishes in the background.
- **Silent session refresh**: the 15-minute access cookie used to bounce long-lived pages to /login. `api-client` now refreshes once on 401 and retries; refresh tokens are one-time use, so refreshes are serialised with Web Locks and a CSRF-cookie change tells a second tab that someone already refreshed. The EventSource cannot do this itself, so on a closed stream the provider refreshes and reopens with back-off.
- **Toasts follow the app theme and drop `richColors`** (its tinted backgrounds and Sonner's default action button failed WCAG contrast); the action button uses our audited primary colours.
- **MLflow per model run** (`log_run_ref`): each model run stores its MLflow run and experiment id, so the registry links straight to the run and its WAPE equals the registry's WAPE (checked in the browser test). The MLflow UI server needs `flask`, `flask-cors` and `aiohttp` beyond the pinned `mlflow` (the Docker image has them).
- **Scheduled work**: ARQ cron `scheduled_retrain` (Mondays 02:00) and `drift_check` (daily 03:00, auto-retrain on alert); both bodies are plain functions also reachable from the UI ("Retrain now", "Check drift now"). Cron times are constants shared with the schedule text on the Models page.
- **Coverage tracing** uses `concurrency = ["greenlet", "thread"]` (Phase 7 finding).

## Phase 9
- **Rate limits count the TCP peer address, not `X-Forwarded-For`**: a client can forge that header, which would bypass limits. Behind a trusted proxy this must be configured explicitly. Redis errors fail open so an outage never blocks logins.
- **Cache invalidation by group**: a write bumps only the groups it can affect (POS and purchase orders bump "stock"; anything unknown bumps all). A sale therefore does not throw away the model leaderboard. TTL only bounds staleness from background writers.
- **Shared engine and pool** (`db/session.py`): the first load test showed a new engine per request. Fixed before any tuning.
- **Argon2 in threads** (`asyncio.to_thread`): memory-hard hashing blocked the event loop for everyone.
- **Production guard** refuses to start with default secrets; development stays frictionless.
- **Skipped on purpose (time)**: strict CSP, Lighthouse, Docker and CI runs. Recorded as not done in README and PROGRESS.
