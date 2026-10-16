# Orbit Metrics OpenTelemetry

`orbit-metrics-opentelemetry` converts immutable `orbit-core` metric snapshots to the OpenTelemetry
Metrics SDK data model and exports them through an injected OTel `MetricExporter`. It is optional;
Core does not install global providers or select a vendor endpoint.

**Status:** unpublished v0.1.0-alpha review candidate. Local tests use in-memory data and a fake
exporter. Collector connectivity and hosted CI are separate release gates.

## Install

```bash
python -m pip install 'orbit-metrics-opentelemetry[otlp-http]'
```

Choose `[otlp-grpc]` for the gRPC exporter. Construct an exporter with its SDK options, then pass it
to `OpenTelemetryMetricsPlugin` with `OpenTelemetryMetricsConfig(service_name=...)`. The plugin
exports bounded Core snapshots periodically, flushes on shutdown, and never registers a global
`MeterProvider`.

The plugin contributes an **Export current metrics** action to Orbit CLI. It exports one bounded
snapshot immediately and reports only the sanitized exporter outcome.

Counters are monotonic cumulative sums, gauges are point values, and histograms carry exact Core
count, sum, finite bucket bounds, minimum, and maximum. Core retains at most four recent exemplars
per metric series. When a counter update or histogram observation occurs inside a valid Orbit trace
context, the adapter exports trace and span IDs as OTel exemplars, never as metric attributes. This
supports trace-to-metric navigation without high-cardinality trace labels.

## Boundaries and limits

Core snapshots are in-process and reset when the process restarts; this adapter does not provide
persistent or distributed aggregation. Metric series and snapshots remain subject to Core and
adapter cardinality bounds. Export failures use generic safe messages. Exporter construction,
resource attributes beyond `service.name`, endpoint configuration, TLS, credentials, sampling, and
collector operations remain application responsibilities.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
ruff check src tests
ruff format --check src tests
mypy
python -m build
```

See [architecture](docs/architecture/overview.md), [operations](docs/operations/README.md),
[security](docs/security/overview.md), and [development](docs/development/README.md). Licensed
under Apache-2.0.
