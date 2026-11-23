# Progress

## Phase 0 - Foundations & repo setup  (code complete; Docker + CI not yet verified)

Done and verified locally (2026-11-21):
- [x] uv workspace; `ruff` clean, `mypy --strict` clean, `pytest` 4 passed (api health x2, worker heartbeat, ml smoke)
- [x] FastAPI app factory + `GET /api/v1/health` (version, git SHA, db, redis, worker)
- [x] Next.js 16 + Tailwind v4 + design tokens; `/status` page with 4 pills; `pnpm build` succeeds
- [x] `pnpm gen:api` -> `artifacts/openapi.json` -> `apps/web/lib/api-schema.d.ts`; `pnpm lint`, `typecheck`, `test` run clean
- [x] Versions pinned (`docs/VERSIONS.md`, `uv.lock`, `pnpm-lock.yaml`)

NOT verified (checklist items still open):
- [ ] `docker compose up` healthy in < 2 min - Docker is not installed on this machine; compose + Dockerfiles are written but untested
- [ ] `/status` shows 4 green pills - needs the running stack
- [ ] CI green on first push - not a git repo yet / not pushed
- [ ] shadcn/ui init and pre-commit hook install deferred (no component needs them until Phase 3)

## Phase 1 - Data layer, benchmark & synthetic generator  (code complete; M5 + Postgres not verified here)

Verified 2026-11-21 (21 pytest tests, ruff + mypy --strict clean):
- [x] Alembic migration `0001` creates all 24 tables (23 from D2 + `uploads`); upgrade and downgrade ran on SQLite
- [x] Pandera validation rejects a broken CSV with readable messages (API returns 422 `{code, message, details}`; nothing is loaded)
- [x] Quality report per commit: rows in/out, duplicates, % missing, IQR outliers, date gaps, score (gaps measured before zero-fill)
- [x] CSV upload -> rapidfuzz mapping suggestion -> preview + validation -> commit; tenant isolation tested (org B gets 404)
- [x] Seeded Indian generator: full demo seed = 1,800 series (6 stores x 300 SKUs), 1,972,800 sales rows; **run twice with identical counts (idempotent)**
- [x] Generator tests: reproducible per seed, Diwali uplift for Kaju Katli > 1.8x baseline, intermittent share within 15-25 %
- [x] `docs/GENERATOR_CARD.md` rendered from the live config
- [x] Open-Meteo fetch with on-disk cache (tested with a mocked HTTP call; **not run against the live API**)
- [x] M5 loader (subset by store/dept/top-N, melt, join calendar + prices) tested on a tiny M5-format fixture
- [x] Temporary `/admin/data` stats page; typed client regenerated

NOT verified / open:
- [ ] Benchmark tenant >= 3,000 series - needs the Kaggle M5 files (`data/raw/m5`); loader is untested on real data
- [ ] Migration + seeds on real PostgreSQL 16 - only SQLite available here (types are portable, but unproven)
- [ ] Festival dates for Ganesh Chaturthi, Onam, Pongal/Sankranti, Durga Puja are hand-curated (`source=curated-verify`) - check against an official calendar
- [ ] Data endpoints use a temporary `X-Org-Id` header, not auth - replaced in Phase 2; do not expose publicly
- [ ] `stockout_flag` is False at ingest (no on-hand history yet); weather seeded for the demo tenant is synthetic, not Open-Meteo
- [ ] Bulk load is row-batched INSERT (~150 s for 2M rows on SQLite); Postgres COPY would be faster

## Phase 2 - Backend core: auth, tenancy, RBAC, CRUD, POS  (code complete; Postgres/real Redis not verified here)

Verified 2026-11-21: 97 pytest tests pass; API coverage 84 % (target 80 %); ruff + mypy --strict clean; web typecheck/lint clean.
- [x] Auth: signup / login / refresh / logout / me / switch-org; argon2id; JWT in HttpOnly cookies (access 15 min, refresh 7 d, refresh path-scoped); CSRF double-submit on all state-changing requests
- [x] Login rate limit (5 failures per IP+email -> 429, resets on success); same error + dummy hash for unknown users
- [x] Refresh tokens are one-time-use and revoked on logout (Redis denylist); removed members lose access on the next request (membership re-checked per request)
- [x] Tenant isolation: `OrgRepo` filters every query by org_id; parametrised suite proves org B gets 404 on GET/PATCH/DELETE for all 5 resources, empty lists, cannot reference org A's FKs (422), cannot sell from org A's store, audit log isolated
- [x] RBAC matrix (Part D3) written out independently in the tests and checked per role x permission; store_manager restricted to their own stores (POS + inventory reads)
- [x] Paginated / sortable / filterable CRUD: locations, suppliers, products, supplier-products, inventory, promotions, members; unknown sort/filter -> 422
- [x] POS checkout: GST + discount (Decimal), Idempotency-Key (replay returns the same receipt; reuse with a different body -> 422), atomic stock decrement via conditional UPDATE, sales_daily upsert, audit row, Redis pub/sub event
- [x] **50 parallel checkouts on 20 units of stock: exactly 20 succeed, 30 get 409, stock ends at 0, sales_daily = 20**; multi-line sale with one short line leaves the other line untouched
- [x] Audit log with before/after JSON (written in the same transaction as the change); `GET /audit` (owner only)
- [x] Uniform error shape `{code, message, details, request_id}`, `X-Request-ID`, security headers, CORS allow-list, request latency pushed to Redis for the Phase 8 System page
- [x] Migration 0002 (membership store scope, idempotency keys); data endpoints now use real auth (temporary X-Org-Id removed)

