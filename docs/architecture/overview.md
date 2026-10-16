# Architecture

The adapter converts immutable Core `MetricSnapshot` values into the OpenTelemetry SDK's metric
data model. It maps counters to monotonic cumulative sums, gauges to gauge points, and Core's
cumulative histogram buckets to the SDK's disjoint histogram counts. Histogram minimum and maximum
come from exact values tracked in Core.

Core records at most four recent exemplars per series when a counter increment or histogram
observation occurs inside valid Orbit trace context. The exporter maps those IDs to OpenTelemetry
exemplars. IDs are never copied into metric attributes. This retains useful trace correlation
without increasing series cardinality.

One periodic task performs sequential snapshot and export, so the plugin cannot create an
unbounded number of concurrent export tasks. Snapshot count, interval, and exporter timeout are
bounded. The injected exporter owns its transport and any retry behavior. No global
`MeterProvider` is installed.
