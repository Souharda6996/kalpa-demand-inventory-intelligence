# Viva Q&A (answers point to real code)

**1. Why probabilistic forecasts?** Replenishment depends on the tail, not the mean: safety stock is a quantile. The TFT is trained with quantile (pinball) loss for P10/P50/P90 (`packages/ml/kalpa_ml/`, TFT model + loss), and the newsvendor policy in `kalpa_ml/policy` uses the quantile at the service level.

**2. How do you know the intervals are honest?** Split-conformal calibration in `kalpa_ml/backtest.py`: fold k is calibrated on fold k-1 residuals, per category. We report raw and calibrated 80% coverage side by side (`docs/RESULTS.md`, from `artifacts/benchmarks/arena.json`).

**3. Why a rolling-origin backtest?** Random splits leak the future. We use expanding windows with a gap, several folds, and report the mean (`backtest.py`).

**4. Why not just pick the lowest WAPE?** Because cost is asymmetric (holding vs lost sales). `kalpa_ml` replay runs each model's reorder policy day by day and reports cost, fill rate, stock-out days. The arena Cost tab shows it. On the demo tenant TFT is both the most accurate and the cheapest; the ranking by cost still differs from the ranking by WAPE for the other models' fill rate and stock-out days, which is why we show both.

**5. Did the TFT beat LightGBM?** Yes, modestly, on both synthetic runs: 3-fold arena WAPE 39.2% vs 40.1%; demo-tenant 2-fold WAPE 45.1% vs 47.5% and replay cost ₹1,05,130 vs ₹1,31,638 (`docs/RESULTS.md`). The margin is small, so we describe it as consistent, not dramatic. The value is also a probabilistic, interpretable model written from scratch in pure PyTorch.

**6. Why synthetic data? Why not M5?** The M5 files were not available offline. The generator (`docs/GENERATOR_CARD.md`) injects known effects (festivals, promotions, price elasticity) so we can check the models recover them. It proves the pipeline, not public-benchmark accuracy.

**7. How is tenant isolation enforced?** Every table has `org_id`; all queries go through the org-scoped repository; the JWT carries the org; tests prove another org's ids return 404 (`services/api/tests`, tenant-isolation suite).

**8. Authentication and session security?** Argon2 hashes (run in threads so they do not block the event loop), short-lived access JWT and one-time refresh tokens in HttpOnly cookies, CSRF token on writes, role checks (owner/planner/store manager/viewer), per-IP and per-user rate limits that use the TCP peer, not a spoofable `X-Forwarded-For` (`core/ratelimit.py`, `tests/test_security.py`).

**9. How do you avoid overselling stock at the POS?** One atomic SQL statement, `UPDATE ... SET on_hand = on_hand - qty WHERE on_hand >= qty RETURNING`, inside the transaction, plus an idempotency key (`modules/pos/service.py`). The 30-minute soak never saw negative stock (`artifacts/perf/simulator_soak_30min.json`).

**10. How do live updates work?** Services publish events to a Redis channel per organisation; `modules/stream` relays them to the browser as Server-Sent Events; the web client reconnects automatically (`lib/live.ts`).

**11. Why a background worker?** Training takes minutes. The API only enqueues (ARQ on Redis), the worker updates job progress, supports cancel via checkpoints, and logs MLflow runs (`services/worker/kalpa_worker/jobs.py`).

**12. How is caching kept correct?** Per-org, per-group version counters in Redis; any successful write bumps the groups it can affect, so a user never sees stale data after their own change; TTL bounds staleness from background writers (`core/cache.py`). Redis failures fail open.

**13. What did the load test show?** 50 users: aggregate p95 fell from 6.9 s to about 400 ms after fixing a new-engine-per-request bug, adding a shared pool, caching, and 4 workers. Heavy cache-miss endpoints are still above the 300 ms budget; this is stated in `artifacts/perf/README.md`.

**14. How do you detect drift?** PSI on feature distributions plus live WAPE once at least 500 actual rows exist (so tiny samples do not raise false alarms); breach triggers one auto-retrain per model version (`kalpa_ml/drift.py`, `modules/mlops`).

**15. Is the explanation real or decorative?** TFT variable-selection weights and attention come from the trained model; LightGBM uses TreeSHAP. The narrative is templated and a guard rejects any number not present in the data (`kalpa_ml/explain.py`, `modules/explain`). The Copilot works without any LLM key and only has read-only tools.

**16. What is not done?** M5 benchmark, Chronos-2, neuralforecast cross-check, ablations, Optuna, hierarchy wired into jobs, risk map on the Command Center, Docker/CI/Lighthouse/cloud runs. Listed in the README, not hidden.

**17. What would you do next?** Run on real retail data, add Chronos-2 as a zero-shot challenger, wire hierarchical reconciliation, deploy with Docker and a managed Postgres, add supplier lead-time uncertainty to the policy.
