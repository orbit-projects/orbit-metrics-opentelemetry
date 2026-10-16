# Security

Metric labels and values can disclose customer identifiers or operational details. Use bounded,
low-cardinality labels and never include credentials, secret values, personal data, request IDs,
trace IDs, or arbitrary user input as metric attributes. Trace correlation is carried as bounded
OpenTelemetry exemplars, not labels.

The plugin reports only generic export failure messages. Exporter endpoint, credentials, TLS,
collector authorization, retention, and access controls remain the application's responsibility.
Keep exporter credentials out of source and telemetry attributes.
