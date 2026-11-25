//! Shared observability primitives for the TensorPlay ecosystem.
//!
//! The Python side (`tp_observability`) provides JSONL logging, OTLP hooks and
//! a metrics registry; this crate provides the dependency-free Prometheus
//! text exposition renderer used by both Rust gateways. The wire format is
//! Prometheus 0.0.4, so any mainstream scraper (Prometheus, VictoriaMetrics,
//! Grafana Cloud, Thanos/Mimir/Cortex, Datadog, New Relic) can consume it.

pub mod metrics;

pub use metrics::{render_prometheus_metrics, render_service_metrics};
