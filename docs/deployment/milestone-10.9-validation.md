# Milestone 10.9 Validation Evidence

## Scope

Milestone 10.9 adds an isolated read-only observability layer to the existing native Windows deployment. It adds CPU, RAM, disk, SQLite, Nginx, backend, WebSocket, MT5 connector, certificate, and heartbeat observations; deterministic alerts; native Windows Event Log delivery; and protected System Metrics and Alerts pages.

No trading feature, deployment packaging, service orchestration, recovery action, database migration, external queue, container, cloud dependency, or second proxy was added.

## Monitoring contracts

- Existing public `/api/v1/health` and authoritative `/api/v1/health/readiness` remain unchanged.
- Authenticated `GET /api/v1/health/liveness` provides bounded process liveness.
- Authenticated `GET /api/v1/monitoring/metrics` provides JSON metrics.
- Authenticated `GET /api/v1/monitoring/metrics/prometheus` provides stable Prometheus text.
- Authenticated `GET /api/v1/monitoring/alerts` provides bounded alert lifecycle records.
- Detailed responses use `Cache-Control: no-store` and existing `READ_DASHBOARD` authorization.

## Alerts and Event Log

Alert categories are CPU, Memory, Disk, SQLite, MT5, WebSocket, Certificate, and Heartbeat. Records support open, deduplicated repetition, escalation, explicit recovery, occurrence counts, and delivery state.

Production Event Log delivery uses source `TradingBotObservability` and fixed event IDs 10901, 10902, and 10903. Tests use fake/unavailable adapters only. Delivery failure remains visible as `INTEGRATION_UNAVAILABLE` and does not break monitoring.

## Validation results

- Kiro spec diagnostics: requirements, design, and tasks clean.
- Full Python Ruff gate: passed.
- Backend non-integration regression: 779 passed, 8 deselected.
- Safety integration regression: 29 passed, 758 deselected.
- Final focused backend gate: 29 passed, 759 deselected.
- Frontend ESLint and TypeScript: passed.
- Frontend full regression: 65 tests passed.
- Final monitoring UI tests: 5 passed across 2 files.
- Vite production build: passed, 1,862 modules transformed.
- `git diff --check`: passed.

## Benchmark

Command: `.venv\Scripts\python.exe -m benchmarks.observability --iterations 5000` from `backend`.

Result: 5,000 uncached collections, 10 components, 826-byte Prometheus payload, 0.014737 ms cached average, 0.1094 ms cached maximum, 0.660341 ms uncached average, 41.1511 ms uncached maximum, and `zero_external_calls: true`.

## Safety audit and limitations

Changed paths were audited: no migration or forbidden Strategy, Risk, Paper, Backtest, Dashboard-domain, Demo, Safety, Authentication, Recovery, or Windows Service/operations module changed. Narrow edits to application wiring, API routing, frontend API/types/navigation, and route policy are intentional. Source audit found no broker, order, service, restore, or recovery mutation calls; the only `.cancel()` is cancellation of the backend-owned monitor task during shutdown.

No production deployment, Windows service change, Nginx reload, real Event Log write, real Nginx/certificate probe, real MT5 connection, or order action was performed. Host-specific validation remains an authorized native-VPS rollout step. No commit was created.
