# Load test: 50 concurrent users (Locust)

Script: `tests/load/locustfile.py`. 50 users, spawn rate 10/s, 150 s, one organisation (600 series, LightGBM champion).
Mix: mostly reads of the main endpoints, plus 1 in ~20 actions a real POS checkout. API p95 budget for reads: 300 ms.

**Machine**: a 12-logical-core Windows laptop with 7.3 GB RAM, shared with Locust itself, Postgres (embedded), Redis,
the ARQ worker and other desktop apps. Numbers are for this setup, not for a server.

## p95 response time (ms) across four runs of the same test

| endpoint | 1 baseline | 2 pool+cache, 1 worker | 3 + 4 workers | 4 final (+argon2 in threads) |
|---|---|---|---|---|
| GET /explain/narrative | 7500 | 4000 | 860 | 770 |
| GET /forecasts/series | 5900 | 2000 | 190 | 150 |
| GET /forecasts/summary | 5000 | 2300 | 180 | 140 |
| GET /inventory (page) | 5100 | 2200 | 140 | 150 |
| GET /live/state | 8600 | 2700 | 330 | 240 |
| GET /models/arena | 5100 | 2600 | 250 | 150 |
| GET /models/drift | 9000 | 5500 | 780 | 260 |
| GET /models/registry | 5800 | 1900 | 140 | 180 |
| GET /policy/recommendations | 7400 | 4000 | 550 | 590 |
| GET /products (page) | 5400 | 2100 | 170 | 180 |
| GET /system/metrics | 11000 | 4400 | 240 | 170 |
| POST /auth/login | 5600 | 4100 | 4700 | 3000 |
| POST /pos/checkout | 10000 | 3700 | 260 | 220 |
| Aggregated | 6900 | 3000 | 500 | 400 |
| **requests / failures** | 1748 / 12 | 3118 / 0 | 5563 / 0 | 5642 / 0 |

## What changed between the runs (each change was driven by the previous run's numbers)

1. **Run 1 -> 2**: the API built and disposed a database engine for EVERY request (a new Postgres connection with
   authentication each time: a ~50 ms floor on every endpoint, and connection storms under load). Now one pooled engine per
   process. Single-user timing alone went from 87 -> 7 ms (`/locations`) and 54 -> 6 ms (`/auth/me`). Also: Redis response
   cache for the heavy reads, and a 500 on `/explain/narrative` (a series the model never trained on) fixed.
2. **Run 2 -> 3**: the cache version was bumped by *every* write, so POS sales in the mix kept discarding the model
   leaderboard. Invalidation is now per group (models / stock). The API runs 4 uvicorn workers (it keeps no per-process state
   that matters: sessions and rate limits are in Redis, the SSE relay uses Redis pub/sub).
3. **Run 3 -> 4**: Argon2 hashing moved off the event loop (`asyncio.to_thread`).

## Reading the final run honestly

- 5,642 requests, 0 failures. Aggregate p50 12 ms, p95 400 ms.
- All cheap reads and the checkout are under the 300 ms p95 budget (140-260 ms).
- **Over budget**: `GET /policy/recommendations` (590 ms) and `GET /explain/narrative` (770 ms) are genuinely heavy
  computations on a cache miss (the cache lives 10 s and 5 min). `POST /auth/login` (3 s) is the 50 simultaneous logins at the
  start of the test: Argon2 is memory-hard on purpose; real traffic does not log in in a burst.
- The cache hides cost only for repeated reads: the first read after a write recomputes.
- Single run per configuration, same machine, so differences of ~20 % between runs 3 and 4 are within noise.

Files: `locust_stats.csv` (final run), `locust.html` (final run report), `run1_*`, `run2_*`, `run3_*` (earlier runs),
`simulator_soak_30min.json` (Phase 8 soak).
