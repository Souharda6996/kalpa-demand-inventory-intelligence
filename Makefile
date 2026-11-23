.PHONY: up down lint test gen-api migrate seed-demo seed-benchmark generator-card

up:
	docker compose up --build -d

down:
	docker compose down

lint:
	uv run ruff check . && uv run mypy services/api/kalpa_api services/worker/kalpa_worker packages/ml/kalpa_ml && pnpm lint && pnpm typecheck

test:
	uv run pytest -q && pnpm test

gen-api:
	pnpm gen:api

migrate:
	cd services/api && uv run alembic upgrade head

seed-demo: migrate
	uv run python -m kalpa_api.scripts.seed demo

# Needs the Kaggle M5 files in data/raw/m5 (sales_train_evaluation.csv, calendar.csv, sell_prices.csv)
seed-benchmark: migrate
	uv run python -m kalpa_api.scripts.seed benchmark --raw-dir data/raw/m5

generator-card:
	uv run python -m kalpa_api.scripts.seed card
