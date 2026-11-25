<div align="center">

# tensorplay-observability

**Shared observability primitives for the [TensorPlay](https://github.com/lexing-2026/TensorPlay) ecosystem.**

<p>
  <a href="#about">About</a> ·
  <a href="#-install">Install</a> ·
  <a href="#-python-usage">Python</a> ·
  <a href="#-snapshot-contract">Contract</a> ·
  <a href="#-rust-usage">Rust</a> ·
  <a href="#-backends-and-mainline-panels">Backends</a> ·
  <a href="#-development">Development</a> ·
  <a href="./CONTRIBUTING.md">Contributing</a> ·
  <a href="./CHANGELOG.md">Changelog</a>
</p>

<p>
  <a href="./README.md"><strong>EN</strong></a> ·
  <a href="./README.zh.md"><strong>中文</strong></a>
</p>

<p>
  <img src="https://img.shields.io/badge/python-%E2%89%A53.12-23347A?style=flat-square&logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/rust-stable-23347A?style=flat-square&logo=rust&logoColor=white" alt="Rust stable">
  <a href="https://github.com/ohtensorplay/tensorplay-observability/actions/workflows/lint.yml"><img src="https://github.com/ohtensorplay/tensorplay-observability/actions/workflows/lint.yml/badge.svg" alt="Lint"></a>
  <a href="https://github.com/ohtensorplay/tensorplay-observability/actions/workflows/test.yml"><img src="https://github.com/ohtensorplay/tensorplay-observability/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-23347A?style=flat-square" alt="License: Apache-2.0"></a>
</p>

</div>

---

## About

This package is the single implementation of observability glue for the
TensorPlay ecosystem — currently used by **megaLM** (inference) and **megaRL**
(RL post-training). One wire contract, four signals, no mandatory third-party
dependency in the data plane:

| Signal | API | Backend / format |
| --- | --- | --- |
| Metrics (pull) | `MetricRegistry.render(prefix=...)` / Rust `render_prometheus_metrics` | Prometheus text 0.0.4, native labels, real histograms |
| Metrics + traces (push) | `configure_telemetry(prefix=...)` | OpenTelemetry OTLP/HTTP (optional extra) |
| Logs | `JsonLogFormatter`, `configure_logging`, `log_event` | Single-line JSON to stderr; Loki / ELK / Better Stack |
| Training curves | `Tracking.log(data, step)` | TensorBoard, W&B, JSONL, stdout, Prometheus |

The Rust and Python Prometheus renderers are **byte-identical** for the same
snapshot (verified by `scripts/check_parity.sh` in CI), so a service can render
in-process or in its gateway without changing dashboards or queries.

## 📦 Install

The package is pure Python and has **no required runtime dependencies**. The
OTLP exporter and training trackers are opt-in extras:

```bash
# From the repository (until the first PyPI release)
pip install "tensorplay-observability[otel,tracking] @ git+https://github.com/ohtensorplay/tensorplay-observability.git"

# Editable, for development
git clone https://github.com/ohtensorplay/tensorplay-observability.git
cd tensorplay-observability
python -m pip install -e ".[test]"
```

| Extra | Adds |
| --- | --- |
| `otel` | `opentelemetry-api/sdk` + OTLP/HTTP exporter |
| `tracking` | `tensorboard`, `wandb` |
| `test` | `pytest` |
| `dev` | `build`, `pre-commit`, `pytest`, `ruff` |
| `all` | `otel` + `tracking` |

Importing the package opens no socket, starts no thread and never mutates the
root logger; every backend is a no-op until explicitly configured.

## Why a shared package instead of only off-the-shelf libraries

- Open standards and SDKs are used wherever they fit: **OpenTelemetry SDK** for
  OTLP, **Prometheus 0.0.4** exposition, **TensorBoard / W&B** for training
  curves, **Grafana / Loki / Alertmanager / Better Stack** as backends.
- What is *not* off-the-shelf is the glue: a zero-dependency snapshot format
  shared by a Rust gateway and a Python controller, bounded cardinality,
  trace/log/metric correlation, and fail-soft behaviour when a backend is
  down. That glue is this package — roughly 2,000 lines, extracted from
  megaLM's proven implementation.
- Everything is a no-op until configured, so the same code runs unchanged in
  unit tests, embedded libraries, and production gateways.

## 🐍 Python usage

```python
from tp_observability import (
    MetricRegistry, configure_logging, configure_telemetry, Tracking,
    render_prometheus_metrics,
)

configure_logging("INFO", service="megarl")
configure_telemetry(service_name="megarl", prefix="megarl")  # optional extra

registry = MetricRegistry(max_series_per_metric=512)
registry.counter("rollout.generate", 1, model="qwen3")
registry.gauge("policy_version", 7)
registry.observe("tool_duration_ms_histogram", 23.4)

text = registry.render(prefix="megarl")  # scrape this at GET /metrics

with Tracking("megarl", "grpo-run", backends=("tensorboard", "wandb")) as t:
    t.log({"rollout/reward_mean@mean": 0.71, "train/pg_loss@sum": 0.03}, step=12)
```

## 📐 Snapshot contract

The snapshot is the cross-language contract shared with megaLM / megaRL:

```json
{
  "proxy": {"admitted": 3},
  "gpu": {"util": 0.5},
  "latency_ms_histogram": {"1": 5, "10": 3, "__sum__": 37.5, "__count__": 8},
  "http_requests": [
    {"__value__": 2, "__type__": "counter",
     "__labels__": {"status": "503", "route": "/v1/chat"}}
  ]
}
```

- Leaf numbers / booleans → `counter` by default; names containing gauge
  markers (`_mean`, `_rate`, `in_flight`, `capacity`, `_bytes`, ...) → `gauge`.
- A key ending in `_histogram` renders `_bucket{le=...}`, `_sum`, `_count`.
  Supply `__sum__` / `__count__` for exact totals.
- A descriptor with `__value__` / `__labels__` renders native labels; a list
  of them renders multiple samples of one metric with one HELP/TYPE header.
- Cardinality is bounded per metric: extra label sets increment
  `registry_dropped_label_sets` instead of growing memory.

Any change to this contract must be made in both renderers and covered by the
parity check — see [CONTRIBUTING.md](CONTRIBUTING.md#the-shared-contract).

## 🦀 Rust usage

```toml
[dependencies]
tensorplay-observability = { path = "../../../../tensorplay-observability/rust" }
```

```rust
let text = tensorplay_observability::render_prometheus_metrics(&snapshot_json, "megarl")?;
```

The crate has a single dependency (`serde_json`) and no runtime, so gateways
can render metrics without pulling in the Python package or an async runtime.

## 🔌 Backends and mainline panels

- **Prometheus / Grafana / VictoriaMetrics / Thanos / Mimir / Cortex**: scrape
  `/metrics` (text) or `/metrics.json` (legacy JSON) from the gateway.
- **OTLP-only SaaS** (Better Stack, Grafana Cloud OTLP, Datadog, New Relic):
  either deploy an OTel Collector with a `prometheus` receiver → `otlphttp`
  exporter, or set `TP_OTEL_ENABLED=1` with the vendor's OTLP endpoint.
- **Better Stack logs**: set `BETTER_STACK_SOURCE_TOKEN` and attach
  `configure_betterstack(logger)`; records use the same JSON schema as stderr.
- **Loki / ELK / OpenSearch**: parse stderr JSON lines; `event`, `level`,
  `trace_id`, `span_id` are first-class fields.
- **TensorBoard / W&B / SwanLab-style trackers**: `Tracking` fans out from one
  `log()` call; reducer tags (`@mean`, `@sum`, `@p99`) are stripped before
  export and W&B step axes are declared per namespace.

A reference Grafana dashboard lives in [`grafana/service-overview.json`](grafana/service-overview.json).

## 🛠 Development

Requirements: Python ≥ 3.12 and a stable Rust toolchain. Run every CI gate
locally with one command:

```bash
./scripts/check.sh
```

Or individually:

```bash
ruff check src tests                                   # Python lint gate
python -m pytest tests -q                              # Python unit tests
cargo fmt --manifest-path rust/Cargo.toml --check      # Rust formatting
cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust/Cargo.toml --locked    # Rust unit tests
./scripts/check_parity.sh                              # Python/Rust byte parity
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow, and
[CHANGELOG.md](CHANGELOG.md) for release history.

## 🌐 Ecosystem

| Project | Role | Relationship |
| --- | --- | --- |
| [TensorPlay](https://github.com/lexing-2026/TensorPlay) | Tensor runtime and kernels | Foundation; untouched by this package |
| [MegaLM](https://github.com/ohtensorplay/megalm) | Inference runtime | Consumes logs, metrics, OTLP and `Tracking` |
| [megaRL](https://github.com/ohtensorplay/megarl) | RL post-training | Consumes controller metrics, rollout histograms and `Tracking` |

## License

Apache-2.0 (same as megaLM / megaRL). See [LICENSE](LICENSE) and [NOTICE](NOTICE).