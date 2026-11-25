"""Shared observability primitives for the TensorPlay ecosystem.

Four signals, one contract, used by both megaLM and megaRL:

* Prometheus text 0.0.4 (Python renderer here; Rust crate for gateways);
* OTLP metrics + traces via the official OpenTelemetry SDK (optional extra);
* single-line JSON logs with trace/span correlation;
* multi-backend training trackers (TensorBoard, W&B, JSONL, console).

Everything is optional and a no-op until configured. Importing this package
never starts a thread, opens a socket or changes the root logger.
"""

from __future__ import annotations

from .config import ObsConfig
from .jsonlog import (
    JsonLogFormatter,
    LATENCY_BUCKETS_MS,
    configure_logging,
    histogram_totals,
    labeled_sample,
    log_event,
    observe_latency,
)
from .metrics import MetricRegistry, get_registry
from .prometheus import render_prometheus_metrics
from .tracking import Tracking, parse_key

__version__ = "0.1.0"

__all__ = [
    "JsonLogFormatter",
    "LATENCY_BUCKETS_MS",
    "MetricRegistry",
    "ObsConfig",
    "Tracking",
    "configure_betterstack",
    "configure_logging",
    "configure_telemetry",
    "get_registry",
    "histogram_totals",
    "labeled_sample",
    "log_event",
    "observe_latency",
    "parse_key",
    "render_prometheus_metrics",
    "__version__",
]


def configure_telemetry(*args: object, **kwargs: object) -> bool:
    """Lazy OTLP setup; returns False when the optional extra is absent."""

    from .otel import configure_telemetry as _configure

    return _configure(*args, **kwargs)


def configure_betterstack(*args: object, **kwargs: object) -> object | None:
    """Lazy Better Stack log sink; no-op without a source token."""

    from .betterstack import configure_betterstack as _configure

    return _configure(*args, **kwargs)
