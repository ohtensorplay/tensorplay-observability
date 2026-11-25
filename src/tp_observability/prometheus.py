"""Prometheus text exposition 0.0.4 for a nested snapshot.

Pure-Python counterpart of ``tensorplay_observability::metrics`` (Rust).
Both renderers take an explicit ``prefix`` and produce byte-identical output
for the same snapshot, so a service can render metrics in-process (Python) or
in its Rust gateway without changing dashboards or queries.

Snapshot contract (shared with megaLM / megaRL):

* leaf numbers and booleans become scalar metrics;
* a path component ending in ``_histogram`` renders real Prometheus buckets
  with ``_sum`` / ``_count`` (exact totals via ``__sum__`` / ``__count__``);
* a labeled leaf ``{"__value__":3,"__type__":"counter","__labels__":{...}}``
  renders native labels; a **list** of these under one key renders several
  samples of the same metric, each with its own label set.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

_COUNTER_OVERRIDES = ("evicted_bytes", "parser_bytes", "sent_bytes")
_GAUGE_MARKERS = (
    "_mean", "_rate", "hit_rate", "max_", "free_", "pending_", "active_",
    "in_flight", "inflight", "waiting_", "running_", "queued_", "allocated_",
    "total_pages", "cached_pages", "pinned_", "evictable_", "page_refs",
    "entries", "nodes", "_occupancy", "_utilization", "util", "capacity",
    "_bytes", "_size", "reclaimable", "used_",
)


@lru_cache(maxsize=4096)
def metric_component(component: str) -> str:
    """Sanitize one path component into a valid Prometheus identifier.

    Cached: snapshot keys repeat across scrapes (``util``, ``in_flight``,
    ...), so sanitising each distinct component once removes the dominant
    per-scrape allocation cost for large snapshots.
    """

    out = "".join(
        ch.lower()
        if ch.isascii() and (ch.isalnum() or ch == "_")
        else "_"
        for ch in str(component)
    )
    if not out:
        out = "value"
    if out[0].isdigit():
        out = "m" + out
    return out


def _is_gauge(path: Sequence[str]) -> bool:
    name = "_".join(path)
    if any(marker in name for marker in _COUNTER_OVERRIDES):
        return False
    return any(marker in name for marker in _GAUGE_MARKERS)


def _scalar_type(path: Sequence[str], declared: str | None) -> str:
    if declared == "gauge":
        return "gauge"
    if declared is not None:
        return "counter"
    return "gauge" if _is_gauge(path) else "counter"


def _declare(name: str, kind: str, out: list[str], declared: set[str]) -> None:
    if name in declared:
        return
    declared.add(name)
    out.append(f"# HELP {name} {name.replace('_', ' ')}.")
    out.append(f"# TYPE {name} {kind}")


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_le(value: float) -> str:
    if float(value).is_integer() and abs(value) != float("inf"):
        return str(int(value))
    return str(value)


def _write_histogram(
    name: str, entries: Mapping[str, Any], out: list[str], declared: set[str]
) -> None:
    _declare(name, "histogram", out, declared)
    buckets: list[tuple[float, int]] = []
    for raw_key, value in entries.items():
        key = str(raw_key)
        if key.startswith("__"):
            continue
        try:
            le = float(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        buckets.append((le, int(value)))
    buckets.sort(key=lambda pair: pair[0])

    cumulative = 0
    approximate_sum = 0.0
    for le, count in buckets:
        cumulative += count
        approximate_sum += le * count
        out.append(f'{name}_bucket{{le="{_format_le(le)}"}} {cumulative}')
    out.append(f'{name}_bucket{{le="+Inf"}} {cumulative}')

    exact_sum = entries.get("__sum__")
    exact_count = entries.get("__count__")
    total = float(exact_sum) if isinstance(exact_sum, (int, float)) else approximate_sum
    count = int(exact_count) if isinstance(exact_count, (int, float)) else cumulative
    out.append(f"{name}_sum {total}")
    out.append(f"{name}_count {count}")


def _write_labeled(
    prefix: str,
    path: Sequence[str],
    sample: Any,
    labels: Mapping[str, Any],
    kind: str,
    out: list[str],
    declared: set[str],
) -> None:
    name = f"{prefix}_{'_'.join(path)}"
    _declare(name, kind, out, declared)
    rendered = ",".join(
        f'{metric_component(key)}="{_escape_label(str(value))}"'
        for key, value in sorted(labels.items(), key=lambda item: str(item[0]))
    )
    out.append(f"{name}{{{rendered}}} {sample}")


def _flatten(
    prefix: str,
    value: Any,
    path: list[str],
    out: list[str],
    declared: set[str],
) -> None:
    if isinstance(value, Mapping):
        if "__value__" in value and isinstance(value.get("__labels__"), Mapping):
            kind = _scalar_type(path, value.get("__type__"))
            _write_labeled(
                prefix, path, value["__value__"], value["__labels__"],
                kind, out, declared,
            )
            return
        if path and str(path[-1]).endswith("_histogram"):
            _write_histogram(f"{prefix}_{'_'.join(path)}", value, out, declared)
            return
        # Rust's serde_json Map iterates keys in sorted order; mirror that so
        # the Python and gateway renderers produce byte-identical output.
        for key, child in sorted(value.items(), key=lambda item: str(item[0])):
            path.append(metric_component(key))
            _flatten(prefix, child, path, out, declared)
            path.pop()
        return

    if isinstance(value, list):
        all_labeled = bool(value) and all(
            isinstance(item, Mapping)
            and "__value__" in item
            and isinstance(item.get("__labels__"), Mapping)
            for item in value
        )
        if all_labeled:
            for item in value:
                kind = _scalar_type(path, item.get("__type__"))
                _write_labeled(
                    prefix, path, item["__value__"], item["__labels__"],
                    kind, out, declared,
                )
            return
        for index, child in enumerate(value):
            path.append(f"item_{index}")
            _flatten(prefix, child, path, out, declared)
            path.pop()
        return

    if isinstance(value, bool):
        kind = _scalar_type(path, None)
        name = f"{prefix}_{'_'.join(path)}"
        _declare(name, kind, out, declared)
        out.append(f"{name} {1 if value else 0}")
        return

    if isinstance(value, (int, float)):
        kind = _scalar_type(path, None)
        name = f"{prefix}_{'_'.join(path)}"
        _declare(name, kind, out, declared)
        out.append(f"{name} {value}")


def render_prometheus_metrics(
    snapshot: Mapping[str, Any] | str, *, prefix: str = "tp"
) -> str:
    """Render a nested snapshot (or a JSON string) as Prometheus 0.0.4 text."""

    payload = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
    out: list[str] = []
    declared: set[str] = set()
    _flatten(metric_component(prefix), payload, [], out, declared)
    return "\n".join(out) + ("\n" if out else "")


__all__ = ["metric_component", "render_prometheus_metrics"]
