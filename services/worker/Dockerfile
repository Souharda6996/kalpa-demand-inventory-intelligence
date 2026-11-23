FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/api ./services/api
COPY services/worker ./services/worker
COPY packages/ml ./packages/ml
RUN uv sync --frozen --package kalpa-worker --no-dev
ENV PATH="/app/.venv/bin:$PATH"
CMD ["arq", "kalpa_worker.main.WorkerSettings"]
