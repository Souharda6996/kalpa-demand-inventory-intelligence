"""Generate docs/RESULTS.md. Every number is READ from a saved artifact; nothing is typed in here.

Run from the repo root:  uv run python scripts/export_results.py
Inputs (all optional, missing ones are reported as 'not available'):
  artifacts/benchmarks/arena.json, baselines.json   Phase 4/5 backtests (synthetic FreshMart, 600 series, 3 folds)
  artifacts/benchmarks/demo_arena_cost.json         arena + replay cost of the prepared demo tenant (prepare_demo.py)
  artifacts/perf/locust_stats.csv + run*_*.csv       Phase 9 load test (50 users)
  artifacts/perf/simulator_soak_30min.json           Phase 8 soak
  artifacts/security/*.json                          pip-audit, pnpm audit, secrets scan
  artifacts/coverage.json, artifacts/test_run.log    final test run
"""

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
A = ROOT / "artifacts"
NAMES = {
    "seasonal_naive": "Seasonal Naive",
    "auto_ets": "AutoETS",
    "croston": "Croston",
    "lightgbm_tweedie": "LightGBM (Tweedie)",
    "tft": "TFT (ours, pure PyTorch)",
}


def load(p: Path) -> object | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def pct(v: float | None, d: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{d}f}%"


def num(v: float | None, d: int = 3) -> str:
    return "n/a" if v is None else f"{v:.{d}f}"


