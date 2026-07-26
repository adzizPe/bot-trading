# Native Windows service operations runbook

This is the primary Milestone 10.8 operator runbook for a native Windows VPS. It is a reviewed operating contract and planning artifact; it does **not** assert that any production host, service, certificate, account, firewall, monitor, or deployment is configured. Repository wrappers are offline `PLAN`/PowerShell `WhatIf` tools by default. They do not install, start, stop, restart, reboot, update, roll back, restore, reload, or connect to a host. Execution requires a separately implemented and security-reviewed host adapter, an approved deployment/change record, two-person authorization where specified, and host-local validation. This repository supplies no production execution adapter.

All lifecycle probes and smoke checks are read-only. They must never connect MT5, start Demo Execution or the paper engine, replay operator intent, or call order check/send/close/modify/cancel. The **Trading-Safe State** is: MT5 disconnected, Demo Execution stopped, paper engine stopped, and zero broker mutation caused by service operations.

## 1. Execution boundary, roles, and command convention

- **Preconditions:** Approved native-Windows scope, named non-production or production target, sanitized change/evidence ID, and confirmation that no repository `.env` or secret value will be read or copied.
- **Authorized roles:** Operations Operator prepares plans; distinct Operations Reviewer approves execution; Security Operator approves ACL/certificate/hardening changes; Recovery Operator owns Milestone 10.7 restore.
- **State and timeouts:** Wrapper output must remain `mode=PLAN`; wrapper bounds are 1–300 seconds and monitor probes 1–5 seconds. No host state changes in this repository workflow.
- **Pass/fail:** Pass only when one sanitized JSON line exits 0, names the expected operation, and contains no secret. Any malformed output, non-zero exit, missing review, or `EXECUTE` result without a reviewed adapter is fail-closed.
- **Escalation/rollback:** Quarantine unsafe evidence and stop. There is nothing to roll back from PLAN; unexpected mutation is a security incident.
- **Trading-Safe State:** Required before and after every procedure; PLAN must report zero broker mutation.
- **Evidence:** Command text with placeholders, UTC time, wrapper exit/status/mode, operator/reviewer IDs, target classification, and adapter/deployment approval status; never store environment dumps, tokens, keys, cookies, credentials, or unrestricted paths.
- **Traceability:** Requirements 1.6, 4.6–4.8, 13.7–13.8, 14.2–14.4, 15.20, 15.22, 15.25.

Run examples from the repository root. Omitting `-Execute` is the canonical offline PLAN. `-Execute -WhatIf` must also remain PLAN, but is shown only to test wrapper behavior:

```powershell
$OpsRoot = 'D:\Ops\XauUsdTradingBot'
.\scripts\Test-NativePreflight.ps1 -Root $OpsRoot -ProcessManager NSSM -ReleaseId '<release-id>'
.\scripts\Start-NativeOperations.ps1 -Root $OpsRoot -ProcessManager NSSM -ReleaseId '<release-id>' -Execute -WhatIf
```

Never treat a passing plan as host readiness or deployment approval.

## 2. Topology, setup, ownership, dependency, and protected configuration

- **Preconditions:** Verified immutable release set, dedicated non-admin service identities, protected state/config root outside the release, Vite `frontend/dist`, native Nginx, and one explicitly selected process manager.
- **Authorized roles:** Operations Operator drafts; Operations Reviewer and Security Operator approve identity, ACL, listener, and manager selection.
- **State and timeouts:** Setup remains offline PLAN. Backend shutdown bound is 120 seconds; edge drain/stop bound is 30 seconds; restart delay is at least 30 seconds with at most 3 attempts in 10 minutes.
- **Pass/fail:** Pass when definitions prove exactly one owner and backend process, one Uvicorn worker, loopback `127.0.0.1:8000`, matching release/Vite dist, edge dependency on backend, and Nginx-only public exposure. Mixed or duplicate ownership fails.
- **Escalation/rollback:** Reject candidate definitions and retain the last reviewed set. Never use a direct/public Uvicorn listener as fallback.
- **Trading-Safe State:** Service definitions contain no MT5 connect, Demo/Paper start, recovery, restore, migration, or broker-mutation action.
- **Evidence:** Selected manager, executable/arguments/working directory, identities, dependency, startup mode, timeout/restart policy, protected metadata source, log destinations, dist/release identity, process/listener inventory, and reviewer decision.
- **Traceability:** Requirements 2.1–2.8, 3.1–3.3, 12.4–12.9, 13.1–13.8, 14.1–14.5.

