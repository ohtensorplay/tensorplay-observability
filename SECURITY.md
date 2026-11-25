# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | ✅ |
| < 0.1 | ❌ |

Security fixes are made on the latest release line only. Please upgrade before
reporting an issue against an older version.

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities. Use GitHub's
private vulnerability reporting for this repository instead:

**https://github.com/ohtensorplay/tensorplay-observability/security/advisories/new**

Include as much of the following as you can:

- Affected version / commit and the component involved (`jsonlog`, `metrics`,
  `prometheus`, `otel`, `tracking`, `betterstack`, or the Rust renderer)
- Steps to reproduce and a minimal proof of concept
- Impact (e.g. credential disclosure, log injection, denial of service) and
  whether the component is reachable from untrusted input
- Any suggested mitigation or fix

You should receive an acknowledgement within 72 hours. We will keep you
informed as we investigate, and will coordinate disclosure and a fix with you
before publishing an advisory.

## Scope notes

- The package is **opt-in and fail-soft by design**: importing it opens no
  socket, starts no thread and never mutates the root logger. Sinks such as
  OTLP export and the Better Stack handler only start when explicitly
  configured.
- Log payloads are serialized with `json.dumps`; label values are escaped for
  the Prometheus text format. Reports of injection through either path are in
  scope.
- Report vulnerabilities in TensorPlay itself to the
  [TensorPlay project](https://github.com/lexing-2026/TensorPlay/security/advisories/new),
  in MegaLM to the [MegaLM project](https://github.com/ohtensorplay/megalm/security/advisories/new),
  and in megaRL to the [megaRL project](https://github.com/ohtensorplay/megarl/security/advisories/new).
- General bugs that are not security-sensitive belong in the
  [issue tracker](https://github.com/ohtensorplay/tensorplay-observability/issues/new/choose).