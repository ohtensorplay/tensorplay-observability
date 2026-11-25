<!-- Thank you for contributing! Please see CONTRIBUTING.md before opening a PR. -->

## Motivation

<!-- What problem does this PR solve, and why is the change needed? -->

## Modifications

<!-- Summarize the changes by component: prometheus/, metrics/, jsonlog/,
     otel/, tracking/, betterstack/, rust/, docs/, CI, ... -->

## Related Issues

<!-- e.g. "Fixes #123", "Part of #456" -->

## Test Plan

<!-- Which tests did you run? Include commands and results, e.g. `pytest tests -q`,
     `cargo test --manifest-path rust/Cargo.toml --locked`, `./scripts/check.sh`. -->

## Contract / Parity Evidence

<!-- For snapshot-format or renderer changes: show that the Python and Rust
     renderers still agree (`./scripts/check_parity.sh`) and paste the added
     case(s). Delete if not applicable. -->

## Checklist

- [ ] The title and description are accurate (they become the squash-commit message).
- [ ] `ruff check src tests` and `python -m pytest tests -q` pass locally.
- [ ] `cargo fmt --check`, `cargo clippy -- -D warnings` and `cargo test --locked` pass.
- [ ] Renderer changes update **both** `prometheus.py` and `rust/src/metrics.rs`
      and keep `./scripts/check_parity.sh` green.
- [ ] Tests and documentation were added or updated as needed.
- [ ] A user-visible change is recorded under `Unreleased` in `CHANGELOG.md`.

## Reviewer Notes

<!-- Optional: areas you would like reviewers to focus on, known limitations. -->