"""Thread-safe, zero-dependency metric registry for TensorPlay services.

The registry stores counters, gauges and latency histograms in a shape that
both the Python and Rust Prometheus renderers understand, so no client-library
dependency (or second metrics stack) is required in the data plane.

Cardinality is bounded per metric: once ``max_series_per_metric`` distinct
label sets are seen, further label sets are folded into an overflow counter
instead of growing memory without limit — the usual failure mode of ad-hoc
label usage in long-running RL jobs (request ids, prompt ids, etc.).
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .jsonlog import LATENCY_BUCKETS_MS
from .prometheus import render_prometheus_metrics

_LabelSet = tuple[tuple[str, str], ...]


@dataclass
class _Histogram:
    buckets: dict[int, int] = field(default_factory=dict)
    total_sum: float = 0.0
    total_count: int = 0

    def observe(self, value: float, boundaries: tuple[int, ...]) -> None:
        for boundary in boundaries:
            if value <= boundary:
                self.buckets[boundary] = self.buckets.get(boundary, 0) + 1
                break
        else:
            last = boundaries[-1]
            self.buckets[last] = self.buckets.get(last, 0) + 1
        self.total_sum += value
        self.total_count += 1

    def as_dict(self) -> dict[str, int | float]:
        result: dict[str, int | float] = dict(self.buckets)
        result["__sum__"] = self.total_sum
        result["__count__"] = self.total_count
        return result


class MetricRegistry:
    """In-process metrics store; safe to call from controller and worker threads."""

    def __init__(self, *, max_series_per_metric: int = 512) -> None:
        self._lock = threading.RLock()
        self._max_series = max_series_per_metric
        self._scalars: dict[str, dict[_LabelSet, float]] = {}
        self._kinds: dict[str, str] = {}
        self._histograms: dict[str, _Histogram] = {}
        self._dropped_series = 0

    @staticmethod
    def _labels(labels: dict[str, Any] | None) -> _LabelSet:
        return tuple(
            sorted((str(key), str(value)) for key, value in (labels or {}).items())
        )

    def counter(self, name: str, value: float = 1, **labels: Any) -> None:
        self._add(name, float(value), "counter", labels)

    def gauge(self, name: str, value: float, **labels: Any) -> None:
        self._add(name, float(value), "gauge", labels)

    def _add(self, name: str, value: float, kind: str, labels: dict[str, Any]) -> None:
        key = str(name)
        label_set = self._labels(labels)
        with self._lock:
            self._kinds.setdefault(key, kind)
            series = self._scalars.get(key)
            if series is None:
                series = {}
                self._scalars[key] = series
            if kind == "counter":
                current = series.get(label_set)
                if current is not None:
                    series[label_set] = current + value
                elif len(series) < self._max_series:
                    series[label_set] = value
                else:
                    self._dropped_series += 1
            else:
                if label_set not in series and len(series) >= self._max_series:
                    self._dropped_series += 1
                    return
                series[label_set] = value

    def observe(
        self,
        name: str,
        value: float,
        *,
        buckets: tuple[int, ...] = LATENCY_BUCKETS_MS,
    ) -> None:
        """Record one observation into a histogram.

        ``name`` should end in ``_histogram`` (the shared renderers recognise
        that suffix and emit real ``_bucket`` / ``_sum`` / ``_count`` series).
        """

        key = str(name) if str(name).endswith("_histogram") else f"{name}_histogram"
        with self._lock:
            self._histograms.setdefault(key, _Histogram()).observe(value, buckets)

    def snapshot(self) -> dict[str, Any]:
        """Return a nested snapshot consumable by either Prometheus renderer."""

        with self._lock:
            result: dict[str, Any] = {}
            for name, series in self._scalars.items():
                if not series:
                    continue
                unlabeled = series.get(())
                if len(series) == 1 and unlabeled is not None:
                    _assign(result, name, unlabeled)
                    continue
                samples = []
                for label_set, value in sorted(series.items()):
                    samples.append(
                        {
                            "__value__": value,
                            "__type__": self._kinds.get(name, "counter"),
                            "__labels__": dict(label_set),
                        }
                    )
                _assign(result, name, samples)

            for name, histogram in self._histograms.items():
                _assign(result, name, histogram.as_dict())

            if self._dropped_series:
                result["registry_dropped_label_sets"] = self._dropped_series
            return result

    def render(self, *, prefix: str = "tp") -> str:
        return render_prometheus_metrics(self.snapshot(), prefix=prefix)

    def clear(self) -> None:
        with self._lock:
            self._scalars.clear()
            self._kinds.clear()
            self._histograms.clear()
            self._dropped_series = 0

    def merge(self, other: MetricRegistry) -> None:
        """Fold another registry's snapshot into this one (driver aggregation).

        Snapshots are merged recursively, so nested/dotted names, labeled
        sample lists and histograms survive the fold instead of being
        silently dropped. Histogram buckets accumulate; exact ``__sum__`` /
        ``__count__`` totals are used when present, otherwise they are
        approximated from the bucket upper bounds exactly like the renderer.
        """

        with other._lock:
            snapshot = other.snapshot()
            kinds = dict(other._kinds)
        with self._lock:
            self._merge_value(snapshot, (), kinds)

    def _merge_value(
        self,
        value: Any,
        path: tuple[str, ...] = (),
        kinds: Mapping[str, str] | None = None,
    ) -> None:
        if isinstance(value, Mapping):
            if "__value__" in value:
                sample = value.get("__value__")
                if isinstance(sample, bool) or not isinstance(sample, (int, float)):
                    return
                labels = value.get("__labels__")
                rendered = dict(labels) if isinstance(labels, Mapping) else {}
                name = ".".join(path)
                if value.get("__type__") == "gauge":
                    self.gauge(name, sample, **rendered)
                else:
                    self.counter(name, sample, **rendered)
                return
            if path and path[-1].endswith("_histogram"):
                self._merge_histogram(".".join(path), value)
                return
            for key, child in value.items():
                self._merge_value(child, path + (str(key),), kinds)
            return
        if isinstance(value, list):
            for item in value:
                self._merge_value(item, path, kinds)
            return
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, (int, float)):
            name = ".".join(path)
            if kinds is not None and kinds.get(name) == "gauge":
                self.gauge(name, value)
            else:
                self.counter(name, value)

    def _merge_histogram(self, name: str, entries: Mapping[str, Any]) -> None:
        target = self._histograms.get(name)
        if target is None:
            target = _Histogram()
            self._histograms[name] = target
        bucket_count = 0
        approximation = 0.0
        for raw_key, raw_value in entries.items():
            key = str(raw_key)
            if key.startswith("__"):
                continue
            try:
                boundary = int(float(key))
                count = int(raw_value)
            except (TypeError, ValueError):
                continue
            target.buckets[boundary] = target.buckets.get(boundary, 0) + count
            bucket_count += count
            approximation += float(key) * count
        exact_sum = entries.get("__sum__")
        exact_count = entries.get("__count__")
        target.total_sum += (
            float(exact_sum) if isinstance(exact_sum, (int, float)) else approximation
        )
        target.total_count += (
            int(exact_count) if isinstance(exact_count, (int, float)) else bucket_count
        )


def _assign(root: dict[str, Any], dotted_name: str, value: Any) -> None:
    parts = [part for part in dotted_name.split(".") if part]
    if not parts:
        return
    node = root
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            return
        node = child
    node[parts[-1]] = value


_default = MetricRegistry()


def get_registry() -> MetricRegistry:
    """Process-wide default registry."""

    return _default


__all__ = ["MetricRegistry", "get_registry"]
