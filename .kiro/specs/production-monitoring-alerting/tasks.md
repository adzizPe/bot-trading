# Implementation Plan: Production Monitoring & Alerting

## Overview

Implement Milestone 10.9 as an isolated, read-only observability layer for the existing native Windows deployment. Tasks preserve all trading, authentication, recovery, and Windows service semantics and finish only after lint, regression, build, benchmark, and audit evidence pass.

## Tasks

- [x] 1. Build isolated observability foundation
  - [x] 1.1 Add bounded metric, snapshot, alert, and delivery models.
  - [x] 1.2 Add native Windows CPU/RAM/process and disk collectors with injected adapters.
  - [x] 1.3 Add SQLite and Nginx read-only collectors with timeout/failure isolation.
  - [x] 1.4 Add passive WebSocket, MT5 connector, backend, certificate, and heartbeat observations.

- [x] 2. Implement alerting and Windows Event Log
  - [x] 2.1 Add deterministic CPU/memory/disk/SQLite/MT5/WebSocket/certificate/heartbeat rules.
  - [x] 2.2 Add bounded deduplicating alert store and explicit recovery transitions.
  - [x] 2.3 Add sanitized injectable Windows Event Log sink with unavailable fallback.
  - [x] 2.4 Prove alert evaluation and delivery cause zero trading/service/recovery mutations.

- [x] 3. Implement monitoring service and APIs
  - [x] 3.1 Add timeout-bounded collector orchestration and short TTL cache.
  - [x] 3.2 Add authenticated JSON metrics, Prometheus metrics, and alerts routes.
  - [x] 3.3 Add bounded `/health/liveness` while preserving `/health` and readiness.
  - [x] 3.4 Wire service only through existing application-state passive dependencies.

- [x] 4. Add monitoring pages
  - [x] 4.1 Add frontend observability types and read-only API calls.
  - [x] 4.2 Add protected System Metrics page with polling/loading/error/stale states.
  - [x] 4.3 Add protected Alerts page with severity, lifecycle, occurrence, and delivery fields.
  - [x] 4.4 Add lazy routes and navigation links without changing existing domain pages.

- [x] 5. Add required tests
  - [x] 5.1 Add Monitoring and Metrics tests for all component groups and failure isolation.
  - [x] 5.2 Add Alert and Windows Event Log tests including deduplication/recovery/failure.
  - [x] 5.3 Add Health compatibility, authorization, no-store, and Prometheus tests.
  - [x] 5.4 Add Disk threshold and Heartbeat freshness tests.
  - [x] 5.5 Add System Metrics and Alerts frontend tests with no mutation controls.

- [x] 6. Validate and stop
  - [x] 6.1 Run backend/frontend lint and focused observability tests.
  - [x] 6.2 Run full non-integration and safety regressions plus frontend tests/build.
  - [x] 6.3 Run generated-adapter observability benchmark and record evidence.
  - [x] 6.4 Audit forbidden modules, zero broker mutations, no production action, and task completion.

## Completion gate

Every checkbox requires passing evidence. Any regression, secret exposure, public backend, trading mutation, or production action leaves Milestone 10.9 incomplete. Stop after 10.9; do not deploy or commit automatically.

## Notes

- Every leaf task is mandatory; no task authorizes production deployment, Windows service changes, Nginx reload, Event Log source registration, real MT5 access, or order activity.
- Tests and benchmark use fake/generated adapters and keep real MT5 integration deselected.
- `.env` must not be read. Monitoring output must remain bounded and exclude secrets, account data, positions, raw paths, hostnames, stack traces, and private keys.
- Native deployment remains one loopback Uvicorn worker behind the existing Nginx edge, with no container, queue, cloud service, or second proxy.
- Task checkboxes are synchronized only after all completion gates and final audits pass.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["2.1", "3.1", "4.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "3.2", "3.3", "4.2", "4.3"] },
    { "id": 4, "tasks": ["2.4", "3.4", "4.4"] },
    { "id": 5, "tasks": ["5.1", "5.2", "5.3", "5.4", "5.5"] },
    { "id": 6, "tasks": ["6.1"] },
    { "id": 7, "tasks": ["6.2", "6.3"] },
    { "id": 8, "tasks": ["6.4"] }
  ]
}
```