NOT verified / open:
- [ ] Everything ran on SQLite + fakeredis. The concurrency result is therefore for SQLite (writes serialised by `BEGIN IMMEDIATE` in the test engine); the decrement is a single conditional UPDATE, which is atomic on Postgres too, but re-run the 50-way test against real Postgres
- [ ] Invites: members are added directly (owner supplies a temporary password). No emailed invite token yet - needed for the Phase 3 "invite-accept" page
- [ ] Audit rows are written by the repository/services (so they carry before/after), not by a blanket middleware; auth failures other than lockouts are not audited
- [ ] No Swagger "example payloads" yet; pip-audit / secrets scan are Phase 9
- [ ] `COOKIE_SECURE` defaults to false for local http; set true in production

## Verification run on a real PostgreSQL (2026-11-21) - Docker still NOT available

Docker could not be installed from the session: not elevated, WSL not installed, Docker Desktop needs admin + reboot.
To finish Phase 0, run once in an **admin** PowerShell: `wsl --install`, reboot, `winget install Docker.DockerDesktop`, reboot,
then `docker compose up --build` and `docker compose ps`. A Redis download was also blocked (unverified binary), so Redis was faked.

What was verified instead (embedded PostgreSQL 16.2 via `pgserver`, no admin needed):
- [x] Alembic 0001 -> 0002: upgrade, downgrade to base, upgrade again - clean on real Postgres (closes the Phase 1 Postgres item for the schema)
- [x] Full API suite (83 tests) passes on real Postgres via `TEST_DATABASE_URL`, including the **50 parallel POS checkouts (20 sold / 30 rejected / stock 0)** - now genuinely concurrent, not serialised
- [x] uvicorn API on Postgres: `/api/v1/health` -> db `up`, `/docs` 200, CORS allows only `http://localhost:3000`, X-Request-ID present
- [x] Next.js production server: `/` and `/status` return 200
- [ ] Worker heartbeat / "4 green pills": NOT verified. ARQ needs real Redis (`INFO` pipeline); the fakeredis TCP server drops the connection. Health correctly reported `worker: down` and `redis: up` (fake)
- [ ] Pills rendering in a browser, `docker compose` health checks, Dockerfile builds, CI: still unverified
- [ ] Phase 1 seeds on Postgres (full 2M-row demo seed) not re-run there yet

Run the suite on Postgres: `TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest services/api`

## Phase 3 - Frontend shell & design system  (browser-verified; Lighthouse + a few items open)