Canonical selection is **NSSM** with SCM ownership: `TradingBotBackend` uses release venv `backend\.venv\Scripts\python.exe`, working directory `backend`, and exact arguments `-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`; `TradingBotNginx` uses native `nginx.exe`, serves the same release's `frontend/dist`, and depends on `TradingBotBackend`. Backend startup may be automatic; edge publication remains readiness-gated. Protected environment metadata is under the restricted operations state root, not a release or command argument. Nginx alone owns public HTTP/HTTPS; API and WebSocket proxy to loopback.

PM2 is a mutually exclusive approved alternative, never a concurrent fallback. A host decision must select PM2 for both managed processes, fork mode, `instances=1`, no cluster/watch/reload, explicit venv interpreter, the same paths/identities/dependency semantics, and bounded restart policy. NSSM, PM2, Task Scheduler, startup folders, and operator shells must not share ownership of either process.

```powershell
.\scripts\Initialize-NativeOperations.ps1 -Root $OpsRoot -ProcessManager NSSM -ReleaseId '<release-id>' -EvidenceId '<evidence-id>'
# Alternative review only; use instead of NSSM, never together.
.\scripts\Initialize-NativeOperations.ps1 -Root $OpsRoot -ProcessManager PM2 -ReleaseId '<release-id>' -EvidenceId '<evidence-id>'
```

## 3. Preflight and ordered start

- **Preconditions:** Restore Hold released by valid evidence; account, ACL, executable, working directory, release identity, database location/readability, protected required non-trading configuration, certificate, capacity, Vite dist, and Last-Known-Good (LKG) references validated.
- **Authorized roles:** Operations Operator; Operations Reviewer required for first production acceptance or post-restore start.
- **State and timeouts:** Backend starts first and must reach authoritative readiness within 120 seconds; end-to-end cold boot must finish within 300 seconds; edge starts only after backend and Nginx candidate gates pass.
- **Pass/fail:** Pass requires one backend process/worker, loopback `GET /api/v1/health/readiness` returning ready through direct loopback and Nginx proxy, valid release/lease/database/trading-safe fields, then HTTPS `/healthz`, static, read-only API, and WebSocket handshake. `/healthz` alone never passes startup.
- **Escalation/rollback:** On backend failure keep edge unpublished; on edge validation failure retain the last valid edge config and keep backend loopback-only; alert and require explicit operator remediation/full gate.
- **Trading-Safe State:** Readiness is compatible with disconnected MT5 and stopped engines and must prove zero lifecycle broker mutation.
- **Evidence:** Preflight checks, process/listener count, release identity, readiness payload/status/header, edge validation, `/healthz` labeled Edge Liveness, smoke results, elapsed time, alert/final decision.
- **Traceability:** Requirements 3.1–3.12, 4.1–4.10, 14.4–14.5.

```powershell
.\scripts\Test-NativePreflight.ps1 -Root $OpsRoot -ProcessManager NSSM -ReleaseId '<release-id>' -TimeoutSeconds 120 -EvidenceId '<evidence-id>'
.\scripts\Start-NativeOperations.ps1 -Root $OpsRoot -ProcessManager NSSM -ReleaseId '<release-id>' -TimeoutSeconds 120 -EvidenceId '<evidence-id>'
```

`/healthz` is static **Edge Liveness** only. Authoritative **Backend Readiness** is exact `/api/v1/health/readiness`, first over loopback and then through Nginx, with `X-Backend-Readiness: authoritative`; neither endpoint authorizes trading.

## 4. Planned stop, restart, and reboot

- **Preconditions:** Approved maintenance/change record; operator has explicitly stopped Demo/Paper and disconnected MT5 through existing authenticated flows; read-only proof of Trading-Safe State; no Restore Hold ambiguity.
- **Authorized roles:** Operations Operator; distinct Reviewer for reboot or an unclean shutdown decision.
- **State and timeouts:** Stop accepting new edge traffic, drain/stop edge within 30 seconds, then gracefully stop backend within 120 seconds. Restart/reboot runs the full 120/300-second Startup Gate afterward.
- **Pass/fail:** Clean pass means no backend process, port-8000 listener, or runtime lease owner remains. Any timeout is `UNCLEAN`, never silently successful, and forces the complete next-start gate.
- **Escalation/rollback:** If safe state cannot be proven, block stop/reboot and escalate. If a timeout occurs, preserve logs/event references, do not infer broker state, and require incident review before startup.
- **Trading-Safe State:** Shutdown never closes/modifies/cancels broker state; reboot never reconnects MT5 or resumes engines.
- **Evidence:** Change, operator/reviewer, edge/backend stop order, drain/graceful timing, clean/unclean classification, stale-process/listener/lease check, post-start readiness/liveness, and zero broker mutation.
- **Traceability:** Requirements 5.1–5.10, 14.1, 14.4–14.5.

