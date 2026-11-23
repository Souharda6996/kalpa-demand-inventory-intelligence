"""Load test: 50 concurrent users on the real API.

Run (API + worker + Postgres + Redis up, an organisation with a champion model, credentials in LOAD_USER_FILE):
    uv run --with locust locust -f tests/load/locustfile.py --headless -u 50 -r 10 -t 150s \
        --host http://localhost:8000 --csv artifacts/perf/locust --html artifacts/perf/locust.html
Every user logs in as the same account (login is only limited after FAILED attempts) and then mixes reads and
a few real checkouts. Reads are the budgeted part (API p95 < 300 ms); the checkout is reported separately.
"""

import json
import os
import random
import uuid
from pathlib import Path

from locust import HttpUser, between, task

CREDS = json.loads(Path(os.environ.get("LOAD_USER_FILE", "data/run/load_user.json")).read_text())
API = "/api/v1"


class Planner(HttpUser):
    wait_time = between(0.5, 2.0)  # think time between actions of one user

    def on_start(self) -> None:
        r = self.client.post(f"{API}/auth/login", json=CREDS, name="POST /auth/login")
        r.raise_for_status()
        self.locs = [x for x in self.client.get(f"{API}/locations?page_size=50", name="setup").json()["items"] if x["type"] == "store"]
        self.prods = self.client.get(f"{API}/products?page_size=100", name="setup").json()["items"]

    def pair(self) -> tuple[str, str]:
        return random.choice(self.locs)["id"], random.choice(self.prods)["id"]

    @task(6)
    def series(self) -> None:
        loc, prod = self.pair()
        self.client.get(f"{API}/forecasts/series?location_id={loc}&product_id={prod}&days=90", name="GET /forecasts/series")

    @task(3)
    def inventory(self) -> None:
        self.client.get(f"{API}/inventory?page_size=50&page={random.randint(1, 10)}", name="GET /inventory (page)")

    @task(2)
    def arena(self) -> None:
        self.client.get(f"{API}/models/arena", name="GET /models/arena")

    @task(2)
    def summary(self) -> None:
        self.client.get(f"{API}/forecasts/summary", name="GET /forecasts/summary")

    @task(2)
    def live(self) -> None:
        self.client.get(f"{API}/live/state", name="GET /live/state")

    @task(2)
    def products(self) -> None:
        self.client.get(f"{API}/products?page_size=50&page={random.randint(1, 2)}", name="GET /products (page)")

    @task(1)
    def recommendations(self) -> None:
        self.client.get(f"{API}/policy/recommendations", name="GET /policy/recommendations")

    @task(1)
    def drift(self) -> None:
        self.client.get(f"{API}/models/drift", name="GET /models/drift")

    @task(1)
    def registry(self) -> None:
        self.client.get(f"{API}/models/registry", name="GET /models/registry")

    @task(1)
    def explain(self) -> None:
        loc, prod = self.pair()
        self.client.get(f"{API}/explain/{loc}/{prod}/narrative", name="GET /explain/narrative")

    @task(1)
    def system(self) -> None:
        self.client.get(f"{API}/system/metrics", name="GET /system/metrics")

    @task(1)
    def checkout(self) -> None:
        """A real sale. 409 (empty shelf) is a correct business answer, not a failure."""
        loc, prod = self.pair()
        body = {"location_id": loc, "lines": [{"product_id": prod, "qty": 1, "unit_price": "10.00"}], "payment_method": "upi"}
        headers = {"Idempotency-Key": uuid.uuid4().hex, "X-CSRF-Token": self.client.cookies.get("kalpa_csrf") or ""}
        with self.client.post(f"{API}/pos/checkout", json=body, headers=headers, name="POST /pos/checkout", catch_response=True) as r:
            if r.status_code in (200, 409):
                r.success()
