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
"""Translate immutable Core snapshots to the OpenTelemetry Metrics data model."""

from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Sequence

from opentelemetry.sdk.metrics import Exemplar as OTelExemplar
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    HistogramDataPoint,
    Metric,
    MetricsData,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
    Sum,
)
from opentelemetry.sdk.metrics.export import (
    Gauge as OTelGauge,
)
from opentelemetry.sdk.metrics.export import (
    Histogram as OTelHistogram,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from orbit.diagnostics import MetricExemplar, MetricSnapshot

_MAX_SNAPSHOTS = 100_000
_SCOPE_NAME = "orbit-metrics-opentelemetry"
_SCHEMA_URL = ""


def snapshots_to_metrics_data(
    snapshots: Sequence[MetricSnapshot],
    *,
    service_name: str,
    start_time_unix_nano: int,
    time_unix_nano: int | None = None,
    max_snapshots: int = 10_000,
) -> MetricsData:
    """Convert one detached Core metric collection into cumulative OTel metric data.

    Core counters remain monotonic cumulative sums, gauges remain gauges, and Core histogram
    bucket counts are converted from cumulative buckets into the disjoint bucket counts required
    by OpenTelemetry. Exact Core histogram minima and maxima are carried when available; ``NaN``
    is used only for hand-constructed snapshots that lack those optional fields, as specified by
    the OTel Python SDK data model to represent omitted min/max information.
    """
    if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)):
        raise TypeError("snapshots must be a bounded sequence of Core MetricSnapshot values.")
    if (
        isinstance(max_snapshots, bool)
        or not isinstance(max_snapshots, int)
        or not 1 <= max_snapshots <= _MAX_SNAPSHOTS
    ):
        raise ValueError(f"max_snapshots must be from 1 through {_MAX_SNAPSHOTS}.")
    if not isinstance(service_name, str) or not service_name.strip() or len(service_name) > 255:
        raise ValueError("service_name must be bounded, non-empty text.")
    if any(ord(character) < 32 or ord(character) == 127 for character in service_name):
        raise ValueError("service_name must contain printable text only.")
    if (
        isinstance(start_time_unix_nano, bool)
        or not isinstance(start_time_unix_nano, int)
        or start_time_unix_nano <= 0
    ):
        raise ValueError("start_time_unix_nano must be a positive integer.")
    now = max(time.time_ns(), start_time_unix_nano) if time_unix_nano is None else time_unix_nano
    if isinstance(now, bool) or not isinstance(now, int) or now < start_time_unix_nano:
        raise ValueError("time_unix_nano must be an integer no earlier than start time.")

    detached: list[MetricSnapshot] = []
    for snapshot in snapshots:
        if len(detached) >= max_snapshots:
            raise ValueError("OpenTelemetry metric collection exceeds its configured series limit.")
        if not isinstance(snapshot, MetricSnapshot):
            raise TypeError("every snapshot must be a Core MetricSnapshot.")
        detached.append(snapshot)

    grouped: dict[str, list[MetricSnapshot]] = defaultdict(list)
    kinds: dict[str, str] = {}
    series: dict[str, set[tuple[tuple[str, str], ...]]] = defaultdict(set)
    for snapshot in detached:
        previous = kinds.setdefault(snapshot.name, snapshot.kind)
        if previous != snapshot.kind:
            raise ValueError("One OpenTelemetry metric name cannot have multiple data kinds.")
        series_key = tuple(sorted(snapshot.labels.items()))
        if series_key in series[snapshot.name]:
            raise ValueError("Metric snapshots cannot contain duplicate label series.")
        series[snapshot.name].add(series_key)
        grouped[snapshot.name].append(snapshot)

    metrics: list[Metric] = []
    for name in sorted(grouped):
        kind = kinds[name]
        points = grouped[name]
        if kind == "counter":
            data: Sum | OTelGauge | OTelHistogram = Sum(
                data_points=tuple(
                    NumberDataPoint(
                        attributes=dict(point.labels),
                        start_time_unix_nano=start_time_unix_nano,
                        time_unix_nano=now,
                        value=point.value,
                        exemplars=tuple(_exemplar(item) for item in point.exemplars),
                    )
                    for point in points
                ),
                aggregation_temporality=AggregationTemporality.CUMULATIVE,
                is_monotonic=True,
            )
        elif kind == "gauge":
            data = OTelGauge(
                data_points=tuple(
                    NumberDataPoint(
                        attributes=dict(point.labels),
                        start_time_unix_nano=0,
                        time_unix_nano=now,
                        value=point.value,
                    )
                    for point in points
                )
            )
        else:
            points = [point for point in points if point.count > 0]
            if not points:
                continue
            data = OTelHistogram(
                data_points=tuple(
                    _histogram_point(point, start_time_unix_nano, now) for point in points
                ),
                aggregation_temporality=AggregationTemporality.CUMULATIVE,
            )
        metrics.append(Metric(name=name, description=None, unit=None, data=data))

    resource = Resource.create({"service.name": service_name})
    scope = InstrumentationScope(_SCOPE_NAME, "0.1.0a1")
    return MetricsData(
        resource_metrics=(
            ResourceMetrics(
                resource=resource,
                scope_metrics=(ScopeMetrics(scope, tuple(metrics), _SCHEMA_URL),),
                schema_url=_SCHEMA_URL,
            ),
        )
    )


def _histogram_point(
    snapshot: MetricSnapshot,
    start_time_unix_nano: int,
    time_unix_nano: int,
) -> HistogramDataPoint:
    """Convert Core's cumulative finite buckets to OTel disjoint buckets without approximation."""
    if snapshot.buckets is None:
        raise ValueError("Core histogram snapshots must include bucket counts.")
    if snapshot.count == 0 and (any(snapshot.buckets.values()) or snapshot.sum != 0):
        raise ValueError("Empty Core histogram snapshots cannot contain observations.")
    bounds = tuple(snapshot.buckets)
    cumulative = tuple(snapshot.buckets.values())
    counts: list[int] = []
    previous = 0
    for count in cumulative:
        if count < previous:
            raise ValueError("Core histogram buckets must be cumulative and nondecreasing.")
        counts.append(count - previous)
        previous = count
    counts.append(snapshot.count - previous)
    if any(count < 0 for count in counts) or sum(counts) != snapshot.count:
        raise ValueError("Core histogram buckets do not reconcile with the observation count.")
    return HistogramDataPoint(
        attributes=dict(snapshot.labels),
        start_time_unix_nano=start_time_unix_nano,
        time_unix_nano=time_unix_nano,
        count=snapshot.count,
        sum=snapshot.sum,
        bucket_counts=tuple(counts),
        explicit_bounds=bounds,
        min=math.nan if snapshot.min is None else snapshot.min,
        max=math.nan if snapshot.max is None else snapshot.max,
        exemplars=tuple(_exemplar(item) for item in snapshot.exemplars),
    )


def _exemplar(value: MetricExemplar) -> OTelExemplar:
    """Translate a Core trace-linked observation without turning IDs into metric attributes."""
    return OTelExemplar(
        filtered_attributes={},
        value=value.value,
        time_unix_nano=value.time_unix_nano,
        trace_id=int(value.trace_id, 16),
        span_id=int(value.span_id, 16),
    )


__all__ = ["snapshots_to_metrics_data"]