```powershell
.\scripts\Stop-NativeOperations.ps1 -Root $OpsRoot -ProcessManager NSSM -TimeoutSeconds 120 -EvidenceId '<evidence-id>'
.\scripts\Restart-NativeOperations.ps1 -Root $OpsRoot -ProcessManager NSSM -ReleaseId '<release-id>' -TimeoutSeconds 120 -EvidenceId '<evidence-id>'
.\scripts\Invoke-NativeReboot.ps1 -Root $OpsRoot -ProcessManager NSSM -ReleaseId '<release-id>' -TimeoutSeconds 300 -EvidenceId '<evidence-id>'
```

The reboot wrapper only produces a plan; it does not invoke a reboot facility.

## 5. Native update and rollback

- **Preconditions:** Approved change/window, distinct operator/reviewer, candidate and LKG immutable release IDs, tests/venv/Vite dist/Nginx/config/certificate/capacity/readiness baseline, recovery `AVAILABLE` with `rpo_met=true`; migration additionally requires local `VALID`, off-host `VERIFIED`, and all writers offline.
- **Authorized roles:** Release Operator and distinct Release Reviewer; Recovery Operator joins only for a separate Milestone 10.7 decision.
- **State and timeouts:** Planned stop order; candidate acceptance window at most 10 minutes; backend readiness 120 seconds and full publication 300 seconds.
- **Pass/fail:** Candidate backend and Vite dist identities must match and pass readiness, Trading-Safe State, Nginx validation, HTTPS/static/read-only API/WebSocket smoke. Incompatible revision or failed migration keeps backend offline.
- **Escalation/rollback:** Within the decision window, atomically select the full LKG set only if database-compatible, then repeat all gates. Never perform automatic downgrade, migration recovery, database Restore, direct Uvicorn publication, MT5 reconnect, or engine resume. Failed rollback remains offline with critical alert.
- **Trading-Safe State:** Required before shutdown and after candidate/LKG startup; all smoke calls are read-only and mutation count is zero.
- **Evidence:** Change/window, two people, old/new/LKG IDs, DB revision/compatibility, recovery/capacity/certificate status, stop classification, gates/smoke/timing, rollback decision/result, and final release state.
- **Traceability:** Requirements 7.1–7.16, 14.1, 14.4–14.5.

```powershell
.\scripts\Update-NativeOperations.ps1 -Root $OpsRoot -ProcessManager NSSM -ChangeId '<change-id>' -OperatorId '<operator-id>' -ReviewerId '<different-reviewer-id>' -ReleaseId '<candidate-id>' -LastKnownGood '<lkg-id>' -EvidenceId '<evidence-id>'
.\scripts\Rollback-NativeOperations.ps1 -Root $OpsRoot -ProcessManager NSSM -ChangeId '<change-id>' -OperatorId '<operator-id>' -ReviewerId '<different-reviewer-id>' -ReleaseId '<lkg-id>' -DatabaseRevision '<revision-id>' -EvidenceId '<evidence-id>'
```

## 6. Crash loop, backend failure, and Nginx failure

- **Preconditions:** Classified process failure, sanitized event references, current Restore Hold and release state known; no update/migration/restore in progress.
- **Authorized roles:** On-call Operations Operator; Reviewer required to release quarantine after restart-loop containment.
- **State and timeouts:** Wait at least 30 seconds; maximum 3 automatic attempts in rolling 10 minutes; critical alert within 5 minutes. Each backend attempt gets a full 120-second readiness gate.
- **Pass/fail:** Backend recovery passes only with exactly one owner/process/worker and full readiness/safe proof. Edge recovery passes only after config/certificate validation and proxied readiness. Report edge-up/backend-down separately from edge-down/backend-up.
- **Escalation/rollback:** At limit, quarantine automatic restart and require explicit operator start/full gate. Preserve last valid Nginx config. No recovery path may update, migrate, restore, expose Uvicorn publicly, or activate trading.
- **Trading-Safe State:** Re-established on every backend attempt; edge failure never changes broker state.
- **Evidence:** Failure category/time/exit, attempt window/delays/count, manager state, process/listener state, config result, split health states, alert delivery, quarantine/release, and zero broker mutation.
- **Traceability:** Requirements 6.1–6.10, 14.1, 14.4.

