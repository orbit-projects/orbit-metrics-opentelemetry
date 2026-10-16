# Operations

Configure an OTLP exporter for your collector and use a consistent `service.name` across logs,
metrics, and traces. Review collector endpoint, TLS, authentication, network policy, retention,
and accepted metric temporality before enabling production export.

The plugin periodically exports current in-process snapshots. Counter and histogram values reset
on process restart, and the adapter does not aggregate across workers or replicas. Use a collector
or backend that understands cumulative temporality and exemplars. Core's finite metric-series and
label limits apply before export. Watch exporter health and collector rejection/drop counters.
