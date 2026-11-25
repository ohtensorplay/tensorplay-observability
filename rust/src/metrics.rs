//! Prometheus text exposition (format 0.0.4) for a nested JSON snapshot.
//!
//! Shared by megaLM and megaRL: the only project-specific part is the prefix
//! passed to [`render_prometheus_metrics`] (`megalm`, `megarl`, ...).
//!
//! Leaf numbers and booleans become scalar metrics; dictionaries whose key
//! ends with `_histogram` become real Prometheus histograms (`_bucket`,
//! `_sum`, `_count`) usable by `histogram_quantile()`.
//!
//! For topology-independent, label-rich series a leaf may instead be an
//! explicit descriptor object:
//!
//! ```json
//! {"http_requests": {"__value__": 3,
//!                    "__type__": "counter",
//!                    "__labels__": {"status": "503", "route": "/v1/chat"}}}
//! ```
//!
//! which renders as `{prefix}_http_requests{route="/v1/chat",status="503"} 3`.
//! HELP/TYPE are emitted once per metric name. Histograms may carry
//! `__sum__` / `__count__` to supply exact observation totals instead of the
//! bucket-upper-bound approximation.

use std::collections::HashSet;
use std::fmt::Write as _;

use serde_json::Map;

fn metric_component(component: &str) -> String {
    let mut sanitized: String = component
        .chars()
        .map(|value| {
            if value.is_ascii_alphanumeric() || value == '_' {
                value.to_ascii_lowercase()
            } else {
                '_'
            }
        })
        .collect();
    if sanitized.is_empty() {
        sanitized.push_str("value");
    }
    if sanitized.as_bytes()[0].is_ascii_digit() {
        sanitized.insert(0, 'm');
    }
    sanitized
}

fn metric_name(prefix: &str, path: &[String]) -> String {
    let capacity = prefix.len() + 1 + path.iter().map(|part| part.len() + 1).sum::<usize>();
    let mut name = String::with_capacity(capacity);
    name.push_str(prefix);
    for part in path {
        name.push('_');
        name.push_str(part);
    }
    name
}

/// Heuristic gauge detection used when a descriptor does not declare
/// `__type__`. Counts and accumulated totals default to `counter`; values
/// representing a current level (means, rates, capacities, queue depths,
/// byte sizes, cache residency) are gauges.
fn is_gauge(name: &str) -> bool {
    // Cumulative totals that happen to match a gauge marker.
    const COUNTER_OVERRIDES: &[&str] = &["evicted_bytes", "parser_bytes", "sent_bytes"];
    if COUNTER_OVERRIDES.iter().any(|marker| name.contains(marker)) {
        return false;
    }
    const GAUGE_MARKERS: &[&str] = &[
        "_mean",
        "_rate",
        "hit_rate",
        "max_",
        "free_",
        "pending_",
        "active_",
        "in_flight",
        "inflight",
        "waiting_",
        "running_",
        "queued_",
        "allocated_",
        "total_pages",
        "cached_pages",
        "pinned_",
        "evictable_",
        "page_refs",
        "entries",
        "nodes",
        "_occupancy",
        "_utilization",
        "util",
        "capacity",
        "_bytes",
        "_size",
        "reclaimable",
        "used_",
    ];
    GAUGE_MARKERS.iter().any(|marker| name.contains(marker))
}

fn declare_metric(
    name: &str,
    metric_type: &str,
    output: &mut String,
    declared: &mut HashSet<String>,
) {
    if !declared.insert(name.to_string()) {
        return;
    }
    output.push_str("# HELP ");
    output.push_str(name);
    output.push(' ');
    for character in name.chars() {
        output.push(if character == '_' { ' ' } else { character });
    }
    output.push_str(".\n# TYPE ");
    output.push_str(name);
    output.push(' ');
    output.push_str(metric_type);
    output.push('\n');
}

fn write_scalar<T: std::fmt::Display>(
    name: &str,
    value: T,
    metric_type: &str,
    output: &mut String,
    declared: &mut HashSet<String>,
) {
    declare_metric(name, metric_type, output, declared);
    let _ = writeln!(output, "{name} {value}");
}

