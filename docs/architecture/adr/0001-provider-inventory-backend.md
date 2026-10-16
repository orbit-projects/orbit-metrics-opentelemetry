# ADR 0001: AWS Resource Explorer as the inventory backend

**Status:** Accepted for pre-alpha implementation  
**Date:** 2026-10-06

## Context

The provider adapter implements the shared `orbit-cloud` read-only inventory protocol. It must map common filters and pagination into the provider API without importing provider behavior into the capability package, granting write access, or returning arbitrary metadata that can contain sensitive values.

## Decision

Use the async aiobotocore Resource Explorer client with a caller-supplied view ARN. The adapter may only call Search; it must not create indexes or views. Scope must equal the configured view ARN; provider filters are constructed from validated identifiers. The adapter uses the ambient AWS SDK credential chain, and operators grant GetView/Search for the selected view.

## Consequences

Provider indexing, query semantics, permissions, result freshness, partial coverage, and pagination remain provider-specific. The common page model does not imply complete or strongly consistent inventory. Fake-client tests cover translation and lifecycle; live cloud behavior is a separate release gate.

## Validation required

Before stable support, validate the least-privilege policy, SDK/API compatibility, page-token behavior, partial-result handling, cancellation, timeouts, and shutdown in a dedicated non-production cloud environment. Record the tested service, SDK, and Python versions.
