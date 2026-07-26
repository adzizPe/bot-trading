# Milestone 10.8 Validation Evidence

Validated on 2026-07-26 using generated data, temporary paths, in-process fake service adapters, and static/sandbox contracts only. No production host, VPS, Windows service installation, Nginx reload, real MT5 connection, or broker order was used.

## Decision

**PASS — Milestone 10.8 implementation is complete.** Canonical process manager: **NSSM**. PM2 remains a mutually exclusive validated alternative. Deployment remains native Windows: venv Python/Uvicorn, Vite `frontend/dist`, and native Nginx.

## Implemented scope

- Control plane: `backend/app/operations/` (policy, evidence, readiness, service ownership, controller, lifecycle, releases, Restore Hold, monitoring, certificate/capacity/log policy, hardening/access/secret audit, CLI).
- API/runtime: `backend/app/main.py`, `backend/app/api/router.py`, `backend/app/api/routes/readiness.py`.
- Edge: `frontend/nginx.conf` with static `/healthz`, exact proxied readiness, REST, and exact/prefix WebSocket contracts.
- Operator interface: `scripts/Operations.Common.ps1` and 17 PLAN/WhatIf-first native-operation wrappers.
- Tests: `backend/tests/test_operations_*.py`, Nginx/route contracts, existing recovery and application regressions.
- Runbooks: `windows-service-operations.md`, cross-references in Windows Nginx/SQLite recovery runbooks, and README.
- No Milestone 10.8 migration was added; repository Alembic head remains `20260728_0009`.

## Required invariant evidence

| Invariant                  | Evidence                                                                                                                                  | Result |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| One backend process/worker | NSSM definition and drill require `--workers 1`; private-backend validator requires exactly one process                                   | PASS   |
| Private backend            | Uvicorn is fixed to `127.0.0.1:8000`; listener validator rejects non-loopback                                                             | PASS   |
| Sole public edge           | Only native Nginx owns public HTTP/HTTPS; backend has no public fallback                                                                  | PASS   |
| Edge/readiness split       | `/healthz` is static edge liveness; `/api/v1/health/readiness` is exact, read-only, proxied, rate-limited, and `no-store`                 | PASS   |
| Trading-safe lifecycle     | MT5 connect, Demo start, Paper start, order check/send/close/modify/cancel counters are each zero in every integrated scenario            | PASS   |
| Restore safety             | Restore Hold blocks automatic startup/restart/update/rollback; restore success/failure starts nothing; first post-restore start is manual | PASS   |
| Release safety             | Immutable complete release set, ten-minute acceptance, compatible LKG rollback, no downgrade/automatic restore                            | PASS   |
| Evidence                   | Sanitized immutable package, distinct reviewer, references instead of raw logs, retention over 180 days                                   | PASS   |

## Validation results

- `python -m ruff check backend/app backend/tests backend/migrations`: PASS.
- Focused lifecycle/security/operations/Nginx/route/WebSocket/recovery-lease gate: **241 passed, 522 deselected in 24.41s**.
- Integrated lifecycle drill: **3 passed in 1.97s**; covers cold boot, readiness/edge failures, clean/forced reboot, backend/edge crash, update/LKG rollback, capacity block, warning/critical delivery, certificate rollback, Restore Hold/manual start, and evidence publication.
- Full backend non-integration: **755 passed, 8 deselected in 182.64s**.
- Offline safety integration: **29 passed, 734 deselected in 14.86s**.
- Generated-data restore drill through `app.recovery.cli drill`: **PASS**, exit code 0, all drill stages successful; the PowerShell wrapper itself is covered by offline parse/contract tests.
- Frontend: ESLint PASS; TypeScript PASS; Vitest **61 passed**; Vite production build PASS (**1860 modules**, 17.49s).
- PowerShell/runbook/Nginx contracts are included in the 241-test focused gate; commands parse offline and fake SCM/NSSM/PM2/Nginx executables are never invoked.
- IDE diagnostics: no issues in all changed core operations/API/test/runbook/Nginx files. `git diff --check`: PASS.
- Property tests used the default Hypothesis seed; no counterexample was produced.

## Lifecycle and drill outcomes

Cold start enforces Backend → readiness → validated Nginx → final gates. Planned stop uses Nginx (30s) → Backend (120s); forced stop persists an unclean marker and requires the full next startup gate. Restart waits 30s and quarantines after three attempts in ten minutes. Failed candidate acceptance returns to a schema-compatible LKG. Critical capacity blocks update. Failed certificate external verification restores/tests/reloads the active set. Monitoring retries failed delivery and exposes `INTEGRATION_UNAVAILABLE`. Restore Hold remains fail-closed through handoff and post-restore sign-off.

## Security and prohibited-technology audit

Runtime/source audit found no Docker, Kubernetes, container runtime, external queue, cloud deployment, public Uvicorn, multi-worker, dev server/watcher, automatic Restore, or production execution instruction. The only `--reload` source match is the validator that rejects it; the only Restore auto-start match is documentation that prohibits it. Secret assignment canaries including generic `secret`, client-secret, and access-key forms are rejected. `.env` was not read.

## Limitations

No production deployment or host adapter was exercised; no actual service/task was installed or reconfigured; no production Nginx/certificate was tested or reloaded; no public network was used; no real MT5 terminal/account was validated; no real order was sent. Host ACL, firewall, Event Log, Windows Update, external monitoring, and certificate observations remain operator-run checks on the target VPS. These limitations do not weaken the offline contracts but must be completed during an authorized native-host rollout.
