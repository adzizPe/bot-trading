# Requirements Document

## Introduction

Milestone 10.9 makes the native Windows deployment production-observable without adding or changing trading behavior. Existing Strategy, Risk, Paper, Backtest, Dashboard domain behavior, Demo, Safety, Authentication, Recovery, and Windows Service orchestration remain unchanged.

## Glossary

- **Observability Service:** The read-only backend component that collects bounded operational metrics and evaluates alerts.
- **Passive observation:** Reading existing runtime state without starting, connecting, remediating, or mutating the observed component.
- **Authoritative readiness:** The existing backend readiness contract used by native Windows operations.
- **Event Log sink:** The injectable adapter that delivers sanitized alert lifecycle events to native Windows Event Log.

## Requirements

### Requirement 1: Scope and safety

**User Story:** As an operator, I want observability isolated from trading and deployment control so that monitoring cannot mutate production behavior.

#### Acceptance Criteria

1. THE system SHALL add only read-only observability, alerts, monitoring pages, Windows Event Log delivery, tests, and benchmark evidence.
2. THE system SHALL NOT connect MT5, start Demo/Paper, invoke trading endpoints, or send/check/close/modify/cancel orders.
3. THE deployment SHALL remain native Windows with one loopback Uvicorn worker and Nginx as sole public edge; no container, cloud, queue, or second proxy SHALL be added.
4. THE implementation SHALL NOT read `.env`, expose secrets, account data, positions, raw paths, hostnames, stack traces, or private keys.

### Requirement 2: System metrics

**User Story:** As an operator, I want bounded infrastructure and runtime metrics so that I can assess production health without inspecting sensitive domain data.

#### Acceptance Criteria

1. THE system SHALL expose bounded CPU, RAM, disk, backend process, uptime, and worker-count observations.
2. THE system SHALL expose SQLite reachability, read latency, database/WAL/SHM size categories, and runtime-lease state without reading domain tables.
3. THE system SHALL expose Nginx availability/connection observations from a loopback-only adapter.
4. THE system SHALL expose passive WebSocket counters, MT5 connector state, and heartbeat age/state without causing lifecycle or broker mutations.
5. WHEN a source is unavailable, THE metric SHALL report `UNKNOWN` or `UNAVAILABLE` rather than fabricate success.

### Requirement 3: Health surfaces

**User Story:** As an authenticated operator, I want distinct liveness, readiness, and metrics surfaces so that each production health signal has a stable meaning.

#### Acceptance Criteria

1. Existing `/health` SHALL remain shallow backend liveness and existing authoritative `/health/readiness` semantics SHALL remain unchanged.
2. THE system SHALL add authenticated `/health/liveness` as a bounded backend-process liveness response while `/health` remains the public minimal liveness surface.
3. Detailed monitoring endpoints SHALL require existing `READ_DASHBOARD` permission and return `Cache-Control: no-store`.
4. THE system SHALL provide JSON system metrics and Prometheus text metrics without adding a metrics daemon or public backend listener.

### Requirement 4: Alert evaluation

**User Story:** As an operator, I want deterministic alert transitions so that infrastructure degradation, escalation, and recovery are clear and deduplicated.

#### Acceptance Criteria

1. THE system SHALL evaluate Disk, Memory, CPU, SQLite, MT5, WebSocket, Certificate, and Heartbeat alert categories.
2. Thresholds SHALL be bounded, explicit, and deterministic with `HEALTHY`, `WARNING`, `CRITICAL`, or `UNKNOWN` states.
3. Alert records SHALL be deduplicated by category/state, retain first/last observation and occurrence count, and recover explicitly.
4. MT5 disconnected SHALL be observable but SHALL NOT trigger reconnect; trading-disabled state SHALL remain safe.

### Requirement 5: Windows Event Log

**User Story:** As a Windows operator, I want sanitized alert lifecycle events in the native Event Log so that alerts integrate with host operations without runtime registration changes.

#### Acceptance Criteria

1. THE system SHALL provide an injectable Windows Event Log sink for alert opened, escalated, recovered, and delivery-failure events.
2. Event IDs, levels, source, and messages SHALL be allowlisted and bounded; messages SHALL contain no secrets or raw exception details.
3. Non-Windows or unavailable Event Log SHALL fail as `INTEGRATION_UNAVAILABLE` without crashing metrics/health endpoints.
4. Tests SHALL use fake adapters only and SHALL NOT register an Event Log source or write to the real host log.

### Requirement 6: Monitoring UI

**User Story:** As an authenticated dashboard user, I want read-only System Metrics and Alerts pages so that I can inspect production observability safely.

#### Acceptance Criteria

1. THE frontend SHALL add protected `System Metrics` and `Alerts` pages using read-only polling.
2. System Metrics SHALL display CPU, RAM, disk, SQLite, Nginx, backend, WebSocket, MT5, and heartbeat state with loading/error/stale handling.
3. Alerts SHALL display category, severity, state, first/last observed timestamps, occurrence count, and delivery status.
4. The pages SHALL add no trading action, mutation control, credential input, or raw log/secret display.

### Requirement 7: Performance and reliability

**User Story:** As an operator, I want bounded and failure-isolated collection so that monitoring load or one failed source cannot disrupt the application.

#### Acceptance Criteria

1. Collection SHALL be timeout-bounded, side-effect-free, cached briefly to prevent probe amplification, and executed by a bounded 60-second read-only monitor loop.
2. A failed collector SHALL degrade only its component and SHALL NOT fail all metrics.
3. Prometheus output SHALL have stable names, bounded labels, finite numeric values, and no account/path/session labels.
4. The benchmark SHALL use generated fake adapters and report collection latency/throughput without production network or service access.

### Requirement 8: Validation and completion

**User Story:** As a maintainer, I want complete regression and benchmark evidence so that Milestone 10.9 can be closed without deployment or unrelated behavior changes.

#### Acceptance Criteria

1. Tests SHALL cover monitoring, alerts, metrics, health/liveness/readiness compatibility, disk thresholds, heartbeat freshness, Event Log failure, API authorization, UI, and zero mutation.
2. Backend/frontend lint, focused tests, full non-integration regression, safety regression, production build, and observability benchmark SHALL pass.
3. Completion evidence SHALL prove existing forbidden modules and recovery/service semantics were not changed.
4. Work SHALL stop after Milestone 10.9; no automatic commit or deployment SHALL occur.
