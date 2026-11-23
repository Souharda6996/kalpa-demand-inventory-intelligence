<div align="center">

# Kalpa

### Demand & Inventory Intelligence: probabilistic forecasting that ends in a purchase order

A multi-tenant retail platform that forecasts demand with a **Temporal Fusion Transformer written from scratch in PyTorch**,
benchmarks it in a **model arena**, judges every model by the **rupee cost of the stocking decisions it would have caused**,
explains each forecast, and turns it into **reorder alerts and purchase orders in real time**.

`Python 3.12` · `FastAPI` · `PyTorch` · `LightGBM` · `statsforecast` · `PostgreSQL` · `Redis` · `Next.js 16` · `React 19` · `Tailwind v4` · `ECharts` · `MLflow`

![Landing page](artifacts/screenshots/landing-hero-light.png)

</div>

---

## Table of contents

1. [Abstract](#1-abstract)
2. [Problem and motivation](#2-problem-and-motivation)
3. [Contributions](#3-contributions)
4. [Feature tour with screenshots](#4-feature-tour-with-screenshots)
5. [System architecture](#5-system-architecture)
6. [Technology stack and exact versions](#6-technology-stack-and-exact-versions)
7. [Data: the Bengaluru FreshMart dataset](#7-data-the-bengaluru-freshmart-dataset)
8. [Machine-learning methodology](#8-machine-learning-methodology)
9. [Decision engine (forecast to order)](#9-decision-engine-forecast-to-order)
10. [Explainability and Copilot](#10-explainability-and-copilot)
11. [Live operations and MLOps](#11-live-operations-and-mlops)
12. [Backend design](#12-backend-design)
13. [Frontend design](#13-frontend-design)
14. [Security](#14-security)
15. [Performance and load testing](#15-performance-and-load-testing)
16. [Quality assurance](#16-quality-assurance)
17. [Experimental results](#17-experimental-results)
18. [Installation and usage](#18-installation-and-usage)
19. [Repository structure](#19-repository-structure)
20. [Scope, assumptions and future work](#20-scope-assumptions-and-future-work)
21. [Documentation index and reproducibility](#21-documentation-index-and-reproducibility)
22. [Keywords and citation](#22-keywords-and-citation)

---

## 1. Abstract

Retailers lose money in two directions: stock-outs lose sales, excess stock ties up cash and spoils. Point forecasts hide this
asymmetry, and accuracy metrics such as WAPE do not say what a forecast is *worth*. **Kalpa** is an end-to-end platform that
(i) produces **calibrated probabilistic forecasts** (P10/P50/P90) with a pure-PyTorch **Temporal Fusion Transformer (TFT)**,
(ii) compares it in a rolling-origin **model arena** against Seasonal Naive, AutoETS, Croston and LightGBM (Tweedie),
(iii) evaluates every model by **replaying its reorder policy day by day** and reporting holding plus lost-sales cost, fill
rate and stock-out days, (iv) converts forecasts into **order-up-to levels, MOQ/pack-rounded purchase orders** and a
**what-if Scenario Studio**, (v) **explains** forecasts (TFT variable-selection weights and attention, LightGBM TreeSHAP,
number-grounded narrative), and (vi) runs **live**: a store simulator drives the real checkout, events stream to the browser
over Server-Sent Events, reorder alerts fire the moment stock crosses a reorder point, and drift detection can retrain models
automatically. The platform is multi-tenant, role-based, rate-limited and load-tested.

On the synthetic *Bengaluru FreshMart* benchmark (600 store-SKU series, 3 rolling folds of 28 days) the TFT reaches
**WAPE 39.2 %** versus 40.0 % (AutoETS), 40.1 % (LightGBM) and 53.6 % (Seasonal Naive), with calibrated 80 % interval
coverage of 81.5 %. On the prepared demo tenant, the TFT's replayed reorder policy costs **₹1,05,130** versus ₹1,31,638 for
LightGBM and ₹1,49,867 for Seasonal Naive.

## 2. Problem and motivation

* **Forecasts must feed decisions.** A planner does not need a number, they need "how much do I order from which supplier, and
  how sure are we?". Safety stock is a *quantile* of demand over the supplier lead time, so the forecast has to be probabilistic.
* **Accuracy is not value.** Two models with similar WAPE can produce very different inventory outcomes. Kalpa measures the
  decision outcome directly (replay cost, fill rate, stock-out days) next to the usual accuracy metrics.
* **Indian retail is festival-driven.** Diwali, Holi, Eid, Onam, Pongal, Ganesh Chaturthi, Durga Puja, Christmas and
  Independence Day shift demand sharply by category (sweets, dry fruits, colours, bakery...). Festival calendars are first-class
  known-future inputs to the models.
* **Operations are live.** Stock changes with every sale. The system must stay consistent under concurrent checkouts, alert at
  the right moment, and let planners act without leaving the screen.
* **Trust requires transparency.** Every KPI shows its source; every forecast can be explained; every number in generated text is
  checked against the data.

## 3. Contributions

1. A **from-scratch TFT** in pure PyTorch (gated residual networks, variable-selection networks, LSTM encoder/decoder,
   interpretable multi-head attention, quantile/pinball loss) with unit tests for each layer.
2. A **fair model arena**: rolling-origin backtest, identical folds for all models, accuracy *and* calibration metrics.
3. **Split-conformal calibration per category** (fold *k* calibrated on fold *k-1* residuals) with measured coverage before and after.
4. **Decision-focused evaluation**: a day-by-day replay simulator that applies each model's reorder policy to real held-out demand.
5. A **policy engine**: protection period (lead time + review), order-up-to level from the *quantile of residual sums*, pack/MOQ rounding.
6. A **scenario engine**: re-inference of the saved LightGBM model with changed known-future inputs (price, promotion depth, festival).
7. **Explainability** that is grounded: TFT attention and variable weights persisted at forecast time; TreeSHAP; a deterministic
   narrative with a guard that rejects numbers not present in the data; a Copilot restricted to read-only tools.
8. A **real-time operations layer**: Redis pub/sub to SSE, exact reorder-point crossing alerts, a simulator that runs through the
   production checkout path, job progress and cancellation.
9. **Visible MLOps**: model registry with MLflow links, PSI and live-WAPE drift monitoring, scheduled and drift-triggered retraining, a System page.
10. A **production-minded engineering baseline**: multi-tenant isolation, RBAC, CSRF, rate limits, caching with correct
    invalidation, a load test with before/after evidence, dependency and secret audits.

## 4. Feature tour with screenshots

All screenshots are taken from the running application (`artifacts/screenshots/`, light and dark themes, plus a 375 px mobile layout).

### Landing page and sign-up
Public forecast chart on synthetic data, product pitch, one-click sign-up.

| Landing (light) | Landing (dark) | Mobile (375 px) |
|---|---|---|
| ![](artifacts/screenshots/landing-light.png) | ![](artifacts/screenshots/landing-dark.png) | ![](artifacts/screenshots/landing-375.png) |

### Command Center: live operations
KPI tiles with animated numbers, a live sales feed, the stream status chip, the reorder-alert bell and the store simulator controls.

| Light | Dark |
|---|---|
| ![](artifacts/screenshots/command-center-live-light.png) | ![](artifacts/screenshots/command-center-live-dark.png) |

### Forecast Explorer: probabilistic forecasts and explanations
P10-P90 fan chart with history, accuracy summary, and the **Explain** panel: TFT variable-selection weights and attention strip,
LightGBM SHAP bars and a number-grounded plain-language narrative. Every chart has a data-table fallback for accessibility.

| Forecast fan chart | TFT explanation | LightGBM SHAP |
|---|---|---|
| ![](artifacts/screenshots/forecasts-light.png) | ![](artifacts/screenshots/explain-tft-light.png) | ![](artifacts/screenshots/explain-shap-light.png) |

### Model Arena and decision cost
Leaderboard with WAPE, MASE, bias, pinball loss and 80 % coverage; the **Cost** tab shows replay cost, fill rate and stock-out days; one click promotes a champion.

| Arena | Arena: cost tab |
|---|---|
| ![](artifacts/screenshots/arena-light.png) | ![](artifacts/screenshots/arena-cost-light.png) |

### Scenario Studio: what-if analysis
Change price, promotion depth or add a festival; baseline and scenario are compared on units, revenue, margin and stock-out risk.

![](artifacts/screenshots/scenarios-light.png)

### Replenishment, Inventory, Purchase orders, POS
Reorder recommendations with the service level, order-up-to level and the supplier's MOQ and pack size; a full purchase-order
lifecycle with owner approval and receiving; an atomic, idempotent point-of-sale.

| Replenishment | Inventory |
|---|---|
| ![](artifacts/screenshots/replenishment-light.png) | ![](artifacts/screenshots/inventory-light.png) |

| Purchase orders | Point of sale |
|---|---|
| ![](artifacts/screenshots/purchase-orders-light.png) | ![](artifacts/screenshots/pos-light.png) |

### Copilot
A drawer that answers questions about stock, forecasts and models using read-only tools and quotes the numbers it used.

![](artifacts/screenshots/copilot-drawer-light.png)

### MLOps: Jobs, Models, System
Live job progress with cancel, model registry with MLflow run links, drift monitor (PSI and live WAPE), and a System page with latency, throughput, errors, queue depth, database size and cache hit rate.

| Jobs | Model registry | Drift monitor |
|---|---|---|
| ![](artifacts/screenshots/jobs-live-light.png) | ![](artifacts/screenshots/models-registry-light.png) | ![](artifacts/screenshots/models-drift-light.png) |

![](artifacts/screenshots/system-light.png)

### Data management, Settings, Sign-up

| Data and quality report | Settings | Sign-up |
|---|---|---|
| ![](artifacts/screenshots/data-light.png) | ![](artifacts/screenshots/settings-light.png) | ![](artifacts/screenshots/signup-light.png) |

### Dark theme
Every page ships in light and dark (and passes automated contrast checks in both).

| Overview | Arena cost | Forecasts |
|---|---|---|
| ![](artifacts/screenshots/overview-dark.png) | ![](artifacts/screenshots/arena-cost-dark.png) | ![](artifacts/screenshots/forecasts-dark.png) |

## 5. System architecture

```mermaid
flowchart LR
  subgraph Browser
    W[Next.js 16 app<br/>React 19, Tailwind v4, ECharts, TanStack Query]
  end
  W -- "REST + HttpOnly cookies (JWT, CSRF)" --> A[FastAPI<br/>Pydantic v2, SQLAlchemy 2 async]
  W -- "SSE /stream/events" --> A
  A <--> PG[(PostgreSQL)]
  A <--> R[(Redis<br/>cache, rate limits, pub/sub, job queue)]
  R <--> K[ARQ worker<br/>backtests, retrain, simulator, cron]
  K <--> PG
  K --> ML[kalpa_ml<br/>TFT, LightGBM, statsforecast,<br/>conformal, policy, replay, drift, explain]
  K --> F[(MLflow tracking)]
  A --> ML
```

* **Forecasts are computed in the background** and stored in PostgreSQL; the API serves reads from the database and a short Redis cache.
* **Scenario inference** runs on demand against a saved LightGBM model; results are cached by request hash.
* **Live events** travel on one Redis channel per organisation and reach the browser as Server-Sent Events.
* **One shared async engine and connection pool** per API process; the API runs multiple Uvicorn workers.

### Entity-relationship model

```mermaid
erDiagram
  ORGS ||--o{ MEMBERSHIPS : has
  USERS ||--o{ MEMBERSHIPS : has
  ORGS ||--o{ LOCATIONS : owns
  ORGS ||--o{ PRODUCTS : owns
  ORGS ||--o{ SUPPLIERS : owns
  SUPPLIERS ||--o{ SUPPLIER_PRODUCTS : supplies
  PRODUCTS ||--o{ SUPPLIER_PRODUCTS : "priced / terms"
  LOCATIONS ||--o{ INVENTORY_POSITIONS : holds
  PRODUCTS ||--o{ INVENTORY_POSITIONS : "stocked as"
  LOCATIONS ||--o{ SALES_DAILY : sells
  PRODUCTS ||--o{ SALES_DAILY : sold
  ORGS ||--o{ MODEL_RUNS : trains
  MODEL_RUNS ||--o{ FORECASTS : produces
  MODEL_RUNS ||--o{ BACKTEST_RESULTS : "scored by"
  MODEL_RUNS ||--o{ FORECAST_EXPLANATIONS : explains
  SUPPLIERS ||--o{ PURCHASE_ORDERS : receives
  PURCHASE_ORDERS ||--o{ PO_LINES : contains
  ORGS ||--o{ JOBS : runs
  ORGS ||--o{ AUDIT_LOG : records
```

Database tables (PostgreSQL, 5 Alembic migrations): `orgs`, `users`, `memberships`, `invites`, `idempotency_keys`, `locations`,
`suppliers`, `products`, `supplier_products`, `inventory_positions`, `sales_daily`, `prices`, `promotions`, `calendar_events`,
`weather_daily`, `purchase_orders`, `po_lines`, `model_runs`, `forecasts`, `forecast_explanations`, `backtest_results`,
`scenarios`, `jobs`, `uploads`, `data_quality_reports`, `audit_log`. Every row is scoped by `org_id`.

### Key flows

**Backtest / retrain job**

```mermaid
sequenceDiagram
  participant U as Planner (browser)
  participant API
  participant Q as Redis / ARQ
  participant W as Worker
  participant DB as PostgreSQL
  U->>API: POST /models/backtests (cookie + CSRF)
  API->>DB: create Job (queued)
  API->>Q: enqueue run_backtest_job
  API-->>U: 202 + job id
  W->>DB: load sales, zero-fill gaps
  W->>W: rolling-origin backtest, conformal calibration, replay cost
  W-->>U: progress events (Redis pub/sub to SSE)
  W->>DB: model runs, backtest results, forecasts, TFT explanations, reorder points
  W->>W: one MLflow run per model
```

**POS checkout (atomic, idempotent, live)**

```mermaid
sequenceDiagram
  participant C as Cashier
  participant API
  participant DB as PostgreSQL
  participant R as Redis
  participant S as Browsers (SSE)
  C->>API: POST /pos/checkout + Idempotency-Key
  API->>DB: BEGIN; UPDATE stock SET on_hand = on_hand - qty WHERE on_hand >= qty RETURNING ...
  alt not enough stock
    API-->>C: 409 insufficient_stock (nothing changed)
  else
    API->>DB: sales_daily += units, audit row; COMMIT
    API->>R: PUBLISH sale (+ alert if the reorder point was crossed by this sale)
    R-->>S: sale / alert events
    API-->>C: receipt
  end
```

## 6. Technology stack and exact versions

| Layer | Technology | Version / note |
|---|---|---|
| Language | Python | 3.12.10 |
| Python tooling | uv (workspace + lockfile), Ruff, mypy, pytest, pytest-cov, hypothesis | `uv.lock` pins every dependency |
| API | FastAPI, Uvicorn, Pydantic v2, pydantic-settings | async, OpenAPI 3 (90 operations, 68 paths) |
| Database | PostgreSQL 16, SQLAlchemy 2 (async), asyncpg, Alembic | 5 migrations; SQLite (aiosqlite) used for fast unit tests |
| Cache / queue / pub-sub | Redis, redis-py asyncio, ARQ (worker) | cache, rate limit, SSE fan-out, job queue |
| Auth | Argon2 (argon2-cffi), PyJWT | HttpOnly cookies, one-time refresh tokens, CSRF |
| Data validation | pandera (polars), Polars, pandas, rapidfuzz | upload pipeline with quality report and fuzzy SKU matching |
| Forecasting | PyTorch (CPU), statsforecast (AutoETS, Croston, Seasonal Naive), mlforecast + LightGBM (Tweedie), hierarchicalforecast, scipy, holidays | TFT implemented from scratch |
| Experiment tracking | MLflow | one run per model run, linked from the UI |
| Explainability | TFT variable-selection weights + attention, SHAP (TreeSHAP via LightGBM) | persisted at forecast time |
| Frontend | Next.js 16.4.6, React 19.3.1, TypeScript 6.0.3 | App Router |
| Styling and UI | Tailwind CSS 4.3.5, Geist font, cmdk (command palette), Sonner (toasts), motion | light and dark themes |
| Data fetching | TanStack Query 5.108.1, TanStack Table, TanStack Virtual, openapi-fetch + openapi-typescript | typed client generated from the OpenAPI schema |
| Charts | ECharts 6.1 (lazy-loaded) | with accessible table fallbacks |
| Forms / validation | react-hook-form, zod | |
| PDF | @react-pdf/renderer | purchase-order export |
| Frontend tests | Vitest 5.1.1 + Testing Library + jsdom; Playwright 1.65 + axe-core | unit, component, end-to-end, accessibility |
| Load / security | Locust, pip-audit, pnpm audit, detect-secrets | evidence in `artifacts/` |
| Packaging / CI | pnpm 12.21 workspace, Node 26, GitHub Actions workflow, Docker Compose definition | |

## 7. Data: the Bengaluru FreshMart dataset

A reproducible **synthetic Indian-retail dataset** (seed 42) is generated so the platform can be developed, tested and demonstrated
without proprietary data, and so the models can be checked against **known injected effects**.

* **Network**: 6 stores in Bengaluru (Koramangala, Indiranagar, Whitefield, Jayanagar, HSR Layout, Malleshwaram), the Peenya distribution centre, 8 suppliers.
* **Catalogue**: 10 categories x 30 SKUs in the full generator (Sweets, Dry Fruits, Colours, Beverages, Dairy, Staples, Snacks, Fresh Produce, Personal Care, Bakery); the demo tenant uses 100 SKUs x 6 stores = **600 series**.
* **Injected effects**: weekly seasonality (weekend lift per category), yearly seasonality, 8 % annual trend, festival ramps (7 days) with category-specific amplitudes (e.g. Diwali x2.5 for sweets, Holi x6 for colours, Christmas x1.5 for bakery), promotions (discount 10-30 %) with price elasticity -1.4, a temperature effect on beverages, negative-binomial noise, and 20 % intermittent series.
* **Festival calendar**: library-derived plus hand-curated dates (`festivals.csv` records the source of each date), including Pongal/Sankranti (2026-01-14) and Republic Day (2026-01-26).
* **Upload pipeline** for real data: CSV upload, schema validation (pandera), fuzzy column and SKU matching, a data-quality report and score, and a safe filename policy.
* **Training frames** are zero-filled (days with no sale become explicit zeros) so that POS-written data, which only stores days with sales, never breaks the models.

The full parameter table and category definitions are in [`docs/GENERATOR_CARD.md`](docs/GENERATOR_CARD.md).
Results on this dataset demonstrate that the pipeline recovers known effects; they are synthetic-data results.

## 8. Machine-learning methodology

### 8.1 Models in the arena

| Model | Family | Role |
|---|---|---|
| **Seasonal Naive** | baseline | the floor every model must beat |
| **AutoETS** | statistical (statsforecast) | strong classical baseline |
| **Croston** | intermittent demand | slow movers |
| **LightGBM (Tweedie)** | gradient boosting (mlforecast) | global model with calendar, price, promotion, festival and lag features; also used by the Scenario Studio and TreeSHAP |
| **TFT (ours)** | deep learning | pure PyTorch Temporal Fusion Transformer, 56-day lookback, d_model 32, quantile outputs |

### 8.2 Temporal Fusion Transformer (pure PyTorch)

Implemented in `packages/ml/kalpa_ml/models/tft/` (`layers.py`, `model.py`, `data.py`, `train.py`, `runner.py`) and tested layer by layer
(`packages/ml/tests/test_tft_layers.py`, `test_tft_model.py`):

* **Gated Linear Unit and Gated Residual Network** with optional context vector,
* **Variable Selection Networks** for static, known-future and observed inputs (their softmax weights are the "which inputs mattered" explanation),
* **LSTM encoder-decoder** for local temporal processing,
* **Interpretable multi-head attention** with shared value projection (its weights are the attention strip in the UI),
* **Quantile (pinball) loss** over P10 / P50 / P90 (`pinball_loss`),
* static embeddings for store, SKU and category; known-future features include calendar fields, festival flags, price and promotion.

### 8.3 Evaluation protocol

* **Rolling-origin backtest**: expanding window, several folds, horizon 28 days; identical folds for every model.
* **Accuracy**: WAPE, MASE, bias. **Probabilistic**: pinball loss, empirical coverage of the 80 % interval.
* **Calibration**: split-conformal prediction **per category**; fold *k* is calibrated with fold *k-1* residuals so no fold is calibrated on its own data.
* **Decision-focused**: replay the last fold day by day with each model's reorder policy; report total cost (holding + lost sales), fill rate, stock-out days, average inventory.
* **Hierarchical reconciliation** (BottomUp, MinT-shrink) is implemented in `kalpa_ml/hierarchy.py` with unit tests.

## 9. Decision engine (forecast to order)

Implemented in `packages/ml/kalpa_ml/policy.py` and the `policy` / `purchase_orders` API modules:

1. **Protection period** `P = supplier lead time L + review period R`.
2. **Service level** from costs via the newsvendor **critical ratio** `tau = c_under / (c_under + c_over)` (per-organisation cost parameters in Settings).
3. **Order-up-to level** `S = sum of P50 over P days + safety`, where *safety* is the tau-quantile of backtest **residual sums** over P days (a quantile of sums, not a sum of per-day quantiles: the P90 of a sum is not the sum of P90s).
4. **Recommended order** `q = ceil(max(0, S - inventory position) / pack) x pack`, raised to the supplier **MOQ** (as a pack multiple) when `q > 0`.
5. **Days of cover** and **projected stock-out date** per store-SKU.
6. **Purchase-order lifecycle**: draft, approved (owner or planner), sent, partially received, received (each receipt updates stock) or cancelled, with an audit trail and PDF export.
7. **Reorder points** are refreshed after every model run; the live checkout raises an **alert exactly when a sale makes stock cross the reorder point** and offers "Create PO" from the alert.

## 10. Explainability and Copilot

* **TFT**: variable-selection weights and attention weights are saved at forecast time (`forecast_explanations`) and shown in the Explain panel.
* **LightGBM**: TreeSHAP contributions per prediction.
* **Narrative**: a deterministic text generator describes the forecast (trend, festival effect, promotion effect, risk). A **number-grounding guard** verifies every number in the text exists in the underlying data, otherwise the system falls back to the deterministic template built from the stored facts.
* **Copilot**: answers with **read-only tools** (stock, forecasts, arena, alerts). It works without any API key (rule-based planner); an LLM transport can be plugged in and is covered by tests with a fake transport.

## 11. Live operations and MLOps

* **Event stream**: Redis pub/sub channel per organisation, relayed by the API as **Server-Sent Events**; the browser reconnects with back-off and refreshes its session silently when the 15-minute access cookie expires.
* **Business simulator**: a worker job simulates trading days through the **real checkout service** (so it exercises stock locking, idempotency, alerts and audit exactly as a real till would), with speed control.
* **Jobs**: queued/running/succeeded/failed/cancelled with live progress and log; cancellation flips the status immediately and the running backtest stops at its next checkpoint.
* **Model registry**: every model run, its metrics, champion/challenger status and the **MLflow run link**; promote a champion with one click.
* **Drift monitor**: **PSI** on input distributions plus **live WAPE** once at least 500 actual rows exist; a breach can trigger **one automatic retrain per model version**.
* **Scheduled work**: weekly retrain (Mondays 02:00) and daily drift check (03:00) via ARQ cron; both are also available as buttons.
* **System page**: p50/p95 latency, requests per minute, 5xx rate, queue depth, database size, cache hit rate, worker heartbeat, versions; every tile states its data source.

## 12. Backend design

* **Modular monolith** (`services/api/kalpa_api/modules/<domain>/{router,service,schemas}`): `auth`, `orgs`, `crud` (catalog), `data`, `forecasts`, `policy`, `purchase_orders`, `pos`, `scenarios`, `explain`, `copilot`, `mlops`, `simulator`, `stream`, `system`, `audit`, `public`.
* **API**: 90 operations over 68 paths, documented in OpenAPI (`artifacts/openapi.json`) and Swagger UI (`/docs`); the web client is generated from the schema.
* **Multi-tenancy**: JWT carries the organisation; every table has `org_id`; an org-scoped repository filters every query; cross-tenant access returns 404.
* **Roles**: owner, planner, store manager (scoped to chosen locations), viewer (read-only).
* **Concurrency correctness**: atomic stock decrement (`UPDATE ... WHERE on_hand >= qty RETURNING`), idempotency keys on checkout, one transaction per sale.
* **Caching**: per-organisation, per-group version counters in Redis; any successful write bumps the groups it can affect, so users never see stale data after their own change; TTL bounds staleness from background writers; Redis errors fail open.
* **Resilience**: standard JSON error shape with request id, catch-all 500 handler, health endpoint reporting DB/Redis/worker, shared connection pool, Argon2 in worker threads so hashing never blocks the event loop.
* **Design decisions** with reasons are recorded in [`docs/DECISIONS.md`](docs/DECISIONS.md).

## 13. Frontend design

* **Pages**: Landing, Sign-up/Login, Overview (Command Center), Forecasts, Arena, Scenarios, Replenishment, Inventory, Purchase orders, POS, Data, Models, Jobs, System, Settings, plus a UI-kit page.
* **Design system**: tokenised colours and spacing in Tailwind v4, light and dark themes, Geist typography, consistent cards, tables, chips, KPI tiles that always show their data source.
* **Accessibility**: automated axe checks in both themes, keyboard-navigable command palette (cmdk), visible focus, text alternatives and **data-table fallbacks for every chart**.
* **Performance**: ECharts and heavy panels are lazy-loaded, virtualised tables for large lists, query caching with TanStack Query, optimistic stream updates.
* **Responsive**: verified at 375 px (mobile) up to desktop.
* **Real-time UI**: animated numbers, alert toasts and a notifications panel driven by the SSE stream; stream status chip shows connected / reconnecting.

## 14. Security

* Argon2 password hashing; short-lived access JWT and **one-time refresh tokens** in **HttpOnly** cookies; **CSRF** token required on every write.
* **Role-based access control** and **tenant isolation** tested exhaustively (`test_rbac.py`, `test_tenancy.py`).
* **Rate limiting** (Redis fixed window) per IP and per user on auth, sign-up, uploads, demo loading and Copilot; the client IP is the **TCP peer**, never a spoofable `X-Forwarded-For`; fails open if Redis is down.
* **Production configuration guard**: the API refuses to start outside `ENVIRONMENT=dev` with the built-in secrets, short or identical JWT secrets, or the default database password.
* **Security headers** on all responses; standard error responses never leak stack traces.
* **Uploads**: size limits, content validation, filename sanitisation (path and backslash safe).
* **SQL injection probes** and expired/forged token tests in `test_security.py` (parameterised queries throughout).
* **Audits** (saved in `artifacts/security/`): `pip-audit` 143 packages, **0 known vulnerabilities**; `pnpm audit` **0 vulnerabilities at any severity**; `detect-secrets` scan reviewed, no keys or tokens.

## 15. Performance and load testing

Locust, 50 concurrent users, 150 s, one organisation with 600 series; mix of reads plus real POS checkouts (`tests/load/locustfile.py`, evidence in `artifacts/perf/`).
Four runs, each change driven by the previous run's numbers (shared connection pool, response cache, 4 workers, Argon2 in threads):

| Run | Aggregated p95 | Failures |
|---|---|---|
| 1. Baseline (new DB engine per request) | 6,900 ms | 12 of 1,748 |
| 2. Shared pool + cache, 1 worker | 3,000 ms | 0 of 3,118 |
| 3. + 4 workers | 500 ms | 0 of 5,563 |
| 4. Final (+ Argon2 in threads) | **400 ms** | **0 of 5,642** |

Typical read endpoints finish with p50 of 7-40 ms and p95 of 140-260 ms (forecast series 150 ms, summary 140 ms, arena 150 ms, drift 260 ms, system metrics 170 ms, POS checkout 220 ms). The heaviest computations on a cache miss (recommendations 590 ms, narrative 770 ms) and the 50 simultaneous Argon2 logins at the start of the test (memory-hard by design) are the slowest.
Hardware: a 12-logical-core Windows laptop with 7.3 GB RAM shared with the load generator, PostgreSQL, Redis and the worker, so these figures are for a modest development machine.

**30-minute simulator soak** (real API, worker, PostgreSQL and Redis): 30 simulated business days, **11,013 checkouts**, 33,250 units, ₹46,83,268 revenue, **0 errors**, stock never negative, 12,806 live events delivered with a largest gap of 1.2 s.

## 16. Quality assurance

| Layer | What is tested | Tooling |
|---|---|---|
| ML package | metrics, features, backtest, calibration, TFT layers and model, policy, replay, hierarchy, drift, explanations, scenario, zero-fill, data loaders and validation, synthetic generator | pytest, hypothesis (property tests) |
| API | auth, invites, RBAC, tenant isolation, data pipeline, forecasts, decisions, POS (including concurrency), scenarios, explain and copilot, realtime and simulator, security, caching, health | pytest, httpx, fakeredis, 93 % line coverage of API + ML code |
| Web unit and component | formatting, live-stream reducer, chart fallbacks, primitives | Vitest + Testing Library |
| Browser end-to-end | landing, sign-up to onboarding, decisions flow, explain, realtime (SSE, alerts, reconnect), status, mobile | Playwright on real Chrome |
| Accessibility | WCAG contrast and structure in light and dark, signed-out and signed-in | axe-core |
| Static analysis | Ruff, mypy, ESLint, `tsc --noEmit` | enforced in CI workflow |

The suite contains over 230 Python test functions and over 50 web tests (unit, component and browser specs).

## 17. Experimental results

> Source of every number: files in `artifacts/` (rendered into [`docs/RESULTS.md`](docs/RESULTS.md) by `scripts/export_results.py`).
> The dataset is the synthetic Bengaluru FreshMart benchmark described in section 7.

### 17.1 Model arena (600 series, 3 rolling folds x 28 days, split-conformal per category)

| Model | WAPE | MASE | Bias | Pinball | Coverage@80 raw | Coverage@80 calibrated |
|---|---|---|---|---|---|---|
| **TFT (ours, pure PyTorch)** | **39.2 %** | **0.734** | -11.3 % | **1.045** | 79.0 % | 81.5 % |
| AutoETS | 40.0 % | 0.760 | -2.9 % | 1.073 | 82.7 % | 82.6 % |
| LightGBM (Tweedie) | 40.1 % | 0.768 | -0.4 % | 1.069 | 86.5 % | 83.3 % |
| Seasonal Naive | 53.6 % | 0.993 | -5.1 % | 1.456 | 92.7 % | 86.2 % |

The TFT has the lowest WAPE, MASE and pinball loss. Conformal calibration moves the TFT's raw 79.0 % coverage to 81.5 % against the 80 % target.
The whole benchmark (all models, 3 folds) runs in 1,113 s on a laptop CPU.

### 17.2 Decision-focused evaluation (demo tenant, last fold replayed day by day)

| Model | WAPE | Replay cost | Fill rate | Stock-out days | Average inventory |
|---|---|---|---|---|---|
| **TFT (ours)** | 45.1 % | **₹1,05,130** | 99.2 % | **80** | 92.4 |
| LightGBM (Tweedie) | 47.5 % | ₹1,31,638 | 99.2 % | 112 | 86.8 |
| Seasonal Naive | 56.9 % | ₹1,49,867 | 99.0 % | 112 | 94.2 |

Total cost = holding cost + lost-sales cost. Relative to LightGBM the TFT policy costs about 20 % less, and about 30 % less than Seasonal Naive, with fewer stock-out days.
The demo tenant uses 2 folds, which is why its WAPE values differ from section 17.1.

### 17.3 Reproduce the numbers

```bash
uv run python -m kalpa_ml.run_arena          # arena benchmark -> artifacts/benchmarks/arena.json
uv run python scripts/prepare_demo.py        # demo tenant + replay cost -> artifacts/benchmarks/demo_arena_cost.json
uv run python scripts/export_results.py      # rebuilds docs/RESULTS.md from the saved files
```

## 18. Installation and usage

### Prerequisites
Python 3.12, [uv](https://docs.astral.sh/uv/), Node 26 with pnpm 12, Redis, and PostgreSQL 16 (the repository can also run an embedded PostgreSQL through `pgserver`, so no installation is needed on Windows). About 2 GB of free RAM is recommended while training the TFT.

### One-command start on Windows
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1     # Redis, PostgreSQL, API (2 workers), worker, web, MLflow
uv run python scripts/prepare_demo.py                               # builds the FreshMart demo tenant (about 10 min, once)
powershell -File scripts\stop_demo.ps1                              # stop everything
```
Open **http://localhost:3000** (web), **http://localhost:8000/docs** (Swagger), **http://localhost:5000** (MLflow).

### Manual start
```bash
uv sync                                  # Python workspace (services/api, services/worker, packages/ml)
pnpm install                             # web dependencies
cd services/api && uv run alembic upgrade head && cd ../..
uv run uvicorn kalpa_api.main:app --app-dir services/api --port 8000 --workers 2
uv run arq kalpa_worker.main.WorkerSettings
cd apps/web && NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm build && pnpm start
```
Environment variables (`DATABASE_URL`, `REDIS_URL`, secrets, rate-limit settings) are read by `kalpa_api/core/config.py`; non-dev mode refuses default secrets. A `docker-compose.yml` is included for container deployment.

### Demo accounts (created by `prepare_demo.py`, password `demo-password-123`)
| Role | Email |
|---|---|
| Owner | `freshmart-owner@example.com` |
| Planner | `freshmart-planner@example.com` |
| Viewer (read-only) | `freshmart-viewer@example.com` |

### Tests and quality gates
```bash
uv run pytest -q                 # Python tests
uv run ruff check . && uv run mypy services/api/kalpa_api services/worker/kalpa_worker packages/ml/kalpa_ml
pnpm test && pnpm lint && pnpm typecheck
cd apps/web && npx playwright test     # browser journeys (needs the stack running)
uvx locust -f tests/load/locustfile.py # load test
```

### Walkthrough
An 8-minute guided demo with timings is in [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md); likely questions and answers tied to the code are in [`docs/VIVA_QA.md`](docs/VIVA_QA.md).

## 19. Repository structure

```
kalpa/
├── apps/web/                  Next.js application
│   ├── app/                   routes: landing, auth, app/{overview,forecasts,arena,scenarios,replenishment,
│   │                          inventory,purchase-orders,pos,data,models,jobs,system,settings}
│   ├── components/            charts, data primitives, layout (command palette, notifications, copilot drawer)
│   ├── lib/                   typed API client, live-stream reducer, formatting
│   └── tests/                 unit (Vitest) and e2e (Playwright + axe)
├── services/
│   ├── api/                   FastAPI app: kalpa_api/{core,db,modules/*}, alembic migrations, tests
│   └── worker/                ARQ worker: backtest/retrain, simulator, scheduled retrain, drift check
├── packages/ml/kalpa_ml/      TFT, baselines, features, backtest, calibration, hierarchy, policy, replay,
│                              scenario, drift, explain, registry (MLflow), synthetic data generator
├── scripts/                   start_demo.ps1, stop_demo.ps1, prepare_demo.py, export_results.py, pg_start.py
├── tests/load/                Locust load test
├── artifacts/                 benchmarks, perf, security audits, screenshots, openapi.json
├── docs/                      PROGRESS, DECISIONS, VERSIONS, GENERATOR_CARD, RESULTS, DEMO_SCRIPT, VIVA_QA
├── .github/workflows/ci.yml   lint, type-check, tests, build
└── docker-compose.yml, Makefile, pyproject.toml, pnpm-workspace.yaml, uv.lock, pnpm-lock.yaml
```

## 20. Scope, assumptions and future work

**Scope of the reported results.** Accuracy and cost figures are measured on the synthetic Bengaluru FreshMart dataset, a
controlled benchmark with known effects; they validate the methodology and the pipeline. Applying the platform to a retailer's own
data uses the same upload pipeline (section 7) and the same arena.

**Modelling assumptions.** Weather is not a model input in the Scenario Studio (it is a calendar-driven synthetic effect in the
generator), so the studio offers price, promotion depth and festival changes. Replay cost uses configurable holding and
lost-sales cost parameters per organisation.

**Roadmap** (extensions that build on the existing interfaces):
* evaluation on public benchmarks such as M5 through the existing loader (`make seed-benchmark`);
* additional arena entrants (foundation time-series models, neural baselines), ablation studies and hyper-parameter search;
* wiring hierarchical reconciliation (already implemented and tested) into scheduled jobs and dashboards;
* a store-network risk map and 28-day KPI strip on the Command Center;
* containerised cloud deployment, Lighthouse budgets, and a strict Content-Security-Policy header;
* supplier lead-time uncertainty in the policy engine.

## 21. Documentation index and reproducibility

| Document | Content |
|---|---|
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | Phase-by-phase build log with acceptance evidence and the bugs found and fixed |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | The reason behind every non-trivial design decision |
| [`docs/RESULTS.md`](docs/RESULTS.md) | All experimental results, generated from saved artifacts |
| [`docs/GENERATOR_CARD.md`](docs/GENERATOR_CARD.md) | Full specification of the synthetic dataset |
| [`docs/VERSIONS.md`](docs/VERSIONS.md) | Pinned toolchain versions |
| [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | Timed 8-minute demonstration |
| [`docs/VIVA_QA.md`](docs/VIVA_QA.md) | Questions and answers with code pointers |
| [`artifacts/`](artifacts/) | Benchmarks, load test, soak test, audits, screenshots, OpenAPI schema |

Reproducibility: fixed seeds (generator seed 42), locked dependencies (`uv.lock`, `pnpm-lock.yaml`), versioned migrations, every
reported number traceable to a file in `artifacts/`.

## 22. Keywords and citation

**Keywords**: demand forecasting, probabilistic forecasting, temporal fusion transformer, quantile regression, conformal
prediction, inventory optimisation, newsvendor, reorder point, replenishment, decision-focused evaluation, retail analytics,
MLOps, drift detection, explainable AI, SHAP, multi-tenant SaaS, FastAPI, Next.js, PyTorch.

**Suggested citation**
```bibtex
@software{kalpa2026,
  title  = {Kalpa: Probabilistic Demand Forecasting and Decision-Focused Inventory Intelligence},
  author = {Souharda6996},
  year   = {2026},
  url    = {https://github.com/Souharda6996/kalpa-demand-inventory-intelligence}
}
```

