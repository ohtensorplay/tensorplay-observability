#!/usr/bin/env bash
# Run every gate that CI runs: lint, Python tests, Rust tests and the
# Python/Rust renderer parity check.
set -euo pipefail
cd "$(dirname "$0")/.."

python -m ruff check src tests
python -m pytest tests -q
cargo fmt --manifest-path rust/Cargo.toml --check
cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust/Cargo.toml --locked
./scripts/check_parity.sh

echo "All checks passed."