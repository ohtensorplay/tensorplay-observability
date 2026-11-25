<div align="center">

# tensorplay-observability

**TensorPlay 生态共享的可观测性基础设施。**

<p>
  <a href="#简介">简介</a> ·
  <a href="#-安装">安装</a> ·
  <a href="#-python-用法">Python</a> ·
  <a href="#-快照契约">契约</a> ·
  <a href="#-rust-用法">Rust</a> ·
  <a href="#-后端与主面板">后端</a> ·
  <a href="#-开发">开发</a> ·
  <a href="./CONTRIBUTING.md">贡献指南</a> ·
  <a href="./CHANGELOG.md">更新日志</a>
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

## 简介

本包是 TensorPlay 生态中可观测性胶水层的唯一实现，当前服务于 **megaLM**
（推理）与 **megaRL**（强化学习后训练）。一套跨语言契约、四类信号、数据面
不强制依赖任何第三方库：

| 信号 | API | 后端 / 格式 |
| --- | --- | --- |
| 指标（拉取） | `MetricRegistry.render(prefix=...)` / Rust `render_prometheus_metrics` | Prometheus text 0.0.4，原生标签，真实直方图 |
| 指标 + 链路（推送） | `configure_telemetry(prefix=...)` | OpenTelemetry OTLP/HTTP（可选 extra） |
| 日志 | `JsonLogFormatter`、`configure_logging`、`log_event` | 单行 JSON 输出到 stderr；Loki / ELK / Better Stack |
| 训练曲线 | `Tracking.log(data, step)` | TensorBoard、W&B、JSONL、stdout、Prometheus |

Rust 与 Python 的 Prometheus 渲染器对同一快照的输出**逐字节一致**（CI 中由
`scripts/check_parity.sh` 验证），因此服务可以自行渲染，也可以交给网关渲染，
仪表盘与查询语句无需任何改动。

## 📦 安装

本包是纯 Python 实现，**没有必需的运行时依赖**。OTLP 导出器与训练跟踪器都是
可选 extra：

```bash
# 从仓库安装（首个 PyPI 版本发布前）
pip install "tensorplay-observability[otel,tracking] @ git+https://github.com/ohtensorplay/tensorplay-observability.git"

# 可编辑安装，用于开发
git clone https://github.com/ohtensorplay/tensorplay-observability.git
cd tensorplay-observability
python -m pip install -e ".[test]"
```

| Extra | 新增依赖 |
| --- | --- |
| `otel` | `opentelemetry-api/sdk` + OTLP/HTTP 导出器 |
| `tracking` | `tensorboard`、`wandb` |
| `test` | `pytest` |
| `dev` | `build`、`pre-commit`、`pytest`、`ruff` |
| `all` | `otel` + `tracking` |

导入本包不会打开 socket、不会启动线程，也不会修改 root logger；所有后端在
显式配置前都是 no-op。

## 为什么需要共享包，而不是只用现成库

- 凡是适合用标准的场景都用标准与官方 SDK：**OpenTelemetry SDK** 负责 OTLP，
  **Prometheus 0.0.4** 负责暴露格式，**TensorBoard / W&B** 负责训练曲线，
  **Grafana / Loki / Alertmanager / Better Stack** 作为后端。
- 真正不现成的是胶水层：Rust 网关与 Python 控制器共享的零依赖快照格式、
  有界基数、trace/log/metric 关联，以及后端不可用时的 fail-soft 行为。这部分
  就是本包 —— 约 2,000 行，从 megaLM 经过验证的实现中抽取而来。
- 一切在配置前都是 no-op，因此同一份代码可以原样运行在单元测试、嵌入式库和
  生产网关中。

## 🐍 Python 用法

```python
from tp_observability import (
    MetricRegistry, configure_logging, configure_telemetry, Tracking,
    render_prometheus_metrics,
)

configure_logging("INFO", service="megarl")
configure_telemetry(service_name="megarl", prefix="megarl")  # 可选 extra

registry = MetricRegistry(max_series_per_metric=512)
registry.counter("rollout.generate", 1, model="qwen3")
registry.gauge("policy_version", 7)
registry.observe("tool_duration_ms_histogram", 23.4)

text = registry.render(prefix="megarl")  # 在 GET /metrics 中返回

with Tracking("megarl", "grpo-run", backends=("tensorboard", "wandb")) as t:
    t.log({"rollout/reward_mean@mean": 0.71, "train/pg_loss@sum": 0.03}, step=12)
```

