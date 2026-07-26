# Native Windows Production Monitoring and Alerting

Milestone 10.9 runs inside the existing single FastAPI/Uvicorn backend. It adds no Windows service, trading worker, external queue, container, or cloud dependency.

## Surfaces

- Public minimal liveness remains `GET /api/v1/health`.
- Authoritative readiness remains `GET /api/v1/health/readiness`.
- Authenticated detailed liveness: `GET /api/v1/health/liveness`.
- Authenticated JSON metrics: `GET /api/v1/monitoring/metrics`.
- Authenticated Prometheus text: `GET /api/v1/monitoring/metrics/prometheus`.
- Authenticated alert history: `GET /api/v1/monitoring/alerts`.

Detailed endpoints require the existing `READ_DASHBOARD` permission, return `Cache-Control: no-store`, and remain behind native Nginx. Uvicorn stays loopback-only and one-worker.

## Collection

The backend-owned monitor refreshes every 60 seconds with a two-second per-collector timeout and five-second request cache. It observes CPU, RAM, disk, backend process/uptime, SQLite read/latency/file categories/runtime lease, local Nginx status, WebSocket counters, MT5 connector state, certificate expiry, and heartbeat freshness.

Collection is read-only. It never calls MT5 connect, Demo/Paper start, order check/send/close/modify/cancel, service restart, recovery, or Restore. An unavailable source becomes `UNKNOWN`/`CRITICAL` for that component and does not crash other collectors.

## Alert thresholds

- CPU and memory: warning at 80%, critical at 90%.
- Disk: warning at 80% used, critical at 90% used.
- SQLite: critical when read probe or runtime lease fails; latency warning at 250 ms and critical at 1000 ms.
- Heartbeat: warning at 15 seconds, critical at 60 seconds or degraded state.
- Certificate: warning at 30 days, critical at 14 days.
- WebSocket drops and MT5 connector degraded/failed states are alerts; disconnected/stopped MT5 is observed without reconnect.

Alerts are deduplicated by category and severity, count repeated observations, and record explicit recovery.

## Windows Event Log

When `APP_ENV=production`, alert open/escalation/recovery events use the native Windows Event Log API under source `TradingBotObservability` with fixed IDs 10901, 10902, and 10903. No source installation or registry mutation occurs at runtime. Development/test uses an unavailable sink and never writes the host Event Log.

If Event Log delivery fails, the alert remains visible with `INTEGRATION_UNAVAILABLE`; metrics and health continue. Messages contain only allowlisted category/state/severity and never include exceptions, paths, credentials, sessions, account data, or private keys.

## Operator checks

1. Authenticate with a read-dashboard role and verify all three monitoring endpoints return `no-store`.
2. Confirm System Metrics and Alerts pages update without mutation controls.
3. Confirm Nginx `/nginx/status` remains loopback-only and backend port 8000 is not public.
4. Run focused tests and `python -m benchmarks.observability --iterations 1000` from `backend`.
5. On an authorized production host, verify Event Viewer receives a generated synthetic infrastructure alert; do not use trading or recovery actions for this check.
