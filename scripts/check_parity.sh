#!/usr/bin/env bash
# Verify the Python and Rust Prometheus renderers produce byte-identical output.
set -euo pipefail
cd "$(dirname "$0")/.."

SNAPSHOT='{"proxy":{"admitted":3},"gpu":{"util":0.5},"latency_ms_histogram":{"1":5,"10":3,"__sum__":37.5,"__count__":8},"http_requests":[{"__value__":3,"__type__":"counter","__labels__":{"status":"503","route":"/v1/chat"}},{"__value__":2,"__type__":"counter","__labels__":{"status":"200","route":"/v1/chat"}}],"cancelled":true}'

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

(cd rust && printf '%s' "$SNAPSHOT" | cargo run --locked -q --example render -- megarl) > "$tmp/rust.txt"
python3 -c '
import json
from tp_observability import render_prometheus_metrics
print(render_prometheus_metrics(json.loads(input()), prefix="megarl"), end="")
' <<< "$SNAPSHOT" > "$tmp/python.txt"

diff -u "$tmp/rust.txt" "$tmp/python.txt"
echo "Python/Rust Prometheus renderers are byte-identical."
