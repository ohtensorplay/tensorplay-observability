"""Shared observability primitives: wire format, registry and tracker."""

from __future__ import annotations

import io
import json
import logging

from tp_observability import (
    JsonLogFormatter,
    MetricRegistry,
    ObsConfig,
    Tracking,
    configure_logging,
    parse_key,
    render_prometheus_metrics,
)
from tp_observability import otel


def test_renderer_matches_rust_contract():
    text = render_prometheus_metrics(
        {
            "proxy": {"admitted_requests": 3},
            "gpu": {"util": 0.5},
            "cancelled": True,
        },
        prefix="megalm",
    )
    assert "# HELP megalm_proxy_admitted_requests megalm proxy admitted requests." in text
    assert "# TYPE megalm_proxy_admitted_requests counter" in text
    assert "megalm_proxy_admitted_requests 3\n" in text
    assert "# TYPE megalm_gpu_util gauge" in text
    assert "megalm_cancelled 1\n" in text


def test_histogram_buckets_sum_and_count():
    text = render_prometheus_metrics(
        {"engine": {"latency_ms_histogram": {"1": 5, "10": 3, "__sum__": 37.5, "__count__": 8}}},
        prefix="megarl",
    )
    assert "# TYPE megarl_engine_latency_ms_histogram histogram" in text
    assert 'megarl_engine_latency_ms_histogram_bucket{le="1"} 5' in text
    assert 'megarl_engine_latency_ms_histogram_bucket{le="10"} 8' in text
    assert 'megarl_engine_latency_ms_histogram_bucket{le="+Inf"} 8' in text
    assert "megarl_engine_latency_ms_histogram_sum 37.5" in text
    assert "megarl_engine_latency_ms_histogram_count 8" in text


def test_labeled_samples_and_escaping():
    text = render_prometheus_metrics(
        {
            "http_requests": [
                {
                    "__value__": 2,
                    "__type__": "counter",
                    "__labels__": {"status": "503", "route": "/v1/chat"},
                },
                {
                    "__value__": 1,
                    "__type__": "counter",
                    "__labels__": {"status": "200", "route": "/v1/chat"},
                },
            ]
        },
        prefix="tp",
    )
    assert text.count("# TYPE tp_http_requests counter") == 1
    assert 'tp_http_requests{route="/v1/chat",status="200"} 1' in text
    assert 'tp_http_requests{route="/v1/chat",status="503"} 2' in text


def test_registry_counters_gauges_histograms():
    registry = MetricRegistry()
    registry.counter("generates", 3)
    registry.counter("errors")
    registry.gauge("in_flight", 4)
    registry.observe("latency_ms_histogram", 7, buckets=(1, 5, 10))
    registry.observe("latency_ms_histogram", 2, buckets=(1, 5, 10))

    snapshot = registry.snapshot()
    assert snapshot["generates"] == 3
    assert snapshot["errors"] == 1
    assert snapshot["in_flight"] == 4
    buckets = snapshot["latency_ms_histogram"]
    assert buckets[5] == 1  # observation 2 lands in the first containing bucket
    assert buckets[10] == 1  # observation 7; renderer cumulates to 2
    assert buckets["__count__"] == 2
    assert buckets["__sum__"] == 9

    text = registry.render(prefix="megarl")
    assert "megarl_generates 3" in text
    assert "megarl_in_flight 4" in text
    assert "megarl_latency_ms_histogram_count 2" in text


def test_registry_bounds_cardinality():
    registry = MetricRegistry(max_series_per_metric=2)
    registry.counter("tool_calls", route="a")
    registry.counter("tool_calls", route="b")
    registry.counter("tool_calls", route="c")
    assert len(registry.snapshot()["tool_calls"]) == 2
    assert registry.snapshot()["registry_dropped_label_sets"] == 1


def test_json_log_formatter_emits_single_line_json():
    record = logging.LogRecord(
        "megarl", logging.INFO, __file__, 1, "staleness", (), None
    )
    setattr(record, "_tp_fields", {"version": 3, "stale_tokens": 7})
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["event"] == "staleness"
    assert payload["level"] == "info"
    assert payload["version"] == 3
    assert payload["stale_tokens"] == 7
    assert "pid" in payload


def test_configure_logging_is_idempotent():
    configure_logging("DEBUG", service="tp-test")
    configure_logging("DEBUG", service="tp-test")
    logger = logging.getLogger("tp-test")
    assert sum(getattr(h, "_tp_json", False) for h in logger.handlers) == 1


def test_tracking_fans_out_and_is_idempotent(tmp_path, capsys):
    registry = MetricRegistry()
    path = tmp_path / "metrics.jsonl"
    tracker = Tracking(
        "megaRL",
        "unit-test",
        backends={"stdout": {}, "jsonl": {"path": path}, "prometheus": {}},
        registry=registry,
    )
    tracker.log({"rollout/reward_mean@mean": 0.75, "train/pg_loss@sum": 0.2}, step=4)
    tracker.finish()
    tracker.finish()

    out = capsys.readouterr().out
    assert '"step": 4' in out
    assert "rollout/reward_mean" in out
    assert "@mean" not in out
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["step"] == 4
    assert registry.snapshot()["rollout"]["reward_mean"] == 0.75
    # Logs after finish are ignored, not duplicated.
    tracker.log({"train/pg_loss": 1.0}, step=5)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_reducer_tag_parsing():
    assert parse_key("actor/pg_loss@sum") == ("actor/pg_loss", "sum")
    assert parse_key("val/score") == ("val/score", None)


def test_otel_noop_when_not_configured():
    assert isinstance(otel.available(), bool)
    otel.mirror_snapshot({"a": 1}, prefix="tp")  # must not raise
    if not otel.configured():
        assert otel.start_server_span("x", {}) == (None, None)


def test_betterstack_is_noop_without_token(monkeypatch):
    from tp_observability import configure_betterstack

    monkeypatch.delenv("BETTER_STACK_SOURCE_TOKEN", raising=False)
    logger = logging.getLogger("tp-bs-test")
    before = list(logger.handlers)
    assert configure_betterstack(logger) is None
    assert logger.handlers == before


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("TP_OBS_TRACKERS", "tensorboard, wandb")
    monkeypatch.setenv("TP_OBS_PREFIX", "megarl")
    monkeypatch.setenv("TP_OTEL_ENABLED", "1")
    config = ObsConfig.from_env(prefix="tp", service="megarl")
    assert config.trackers == ("tensorboard", "wandb")
    assert config.prefix == "megarl"
    assert config.otel_enabled is True
    assert config.log_service == "megarl"
