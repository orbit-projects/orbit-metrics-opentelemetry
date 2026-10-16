# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Exercise Core snapshot conversion and exporter lifecycle behavior."""

import pytest
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from orbit.diagnostics import MetricExemplar, MetricSnapshot

from orbit_metrics_opentelemetry import (
    OpenTelemetryMetricsConfig,
    OpenTelemetryMetricsPlugin,
    snapshots_to_metrics_data,
)


def test_conversion_preserves_histogram_and_trace_exemplar() -> None:
    snapshot = MetricSnapshot(
        "request_duration_seconds",
        "histogram",
        {"method": "GET"},
        1.5,
        count=2,
        sum=1.5,
        buckets={1.0: 1},
        min=0.5,
        max=1.0,
        exemplars=(MetricExemplar(1.0, 10, "a" * 32, "b" * 16),),
    )
    result = snapshots_to_metrics_data(
        (snapshot,),
        service_name="api",
        start_time_unix_nano=1,
        time_unix_nano=20,
    )
    metric = result.resource_metrics[0].scope_metrics[0].metrics[0]
    point = metric.data.data_points[0]
    assert point.bucket_counts == (1, 1)
    assert point.min == 0.5 and point.max == 1.0
    assert point.exemplars[0].trace_id == int("a" * 32, 16)
    assert point.exemplars[0].span_id == int("b" * 16, 16)
    assert point.attributes == {"method": "GET"}


def test_conversion_marks_unavailable_histogram_extrema_as_otel_nan() -> None:
    snapshot = MetricSnapshot(
        "latency",
        "histogram",
        {},
        0.5,
        count=1,
        sum=0.5,
        buckets={1.0: 1},
    )
    result = snapshots_to_metrics_data((snapshot,), service_name="api", start_time_unix_nano=1)
    point = result.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points[0]
    assert point.min != point.min and point.max != point.max


class _MemoryExporter(MetricExporter):
    def __init__(self) -> None:
        super().__init__()
        self.items = []
        self.shutdown_count = 0

    def export(self, metrics_data, timeout_millis=10_000, **kwargs):
        self.items.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis=10_000):
        return True

    def shutdown(self, timeout_millis=30_000, **kwargs):
        self.shutdown_count += 1


async def test_plugin_exports_and_shuts_down_idempotently() -> None:
    exporter = _MemoryExporter()
    plugin = OpenTelemetryMetricsPlugin(
        OpenTelemetryMetricsConfig(service_name="orbit-test", export_interval_millis=100),
        exporter=exporter,
    )
    from orbit import Application, ApplicationConfig

    application = Application(ApplicationConfig(name="metrics-test"))
    application.register_plugin(plugin)
    application.plugins.setup(application)
    await plugin.activate()
    action_result = await plugin.control_actions[0].handler({})
    assert action_result is not None and action_result.succeeded
    await plugin.deactivate()
    await plugin.deactivate()

    assert plugin.last_export_succeeded is True
    assert exporter.shutdown_count == 1
    assert len(exporter.items) >= 1


def test_conversion_rejects_non_monotonic_time_and_invalid_histogram() -> None:
    with pytest.raises(ValueError):
        snapshots_to_metrics_data((), service_name="api", start_time_unix_nano=10, time_unix_nano=9)
    with pytest.raises(ValueError, match="max_snapshots"):
        snapshots_to_metrics_data(
            (), service_name="api", start_time_unix_nano=1, max_snapshots=True
        )
    with pytest.raises(ValueError, match="printable"):
        snapshots_to_metrics_data((), service_name="api\n", start_time_unix_nano=1)
    duplicate = MetricSnapshot("requests", "counter", {"method": "GET"}, 1)
    with pytest.raises(ValueError, match="duplicate"):
        snapshots_to_metrics_data(
            (duplicate, duplicate), service_name="api", start_time_unix_nano=1
        )