```powershell
.\scripts\Test-CrashLoop.ps1 -Root $OpsRoot -ProcessManager NSSM -Service TradingBotBackend -EvidenceId '<evidence-id>'
.\scripts\Invoke-MonitoringCheck.ps1 -Root $OpsRoot -ProcessManager NSSM -Category BACKEND -TimeoutSeconds 5 -EvidenceId '<evidence-id>'
.\scripts\Invoke-MonitoringCheck.ps1 -Root $OpsRoot -ProcessManager NSSM -Category EDGE -TimeoutSeconds 5 -EvidenceId '<evidence-id>'
```

## 7. Windows Update maintenance

- **Preconditions:** Critical update assessed within 24 hours; approved application/exception within 7 days; supported Windows lifecycle, clean maintenance record, LKG, recovery/RPO/capacity/certificate checks, and explicit Trading-Safe State.
- **Authorized roles:** Windows/Operations Operator and distinct Reviewer; Security Operator approves a time-bounded exception.
- **State and timeouts:** Use the planned 30-second edge and 120-second backend stop bounds; after the separately administered host update/reboot, use the 300-second cold-start gate.
- **Pass/fail:** Pass when update identity and UTC clock are valid (drift no more than 60 seconds), no stale process/listener/lease remains pre-maintenance, and post-boot host/backend/edge/certificate/capacity/recovery/monitoring gates pass.
- **Escalation/rollback:** Mark stop timeout as unclean. Follow approved Windows rollback/exception ownership outside these wrappers; do not weaken firewall or publish Uvicorn. Keep application offline if post-boot gates fail.
- **Trading-Safe State:** Must be explicit before maintenance and restored after boot; process exit is not proof, and no trading state resumes.
- **Evidence:** Update identifier/severity, assessment/application or exception dates, change/reviewer, clean/unclean stop, clock, reboot timing, post-boot gates, and final decision.
- **Traceability:** Requirements 5.6, 12.1–12.3, 12.7–12.12, 14.1, 14.4.

Use the preflight, stop, reboot-plan, and start-plan wrappers from Sections 3–4. Actual Windows servicing is controlled by the approved host maintenance system and is not implemented by this repository.

## 8. Certificate monitoring, renewal, and rollback

- **Preconditions:** Daily host and external observation, approved hostname, active certificate backup reference, candidate chain/key supplied through protected channel, restrictive private-key ACL, and change record.
- **Authorized roles:** Certificate/Security Operator and distinct Reviewer.
- **State and timeouts:** Warning at 30 days or fewer; critical at 14 days or fewer. Candidate validation precedes graceful edge reload; external fingerprint/hostname/chain/expiry verification completes within 5 minutes.
- **Pass/fail:** Pass requires hostname, validity, chain, key pairing, private-key readability by edge identity only, Nginx config test, approved fingerprint, and external observation. Expired/mismatch/invalid/unreadable is critical.
- **Escalation/rollback:** Never reload an invalid candidate. If external verification fails, restore the last validated certificate set, revalidate, then perform the approved rollback reload; alert on any rollback failure.
- **Trading-Safe State:** Backend is not restarted; certificate work and probes have no trading permission or mutation.
- **Evidence:** Old/new fingerprints and expiry UTC, hostname/chain/key-pair/ACL/config result, reload and external/OCSP observations, rollback result, two-person sign-off—never private-key content or sensitive path.
- **Traceability:** Requirements 10.1–10.10, 14.1, 14.4, 14.11.

```powershell
.\scripts\Test-CertificateHealth.ps1 -Root $OpsRoot -ProcessManager NSSM -TimeoutSeconds 5 -EvidenceId '<evidence-id>'
.\scripts\Test-NginxConfig.ps1 -NginxRoot 'C:\nginx'
```

Both examples are validation-only; certificate installation and reload require the reviewed host adapter/change workflow.