Verified 2026-11-21 in **real Google Chrome via Playwright**, against the API on **real PostgreSQL 16.2** (`pnpm e2e`): 6/6 journeys pass.
- [x] Landing page (static sections) renders; no horizontal overflow at 375 px
- [x] **signup -> onboarding (org, first store, load demo data) -> inventory**: 600 rows visible (demo-lite load = 219,000 sales rows, ~20-25 s)
- [x] Ctrl+K palette navigates; sidebar/keyboard focus rings; theme toggle; auth guard (signed-out /app/* redirects to /login)
- [x] POS: search, cart, GST/discount, checkout via Idempotency-Key, receipt, **PDF receipt downloads** (`@react-pdf/renderer`)
- [x] Data wizard: bad CSV shows readable validation errors and nothing loads; good CSV commits (zero-filled gap, quality score row)
- [x] Settings: org rename, members (add / change role / remove), suppliers; org switcher; onboarding "Load demo data" endpoint (owner only, empty org only; 409 otherwise - unit-tested)
- [x] No horizontal overflow at 375 px on overview, inventory, POS, settings, data
- [x] Light + dark screenshots of every page in `artifacts/screenshots/`; `pnpm typecheck`, `lint`, unit tests, `build` clean

Still open:
- [ ] **Lighthouse (landing >= 95, app >= 85) NOT run** - the run was blocked (unpinned npx download). Run it yourself in Chrome DevTools or `npx lighthouse http://localhost:3000`
- [ ] Live fan chart in the landing hero: needs forecasts + a public read-only endpoint (Phase 4/5); `FanChart` itself exists and is shown in the dev-only `/app/ui-kit`
- [ ] Global store/date filter (nuqs), notifications bell (Phase 8), invite-accept page + emailed invites, HeatmapGrid, ErrorBoundary, chart-data-as-table fallback, axe checks (Phase 9)
- [ ] Products have no price master yet - POS takes the unit price per line
- [ ] shadcn/ui not used (hand-written components against the tokens)
- [ ] Docker / worker / CI still unverified (see above)

How to rerun the browser check: start Postgres + Redis (or the fake), `alembic upgrade head`, API on :8000, `pnpm build && pnpm start` on :3000, then `pnpm --filter web e2e`.


## Phase 4 - Forecasting v1: baselines, backtesting, MLflow  (verified on synthetic data; M5 + worker + Docker open)

Verified 2026-11-21: 117 pytest tests pass (83 % coverage), ruff + mypy --strict clean, web typecheck/lint clean, **7/7 Playwright journeys in real Chrome** (incl. Forecast Explorer).
- [x] `metrics.py` (WAPE, MASE, bias, pinball, coverage@80) - unit-tested against hand-calculated values
- [x] `features.py` (calendar sin/cos, festival flags + days-to-festival, price ratio, promo) and `backtest.py` (rolling origin, 3 x 28 d, gap 0)
- [x] **Leakage test**: setting every held-out target to 1,000,000 changes the scored actuals but leaves all forecasts identical
- [x] Baselines: SeasonalNaive(7), AutoETS, CrostonOptimized (statsforecast), LightGBM-Tweedie + P10/P90 quantile models (mlforecast); logged to MLflow (SQLite store `data/mlflow.db`)
- [x] DB-backed job `execute_backtest` -> `model_runs`, `backtest_results`, `forecasts`, `jobs` rows; API `POST /models/backtests`, `GET /models/runs|arena`, `GET /forecasts/series|summary`, `GET /jobs`; owner/planner only; tenant-isolated (tested)
- [x] Forecast Explorer UI: store / product / model pickers, FanChart with history + last-fold forecast band, metrics panel with `source: run`, baseline leaderboard, "Run baseline backtest" button (about 3 min on 600 series x 365 d)
- [x] Bugs found and fixed by tests/browser run: NaN festival gap after the last festival date; missing interval bounds silently filled with the median (fake zero-width band for Croston); backtest crashed when a live POS sale for "today" or a short/early-ending series made series ragged - now anchored on the common end date, short series skipped (`series_skipped` recorded per fold)

`artifacts/benchmarks/baselines.json` (600 series, 2024-2025, 3 folds, **SYNTHETIC FreshMart data - not M5**), mean over folds:

| model | WAPE | MASE | bias | pinball | coverage@80 |
|---|---|---|---|---|---|
| seasonal_naive | 0.536 | 0.993 | -0.051 | 1.456 | 0.927 |
| auto_ets | 0.400 | 0.760 | -0.029 | 1.073 | 0.827 |
| croston | 0.417 | 0.785 | -0.053 | 1.748 | n/a |
| lightgbm_tweedie | 0.401 | 0.768 | -0.004 | 1.069 | 0.865 |

Sanity check from code: `lightgbm_beats_seasonal_naive_wape = True`. AutoETS and LightGBM are within about 0.5 points of WAPE, so the covariates give LightGBM no clear edge on this data. Raw LightGBM bands (86 % coverage) are close to nominal 80 %; SeasonalNaive bands are too wide (93 %). Calibration comes in Phase 5.

Open:
- [ ] **M5 baseline table** (acceptance asks for the M5 subset): needs the Kaggle files; the JSON says `is_m5: false` and must not be cited as M5
- [ ] **Worker path unverified**: the ARQ job `run_backtest_job` and `POST /models/backtests` enqueue need a real Redis. Everything above ran with `INLINE_JOBS=true`; the progress bar is therefore only meaningful with the worker
- [ ] `run_forecast` for true future days (needs planned promo/price inputs) - the explorer shows the last backtest fold, not future days; the landing hero fan chart still waits on this
- [ ] Per-category / per-level metrics and hierarchy reconciliation (Phase 5); MLflow UI screenshot not taken
- [ ] Docker / CI unverified; Lighthouse not run

## Real Redis + worker verification (2026-11-21)

Environment: real Redis 5.0.14 (Windows build, user-installed on :6379), embedded PostgreSQL 16.2, API + ARQ worker as separate processes, `INLINE_JOBS` off.
- [x] `/api/v1/health` -> `{status: ok, db: up, redis: up, worker: up}` (first fully green health)
- [x] **Queue path**: `POST /models/backtests` returned `queued`; the ARQ worker picked it up (`run_backtest_job` in worker log, 30 s); the jobs row went queued -> running (0.05 / 0.15 / 0.95) -> succeeded; 2 model runs + 33,600 forecast rows persisted; arena populated (LightGBM WAPE 0.397 vs SeasonalNaive 0.534 on that org's data)
- [x] **Status page in real Chrome shows 4 green pills** (`status.spec.ts`, screenshot `artifacts/screenshots/status-green.png`)
- [ ] Docker (`docker compose up`, image builds, container health checks) still NOT verified - Docker not installed
- [ ] CI on GitHub still NOT verified - not a git repository / not pushed

## Premium frontend pass, leftovers, and verification round (2026-11-21)

Verified in real Chrome against real Redis + PostgreSQL + the ARQ worker:
- [x] **Real Redis + worker**: queue -> worker -> DB path proven; `/status` shows 4 green pills
- [x] **Forecast Explorer backtest through the real worker queue passes (7 min)**. This exposed a real bug: ARQ's default 300 s job timeout killed long backtests and left the job row "running" forever. Fixed: worker `job_timeout=3600`, `max_tries=1`; cancellation now marks the job `failed` with a message (regression test); UI stops polling after 45 min
- [x] **True future forecasts**: `forecast_future` (+28 days, assumes no promo, flat price, documented); stored with every backtest run; Explorer shows backtest fold + future; "today" marker at the data end; public `GET /public/demo-forecast` (demo tenant only, rate-limited, cached) powers the live landing-page chart
- [x] **axe-core: 0 WCAG 2.0/2.1 A+AA violations on 10 pages in light and dark** (initial run found contrast, ARIA-structure, scroll-focus and label-mismatch issues; all fixed via tokens/markup)
- [x] **Invite flow** (shareable one-time link, 7 days, token hashed at rest, existing users prove their password) with 6 API tests; migration 0003; `/invite` page; Settings invite UI. **No email is sent** (no provider configured)
- [x] Global store + range filter (URL state via nuqs), notifications bell (real job outcomes), HeatmapGrid (+ table fallback), ErrorBoundary
- [x] **Premium design pass**: Geist type, layered tokens (elevation, tints), icon set + brand mark, grouped sidebar, blurred top bar, `.card/.btn/.input` system, KPI tiles with accent bars + sparklines, motion/focus polish, reduced-motion support, redesigned landing page (hero glow, framed live chart, sections), honest copy (no claims for unbuilt features)
- [x] 375 px: no horizontal overflow on all app pages (a regression from the redesign was found and fixed)
- [x] Local CI-parity run: ruff, mypy --strict, 117+ pytest, web lint/typecheck/unit tests all green (ruff had failed on my scratch scripts; fixed, `data/` excluded)

Still NOT verified:
- [ ] Docker (`docker compose up`, image builds, container health) - Docker not installed
- [ ] GitHub Actions - folder is not a git repo / not pushed (steps verified locally only)
- [ ] M5 baseline table - Kaggle files not provided
- [ ] Lighthouse scores - download blocked; run in Chrome DevTools


## Phase 5 - TFT + calibration + hierarchy + Arena  (core built and verified on CPU; several spec items NOT done)

Verified 2026-11-21: 150 pytest tests pass (83 % coverage), ruff + mypy --strict clean.
- [x] **Pure-PyTorch TFT** (`kalpa_ml/models/tft/`): GLU, GRN (optional context), VariableSelectionNetwork (static / encoder / decoder), 4 static contexts, LSTM encoder-decoder with gated skip, static enrichment, **interpretable multi-head attention (shared values) with causal mask**, position-wise GRN, quantile head (P10/P50/P90), pinball loss, per-series scaling from history only, AdamW + OneCycle, grad clipping, early stopping, deterministic seeds
- [x] Tests written first (as the guide requires): GRN/VSN shapes, VSN weights are distributions, **attention is causal and later inputs never change earlier outputs**, pinball hand-calculated, training reduces loss, same seed gives the same model, quantiles never cross, **corrupting held-out targets leaves the whole TFT pipeline's forecasts identical**
- [x] **Split-conformal calibration** (`calibration.py`, per category, fold k calibrated with fold k-1 residuals): on held-out data an overconfident band goes from < 50 % to 80 % +/- 3 % coverage (tested); small groups fall back to the pooled delta
- [x] **Hierarchical reconciliation** (`hierarchy.py`, hierarchicalforecast BottomUp + MinT-shrink over total/store/category/store x category/SKU): reconciled forecasts add up at every level (error < 1e-6) while the unreconciled mix does not (> 0.5); P50 only (quantiles are not summed)
- [x] Arena backend: TFT accepted as a model, calibrated bands stored with forecasts (backtest + 28 future days), `POST /models/runs/{id}/promote` (owner/planner, exactly one champion, tenant-isolated)
- [x] **Model Arena page**: leaderboard (WAPE, MASE, bias, pinball, raw + calibrated coverage, series scored), champion badge + Promote, calibration bars against the 80 % target, run id as source

`artifacts/benchmarks/arena.json` - 600 series, 3 folds x 28 d, CPU, **SYNTHETIC FreshMart data (not M5)**, TFT lookback 56 / d_model 32 / <= 8 epochs, single seed; runtime 1112.6 s. Mean over folds (calibrated coverage = folds 1-2 only):

| model | WAPE | MASE | bias | pinball | coverage@80 raw | coverage@80 calibrated |
|---|---|---|---|---|---|---|
| seasonal_naive | 0.536 | 0.993 | -0.051 | 1.456 | 92.7 % | 86.2 % |
| auto_ets | 0.400 | 0.760 | -0.029 | 1.073 | 82.7 % | 82.6 % |
| lightgbm_tweedie | 0.401 | 0.768 | -0.004 | 1.069 | 86.5 % | 83.3 % |
| tft | 0.392 | 0.734 | -0.113 | 1.045 | 79.0 % | 81.5 % |

Reading it honestly: the TFT is best on WAPE, MASE and pinball, but only ~2 % relative better than AutoETS (0.392 vs 0.400), from a single seed on synthetic data - not evidence of a real advantage. It under-forecasts (bias -11 %, worst of the four). Calibrated TFT coverage (81.5 %) meets the 75-85 % acceptance band; SeasonalNaive stays over-wide (86 %).

NOT done / not verified (spec items skipped):
- [ ] **Chronos-2** zero-shot runner (needs a model download from Hugging Face) - not implemented
- [ ] **neuralforecast TFT cross-check** (within ~10 % of a reference implementation) - not run
- [ ] **Ablations** (without festivals / price+promo / weather) - not run
- [ ] **Cold-start** (nearest-neighbour SKU embedding) - not implemented
- [ ] **Optuna search** - not run; TFT hyperparameters are untuned defaults; lookback is 56 (contract says 112), CPU only, <= 8 epochs
- [ ] **Per-category champions** and per-category / per-level metrics; hierarchy is not wired into jobs or dashboards yet
- [ ] TFT variable-selection weights and attention are returned by `predict` but NOT yet persisted to `forecast_explanations` (Phase 7)
- [ ] TFT run through the API/worker end to end: the browser test runs the baselines; TFT was run via `kalpa_ml.run_arena`
- [ ] M5 benchmark, Docker, GitHub CI, Lighthouse: unchanged (see above)

### Phase 5 browser verification (real Chrome, real Redis + worker + PostgreSQL)
- [x] Forecast Explorer journey through the real worker queue (6.6 min) now includes the **Model Arena**: leaderboard rows, "Interval calibration" section, **Promote -> champion badge** - all pass
- [x] Bug found and fixed: component CSS (`.btn` etc.) sat outside Tailwind's cascade layers and overrode utilities such as `md:hidden`, so the mobile Sign-out button also showed on desktop. Component styles now live in `@layer components`; a regression test asserts exactly one Sign out button on desktop
- [x] axe 0 violations (10 pages x light/dark), no horizontal overflow at 375 px, landing hero chart, 4 green status pills: all re-run and passing after the change

## Phase 6 - Decision engine: replenishment, purchase orders, scenario studio, cost replay  (built; browser result recorded below)

Verified 2026-11-21 (191 pytest tests, 82 % coverage, ruff + mypy --strict clean, web typecheck/lint clean):
- [x] **Policy engine** (`kalpa_ml/policy.py`): newsvendor service level tau = c_u/(c_u+c_o); order-up-to S = sum of P50 over the protection period (lead + review) + safety = tau-quantile of backtest residual SUMS (not summed quantiles); q = ceil(max(0,S-IP)/pack)*pack raised to MOQ; days of cover; projected stock-out date. 11 tests: hand-calculated (MOQ rounding, zero lead time, negative inventory position) + **3 Hypothesis property tests** (whole packs, MOQ respected, covers the gap, more stock never increases the order, critical ratio is a probability)
- [x] **Replay simulator** (`kalpa_ml/replay.py`): day-by-day replay of each model's policy on the held-out fold, orders arrive after lead time, lost sales; cost = holding + stock-out penalty; fill rate, stock-out days, avg inventory. Hand-simulated tests (perfect forecast costs exactly 2.0; zero forecast costs 62.0 with 40 % fill rate), deterministic, pack/MOQ respected. Wired into the backtest: **fold k uses only fold k-1 residuals for safety stock** (fold 0 has no replay); costs and safety tables are stored with each run
- [x] **Replenishment API + page**: recommendations from the champion (else best-WAPE) run's future forecasts, per-position service level, safety units, order-up-to, whole-pack quantity, cover, stock-out date, value at risk (at cost), reason chips, shelf-capacity flag; grouped by supplier, editable quantities with pack/MOQ validation, bulk Create POs
- [x] **Purchase-order workflow** (draft -> approved -> sent -> partially_received -> received / cancelled): sending raises on_order, receiving moves on_order to on_hand, cancelling releases the open remainder; MOQ/pack enforced server-side; owner/planner approve; store managers create/receive for their own stores only; audited; tenant-isolated; Kanban board + detail drawer + PDF export. 12 API tests
- [x] **Scenario Studio**: re-infers with the **saved LightGBM model** (not hand-set multipliers) after changing price, promotion depth or adding a festival; baseline vs scenario, change in units / revenue / margin / series at stock-out risk, fan charts for a single product, saved scenarios. Tests: **price -20 % raises the forecast > 5 %, +20 % lowers it > 5 %** (ML layer and through the API), deterministic, scope limits, role/store scoping, tenant isolation, cached repeat; weather shift is reported as "not modelled" rather than faked
- [x] **Arena Cost tab**: total cost, fill rate, stock-out days, average inventory per model, and a sentence stating from the data whether best WAPE is also the cheapest policy; assumptions shown (editable in Settings > Cost parameters; migration 0004)

Honest limits / assumptions:
- Cost assumptions are defaults, not facts: holding 25 %/yr of unit cost, stock-out penalty 0.5 x unit cost per lost unit, review every 7 days. Value at risk is valued at COST (there is no price master)
- Scenario uses LightGBM only (TFT is not persisted); promo depth is modelled through the price ratio + promo flag; weather is not a model input; baseline assumes unchanged price and no promotion
- Aggregate scenario views show summed P50 only (quantiles of a sum are not the sum of quantiles); fan charts appear for a single product
- The saved model lives in `MODEL_DIR` which the API and worker must share (absolute path; a shared volume in Docker)
- Replay needs >= 2 backtest folds and supplier terms; lost sales are not back-ordered

### Phase 6 browser verification (final)
- `decisions.spec.ts`: 7/7 passed in real Chrome on real Postgres + Redis + ARQ worker (log: `data/run/e2e6.log`), after the PO board header and replenishment chip-density polish.
- Replenishment is paged (40 rows, "Show N more"); reason chips capped at 2 plus "+N more" tooltip.
- Cost-tab finding on a 2-model run: LightGBM was both most accurate and cheapest, so "best WAPE is not always cheapest" is NOT yet demonstrated for TFT/AutoETS.
- Still open: Docker, CI, M5 table, Lighthouse, skipped Phase 5 items, TFT via API/worker.

### Phase 5 follow-up: TFT through the real API + ARQ worker
- Fresh org, 600 series (demo data), `POST /models/backtests` with `["seasonal_naive","lightgbm_tweedie","tft"]`, 1 fold, executed by the ARQ worker on Postgres + Redis (script `data/run/tft_check.py`, log `data/run/tft_check.log`).
- Job went queued -> running -> succeeded in 378 s; arena WAPE: LightGBM 0.397, TFT 0.398, SeasonalNaive 0.534; 100,800 forecast rows persisted for 3 runs.
- Caveats: 1 fold, single seed, synthetic data. LightGBM and TFT are effectively tied; this does not show TFT is better.
- Still open: persisting TFT explanations (variable-selection weights / attention), which Phase 7 needs.

## Phase 7 - Explainability & Grounded Copilot

**Built**
- `kalpa_ml/explain.py`: TFT interpretability summaries, LightGBM SHAP (built-in TreeSHAP), deterministic narrative, number-grounding guard.
- TFT variable-selection weights + attention are now persisted to `forecast_explanations` by the backtest job (worker path).
- API: `GET /forecasts/{location}/{product}/explanation`, `GET /explain/{location}/{product}/narrative`, `GET /copilot/status`, `POST /copilot/chat` (all read-only, org-scoped, `view` permission).
- Web: Explain panel in Forecast Explorer (static / past / future importance bars, attention strip with table fallback, SHAP bars, narrative) and a Copilot drawer (focus-trapped, Esc closes, returns focus).
- Optional LLM (Anthropic Messages API via `httpx`, `ANTHROPIC_API_KEY`, `COPILOT_MODEL`): rewords the narrative and does tool-calling; both are guarded. Nothing is sent without a key.

**Acceptance checklist**
- [x] Number-grounding validator has tests (injected fake number -> rejected): `packages/ml/tests/test_explain.py` (fake `92%` rejected, run-id digits cannot whitelist numbers) and `services/api/tests/test_explain_copilot.py` (LLM reply with invented figures falls back to the template / rule-based answer).
- [x] App fully functional with the copilot disabled: with no key the narrative is a template and the copilot is rule-based; browser test 4 loads Overview, Inventory, Replenishment and Arena without opening the drawer.
- [~] 10 scripted copilot questions answered correctly: all 10 run and are asserted against API data in `test_ten_scripted_questions_with_rule_based_copilot` (9 tool questions + 1 refusal), on a small 3-series test organisation, in rule-based mode. In the browser, 3 of them ran on the 600-series demo tenant. NOT done: a scripted 10-question pass on the demo tenant, and any run against the real Anthropic API (no key available here; the LLM path is tested only with a fake HTTP transport).
- [x] Explain panel shows TFT VSN importance bars, attention heatmap (strip + table), SHAP for LightGBM, grounded narrative. Evidence: `artifacts/screenshots/explain-tft-*.png`, `explain-shap-*.png`, `copilot-drawer-*.png` (light and dark).

**Verification (real stack: Postgres + Redis + ARQ worker + Chrome)**
- `explain.spec.ts` 4/4 passed: a 3-model backtest (Seasonal Naive, LightGBM, TFT, 2 folds, 600 series) through the worker in 8.2 min; Explain panel with exact narrative == API text, every narrative number found in the facts, 56 attention rows, axe zero violations in both themes; copilot drawer answers + refusal + axe + focus return; app works with copilot unopened.
- Python: 213 passed; coverage (API + ML) 93 % after enabling greenlet tracing (see below). New modules: explain 91 %, copilot 93 %, `kalpa_ml/explain.py` 98 %.
- Lint (ruff), types (mypy strict on source), tsc, eslint clean.

**Bugs found and fixed during verification**
- A `₹` in a logged copilot answer raised `UnicodeEncodeError` on a Windows cp1252 console and turned the request into a 500. Only the real-browser run exposed it. Fix: console streams use `errors="replace"`; regression test added. The drawer also hung on "Thinking..." after a failed request; it now shows an error.
- Coverage was under-reported: without `concurrency = ["greenlet", "thread"]` code after the first `await` in SQLAlchemy greenlets is untraced. The earlier 82 % figure was not comparable to 93 %.
- Copilot matched one-letter SKUs as substrings of any question; matching is now whole-word. "Which model is cheapest?" on a 1-fold run now says replay cost needs >= 2 folds instead of silently omitting it.

**Known limitations (honest)**
- SHAP values are on LightGBM's log-demand scale; in the 3-series API test org the top SHAP driver was a time feature, not price (in the unit test, where demand is built from price, price is top). SHAP reports what the model uses, not what is causal.
- TFT weights are averaged over the lookback / horizon; per-day weights and the full attention matrix are not stored.
- `/forecasts/series` etc. are unchanged; narrative horizon is 7-28 days (default 14); festival named in the narrative is the next one inside that window.
- The leaderboard footnote text on Forecast Explorer was changed after the last browser run (text only; typecheck and lint pass).

**Still open (unchanged)**: Docker run, GitHub CI, M5 benchmark, Lighthouse, Phase 5 items (Chronos-2, neuralforecast cross-check, ablations, cold-start, Optuna, per-category champions), hierarchy not wired into jobs, Models & Jobs and System pages (Phase 8), Phases 8-10.

## Phase 8 - Real-time operations & visible MLOps

**Built**
- **Live stream**: Redis pub/sub per org -> `GET /stream/events` (SSE) + `GET /live/state` (catch-up after load/reconnect). Events: `sale`, `alert`, `job`, `sim`.
- **Reorder alerts**: reorder point = lead-time demand + safety stock, stored on inventory after each backtest; an alert fires exactly when a sale crosses it (computed inside the stock `UPDATE ... RETURNING`).
- **Live store simulator** (demo tenant only): ARQ job that sells through the real checkout on a simulated business calendar (starts the day after the data ends), following the forecast with Poisson noise and an hourly traffic profile; demand multiplier for forced drift; start/stop from the Command Center.
- **Command Center**: ticking revenue and units, live activity feed, stock watch (bars vs reorder point), alerts with "Create PO" (also from the toast), simulator card, connection light, bell with alerts + jobs.
- **Jobs page**: live progress bars, log tail, cancel (queued or running; a cancelled job can never be overwritten by a late success).
- **Models page**: registry (all versions, metrics, MLflow link per run), champion history, drift monitor (PSI + live WAPE), "Retrain now", "Check drift now"; weekly retrain and daily drift-check ARQ crons.
- **System page**: p50/p95 latency and requests per minute (chart + table), error rate, queue depth, DB size, cache hit rate, versions, worker status, links to Swagger and MLflow.
- **Silent session refresh** in the API client (the 15-minute access cookie used to bounce long-lived pages to /login), and an SSE client that renews the session and reconnects with back-off.

**Acceptance checklist**
- [x] **Simulator runs 30 min without errors or negative stock.** `artifacts/perf/simulator_soak_30min.json` (script `data/run/soak.py`, real API + worker + Postgres + Redis): 30 min, 30 simulated days, 11,013 checkouts, 33,250 units, 0 errors, job `succeeded`; stock sampled every 30 s (56 samples) never below 0. The live stream delivered 12,806 events with a largest gap of 1.2 s. Shelves emptied after about 7 simulated days (nothing replenishes them in the soak), so 116,367 units of demand were refused as lost sales: that is the intended behaviour (a sale on an empty shelf is refused, stock never goes negative), not an error. A first soak attempt stopped at 14 min because the *script's* 15-minute login cookie expired (the service itself had 0 errors); the script now refreshes like the web app, and this is the full second run.
- [x] **SSE reconnects automatically after an API restart.** Browser test: API process killed, the page shows "Reconnecting", API started again, the page shows "Live" again, and a sale made after the restart appears in the live feed.
- [x] **A forced drift raises an alert and the retrain job runs end to end.** Browser test: simulator at x2.5 demand -> `GET /models/drift` reports `alert` (PSI on scaled units 0.77 in one run, plus live WAPE) -> "Check drift now" -> "Drift detected: retrain started" -> retrain job (reason `drift`) `succeeded` -> two new registry versions, data cut-off moved forward, card back to "not enough data". The registry's WAPE for a run equals the WAPE read from the MLflow server for the same run (checked).

**Verification (real Postgres + Redis + ARQ worker + MLflow server + Chrome)**
- Python: 247 passed, combined API + ML coverage 93 % (new Phase 8 tests: 26 in `test_realtime.py`, 5 PSI/WAPE, 3 zero-fill); ruff, mypy (strict), tsc, eslint clean.
- Vitest: 12 passed (the live-state reducer, including the case that froze revenue at 0).
- Playwright `realtime.spec.ts`: 7/7 passed with axe zero violations in both themes on Command Center, Jobs, Models and System: tests 1-5 in one full run on the final app code; tests 6-7 in a separate run after a test-only fix (a full single run was not repeated). Getting there took 14 attempts; every failure was a real finding or a test-harness flaw, listed below.
- Screenshots (light and dark): `artifacts/screenshots/command-center-live-*`, `jobs-live-*`, `models-drift-*`, `models-registry-*`, `system-*`.

**Bugs found by running it end to end (all fixed, all with tests)**
- Revenue stayed at 0: the simulator's calendar is earlier than the real date and the live totals only moved the business day forward. Unit-tested.
- **Retraining broke on any POS-driven data**: checkout and the simulator only write a row when something sold, and statsforecast failed with "missing or duplicate timestamps". The training load now zero-fills (`frames.zero_fill`); the regression test fails with the fix switched off and passes with it.
- **The drift endpoint took 72 s** on the large shared test database (a SQL join of forecasts and sales with stale planner statistics). It now reads two indexed ranges and merges in pandas (0.8 s on the same data), and `ANALYZE forecasts` runs after each backtest.
- PSI on small samples gave a false alert (0.217 on 75 rows of unchanged data): PSI is now computed only from 500+ new rows.
- A `uv`-wrapper kill, `ECONNRESET` on 5-second polls, and stale-log monitors were test-harness problems (fixed in the spec, not the product).
- Dark-mode toasts failed contrast (Sonner's tinted `richColors` and its default action button): the toaster now follows the app theme with our own button colours. The bell panel now closes on Escape.
- The Windows console crash on a rupee sign in a logged line (Phase 7) and the missing session refresh are covered above.

**Environment incidents (not product bugs)**: the machine ran out of memory once (about 0.4 GB free with other apps open); that took down Postgres, Redis, the API and the worker and crashed a Playwright run, and Postgres needed crash recovery. Memory had to be freed before the runs could finish.

**Known limitations (honest)**
- The simulated days are real rows in the demo organisation's sales history (that is the point: forecasts, drift and retraining then see them), and a retrain while the simulator is running can include its in-progress day when more than half of the series already have a row for it. Stopping the simulator mid-day leaves a partial day that the drift check counts as complete.
- Cancelling a running backtest stops it at its next checkpoint; the CPU-bound modelling thread itself cannot be interrupted and finishes in the background.
- Champion history is verified in the Python tests; the browser run shows it empty because nothing was promoted.
- The Command Center has the live widgets but **not** the full Part D4 spec (28-day revenue, forecast and accuracy KPIs, store-network risk map, upcoming-events strip): the map and those KPIs are still open.
- The MLflow UI server needs `flask`, `flask-cors` and `aiohttp` on top of the pinned `mlflow` (not added to the lockfile; the Docker image has them).
- The shared test database grows with every browser run (about 45 demo organisations, 9.6 M sales rows by now); there is no cleanup job.

**Still open (unchanged)**: Docker run, GitHub CI, M5 benchmark, Lighthouse, Phase 5 items (Chronos-2, neuralforecast cross-check, ablations, cold-start, Optuna, per-category champions), hierarchy not wired into jobs, Phases 9-10.

---

## Phase 9: Security, performance, polish (done, with the gaps listed)

**Built**: Redis fixed-window rate limiting (per IP and per user, fail-open, TCP peer not `X-Forwarded-For`); production config guard (refuses default secrets); security headers; standard 500 JSON; response cache with per-org, per-group versions and write-triggered invalidation; one shared DB pool; multi-worker uvicorn; Argon2 in threads; lazy-loaded charts; table fallbacks for charts; component tests.

**Evidence**
- Security tests: `services/api/tests/test_security.py` (17) and `test_cache.py` (9) pass. Audits in `artifacts/security/` (pip-audit, pnpm audit, secrets scan).
- Load test (Locust, 50 users): `artifacts/perf/` (`README.md` has the before/after story; aggregate p95 6.9 s to about 400 ms). Cache-miss heavy endpoints still exceed the 300 ms budget; this is stated, not hidden.
- 30-minute simulator soak: `artifacts/perf/simulator_soak_30min.json`.
- Vitest: 21 tests pass; typecheck and lint clean.

**Not done / not verified**: strict CSP header; Lighthouse; Docker run; GitHub Actions run (workflow file rewritten but never executed here). Two Playwright specs (signed-in a11y, journeys) failed once because the demo load took more than 120 s under memory pressure on a 7.3 GB machine; they are re-run in the final check (see Phase 10).

## Phase 10: Presentation package (in progress)

Written: `README.md` (architecture, ER and sequence diagrams, honest scope), `docs/DEMO_SCRIPT.md`, `docs/VIVA_QA.md`, `scripts/` (start/stop, demo preparation, `export_results.py` which generates `docs/RESULTS.md` only from saved artifacts).
Open: run `scripts/prepare_demo.py` (needs about 1.5 GB free RAM), final test run, regenerate `docs/RESULTS.md`, one full dry run of the demo, then freeze.
