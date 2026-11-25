"""Structured single-line JSON logging shared by TensorPlay services.

Copied from megaLM's proven ``observability.py`` and generalized: logger names
and the record field key are service-independent. Log fields are merged with
the active OpenTelemetry trace context when the optional extra is configured,
so JSON logs, OTLP traces and Prometheus metrics correlate on the same run.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import sys
from typing import Any

_FIELDS_KEY = "_tp_fields"


class JsonLogFormatter(logging.Formatter):
    """One-line JSON logs for cold-start and failure paths."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
            "pid": os.getpid(),
        }
        fields = getattr(record, _FIELDS_KEY, None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )


def configure_logging(level: str = "INFO", *, service: str = "tp") -> None:
    """Configure a service-owned logger without mutating the root logger."""

    logger = logging.getLogger(service)
    if not any(
        getattr(handler, "_tp_json", False)
        for handler in logger.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonLogFormatter())
        handler._tp_json = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: object,
) -> None:
    merged = dict(fields)
    try:
        from .otel import get_current_trace_fields

        merged.update(get_current_trace_fields())
    except Exception:  # pragma: no cover - optional dependency
        pass
    logger.log(level, event, extra={_FIELDS_KEY: merged})


# Latency histogram boundaries (milliseconds). The Rust Prometheus renderer
# treats the integer keys of a `*_histogram` counter as cumulative `le`
# buckets, so these become real Prometheus histogram series.
LATENCY_BUCKETS_MS: tuple[int, ...] = (
    1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 60000,
)


def observe_latency(histogram: dict[int, int], milliseconds: float) -> None:
    """Bucket one latency observation into the first boundary containing it.

    ``histogram`` is the raw ``Counter``/dict keyed by bucket boundary. Values
    beyond the largest boundary are folded into it so the histogram `_count`
    stays equal to the number of observations.
    """

    for boundary in LATENCY_BUCKETS_MS:
        if milliseconds <= boundary:
            histogram[boundary] = histogram.get(boundary, 0) + 1
            return
    last = LATENCY_BUCKETS_MS[-1]
    histogram[last] = histogram.get(last, 0) + 1


def labeled_sample(
    value: int | float,
    metric_type: str = "counter",
    **labels: object,
) -> dict[str, object]:
    """Build a Prometheus sample with native labels.

    Pass a list of these under a snapshot key to emit multiple samples of one
    metric distinguished by label sets (e.g. HTTP status codes by route).
    """

    return {
        "__value__": value,
        "__type__": metric_type,
        "__labels__": dict(labels),
    }


def histogram_totals(
    buckets: dict[int, int],
    *,
    total_sum: float | None = None,
    total_count: int | None = None,
) -> dict[str, int | float]:
    """Attach exact observation totals to a Prometheus histogram snapshot."""

    result: dict[str, int | float] = dict(buckets)
    if total_sum is not None:
        result["__sum__"] = total_sum
    if total_count is not None:
        result["__count__"] = total_count
    return result


__all__ = [
    "JsonLogFormatter",
    "configure_logging",
    "log_event",
    "LATENCY_BUCKETS_MS",
    "observe_latency",
    "labeled_sample",
    "histogram_totals",
]
