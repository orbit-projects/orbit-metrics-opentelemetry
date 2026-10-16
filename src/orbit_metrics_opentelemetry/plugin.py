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
"""Lifecycle-owned periodic OpenTelemetry export for Core metric snapshots."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING

from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from orbit.plugins import ControlAction, ControlResult, Plugin, PluginMetadata

from orbit_metrics_opentelemetry.config import OpenTelemetryMetricsConfig
from orbit_metrics_opentelemetry.exporter import snapshots_to_metrics_data

if TYPE_CHECKING:
    from orbit import Application

_LOG = logging.getLogger(__name__)


class OpenTelemetryMetricsPlugin(Plugin):
    """Periodically export detached Core snapshots through an injected OTel MetricExporter.

    The plugin never installs a global MeterProvider. It owns the exporter and its export task,
    uses one sequential bounded collection loop, and records no raw exporter exception details.
    """

    metadata = PluginMetadata(
        name="orbit-metrics-opentelemetry",
        version="0.1.0a1",
        capabilities=frozenset({"metrics.export"}),
    )

    def __init__(self, config: OpenTelemetryMetricsConfig, *, exporter: MetricExporter) -> None:
        """Validate ownership and prepare the exporter without starting background work."""
        if not isinstance(config, OpenTelemetryMetricsConfig):
            raise TypeError("config must be OpenTelemetryMetricsConfig.")
        if not isinstance(exporter, MetricExporter):
            raise TypeError("exporter must implement OpenTelemetry MetricExporter.")
        self.config = config
        self._exporter = exporter
        self._application: Application | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._started_at = time.time_ns()
        self._closing = False
        self._last_export_succeeded: bool | None = None
        self.control_actions = (
            ControlAction(
                id="export-metrics",
                title="Export current metrics",
                description="Export one bounded Core metric snapshot through the backend.",
                handler=self._export_metrics_action,
            ),
        )

    @property
    def last_export_succeeded(self) -> bool | None:
        """Return the last export outcome without exposing exporter diagnostics."""
        return self._last_export_succeeded

    def setup(self, application: Application) -> None:
        """Bind the Core application before its plugin registry freezes."""
        from orbit import Application as OrbitApplication

        if not isinstance(application, OrbitApplication):
            raise TypeError("OpenTelemetryMetricsPlugin requires an Orbit Application.")
        if self._application is not None:
            raise RuntimeError("OpenTelemetry metrics plugin is already bound to an application.")
        self._application = application

    async def activate(self) -> None:
        """Start the sequential collection loop after successful application startup."""
        if self._closing:
            raise RuntimeError("A stopped OpenTelemetry metrics plugin cannot be reactivated.")
        if self._application is None:
            raise RuntimeError(
                "OpenTelemetry metrics plugin must be registered with an application."
            )
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="orbit-otel-metrics-export")

    async def _run(self) -> None:
        """Export immediately, then wait at a fixed bounded interval until shutdown."""
        await self._export_once()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.config.export_interval_millis / 1_000
                )
            except TimeoutError:
                await self._export_once()
        await self._export_once()

    async def _export_once(self) -> None:
        """Take and convert one current snapshot, then call the blocking exporter off-loop."""
        application = self._application
        if application is None:
            self._last_export_succeeded = False
            return
        try:
            application.diagnostics.update_runtime(application)
            snapshots = application.diagnostics.metrics.snapshots()
            data = snapshots_to_metrics_data(
                snapshots,
                service_name=self.config.service_name,
                start_time_unix_nano=self._started_at,
                max_snapshots=self.config.max_snapshots,
            )
            async with asyncio.timeout(self.config.export_timeout_millis / 1_000):
                result = await asyncio.to_thread(
                    self._exporter.export,
                    data,
                    timeout_millis=self.config.export_timeout_millis,
                )
            if result is not MetricExportResult.SUCCESS:
                self._last_export_succeeded = False
                _LOG.warning("OpenTelemetry metric exporter returned a failed status.")
                return
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._last_export_succeeded = False
            _LOG.warning("OpenTelemetry metric export timed out.")
        except ValueError:
            self._last_export_succeeded = False
            _LOG.warning("OpenTelemetry metric snapshot could not be converted.")
        except Exception:
            self._last_export_succeeded = False
            # Never emit exporter exception text; it may contain labels, endpoints or payloads.
            _LOG.warning("OpenTelemetry metric export failed.")
        else:
            self._last_export_succeeded = True

    async def _export_metrics_action(self, _: Mapping[str, object]) -> ControlResult:
        """Export one current snapshot and return only its sanitized outcome."""
        if self._closing:
            return ControlResult("Metric exporter is shutting down.", succeeded=False)
        await self._export_once()
        succeeded = self._last_export_succeeded is True
        return ControlResult(
            "Current metrics were exported."
            if succeeded
            else "Metric exporter did not accept the current snapshot.",
            succeeded=succeeded,
        )

    async def deactivate(self) -> None:
        """Stop collection, await its final export, flush, and close the owned exporter."""
        self._closing = True
        self._stop.set()
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        try:
            await asyncio.shield(self._shutdown_task)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RuntimeError("OpenTelemetry metrics exporter shutdown failed.") from None

    async def _shutdown(self) -> None:
        """Perform exporter cleanup exactly once, even when shutdown has multiple callers."""
        if self._task is not None:
            await self._task
        flush_error: Exception | None = None
        flushed = False
        try:
            flushed = await asyncio.to_thread(
                self._exporter.force_flush,
                timeout_millis=self.config.export_timeout_millis,
            )
        except Exception as exc:
            flush_error = exc
        try:
            await asyncio.to_thread(
                self._exporter.shutdown,
                timeout_millis=self.config.export_timeout_millis,
            )
        except Exception as exc:
            if flush_error is None:
                flush_error = exc
        if flush_error is not None:
            raise RuntimeError("OpenTelemetry metrics exporter cleanup failed.") from None
        if not flushed:
            raise RuntimeError("OpenTelemetry metrics exporter flush failed.")


__all__ = ["OpenTelemetryMetricsPlugin"]