## 9. Disk, recovery capacity, and log failure

- **Preconditions:** Inventory release, venv, dist, backend/Nginx logs, DB/WAL/SHM, local/work/forensic backup roots, and mounted off-host volume; allowlist managed rotated logs.
- **Authorized roles:** Operations Operator; Recovery Operator for retention; Reviewer for emergency capacity action.
- **State and timeouts:** Capacity every 5 minutes; warning at free `<=20%` or `<=10 GiB`, critical at `<=10%` or `<=5 GiB`. Rotation daily, retention 30 days or aggregate 5 GiB; warning at 80%, critical at full quota or two consecutive failures.
- **Pass/fail:** Pass when all roles are inventoried, update volumes are non-critical, owned rotation succeeds, local `VALID` and off-host `VERIFIED` status are distinguished, RPO is met, and drill is not stale beyond 31 days.
- **Escalation/rollback:** Stop update/recovery writes at critical capacity. Never ad-hoc delete active DB/WAL/SHM, unknown backup, forensic evidence, certificate, secret, active log, or unowned file. Use reviewed log allowlist or Milestone 10.7 retention dry-run only.
- **Trading-Safe State:** Capacity checks and cleanup plans are read-only/non-trading and never trigger service/trading actions.
- **Evidence:** Per-volume sanitized role/free percent/GiB, thresholds, quota/rotation history, recovery status, proposed/approved allowlisted action, operator/reviewer, and result.
- **Traceability:** Requirements 11.1–11.13, 14.1, 14.4.

```powershell
.\scripts\Test-CapacityHealth.ps1 -Root $OpsRoot -ProcessManager NSSM -TimeoutSeconds 5 -EvidenceId '<evidence-id>'
.\scripts\Test-LogHealth.ps1 -Root $OpsRoot -ProcessManager NSSM -TimeoutSeconds 5 -EvidenceId '<evidence-id>'
.\scripts\Rotate-NginxLogs.ps1 -NginxRoot 'C:\nginx' -RetentionDays 30 -WhatIf
.\scripts\Invoke-BackupRetention.ps1 -DryRun
```

## 10. Monitoring delivery and synthetic alerts

- **Preconditions:** Read-only monitoring identity, vendor-neutral destination, sanitized schema, non-overlap lock, configured heartbeat, and no trading/service mutation permission.
- **Authorized roles:** Monitoring Operator; distinct on-call Reviewer records monthly synthetic test.
- **State and timeouts:** Edge/backend cadence at most 60 seconds, timeout at most 5 seconds; alert after 3 consecutive failures within 5 minutes of first failure; delivery heartbeat unavailable after 10 minutes; recovery watchdog every 15 minutes; synthetic warning/critical every 30 days.
- **Pass/fail:** Pass distinguishes host, manager, process count, Edge Liveness, proxied Backend Readiness, certificate, capacity, logs, recovery, tasks, and delivery. Expected disconnected/stopped trading remains healthy. Synthetic delivery must arrive within 5 minutes.
- **Escalation/rollback:** Treat delivery loss separately from service health; page through approved secondary human channel. Monitoring never remediates, restarts, updates, rolls back, deletes retention data, restores, or invokes trading.
- **Trading-Safe State:** All probes are read-only and lack trading credentials/permissions; readiness does not require MT5.
- **Evidence:** Probe category/timestamp/latency/status, consecutive count, sanitized release identity, alert/heartbeat delivery IDs and timing, synthetic scenario/result, and no secret/session/path/stack data.
- **Traceability:** Requirements 9.1–9.12, 14.1, 14.4.

```powershell
.\scripts\Invoke-MonitoringCheck.ps1 -Root $OpsRoot -ProcessManager NSSM -Category EDGE -TimeoutSeconds 5 -EvidenceId '<evidence-id>'
.\scripts\Invoke-MonitoringCheck.ps1 -Root $OpsRoot -ProcessManager NSSM -Category BACKEND -TimeoutSeconds 5 -EvidenceId '<evidence-id>'
.\scripts\Invoke-MonitoringCheck.ps1 -Root $OpsRoot -ProcessManager NSSM -Category DELIVERY -TimeoutSeconds 5 -EvidenceId '<evidence-id>'
.\scripts\Get-BackupStatus.ps1
```

## 11. Hardening, listeners, firewall, ACL, and secret lifecycle

