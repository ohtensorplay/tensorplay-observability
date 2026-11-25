# Contributing to tensorplay-observability

Thanks for taking the time to improve the shared observability layer. This
document describes the development setup, checks, and pull-request process.

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).
For security vulnerabilities, please follow [SECURITY.md](SECURITY.md) instead
of opening a public issue.

## 🌱 Your first PR

New here? Pick an issue labelled
[`good first issue`](https://github.com/ohtensorplay/tensorplay-observability/labels/good%20first%20issue),
comment that you would like to work on it, then:

1. Follow [Development setup](#development-setup) and create a branch.
2. Make one small, focused change — one issue per PR.
3. Run the [Checks](#checks) locally.
4. Open a PR against `main` with the default template and link the issue
   (`Fixes #123`).
5. Respond to review feedback — a few rounds of iteration are normal.

Ask task-specific questions in the issue; general questions belong in the
[community forum](https://forum.tensorplay.org).

## Development setup

Requirements:

- Python 3.12 or newer
- A stable Rust toolchain (the renderer crate is dependency-light but CI uses
  `cargo fmt`, `cargo clippy -D warnings`, `cargo test`)

The package has no required runtime dependencies. Optional extras add the
OpenTelemetry SDK and the training trackers:

```bash
git clone https://github.com/ohtensorplay/tensorplay-observability.git
cd tensorplay-observability
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
```

For OTLP or tracker work, install the matching extra:

```bash
python -m pip install -e ".[otel,tracking]"
```

The Rust crate is a plain `cargo` project under `rust/`:

```bash
cargo check --manifest-path rust/Cargo.toml --locked
cargo test  --manifest-path rust/Cargo.toml --locked
```

## The shared contract

The snapshot format is the cross-language contract. A change to it is a
**two-sided** change:

1. Update `src/tp_observability/prometheus.py` and `rust/src/metrics.rs`.
2. Add the same behaviour test on both sides (`tests/test_observability.py`
   and `#[test]` cases in `rust/src/metrics.rs`).
3. Add or update a case in `scripts/check_parity.sh` so CI proves the two
   renderers still produce byte-identical output.
4. Update the snapshot contract section in `README.md` and `README.zh.md`.

A change that only touches one renderer will fail CI.

## Checks

Run these before opening a pull request — the same gates run in CI:

```bash
python -m ruff check src tests                        # syntax / undefined-name gate
python -m pytest tests -q                             # Python unit tests
cargo fmt --manifest-path rust/Cargo.toml --check      # Rust formatting
cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust/Cargo.toml --locked    # Rust unit tests
./scripts/check_parity.sh                             # Python/Rust byte parity
```

`./scripts/check.sh` runs all of the above in one command.

To install the repository hygiene hooks:

```bash
python -m pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## Pull requests

- Use the default PR template and keep the title/description accurate — they
  become the squash-commit message used when bisecting.
- Keep PRs focused. Separate features, fixes, and refactors into separate PRs.
- Add or update tests for behavioural changes.
- Add an entry under `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for
  user-visible changes.
- AI-assisted contributions are welcome, but authors are expected to
  understand and be able to explain their changes during review.

CI runs on pull requests into `main` and on pushes to `main`. Draft PRs are
supported; mark a PR ready for review when you want the full check suite and
reviewer attention.

## Releases

Versions follow `major.minor.patch` in `pyproject.toml` and
`rust/Cargo.toml`, and the two must match. Maintainers publish by pushing a
`v*` tag, which builds the Python sdist/wheel, publishes to PyPI through
trusted publishing, and creates a GitHub Release. Update `CHANGELOG.md` first.

## License

By contributing you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).