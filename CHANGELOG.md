# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- PEP 561 `py.typed` marker and `Typing :: Typed` metadata.
- `test`, `dev` and `all` install extras; typed package metadata (classifiers,
  keywords, issue/changelog URLs).
- Full CI: Python lint, Python tests on 3.12/3.13, Rust `fmt`/`clippy`/tests,
  and the Python/Rust byte-parity gate; tag-driven sdist/wheel build with PyPI
  trusted publishing and GitHub Releases.
- Repository policies: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`,
  `CHANGELOG.md`, issue/PR templates, CODEOWNERS, Dependabot and pre-commit.

### Changed

- Prometheus renderers are faster without changing output: the Python renderer
  caches sanitised path components, and the Rust renderer writes through
  `fmt::Write` with one-allocation metric names and in-place label escaping.
  On the same representative snapshot (~2,200 series: 2,000 scalars, 200
  labelled samples, one histogram), Python render latency dropped from 450 µs
  to 251 µs (1.8×) and Rust from 160 µs to 128 µs (1.25×);
  `scripts/check_parity.sh` still proves both outputs are byte-identical.
- `MetricRegistry` counter/gauge updates take the fast path for existing label
  sets instead of re-initialising the series on every call.
- `MetricRegistry.merge` now folds nested/dotted names, labeled sample lists
  and histograms recursively (with exact or bucket-approximated totals) and
  preserves gauge types, instead of silently dropping non-scalar values.

## [0.1.0] - 2026-11-25

### Added

- Initial extraction of the shared observability layer used by megaLM and
  megaRL into a standalone package.
- Prometheus text exposition 0.0.4 renderer in pure Python
  (`render_prometheus_metrics`) and a dependency-free Rust crate
  (`tensorplay_observability::render_prometheus_metrics`) that produce
  byte-identical output for the same snapshot, verified by
  `scripts/check_parity.sh`.
- Native-label descriptors (`__value__` / `__type__` / `__labels__`), real
  histograms (`_bucket` / `_sum` / `_count`) and exact totals via
  `__sum__` / `__count__`.
- Thread-safe `MetricRegistry` with bounded per-metric cardinality and an
  overflow counter (`registry_dropped_label_sets`).
- Single-line JSON logging (`JsonLogFormatter`, `configure_logging`,
  `log_event`), latency bucketing helpers, and optional W3C trace/span
  correlation.
- Optional OTLP metrics + traces through the official OpenTelemetry SDK
  (`pip install tensorplay-observability[otel]`); every signal is a no-op
  until `configure_telemetry` is called.
- Multi-backend training tracker (`Tracking`) with reducer tags (`@mean`,
  `@sum`, `@p99`, ...) and fan-out to TensorBoard, W&B, JSONL, stdout and
  Prometheus.
- Batched, fail-soft Better Stack log sink (stdlib only).
- `ObsConfig` environment-driven configuration (`TP_OBS_*`, `OTEL_*`).
- Reference Grafana dashboard and repository infrastructure: CI for lint,
  tests, Rust and renderer parity; issue/PR templates; Dependabot;
  pre-commit; contribution, security and changelog policies.

[Unreleased]: https://github.com/ohtensorplay/tensorplay-observability/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ohtensorplay/tensorplay-observability/releases/tag/v0.1.0