## 📐 快照契约

快照是与 megaLM / megaRL 共享的跨语言契约：

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

- 叶子数字 / 布尔值默认渲染为 `counter`；名称中包含 gauge 标记
  （`_mean`、`_rate`、`in_flight`、`capacity`、`_bytes` 等）时渲染为 `gauge`。
- 以 `_histogram` 结尾的 key 渲染为 `_bucket{le=...}`、`_sum`、`_count`。
  通过 `__sum__` / `__count__` 提供精确总量。
- 带 `__value__` / `__labels__` 的描述符渲染为原生标签；描述符列表表示同一
  指标的多条样本，只输出一次 HELP/TYPE。
- 每个指标的基数有上限：超出的标签集合会递增
  `registry_dropped_label_sets`，而不是无限增长内存。

对该契约的任何修改都必须同时落到两个渲染器，并由 parity 检查覆盖 —— 参见
[CONTRIBUTING.md](CONTRIBUTING.md#the-shared-contract)。

## 🦀 Rust 用法

```toml
[dependencies]
tensorplay-observability = { path = "../../../../tensorplay-observability/rust" }
```

```rust
let text = tensorplay_observability::render_prometheus_metrics(&snapshot_json, "megarl")?;
```

该 crate 只有一个依赖（`serde_json`）且无需运行时，网关因此可以在不引入
Python 包或异步运行时的情况下渲染指标。

## 🔌 后端与主面板

- **Prometheus / Grafana / VictoriaMetrics / Thanos / Mimir / Cortex**：抓取
  网关的 `/metrics`（文本）或 `/metrics.json`（旧版 JSON）。
- **仅支持 OTLP 的 SaaS**（Better Stack、Grafana Cloud OTLP、Datadog、
  New Relic）：部署带 `prometheus` receiver → `otlphttp` exporter 的 OTel
  Collector，或设置 `TP_OTEL_ENABLED=1` 并使用厂商的 OTLP endpoint。
- **Better Stack 日志**：设置 `BETTER_STACK_SOURCE_TOKEN` 后挂载
  `configure_betterstack(logger)`；日志记录与 stderr 使用同一 JSON schema。
- **Loki / ELK / OpenSearch**：直接解析 stderr 的 JSON 行；`event`、`level`、
  `trace_id`、`span_id` 都是一等字段。
- **TensorBoard / W&B / SwanLab 类跟踪器**：`Tracking` 从一次 `log()` 调用
  fan-out 到所有后端；导出前会去掉 reducer 标记（`@mean`、`@sum`、`@p99`），
  并按命名空间声明 W&B 的 step 轴。

参考 Grafana 仪表盘位于 [`grafana/service-overview.json`](grafana/service-overview.json)。

## 🛠 开发

环境要求：Python ≥ 3.12 和稳定版 Rust 工具链。用一条命令在本地运行全部 CI 检查：

```bash
./scripts/check.sh
```

或分别运行：

```bash
ruff check src tests                                   # Python lint
python -m pytest tests -q                              # Python 单元测试
cargo fmt --manifest-path rust/Cargo.toml --check      # Rust 格式
cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust/Cargo.toml --locked    # Rust 单元测试
./scripts/check_parity.sh                              # Python/Rust 逐字节一致
```

完整流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，版本历史见
[CHANGELOG.md](CHANGELOG.md)。

## 🌐 生态

| 项目 | 角色 | 与本包的关系 |
| --- | --- | --- |
| [TensorPlay](https://github.com/lexing-2026/TensorPlay) | 张量运行时与算子 | 基础层；本包不修改它 |
| [MegaLM](https://github.com/ohtensorplay/megalm) | 推理运行时 | 使用日志、指标、OTLP 与 `Tracking` |
| [megaRL](https://github.com/ohtensorplay/megarl) | RL 后训练 | 使用控制器指标、rollout 直方图与 `Tracking` |

## 许可证

Apache-2.0（与 megaLM / megaRL 相同）。参见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。