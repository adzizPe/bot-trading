# Design Document

## Overview

Milestone 10.9 adds production observability as an isolated read-only layer. It preserves the existing native Windows deployment, trading boundaries, readiness semantics, and operational control plane.

## Architecture

```text
Authenticated API/UI
  -> ObservabilityService (short TTL, per-collector containment)
     -> NativeSystemCollector (CPU/RAM/disk/process)
     -> SQLiteCollector (SELECT 1 + file-size categories)
     -> passive app-state adapters (WebSocket, MT5, heartbeat, lease)
     -> Nginx loopback status adapter
     -> Certificate observation adapter
  -> AlertEvaluator -> in-memory bounded AlertStore -> WindowsEventLogSink
```

The service is created by `create_app` from existing injected instances. It never constructs a connector, engine, scheduler, database engine, service manager, or recovery object. A backend-owned 60-second read-only monitor task refreshes observations and alerts; it is cancelled by the existing application lifespan and has no remediation authority.

## Components and Interfaces

### Native collection

CPU and memory use a native Windows adapter backed by `ctypes` (`GetSystemTimes`, `GlobalMemoryStatusEx`, current-process information). Disk uses `shutil.disk_usage`. The first CPU sample may be `UNKNOWN`; subsequent deltas are clamped to 0–100. Tests inject deterministic native snapshots.

SQLite executes only `SELECT 1`, measures latency, reports lease state, and categorizes DB/WAL/SHM bytes. Paths never leave the collector. Nginx parsing accepts only loopback `stub_status` text through an injected fetcher. WebSocket uses existing `status()`, MT5 uses existing `status()`, and heartbeat uses existing `snapshot()`; none may call start/connect/run_once.

### Alerts

Fixed rules cover CPU, memory, disk, SQLite reachability/latency, MT5 connector error, WebSocket drops/health, certificate expiry, and heartbeat age/state. Disconnected MT5 is a state observation, never remediation. AlertStore caps history, deduplicates unchanged states, increments occurrences, and emits explicit recovery. Event Log receives sanitized lifecycle messages through an adapter; unavailable integration is recorded without breaking collection.

### API and edge

- Public unchanged: `/health`, `/health/readiness`; new authenticated `/health/liveness`.
- Authenticated read-only: `/monitoring/metrics`, `/monitoring/metrics/prometheus`, `/monitoring/alerts`.
- All monitoring responses are `no-store`; Prometheus endpoint is text/plain and remains behind Nginx/auth.
- Existing local-only Nginx `/nginx/status` remains non-public.

### Frontend

Two lazy protected routes are added to the existing shell: `/system-metrics` and `/alerts`. TanStack Query polls read-only endpoints. Components render semantic tables/cards, clear stale/error states, and no buttons that mutate backend state.

## Data Models

`MetricState` is `HEALTHY|WARNING|CRITICAL|UNKNOWN`. `MetricObservation` contains only name/state/value/unit/observed UTC/detail code. `SystemMetricsSnapshot` contains release/timestamp and fixed component groups. `AlertRecord` contains deterministic ID, category/severity/state, first/last UTC, occurrences, active flag, and delivery state.

Collectors implement async protocols and return bounded models. A monotonic TTL cache coalesces concurrent reads. Prometheus serialization uses a fixed metric-name map and no dynamic sensitive labels.

## Error Handling

Collector exceptions and timeout become an `UNKNOWN` component rather than failing the snapshot. Event Log delivery failures become `INTEGRATION_UNAVAILABLE` and never break metrics, liveness, or alerts responses. Monitoring performs no reconnect, restart, recovery, restore, or trading remediation.

## Correctness Properties

### Property 1: Collector failure isolation

**Validates: Requirements 2.5, 7.2**

One collector exception changes only that component to `UNKNOWN`.

### Property 2: Zero mutation

**Validates: Requirements 1.1, 1.2, 4.4**

Collection and alert evaluation never call MT5 connect or any trading, service, or recovery mutation.

### Property 3: Bounded output

**Validates: Requirements 1.4, 5.2, 7.3**

Model fields, labels, alert history, and Event Log messages remain bounded and secret-free.

### Property 4: Determinism

**Validates: Requirements 4.2, 4.3**

Identical snapshots produce identical metrics and alert transitions.

### Property 5: Health compatibility

**Validates: Requirements 3.1, 3.2**

Liveness additions cannot change readiness or the existing `/health` output.

### Property 6: Native privacy

**Validates: Requirements 1.3, 3.3, 3.4**

The backend remains loopback-only and detailed metrics remain authenticated.

## Testing Strategy

Unit/property tests use fake clocks, native snapshots, SQLite sessions, Nginx text, app-state fakes, and Event Log fakes. Route tests verify auth, content types, cache headers, schemas, and zero mutation. UI tests verify all groups and alerts. Full backend/frontend regressions run with real MT5 integration deselected. A standalone offline benchmark measures cached and uncached collection latency and fails only on malformed/non-finite output, not host-specific speed.
