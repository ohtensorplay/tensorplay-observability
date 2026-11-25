"""Optional OpenTelemetry metrics + traces for TensorPlay services.

Everything in this module is a no-op unless :func:`configure_telemetry` has
been called, so importing it never changes behaviour in tests or embedded use.
megaLM and megaRL share this module and select their metric prefix via
``configure_telemetry(prefix=...)`` / ``mirror_snapshot(..., prefix=...)``.
OTLP-over-HTTP is used (the installed exporter); the standard ``OTEL_*``
environment variables are honoured in addition to explicit arguments.

Signals exported:

* request SLI histograms (TTFT / TPOT / E2E) with semantic attributes;
* HTTP response counters and duration histogram with ``http.*`` attributes;
* every numeric leaf of the Prometheus snapshot, as an observable gauge named
  ``<prefix>.<prometheus_name>`` (keeps OTLP and /metrics in sync);
* request lifecycle spans, started by the gateway with W3C ``traceparent``
  extraction and propagated to worker processes through the request frame.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from typing import Any

_LOGGER = logging.getLogger("tp.otel")

try:  # Optional extra: pip install tensorplay-observability[otel]
    from opentelemetry import metrics as _ot_metrics
    from opentelemetry.metrics import Observation as _Observation
    from opentelemetry import propagate as _ot_propagate
    from opentelemetry import trace as _ot_trace
    from opentelemetry.trace import SpanKind as _SpanKind
    from opentelemetry.trace import Status as _Status
    from opentelemetry.trace import StatusCode as _StatusCode
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter as _OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as _OTLPSpanExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider as _MeterProvider
    from opentelemetry.sdk.metrics.export import (
        PeriodicExportingMetricReader as _PeriodicReader,
    )
    from opentelemetry.sdk.resources import Resource as _Resource
    from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor as _BatchSpanProcessor
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator as _TraceContextPropagator,
    )

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional extra
    _OTEL_AVAILABLE = False

_configured = False
_prefix = "tp"
_lock = threading.RLock()
_meter: Any = None
_tracer: Any = None
_instruments: dict[str, Any] = {}
_snapshot_values: dict[str, float] = {}
_export_lock = threading.Lock()
_providers: list[Any] = []


def available() -> bool:
    """Whether the OpenTelemetry SDK / OTLP exporter are installed."""

    return _OTEL_AVAILABLE


def configured() -> bool:
    """Whether :func:`configure_telemetry` has succeeded."""

    return _configured


def configure_telemetry(
    *,
    service_name: str = "tp",
    service_version: str = "0.1.0",
    replica_mode: str = "embedded",
    process_kind: str = "gateway",
    prefix: str = "tp",
    metrics_endpoint: str | None = None,
    traces_endpoint: str | None = None,
) -> bool:
    """Install OTLP providers. Idempotent; returns whether export is active."""

    global _configured, _prefix, _meter, _tracer
    if not _OTEL_AVAILABLE:
        return False
    with _lock:
        if _configured:
            return True
        try:
            resource = _Resource.create(
                {
                    "service.name": os.getenv("OTEL_SERVICE_NAME", service_name),
                    "service.version": service_version,
                    f"{prefix}.replica_mode": replica_mode,
                    f"{prefix}.process_kind": process_kind,
                    f"{prefix}.pid": str(os.getpid()),
                }
            )
            _ot_propagate.set_global_textmap(_TraceContextPropagator())

            traces_endpoint = (
                traces_endpoint
                or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
                or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
                or "http://localhost:4318/v1/traces"
            )
            trace_provider = _TracerProvider(resource=resource)
            trace_provider.add_span_processor(
                _BatchSpanProcessor(
                    _OTLPSpanExporter(endpoint=traces_endpoint)
                )
            )
            _ot_trace.set_tracer_provider(trace_provider)
            _providers.append(trace_provider)

            metrics_endpoint = (
                metrics_endpoint
                or os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
                or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
                or "http://localhost:4318/v1/metrics"
            )
            metric_provider = _MeterProvider(
                resource=resource,
                metric_readers=[
                    _PeriodicReader(
                        _OTLPMetricExporter(endpoint=metrics_endpoint)
                    )
                ],
            )
            _ot_metrics.set_meter_provider(metric_provider)
            _providers.append(metric_provider)

            _prefix = prefix
            _meter = _ot_metrics.get_meter(f"{prefix}.metrics")
            _tracer = _ot_trace.get_tracer(f"{prefix}.trace")
            _register_sli_instruments()
            atexit.register(shutdown)
            _configured = True
            _LOGGER.info(
                "otel_configured",
                extra={
                    "metrics_endpoint": metrics_endpoint,
                    "traces_endpoint": traces_endpoint,
                },
            )
            return True
        except Exception as exc:  # pragma: no cover - config dependent
            _LOGGER.warning("otel_configuration_failed", extra={"error": str(exc)})
            return False


def _register_sli_instruments() -> None:
    if _meter is None:
        return
    prefix = _prefix
    _instruments["ttft"] = _meter.create_histogram(
        f"{prefix}.ttft", unit="ms", description="Time to first token"
    )
    _instruments["tpot"] = _meter.create_histogram(
        f"{prefix}.tpot", unit="ms", description="Time per output token"
    )
    _instruments["e2e"] = _meter.create_histogram(
        f"{prefix}.e2e", unit="ms", description="End-to-end request latency"
    )
    _instruments["http_duration"] = _meter.create_histogram(
        f"{prefix}.http.request.duration",
        unit="ms",
        description="HTTP request duration",
    )
    _instruments["requests"] = _meter.create_counter(
        f"{prefix}.requests", description="Completed requests"
    )
    _instruments["http_responses"] = _meter.create_counter(
        f"{prefix}.http.responses", description="HTTP responses"
    )


def shutdown() -> None:
    """Flush and shut down providers (registered with atexit)."""

    global _configured
    with _lock:
        if not _configured:
            return
        for provider in _providers:
            try:
                provider.shutdown()
            except Exception:  # pragma: no cover
                pass
        _configured = False


def start_server_span(
    name: str,
    headers: Any,
    **attributes: object,
) -> tuple[Any, Any]:
    """Start a SERVER span with W3C context extraction.

    Returns ``(span, token)``; pass both to :func:`end_server_span`. When OTel
    is unavailable both are ``None`` and end_server_span is a no-op.
    """

    if not _OTEL_AVAILABLE or not _configured:
        return None, None
    try:
        carrier = extract_traceparent(headers)
        context = (
            _ot_propagate.extract(carrier) if carrier else None
        )
        span = _ot_trace.get_tracer(f"{_prefix}.http").start_span(
            name,
            context=context,
            kind=_SpanKind.SERVER,
        )
        for key, value in attributes.items():
            span.set_attribute(key, value)
        token = _ot_trace.use_span(span, end_on_exit=True)
        token.__enter__()
        return span, token
    except Exception:  # pragma: no cover
        return None, None


def end_server_span(
    span: Any,
    token: Any,
    *,
    status: int | None = None,
    error: BaseException | None = None,
) -> None:
    """Finish a span started by :func:`start_server_span`."""

    if span is None:
        return
    try:
        if status is not None:
            span.set_attribute("http.response.status_code", status)
            if status >= 500:
                span.set_status(_Status(_StatusCode.ERROR))
        if error is not None:
            span.record_exception(error)
            span.set_status(_Status(_StatusCode.ERROR, str(error)))
    finally:
        if token is not None:
            token.__exit__(None, None, None)


def start_child_span(name: str, **attributes: object) -> Any:
    """Context manager for a child span (no-op when OTel unavailable)."""

    if not _OTEL_AVAILABLE or not _configured:
        from contextlib import nullcontext

        return nullcontext()
    tracer = _ot_trace.get_tracer(_prefix)
    span = tracer.start_as_current_span(name)
    # Attributes are set inside via a small wrapper.
    return _SpanAttributesContext(span, attributes)


class _SpanAttributesContext:
    def __init__(self, context_manager: Any, attributes: dict[str, object]):
        self._context = context_manager
        self._attributes = attributes
        self._span: Any = None

    def __enter__(self) -> Any:
        self._span = self._context.__enter__()
        for key, value in self._attributes.items():
            if value is not None:
                self._span.set_attribute(key, value)
        return self._span

    def __exit__(self, *exc_info: object) -> None:
        self._context.__exit__(*exc_info)


def get_tracer(name: str | None = None) -> Any:
    """Return a real tracer when configured, otherwise the API no-op tracer."""

    if _OTEL_AVAILABLE:
        return _ot_trace.get_tracer(name or _prefix)
    return None


def get_current_trace_fields() -> dict[str, str]:
    """Fields to merge into JSON logs for trace correlation."""

    if not _OTEL_AVAILABLE:
        return {}
    span = _ot_trace.get_current_span()
    context = span.get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }


def extract_traceparent(headers: Any) -> dict[str, str]:
    """Extract W3C trace context from an HTTP header mapping.

    Pure header parsing: it works even when the optional OTel SDK/exporter are
    not installed (the API alone is enough to consume the resulting carrier).
    """

    carrier: dict[str, str] = {}
    try:
        items = headers.items() if hasattr(headers, "items") else headers
        for key, value in items:
            if isinstance(key, bytes):
                key = key.decode("latin-1")
            lowered = key.lower()
            if lowered in {"traceparent", "tracestate", "baggage"}:
                carrier[lowered] = (
                    value.decode("latin-1")
                    if isinstance(value, bytes)
                    else str(value)
                )
    except Exception:  # pragma: no cover
        return {}
    return carrier


def record_request_latency(
    *,
    ttft_ms: float | None = None,
    tpot_ms: float | None = None,
    e2e_ms: float | None = None,
    **attributes: object,
) -> None:
    """Record request SLI histograms (no-op unless configured)."""

    if not _configured:
        return
    with _export_lock:
        attrs = {key: value for key, value in attributes.items() if value is not None}
        if ttft_ms is not None:
            _instruments["ttft"].record(ttft_ms, attrs)
        if tpot_ms is not None:
            _instruments["tpot"].record(tpot_ms, attrs)
        if e2e_ms is not None:
            _instruments["e2e"].record(e2e_ms, attrs)
        _instruments["requests"].add(1, attrs)


def record_http_response(status: int, route: str, duration_ms: float) -> None:
    """Record an HTTP response (no-op unless configured)."""

    if not _configured:
        return
    with _export_lock:
        attrs = {
            "http.response.status_code": status,
            "http.route": route,
        }
        _instruments["http_responses"].add(1, attrs)
        _instruments["http_duration"].record(duration_ms, attrs)


def _flatten_numbers(value: Any, path: list[str], out: dict[str, float]) -> None:
    if isinstance(value, dict):
        if "__value__" in value:
            sample = value["__value__"]
            if isinstance(sample, (int, float)):
                out[".".join(path)] = float(sample)
            return
        for key, child in value.items():
            if isinstance(key, str) and key.startswith("__"):
                continue
            path.append(str(key))
            _flatten_numbers(child, path, out)
            path.pop()
    elif isinstance(value, bool):
        out[".".join(path)] = 1.0 if value else 0.0
    elif isinstance(value, (int, float)):
        out[".".join(path)] = float(value)


def mirror_snapshot(snapshot: dict[str, Any], *, prefix: str | None = None) -> None:
    """Mirror Prometheus snapshot numbers into OTLP observable gauges.

    New metric names register a lazy observable gauge once; subsequent
    snapshots only refresh the cached value. This keeps OTLP consistent with
    the scrape endpoint without re-declaring instruments on every request.
    """

    if not _configured or _meter is None:
        return
    prefix = prefix or _prefix
    fresh: dict[str, float] = {}
    _flatten_numbers(snapshot, [], fresh)
    with _export_lock:
        _snapshot_values.clear()
        _snapshot_values.update(fresh)
        for name in fresh:
            if name in _instruments:
                continue

            def observe(
                options: Any, *, metric: str = name
            ) -> list[Any]:
                value = _snapshot_values.get(metric)
                if value is None:
                    return []
                return [_Observation(value)]

            try:
                gauge = _meter.create_observable_gauge(
                    f"{prefix}.{name.replace('.', '_')}",
                    callbacks=[observe],
                    description=f"Mirrored from /metrics: {name}",
                )
                _instruments[name] = gauge
            except Exception:  # pragma: no cover - duplicate registration
                pass


__all__ = [
    "available",
    "configured",
    "configure_telemetry",
    "shutdown",
    "get_tracer",
    "start_server_span",
    "end_server_span",
    "start_child_span",
    "get_current_trace_fields",
    "extract_traceparent",
    "record_request_latency",
    "record_http_response",
    "mirror_snapshot",
]
