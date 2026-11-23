"""Prepare the demo tenant 'Bengaluru FreshMart' through the real API (so the demo never waits on training).

Needs the stack running (API + worker + Postgres + Redis). Takes ~10-12 min the first time (TFT training on CPU).
Run:  uv run python scripts/prepare_demo.py
Idempotent: if the organisation already has data and models it only (re)writes artifacts/benchmarks/demo_arena_cost.json.

Accounts (password for all: demo-password-123):
  freshmart-owner@example.com    owner   (full access, can approve POs and run everything)
  freshmart-planner@example.com  planner (runs backtests, creates/approves POs)
  freshmart-viewer@example.com   viewer  (read-only demo login)
"""

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:8000/api/v1"
PASSWORD = "demo-password-123"
ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS = [
    ("freshmart-owner@example.com", "Asha Rao (Owner)", "owner"),
    ("freshmart-planner@example.com", "Vikram Nair (Planner)", "planner"),
    ("freshmart-viewer@example.com", "Meera Iyer (Viewer)", "viewer"),
]
c = httpx.Client(base_url=BASE, timeout=600)


def post(url: str, **kw: object) -> httpx.Response:
    return c.post(url, headers={"X-CSRF-Token": c.cookies.get("kalpa_csrf") or ""}, **kw)  # type: ignore[arg-type]


def get(url: str, **kw: object) -> httpx.Response:
    r = c.get(url, **kw)  # type: ignore[arg-type]
    if r.status_code == 401:  # the 15-minute access cookie expired during the long TFT job
        post("/auth/refresh")
        r = c.get(url, **kw)  # type: ignore[arg-type]
    return r


def wait(job_id: str) -> str:
    while True:
        j = get(f"/jobs/{job_id}").json()
        if j["status"] not in ("queued", "running"):
            return str(j["status"])
        print(f"  job {j['status']} {j['progress']:.0%}", end="\r", flush=True)
        time.sleep(5)


def main() -> None:
    email = ACCOUNTS[0][0]
    r = post("/auth/login", json={"email": email, "password": PASSWORD})
    if r.status_code != 200:
        r = post(
            "/auth/signup",
            json={"email": email, "password": PASSWORD, "name": ACCOUNTS[0][1], "org_name": "Bengaluru FreshMart"},
        )
        assert r.status_code == 201, r.text
        print("created organisation + owner")
        for em, name, role in ACCOUNTS[1:]:
            m = post("/org/members", json={"email": em, "name": name, "password": PASSWORD, "role": role})
            assert m.status_code == 201, m.text
            print(f"created {role}: {em}")
    stats = get("/data/stats").json()
    if not stats["series"]:
        print("loading the synthetic FreshMart data (6 stores, 100 SKUs, 2 years)...")
        print(post("/data/demo").json())
    arena = get("/models/arena").json()
    if not any(a["model_name"] == "tft" for a in arena):
        print(
            "running the backtest: Seasonal Naive + LightGBM + TFT, 2 folds (the worker trains TFT on CPU; ~10 min)..."
        )
        job = post(
            "/models/backtests", json={"models": ["seasonal_naive", "lightgbm_tweedie", "tft"], "folds": 2}
        ).json()
        status = wait(job["id"])
        print(f"\nbacktest {status}")
        if status != "succeeded":
            sys.exit("backtest failed: " + str(get(f"/jobs/{job['id']}").json().get("error")))
        arena = get("/models/arena").json()
    costed = [a for a in arena if (a["metrics"] or {}).get("cost") is not None]
    if costed and not any(a["is_champion"] for a in arena):
        best = min(
            costed, key=lambda a: a["metrics"]["cost"]
        )  # champion = cheapest replay policy (the configurable default)
        post(f"/models/runs/{best['id']}/promote")
        print(f"champion promoted: {best['model_name']} (lowest replay cost)")
        arena = get("/models/arena").json()
    rows = []
    for a in arena:
        m = a["metrics"] or {}
        rows.append(
            {
                "model_name": a["model_name"],
                "is_champion": a["is_champion"],
                **{
                    k: m.get(k)
                    for k in (
                        "wape",
                        "mase",
                        "bias",
                        "pinball",
                        "coverage80",
                        "cost",
                        "fill_rate",
                        "stockout_days",
                        "avg_inventory",
                    )
                },
            }
        )
    out = {
        "source": "GET /models/arena of the demo tenant (Bengaluru FreshMart, synthetic)",
        "folds": 2,
        "models": rows,
    }
    path = ROOT / "artifacts/benchmarks/demo_arena_cost.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")
    print("\nDemo accounts (password demo-password-123): " + ", ".join(a[0] for a in ACCOUNTS))


if __name__ == "__main__":
    main()