fn write_escaped_label(output: &mut String, value: &str) {
    for character in value.chars() {
        match character {
            '\\' => output.push_str("\\\\"),
            '"' => output.push_str("\\\""),
            '\n' => output.push_str("\\n"),
            _ => output.push(character),
        }
    }
}

fn write_labeled(
    name: &str,
    value: &serde_json::Value,
    labels: &Map<String, serde_json::Value>,
    metric_type: &str,
    output: &mut String,
    declared: &mut HashSet<String>,
) {
    declare_metric(name, metric_type, output, declared);
    output.push_str(name);
    output.push('{');
    let mut entries: Vec<(&String, &serde_json::Value)> = labels.iter().collect();
    entries.sort_by(|a, b| a.0.cmp(b.0));
    for (index, (key, label_value)) in entries.iter().enumerate() {
        if index > 0 {
            output.push(',');
        }
        output.push_str(&metric_component(key));
        output.push_str("=\"");
        match label_value {
            serde_json::Value::String(text) => write_escaped_label(output, text),
            other => {
                let rendered = other.to_string();
                write_escaped_label(output, &rendered);
            }
        }
        output.push('"');
    }
    output.push_str("} ");
    output.push_str(&value.to_string());
    output.push('\n');
}

fn write_le(output: &mut String, value: f64) {
    if value.fract() == 0.0 && value.is_finite() {
        let _ = write!(output, "{}", value as i64);
    } else {
        let _ = write!(output, "{value}");
    }
}

fn write_histogram(
    prefix: &str,
    path: &[String],
    entries: &Map<String, serde_json::Value>,
    output: &mut String,
    declared: &mut HashSet<String>,
) {
    let name = metric_name(prefix, path);
    declare_metric(&name, "histogram", output, declared);

    let mut buckets: Vec<(f64, u64)> = Vec::new();
    for (key, value) in entries {
        if key.starts_with("__") {
            continue;
        }
        if let Ok(bucket) = key.parse::<f64>() {
            if let Some(count) = value.as_u64().or_else(|| value.as_f64().map(|v| v as u64)) {
                buckets.push((bucket, count));
            }
        }
    }
    buckets.sort_by(|a, b| a.0.total_cmp(&b.0));

    let mut cumulative = 0u64;
    let mut sum = 0.0f64;
    for (le, count) in buckets {
        cumulative = cumulative.saturating_add(count);
        sum += le * count as f64;
        let _ = write!(output, "{name}_bucket{{le=\"");
        write_le(output, le);
        let _ = writeln!(output, "\"}} {cumulative}");
    }
    let _ = writeln!(output, "{name}_bucket{{le=\"+Inf\"}} {cumulative}");

    // Prefer exact observation totals when the producer supplies them.
    let exact_sum = entries.get("__sum__").and_then(|v| v.as_f64());
    let exact_count = entries.get("__count__").and_then(|v| v.as_u64());
    let _ = writeln!(output, "{name}_sum {}", exact_sum.unwrap_or(sum));
    let _ = writeln!(output, "{name}_count {}", exact_count.unwrap_or(cumulative));
}

fn scalar_type(name: &str, declared_type: Option<&str>) -> &'static str {
    match declared_type {
        Some("gauge") => "gauge",
        Some(_) => "counter",
        None => {
            if is_gauge(name) {
                "gauge"
            } else {
                "counter"
            }
        }
    }
}

