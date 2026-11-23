"""MLflow logging (local SQLite tracking store by default)."""

from pathlib import Path
from typing import Any

import mlflow

DEFAULT_URI = "sqlite:///data/mlflow.db"


def log_run_ref(
    experiment: str,
    run_name: str,
    params: dict[str, Any],
    metrics: dict[str, float],
    artifacts: list[Path],
    tracking_uri: str = DEFAULT_URI,
) -> tuple[str, str]:
    """Log params/metrics/artifacts as one MLflow run; returns (run id, experiment id). NaN metrics are skipped."""
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params({k: str(v)[:250] for k, v in params.items()})
        mlflow.log_metrics({k: v for k, v in metrics.items() if v == v})
        for a in artifacts:
            mlflow.log_artifact(str(a))
        return str(run.info.run_id), str(run.info.experiment_id)


def log_run(
    experiment: str,
    run_name: str,
    params: dict[str, Any],
    metrics: dict[str, float],
    artifacts: list[Path],
    tracking_uri: str = DEFAULT_URI,
) -> str:
    """Like log_run_ref but returns only the run id."""
    return log_run_ref(experiment, run_name, params, metrics, artifacts, tracking_uri)[0]