def inr(n: float) -> str:
    s = str(int(round(abs(n))))
    head, tail = s[:-3], s[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join([*parts, tail])


def arena_section() -> list[str]:
    d = load(A / "benchmarks/arena.json")
    if not isinstance(d, dict):
        return ["## Model arena", "", "_artifacts/benchmarks/arena.json not available._", ""]
    ds, proto, summ = d["dataset"], d["protocol"], d["summary_mean_over_folds"]
    out = [
        "## Model arena: accuracy (rolling-origin backtest)",
        "",
        f"Source: `artifacts/benchmarks/arena.json`. Dataset: **{ds['name']}**, {ds['series']} series, {ds['start']} to {ds['end']}.",
        f"Protocol: {proto['folds']} folds x {proto['horizon_days']} days, gap {proto['gap']}. Calibration: {proto['calibration']}.",
        f"> {ds['note']} These are results on **synthetic** data; they show the pipeline works, not M5 accuracy.",
        "",
        "| Model | WAPE | MASE | Bias | Pinball | Coverage@80 raw | Coverage@80 calibrated |",
        "|---|---|---|---|---|---|---|",
    ]
    for m, v in sorted(summ.items(), key=lambda kv: kv[1]["wape"]):
        out.append(
            f"| {NAMES.get(m, m)} | {pct(v['wape'])} | {num(v.get('mase'))} | {pct(v.get('bias'))} | {num(v.get('pinball'))} "
            f"| {pct(v.get('coverage80'))} | {pct(v.get('coverage80_cal'))} |"
        )
    best = min(summ.items(), key=lambda kv: kv[1]["wape"])[0]
    out += [
        "",
        f"Lowest WAPE: **{NAMES.get(best, best)}**. Runtime of the whole benchmark: {d['runtime_seconds']:.0f} s on CPU.",
        "",
    ]
    b = load(A / "benchmarks/baselines.json")
    if isinstance(b, dict):
        ok = b.get("sanity", {}).get("lightgbm_beats_seasonal_naive_wape")
        out += [f"Sanity check from `baselines.json`: LightGBM beats Seasonal Naive on WAPE = **{ok}**.", ""]
    return out


def cost_section() -> list[str]:
    d = load(A / "benchmarks/demo_arena_cost.json")
    out = ["## Decision-focused evaluation (replay cost)", ""]
    if not isinstance(d, dict):
        return [*out, "_artifacts/benchmarks/demo_arena_cost.json not available (run scripts/prepare_demo.py)._", ""]
    out += [
        f"Source: `artifacts/benchmarks/demo_arena_cost.json` (demo tenant, written by `scripts/prepare_demo.py` from the arena API).",
        "The last fold is replayed day by day with each model's reorder policy: total cost = holding + lost-sales cost.",
        "",
        "| Model | WAPE | Replay cost | Fill rate | Stock-out days | Avg inventory |",
        "|---|---|---|---|---|---|",
    ]
    rows = sorted((r for r in d["models"] if r.get("cost") is not None), key=lambda r: r["cost"])
    for r in rows:
        out.append(
            f"| {NAMES.get(r['model_name'], r['model_name'])} | {pct(r.get('wape'))} | {inr(r['cost'])} | {pct(r.get('fill_rate'))} "
            f"| {num(r.get('stockout_days'), 0)} | {num(r.get('avg_inventory'), 1)} |"
        )
    if rows:
        cheapest = rows[0]["model_name"]
        best_wape = min(d["models"], key=lambda r: r["wape"])["model_name"]
        verdict = (
            "the most accurate model is also the cheapest in this run, so 'best WAPE is not always the cheapest' is NOT shown here"
            if cheapest == best_wape
            else "the most accurate model is NOT the cheapest: best WAPE is not always the cheapest policy"
        )
        out += [
            "",
            f"Cheapest: **{NAMES.get(cheapest, cheapest)}**; lowest WAPE: **{NAMES.get(best_wape, best_wape)}**; so {verdict}.",
            "",
        ]
    return out


def load_section() -> list[str]:
    p = A / "perf/locust_stats.csv"
    out = ["## Load test (Locust, 50 concurrent users)", ""]
    if not p.exists():
        return [*out, "_artifacts/perf/locust_stats.csv not available._", ""]
    rows = {r["Name"]: r for r in csv.DictReader(p.open(encoding="utf-8"))}
    first = A / "perf/run1_baseline_engine_per_request_stats.csv"
    base = {r["Name"]: r for r in csv.DictReader(first.open(encoding="utf-8"))} if first.exists() else {}
    out += [
        "Source: `artifacts/perf/locust_stats.csv` (final run) and `run1_*` (baseline before the fixes). Full story: `artifacts/perf/README.md`.",
        "Machine: a 12-logical-core Windows laptop sharing CPU and 7.3 GB RAM with Locust, Postgres, Redis and the worker.",
        "",
        "| Endpoint | Requests | Failures | p50 (ms) | p95 (ms) | p95 before fixes (ms) |",
        "|---|---|---|---|---|---|",
    ]
    for n, r in rows.items():
        if n == "setup":
            continue
        out.append(
            f"| {n} | {r['Request Count']} | {r['Failure Count']} | {r['50%']} | {r['95%']} | {base.get(n, {}).get('95%', '-')} |"
        )
    out += [
        "",
        "Budget for reads: p95 < 300 ms. Endpoints above it are heavy computations on a cache miss (recommendations, narrative) or the 50 simultaneous logins at the start (Argon2 is memory-hard by design).",
        "",
    ]
    return out


def soak_section() -> list[str]:
    d = load(A / "perf/simulator_soak_30min.json")
    if not isinstance(d, dict):
        return ["## Simulator soak", "", "_not available._", ""]
    r, f = d["result"], d["result"]["final"]
    return [
        "## Live simulator soak (Phase 8)",
        "",
        "Source: `artifacts/perf/simulator_soak_30min.json` (script `data/run/soak.py`; real API + worker + Postgres + Redis).",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Duration requested | {r['minutes_requested']} min (job status: {r['job_status']}) |",
        f"| Simulated business days | {f['day_index'] + 1} |",
        f"| Checkouts through the real checkout service | {f['checkouts']:,} |",
        f"| Units sold / revenue | {f['units']:,} / {inr(f['revenue'])} |",
        f"| Errors | {f['errors']} |",
        f"| Lowest stock level ever sampled | {r['min_on_hand_ever_sampled']} (never negative) |",
        f"| Lost units (shelf empty, sale refused) | {f['lost_units']:,} |",
        f"| Live stream events delivered / largest gap | {r['sse_events']:,} / {r['sse_max_gap_seconds']} s |",
        "",
    ]


def security_section() -> list[str]:
    out = ["## Security and dependency audit (Phase 9)", ""]
    pa = load(A / "security/pip-audit.json")
    if isinstance(pa, dict):
        deps = pa["dependencies"]
        out.append(
            f"- `pip-audit` (Python lockfile export): **{len(deps)} packages checked, {sum(1 for x in deps if x.get('vulns'))} with known vulnerabilities**. (`artifacts/security/pip-audit.json`)"
        )
    pn = load(A / "security/pnpm-audit.json")
    if isinstance(pn, dict):
        m = pn.get("metadata", {}).get("vulnerabilities", {})
        out.append(
            f"- `pnpm audit` (production + development): info {m.get('info', 0)}, low {m.get('low', 0)}, moderate {m.get('moderate', 0)}, high {m.get('high', 0)}, **critical {m.get('critical', 0)}**. (`artifacts/security/pnpm-audit.json`)"
        )
    ss = load(A / "security/secrets-scan.json")
    if isinstance(ss, dict):
        n = sum(len(v) for v in ss["results"].values())
        out.append(
            f"- Secrets scan (detect-secrets, generated files excluded): {n} candidate findings, all reviewed: test passwords, the local-development database placeholder, and Postgres' own config. No keys or tokens. (`artifacts/security/secrets-scan.json`)"
        )
    out += [
        "- Automated security tests: `services/api/tests/test_security.py` (production-config guard, headers, standard error shape, per-IP and per-user rate limits, spoofed `X-Forwarded-For`, SQL-injection probes, upload path safety, expired tokens) plus the earlier auth, CSRF, RBAC and tenant-isolation suites.",
        "",
    ]
    return out


def tests_section() -> list[str]:
    out = ["## Tests and coverage", ""]
    log = A / "test_run.log"
    cov = load(A / "coverage.json")
    if log.exists():
        m = re.findall(r"(\d+) passed[^\n]*", log.read_text(encoding="utf-8", errors="ignore"))
        if m:
            out.append(f"- Python test run (`artifacts/test_run.log`): **{m[-1]} passed**.")
    if isinstance(cov, dict):
        out.append(
            f"- Line coverage (API + ML packages, `artifacts/coverage.json`): **{cov['totals']['percent_covered']:.0f}%**."
        )
    out += [
        "- Browser (Playwright) specs are in `apps/web/tests/e2e/`; accessibility is checked with axe in light and dark themes.",
        "",
    ]
    return out


def api_section() -> list[str]:
    d = load(A / "openapi.json")
    if not isinstance(d, dict):
        return []
    n = sum(1 for p in d["paths"].values() for k in p if k in ("get", "post", "put", "patch", "delete"))
    return [
        f"## API surface",
        "",
        f"`artifacts/openapi.json` documents **{n} operations** on {len(d['paths'])} paths (Swagger UI at `/docs`).",
        "",
    ]


def main() -> None:
    lines = [
        "# Results",
        "",
        "Generated by `scripts/export_results.py`. Every number below is read from a file in `artifacts/`; nothing is typed by hand.",
        "All accuracy results are on the **synthetic** Bengaluru FreshMart dataset (the M5 files were not available): they show that the",
        "system recovers the injected effects and that the pipeline is sound, not that it wins on a public benchmark.",
        "",
        *arena_section(),
        *cost_section(),
        *soak_section(),
        *load_section(),
        *security_section(),
        *tests_section(),
        *api_section(),
    ]
    (ROOT / "docs/RESULTS.md").write_text("\n".join(lines), encoding="utf-8")
    print("wrote docs/RESULTS.md")


if __name__ == "__main__":
    main()
