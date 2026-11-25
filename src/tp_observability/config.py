"""Environment-driven configuration shared by TensorPlay services.

Nothing here starts a background component: it only describes how callers
should wire logging, metrics and trackers. Defaults are zero-cost — no log
file, no tracker, no OTLP export unless a variable is set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class ObsConfig:
    prefix: str = "tp"
    log_level: str = "INFO"
    log_service: str = "tp"
    jsonl_path: str | None = None
    trackers: tuple[str, ...] = ()
    tensorboard_dir: str | None = None
    wandb_project: str | None = None
    wandb_run: str | None = None
    wandb_mode: str = "online"
    otel_enabled: bool = False
    otel_metrics_endpoint: str | None = None
    otel_traces_endpoint: str | None = None
    max_series_per_metric: int = 512
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, *, prefix: str = "tp", service: str = "tp") -> ObsConfig:
        trackers = tuple(
            item.strip()
            for item in os.getenv("TP_OBS_TRACKERS", "").split(",")
            if item.strip()
        )
        return cls(
            prefix=os.getenv("TP_OBS_PREFIX", prefix),
            log_level=os.getenv("TP_OBS_LOG_LEVEL", "INFO"),
            log_service=service,
            jsonl_path=os.getenv("TP_OBS_JSONL"),
            trackers=trackers,
            tensorboard_dir=os.getenv("TP_TENSORBOARD_DIR"),
            wandb_project=os.getenv("TP_WANDB_PROJECT"),
            wandb_run=os.getenv("TP_WANDB_RUN"),
            wandb_mode=os.getenv("TP_WANDB_MODE", "online"),
            otel_enabled=os.getenv("TP_OTEL_ENABLED", "").lower() in {"1", "true", "yes"},
            otel_metrics_endpoint=os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"),
            otel_traces_endpoint=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"),
            max_series_per_metric=_int_env("TP_OBS_MAX_SERIES", 512),
        )


__all__ = ["ObsConfig"]