fn flatten_metrics(
    prefix: &str,
    value: &serde_json::Value,
    path: &mut Vec<String>,
    output: &mut String,
    declared: &mut HashSet<String>,
) {
    match value {
        serde_json::Value::Object(entries) => {
            // Explicit labeled descriptor: {"__value__":..,"__labels__":{...}}
            if let (Some(sample), Some(serde_json::Value::Object(label_map))) =
                (entries.get("__value__"), entries.get("__labels__"))
            {
                let name = metric_name(prefix, path);
                let declared_type = entries.get("__type__").and_then(|v| v.as_str());
                let metric_type = scalar_type(&name, declared_type);
                write_labeled(&name, sample, label_map, metric_type, output, declared);
                return;
            }
            if path.last().is_some_and(|key| key.ends_with("_histogram")) {
                write_histogram(prefix, path, entries, output, declared);
                return;
            }
            for (key, child) in entries {
                path.push(metric_component(key));
                flatten_metrics(prefix, child, path, output, declared);
                path.pop();
            }
        }
        serde_json::Value::Array(items) => {
            // An array of labeled descriptors represents multiple samples of
            // the SAME metric, distinguished by their label sets.
            let all_labeled = !items.is_empty()
                && items.iter().all(|item| {
                    item.as_object().is_some_and(|object| {
                        object.contains_key("__value__") && object.contains_key("__labels__")
                    })
                });
            if all_labeled {
                let name = metric_name(prefix, path);
                for item in items {
                    let object = item.as_object().unwrap();
                    let sample = object.get("__value__").unwrap();
                    let labels = object.get("__labels__").unwrap().as_object().unwrap();
                    let metric_type =
                        scalar_type(&name, object.get("__type__").and_then(|v| v.as_str()));
                    write_labeled(&name, sample, labels, metric_type, output, declared);
                }
                return;
            }
            for (index, child) in items.iter().enumerate() {
                path.push(format!("item_{index}"));
                flatten_metrics(prefix, child, path, output, declared);
                path.pop();
            }
        }
        serde_json::Value::Number(number) => {
            let name = metric_name(prefix, path);
            let metric_type = scalar_type(&name, None);
            write_scalar(&name, number, metric_type, output, declared);
        }
        serde_json::Value::Bool(flag) => {
            let name = metric_name(prefix, path);
            let metric_type = scalar_type(&name, None);
            write_scalar(
                &name,
                if *flag { 1 } else { 0 },
                metric_type,
                output,
                declared,
            );
        }
        _ => {}
    }
}

/// Render a nested snapshot as Prometheus text exposition 0.0.4 with a
/// caller-supplied metric prefix.
pub fn render_prometheus_metrics(
    payload_json: &str,
    prefix: &str,
) -> Result<String, serde_json::Error> {
    let prefix = metric_component(prefix);
    let payload: serde_json::Value = serde_json::from_str(payload_json)?;
    let mut output = String::with_capacity(payload_json.len().saturating_add(256));
    let mut declared = HashSet::new();
    let mut path = Vec::new();
    flatten_metrics(&prefix, &payload, &mut path, &mut output, &mut declared);
    Ok(output)
}

/// Convenience wrapper for a service-named prefix.
pub fn render_service_metrics(
    payload_json: &str,
    service: &str,
) -> Result<String, serde_json::Error> {
    render_prometheus_metrics(payload_json, service)
}

#[cfg(test)]
mod metrics_tests {
    use super::render_prometheus_metrics;

    #[test]
    fn renders_nested_numeric_fields_with_help_and_type() {
        let rendered = render_prometheus_metrics(
            r#"{"proxy":{"admitted_requests":3},"runtime":{"gpu":{"util":0.5}}}"#,
            "megalm",
        )
        .unwrap();
        assert!(rendered
            .contains("# HELP megalm_proxy_admitted_requests megalm proxy admitted requests.\n"));
        assert!(rendered.contains("# TYPE megalm_proxy_admitted_requests counter\n"));
        assert!(rendered.contains("megalm_proxy_admitted_requests 3\n"));
        assert!(rendered.contains("# TYPE megalm_runtime_gpu_util gauge\n"));
        assert!(rendered.contains("megalm_runtime_gpu_util 0.5\n"));
    }

