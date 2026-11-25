"""Renderer edge cases and small helpers of the shared snapshot contract."""

from __future__ import annotations

from tp_observability import (
    LATENCY_BUCKETS_MS,
    MetricRegistry,
    histogram_totals,
    labeled_sample,
    observe_latency,
    render_prometheus_metrics,
)


def test_empty_snapshot_renders_nothing():
    assert render_prometheus_metrics({}, prefix="tp") == ""


def test_prefix_and_keys_are_sanitized():
    text = render_prometheus_metrics({"1bad-key": 2}, prefix="Mega RL")
    assert "mega_rl_m1bad_key 2\n" in text


def test_booleans_become_counters_and_strings_are_ignored():
    text = render_prometheus_metrics({"ready": False, "note": "skip"}, prefix="tp")
    assert "tp_ready 0\n" in text
    assert "note" not in text


def test_float_histogram_boundaries():
    text = render_prometheus_metrics(
        {"latency_ms_histogram": {"0.5": 2, "2.5": 1}}, prefix="tp"
    )
    assert 'tp_latency_ms_histogram_bucket{le="0.5"} 2' in text
    assert 'tp_latency_ms_histogram_bucket{le="2.5"} 3' in text
    assert 'tp_latency_ms_histogram_bucket{le="+Inf"} 3' in text


def test_observe_latency_buckets_and_folds_the_tail():
    histogram: dict[int, int] = {}
    observe_latency(histogram, 0.4)
    observe_latency(histogram, 1000)
    observe_latency(histogram, 10**9)
    assert histogram[1] == 1
    assert histogram[1000] == 1
    assert histogram[LATENCY_BUCKETS_MS[-1]] == 1


def test_labeled_sample_and_histogram_totals():
    sample = labeled_sample(2, route="/v1/chat", status=503)
    assert sample == {
        "__value__": 2,
        "__type__": "counter",
        "__labels__": {"route": "/v1/chat", "status": 503},
    }
    totals = histogram_totals({5: 1}, total_sum=7.5, total_count=1)
    assert totals["__sum__"] == 7.5
    assert totals["__count__"] == 1


def test_registry_snapshot_renders_named_label_sets():
    registry = MetricRegistry()
    registry.counter("tool_calls", route="a")
    registry.counter("tool_calls", route="a")
    registry.counter("tool_calls", route="b")
    text = registry.render(prefix="tp")
    assert text.count("# TYPE tp_tool_calls counter") == 1
    assert 'tp_tool_calls{route="a"} 2' in text
    assert 'tp_tool_calls{route="b"} 1' in text


def test_registry_snapshot_keeps_unlabeled_series_scalar():
    registry = MetricRegistry()
    registry.gauge("in_flight", 3)
    assert registry.snapshot() == {"in_flight": 3}


def test_registry_observe_appends_histogram_suffix():
    registry = MetricRegistry()
    registry.observe("tool_duration_ms", 3)
    assert "tool_duration_ms_histogram" in registry.snapshot()


def test_registry_clear_resets_everything():
    registry = MetricRegistry()
    registry.counter("calls")
    registry.observe("latency_ms_histogram", 3)
    registry.clear()
    assert registry.snapshot() == {}


def test_registry_merge_preserves_nested_names_labels_types_and_histograms():
    source = MetricRegistry()
    source.counter("engine.requests", 2, route="/v1/chat")
    source.gauge("engine.queue.in_flight", 3)
    source.observe("engine.latency_ms_histogram", 5, buckets=(1, 5, 10))
    source.observe("engine.latency_ms_histogram", 7, buckets=(1, 5, 10))

    target = MetricRegistry()
    target.merge(source)

    snapshot = target.snapshot()
    assert snapshot["engine"]["queue"] == {"in_flight": 3}
    assert snapshot["engine"]["requests"][0]["__value__"] == 2
    assert snapshot["engine"]["requests"][0]["__labels__"] == {"route": "/v1/chat"}

    histogram = snapshot["engine"]["latency_ms_histogram"]
    assert histogram[5] == 1
    assert histogram[10] == 1
    assert histogram["__count__"] == 2
    assert histogram["__sum__"] == 12.0

    text = target.render(prefix="tp")
    assert "# TYPE tp_engine_queue_in_flight gauge" in text
    assert 'tp_engine_requests{route="/v1/chat"} 2' in text
    assert "tp_engine_latency_ms_histogram_count 2" in text


def test_registry_merge_accumulates_across_merges():
    target = MetricRegistry()
    for value in (1, 2):
        source = MetricRegistry()
        source.counter("generated")
        source.observe("latency_ms_histogram", value, buckets=(1, 5))
        target.merge(source)
    assert target.snapshot()["generated"] == 2
    assert target.snapshot()["latency_ms_histogram"]["__count__"] == 2