- **Preconditions:** Supported Windows baseline, named identities/source networks, listener/port approvals, encrypted protected storage, secret inventory metadata without values, and current update/malware/firewall/audit/time state.
- **Authorized roles:** Security Operator and distinct Reviewer; secret owner participates in rotation/revocation; Operations Operator supplies read-only evidence.
- **State and timeouts:** Review before acceptance and every 90 days; clock drift over 60 seconds warns and blocks update completion; hardening exception expires within 30 days with owner/control/reviewer.
- **Pass/fail:** Pass requires default-deny inbound, only approved HTTP/HTTPS/admin access, backend/status/database/manager/recovery non-public, every listener owned/approved, non-admin non-interactive service identities, debug/docs/dev watcher/reload off, audit retention at least 30 days, and least-privilege NTFS ACLs.
- **Escalation/rollback:** Block acceptance on unapproved listener, stale update, excessive clock drift, broad ACL, or secret leak. Revoke exposed material via approved secret facility, quarantine evidence/logs, restore last reviewed ACL, and investigate; never place replacement values in argv, repository, service/task definitions, logs, or evidence.
- **Trading-Safe State:** Hardening and secret workflows do not grant trading permission or start/connect engines; missing MT5 credential remains disconnected, while missing required startup secret is sanitized not-ready.
- **Evidence:** Listener owner/purpose/approval, firewall/admin source, update/time/malware/audit status, identity rights and ACL matrix, secret owner/consumer/version lifecycle timestamps/status (not value), rotation/revocation result, exception expiry, and two-person decision.
- **Traceability:** Requirements 12.1–12.12, 13.1–13.14, 14.1, 14.4.

```powershell
.\scripts\Test-HostHardening.ps1 -Root $OpsRoot -ProcessManager NSSM -TimeoutSeconds 5 -EvidenceId '<evidence-id>'
```

