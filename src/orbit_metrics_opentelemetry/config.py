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
"""Validated OpenTelemetry Metrics resource and collection limits."""

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator


class OpenTelemetryMetricsConfig(BaseModel):
    """Bounded periodic export settings for Core's in-process metric snapshots."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, validate_default=True)

    service_name: StrictStr = Field(min_length=1, max_length=255)
    export_interval_millis: StrictInt = Field(default=15_000, ge=100, le=3_600_000)
    export_timeout_millis: StrictInt = Field(default=10_000, ge=1, le=120_000)
    max_snapshots: StrictInt = Field(default=10_000, ge=1, le=100_000)

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        """Reject whitespace-only and control-bearing service identities."""
        if not value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("service_name must be non-empty printable text.")
        return value