    #[test]
    fn prefix_is_callers_choice() {
        let rendered = render_prometheus_metrics(r#"{"generates":3}"#, "megarl").unwrap();
        assert!(rendered.contains("megarl_generates 3\n"));
        assert!(!rendered.contains("megalm_generates"));
    }

    #[test]
    fn cumulative_byte_totals_are_counters_not_gauges() {
        let rendered = render_prometheus_metrics(
            r#"{"replica":{"parser":{"bytes":1024}},"worker":{"sent_bytes":2048},"gpu":{"memory_bytes":4096}}"#,
            "megalm",
        )
        .unwrap();
        assert!(
            rendered.contains("# TYPE megalm_replica_parser_bytes counter\n"),
            "{rendered}"
        );
        assert!(
            rendered.contains("# TYPE megalm_worker_sent_bytes counter\n"),
            "{rendered}"
        );
        assert!(
            rendered.contains("# TYPE megalm_gpu_memory_bytes gauge\n"),
            "{rendered}"
        );
    }

    #[test]
    fn skips_non_numeric_fields() {
        let rendered = render_prometheus_metrics(
            r#"{"status":"ok","cancelled":true,"hist":{"1":2}}"#,
            "megalm",
        )
        .unwrap();
        assert!(!rendered.contains("status"));
        assert!(rendered.contains("megalm_cancelled 1\n"));
        assert!(rendered.contains("megalm_hist_m1 2\n"));
    }

    #[test]
    fn histogram_keys_ending_in_histogram_emit_bucket_sum_and_count() {
        let rendered = render_prometheus_metrics(
            r#"{"engine":{"sequence_batch_histogram":{"1":5,"2":3,"10":1}}}"#,
            "megalm",
        )
        .unwrap();
        let name = "megalm_engine_sequence_batch_histogram";
        assert!(rendered.contains(&format!("# TYPE {name} histogram\n")));
        assert!(rendered.contains(&format!("{name}_bucket{{le=\"1\"}} 5\n")));
        assert!(rendered.contains(&format!("{name}_bucket{{le=\"2\"}} 8\n")));
        assert!(rendered.contains(&format!("{name}_bucket{{le=\"10\"}} 9\n")));
        assert!(rendered.contains(&format!("{name}_bucket{{le=\"+Inf\"}} 9\n")));
        assert!(rendered.contains(&format!("{name}_sum 21\n")));
        assert!(rendered.contains(&format!("{name}_count 9\n")));
    }

    #[test]
    fn empty_histogram_emits_zero_series() {
        let rendered = render_prometheus_metrics(r#"{"h_histogram":{}}"#, "megalm").unwrap();
        assert!(rendered.contains("megalm_h_histogram_count 0\n"));
        assert!(rendered.contains("megalm_h_histogram_sum 0\n"));
        assert!(rendered.contains("megalm_h_histogram_bucket{le=\"+Inf\"} 0\n"));
    }

    #[test]
    fn metric_components_are_sanitized() {
        let rendered = render_prometheus_metrics(r#"{"1bad-key":2}"#, "megalm").unwrap();
        assert!(rendered.contains("megalm_m1bad_key 2\n"));
    }

    #[test]
    fn labeled_descriptors_render_native_labels_and_declare_header_once() {
        let rendered = render_prometheus_metrics(
            r#"{"http_requests":[
                 {"__value__":3,"__type__":"counter","__labels__":{"reason":"stop","route":"/v1/chat"}},
                 {"__value__":2,"__type__":"counter","__labels__":{"reason":"error","route":"/v1/chat"}}
            ]}"#,
            "megalm",
        )
        .unwrap();
        assert_eq!(
            rendered
                .matches("# TYPE megalm_http_requests counter\n")
                .count(),
            1
        );
        assert!(rendered.contains("megalm_http_requests{reason=\"error\",route=\"/v1/chat\"} 2\n"));
        assert!(rendered.contains("megalm_http_requests{reason=\"stop\",route=\"/v1/chat\"} 3\n"));
    }

    #[test]
    fn histogram_accepts_exact_sum_and_count() {
        let rendered = render_prometheus_metrics(
            r#"{"latency_ms_histogram":{"5":1,"25":2,"__sum__":37.5,"__count__":3}}"#,
            "megalm",
        )
        .unwrap();
        assert!(rendered.contains("megalm_latency_ms_histogram_sum 37.5\n"));
        assert!(rendered.contains("megalm_latency_ms_histogram_count 3\n"));
    }

    #[test]
    fn label_values_are_escaped() {
        let rendered = render_prometheus_metrics(
            r#"{"e":{"v":{"__value__":1,"__labels__":{"msg":"a\"b\\c"}}}}"#,
            "megalm",
        )
        .unwrap();
        assert!(rendered.contains("msg=\"a\\\"b\\\\c\""));
    }
}