Secret rotation sequence is: inventory dependencies by metadata; provision new version through the approved Windows-protected channel; verify consumer ACL; use planned restart only if required; prove sanitized readiness; revoke old version; retain audit metadata. Recovery-key rotation remains governed by [Milestone 10.7 recovery](windows-sqlite-recovery.md#encryption-key-lifecycle). Quarterly ACL review confirms Backend, Edge, Recovery, Monitoring, and human roles remain separated.

## 12. Restore Hold handoff to Milestone 10.7

- **Preconditions:** Incident/change and backup ID, distinct Recovery Operator/Reviewer, durable Restore Hold, edge/backend and every SQLite writer proven offline, and protected historical key available through the Milestone 10.7 channel.
- **Authorized roles:** Operations Operator enters/maintains hold; Recovery Operator performs the manual recovery runbook; distinct Reviewer signs; no scheduled or monitoring identity may restore.
- **State and timeouts:** Restore Hold remains fail-closed through dry-run, forensic preservation, replacement, integrity/revision/smoke/post-check, including every failure. RTO objective remains Milestone 10.7's 2 hours; service startup is outside restore.
- **Pass/fail:** Handoff passes only with writer/process/listener/lease-offline proof. Hold release requires complete successful or explicitly remediated Milestone 10.7 evidence and two-person sign-off; restore output alone is not release authorization.
- **Escalation/rollback:** On any ambiguity/failure keep hold active and services offline. Recovery rollback/remediation is a manual Milestone 10.7 decision; do not retry by manipulating DB/WAL/SHM and do not auto-start either service.
- **Trading-Safe State:** MT5, Demo, and Paper remain stopped throughout and after restore. First post-restore start is separately approved, manual, and runs the full Startup Gate.
- **Evidence:** Change/restore/backup IDs, hold state, all-writer offline proof, links to dry-run/forensic/integrity/revision/smoke/post-check evidence, two distinct signers, hold release, manual start gate, and zero broker mutation.
- **Traceability:** Requirements 8.1–8.10, 14.1, 14.6; semantics owned by [Milestone 10.7 Native Windows SQLite Backup and Recovery](windows-sqlite-recovery.md).

```powershell
.\scripts\Enter-RecoveryHandoff.ps1 -Root $OpsRoot -ProcessManager NSSM -ChangeId '<change-id>' -OperatorId '<operator-id>' -ReviewerId '<different-reviewer-id>' -RestoreId '<restore-id>' -EvidenceId '<evidence-id>'
.\scripts\Get-RestoreHoldStatus.ps1 -Root $OpsRoot -ProcessManager NSSM -EvidenceId '<evidence-id>'
.\scripts\Release-RestoreHold.ps1 -Root $OpsRoot -ProcessManager NSSM -ChangeId '<change-id>' -OperatorId '<operator-id>' -ReviewerId '<different-reviewer-id>' -RestoreId '<restore-id>' -EvidenceId '<evidence-id>'
```

These are handoff/release plans only. Follow the linked Milestone 10.7 runbook exactly; this document neither duplicates nor changes restore semantics.

## 13. Native disaster recovery

- **Preconditions:** Declared disaster, clean supported Windows VPS, verified LKG release and off-host backup ID, approved native Nginx/certificate, protected secret/key channels, firewall/accounts/ACL/time/malware baseline, and Restore Hold.
- **Authorized roles:** Incident Commander, Recovery Operator, Operations Operator, Security Operator, and distinct Recovery Reviewer; cutover requires two-person approval.
- **State and timeouts:** Build and verification remain offline until Milestone 10.7 restore/post-check/sign-off succeeds; then manual 120-second backend and 300-second full Startup Gate. DNS/public cutover is last.
- **Pass/fail:** Pass requires verified release/dist identity, one loopback worker/process, Nginx-only edge, certificate/fingerprint, recovery integrity/revision, proxied readiness, `/healthz` Edge Liveness, monitoring, capacity, and Trading-Safe State.
- **Escalation/rollback:** Failed baseline/restore/gate keeps the new host offline and hold active; preserve the prior target and DNS. Use LKG or manual recovery decision—never automatic schema downgrade/restore or public backend fallback.
- **Trading-Safe State:** Disaster recovery never reconciles or activates broker state; MT5/Demo/Paper remain stopped after cutover until a separate authenticated decision.
- **Evidence:** Disaster/change, clean-host baseline, source hashes/IDs, off-host receipt/backup ID, certificate fingerprint, protected-channel attestations, restore evidence link, process/listeners/gates/timings, monitoring, cutover/rollback, and two-person final decision.
- **Traceability:** Requirements 1.5–1.6, 8.1–8.10, 12.1–12.12, 14.1, 14.3, 14.6.

Use only native Windows release, venv, Vite dist, Nginx, selected NSSM (or exclusively approved PM2), and Milestone 10.7 recovery. Packaging virtualization, external queues, second proxies, and provider-specific deployment automation are outside and prohibited by this runbook.

## 14. Operator Evidence Package and 180-day retention

- **Preconditions:** Unique event/evidence ID, approved protected evidence root, restrictive ACL to named operators/reviewers/auditors, UTC clock, and sanitization policy.
- **Authorized roles:** Event Operator creates; a distinct Reviewer signs update, rollback, certificate renewal, hardening exception, and post-restore release; Evidence Custodian controls retention.
- **State and timeouts:** Assemble immediately after the event; retain at least 180 days unless a longer incident/legal hold applies. Stale, incomplete, inconsistent, unsigned, or secret-bearing package cannot be approved.
- **Pass/fail:** Pass requires all mandatory fields, cross-consistent timestamps/IDs/statuses, references rather than raw logs, zero broker mutation, and required two-person separation. Sanitization failure rejects and quarantines the package.
- **Escalation/rollback:** Withdraw sign-off, quarantine unsafe material, revoke leaked secret if applicable, regenerate from sanitized references, and escalate missing evidence; operational state remains last safely proven state.
- **Trading-Safe State:** Package states MT5/Demo/Paper status and `broker_mutation_count=0`; evidence collection itself is read-only.
- **Evidence:** The package is the evidence; record custody, ACL review, retention start/expiry, hold status, operator/reviewer signatures, and final `APPROVE`, `REJECT`, or `REMAIN_OFFLINE` decision.
- **Traceability:** Requirements 14.7–14.12, 16.2–16.6.

Mandatory package fields:

1. event/change/evidence IDs, category, target classification, UTC start/end/duration;
2. operator and distinct reviewer identities/roles/sign-off UTC;
3. old/new/candidate/LKG release IDs, DB revision, selected process manager;
4. service definitions, dependency/order, process/worker/listener counts and ownership;
5. preflight, Backend Readiness loopback/proxied, Edge Liveness `/healthz`, static/API/WebSocket read-only smoke results;
6. Trading-Safe State and explicit zero broker mutation;
7. certificate fingerprint/expiry, capacity/log status, recovery/RPO/off-host/drill status, monitoring/delivery status;
8. clean/unclean state, timeout/attempts/alerts, rollback/escalation and final decision;
9. sanitized test/command exit summaries and immutable event/time-range references to logs—not raw logs;
10. sanitization result, ACL/custody, `retention_days >= 180`, expiry, and legal/incident hold.

Final decision template:

```text
Decision: APPROVE | REJECT | REMAIN_OFFLINE
Evidence-ID: <safe-id>
Operator: <named-id>  Reviewer: <different-named-id>
Release/LKG/Revision: <ids>
Backend-Readiness: <loopback-result>; <proxied-result>
Edge-Liveness-/healthz: <result>
Trading-Safe-State: PASS  Broker-Mutation-Count: 0
Rollback-or-Escalation: <reference>
Retention-Days: 180  ACL-Review: PASS
Signed-UTC: <timestamp>
```

## 15. Isolated 90-day runbook drill

- **Preconditions:** Isolated native Windows sandbox, generated database/secret/certificate, fake service manager/monitor/edge, no production database/secret/account/network, and approved drill record.
- **Authorized roles:** Drill Operator and distinct Drill Reviewer; Security/Recovery observers as scenarios require.
- **State and timeouts:** At least every 90 days; use normal 120/300-second lifecycle, 30-second restart delay, 3-in-10-minute containment, and 5-minute alert/certificate verification objectives with fake clock where supported.
- **Pass/fail:** All eight scenarios pass independently: cold boot; backend crash; edge crash; failed-update LKG rollback; monitoring warning/critical delivery; certificate candidate failure/rollback; critical capacity update block; and Restore Hold handoff with no automatic post-restore start. Any missing evidence or unexpected mutation fails the drill.
- **Escalation/rollback:** Keep sandbox isolated, file corrective action with owner/due date, and repeat failed scenario before production sign-off. Never substitute an active host or data.
- **Trading-Safe State:** No MT5 connection, Demo Execution, paper engine, order, broker credential, or production endpoint; assert zero broker mutation in every scenario.
- **Evidence:** Generated fixture IDs/fingerprints (not values), scenario inputs/timings/gates/alerts/rollback, fake ownership/process/listeners, command/test results, operator/reviewer, corrective actions, and next due UTC.
- **Traceability:** Requirements 14.13–14.14, 15.22–15.25.

Drill checklist: prove cold-start ordering/readiness split; reject second backend; contain backend restart loop; preserve loopback backend on edge loss; reject invalid Nginx/certificate candidate; roll back complete release set after candidate smoke failure; distinguish monitor delivery loss; block update on critical capacity; keep Restore Hold stopped through handoff and require the manual first post-restore start; validate package redaction/retention/two-person sign-off. The Milestone 10.7 isolated restore drill remains separate and unchanged.

## 16. Completion and traceability gate

- **Preconditions:** Sections 1–15 reviewed, links resolve, examples parse offline, focused and prior operations tests pass, and no production action was performed.
- **Authorized roles:** Documentation Maintainer and distinct Release Reviewer.
- **State and timeouts:** Documentation gate is fail-closed; test timeout follows the local test harness and does not alter hosts.
- **Pass/fail:** Pass requires coverage of setup/preflight/start/stop/restart/reboot/update/rollback/crash/backend/Nginx/Windows Update/certificate/disk/log/monitoring/hardening/secret/ACL/Restore Hold/disaster/evidence/drill, consistent readiness terminology, and prohibited-boundary checks.
- **Escalation/rollback:** Correct documentation/tests before progression; revert only the documentation candidate if contracts regress. Do not relax Milestone 10.7 or operational safety semantics.
- **Trading-Safe State:** Validation uses static parsing, fake adapters, generated data, and no broker/production connection.
- **Evidence:** Changed files, Ruff/test commands and results, skipped-test reasons, link/PowerShell parse results, limitations, reviewer, and final decision.
- **Traceability:** Task group 12.1, 12.2, 12.3, 12.4, 12.5, and 12.6; Requirements 14.1–14.14, 15.20, 15.22–15.25, 16.1–16.6.

Related details: [Nginx production edge runbook](windows-nginx.md), [Milestone 10.7 SQLite recovery runbook](windows-sqlite-recovery.md), and [Milestone 10.8 validation evidence](milestone-10.8-validation.md).
