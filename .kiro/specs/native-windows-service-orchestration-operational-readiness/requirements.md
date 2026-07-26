# Requirements Document

## Introduction

**Milestone 10.8 — Native Windows Service Orchestration and Operational Readiness** menutup blocker operasional yang tersisa setelah Milestone 10.7 dengan menetapkan orchestration service native Windows, lifecycle yang aman, update/rollback, monitoring, certificate dan capacity operations, host hardening, least privilege, runbook, serta operator evidence.

Milestone ini tidak mendesain ulang Milestone 10.1–10.7. Backend tetap dijalankan langsung dari Python virtual environment dengan FastAPI/Uvicorn dan tepat satu worker. Frontend tetap dibangun oleh Vite ke `frontend/dist` dan dilayani Nginx native. Nginx tetap menjadi satu-satunya entry point publik untuk frontend, REST API, dan WebSocket. Docker, container, external queue, dan workflow khusus cloud tetap dilarang.

Backend dan Nginx boleh autostart. Autostart, reboot, crash recovery, update, rollback, health probe, dan monitoring tidak boleh menghubungkan MT5, memulai Demo Execution, memulai paper engine, atau menyebabkan broker mutation. Restore Milestone 10.7 tetap manual, offline, dan tidak pernah memulai service secara otomatis.

## Source of Truth

- #[[file:../../../README.md]]
- #[[file:../../../.kiro/steering/deployment-policy.md]]
- #[[file:../../../docs/deployment/windows-nginx.md]]
- #[[file:../../../docs/deployment/windows-sqlite-recovery.md]]
- Kontrak source dan test Milestone 10.1–10.7 di repository.

## Glossary

- **Backend Service**: Satu process FastAPI/Uvicorn dari Python virtual environment, tepat satu worker, bind hanya ke loopback.
- **Edge Service**: Nginx native yang melayani Vite dist dan mem-proxy REST API serta WebSocket.
- **Process Manager**: NSSM atau PM2 yang dipilih secara eksplisit; satu process tidak boleh dimiliki keduanya.
- **Backend Readiness**: Bukti startup backend selesai, runtime lease diperoleh, database dapat dibaca, dan request loopback berhasil; tidak mensyaratkan MT5 atau engine trading aktif.
- **Edge Liveness**: Bukti Nginx menerima HTTPS dan dapat melayani respons edge. Static `/healthz` hanya membuktikan Edge Liveness.
- **Trading-Safe State**: MT5 disconnected, Demo Execution stopped, paper engine stopped, dan nol broker mutation akibat lifecycle service.
- **Startup Gate**: Pemeriksaan wajib sebelum service/release dinyatakan available.
- **Release Aktif**: Pasangan backend dan Vite dist dengan identitas release yang sama dan telah lulus seluruh gate.
- **Last-Known-Good Release**: Release terakhir dengan readiness, smoke test, dan sign-off yang lulus.
- **Restore Hold**: Keadaan maintenance yang mencegah backend direstart selama restore Milestone 10.7.
- **External Monitoring**: Monitoring vendor-neutral dan read-only untuk host, service, readiness, certificate, capacity, backup, dan scheduled task.
- **Operator Evidence Package**: Bukti tersanitasi untuk setup, reboot, update, rollback, recovery, drill, atau perubahan operasional.

## Requirements

### Requirement 1: Scope dan deployment boundaries

**User Story:** Sebagai release reviewer, saya ingin blocker operasional ditutup tanpa mengubah fitur trading atau milestone lama.

#### Acceptance Criteria

1. THE system SHALL menggunakan repository dan kontrak Milestone 10.1–10.7 sebagai source of truth.
2. THE milestone SHALL dibatasi pada service orchestration, lifecycle, reboot, update, rollback, monitoring, certificate, capacity, hardening, least privilege, runbook, testing, dan evidence.
3. THE implementation SHALL NOT mengubah authentication/RBAC, MT5 isolation, WebSocket, Nginx hardening, atau backup/restore semantics existing.
4. THE implementation SHALL NOT mengubah Strategy, Risk, Paper, Backtest, Demo, Safety decision, atau broker execution semantics.
5. THE deployment SHALL tetap native pada Windows VPS tanpa Docker, container, external queue, atau tooling khusus cloud.
6. THE milestone SHALL NOT menjalankan deployment production atau mengirim broker order sebagai bagian dari implementasi dan validasinya.
7. IF requirement baru bertentangan dengan safety contract existing, THEN contract existing SHALL dipertahankan dan milestone SHALL dinyatakan belum lengkap.

### Requirement 2: Service topology dan single-process ownership

**User Story:** Sebagai operator, saya ingin setiap runtime resource memiliki satu owner yang deterministik.

#### Acceptance Criteria

1. THE Backend Service SHALL berjalan dari virtual environment existing dengan tepat satu Uvicorn worker dan satu process production instance.
2. THE Backend Service SHALL bind hanya ke loopback dan SHALL NOT dapat diakses langsung dari interface publik.
3. THE Edge Service SHALL menjadi satu-satunya entry point publik dan SHALL melayani Vite dist serta proxy API/WebSocket existing.
4. THE operator SHALL memilih NSSM atau PM2 sebagai Process Manager dan mencatat keputusan tersebut dalam evidence.
5. A managed process SHALL mempunyai tepat satu lifecycle owner dan SHALL NOT dikelola bersamaan oleh NSSM, PM2, Task Scheduler, startup folder, atau invocation kedua.
6. EACH service definition SHALL menetapkan executable, argument, working directory, environment source, startup mode, shutdown timeout, restart policy, dan log destination secara eksplisit.
7. THE system SHALL NOT membuat MT5 terminal, Demo Execution, paper engine, restore, atau restore drill sebagai service autostart terpisah.
8. IF jumlah Backend Service process bukan tepat satu, THEN Startup Gate SHALL gagal.

### Requirement 3: Startup ordering dan authoritative readiness

**User Story:** Sebagai operator, saya ingin service dimulai berdasarkan dependency dan readiness backend yang nyata.

#### Acceptance Criteria

1. WHEN cold boot atau manual start dimulai, THE system SHALL memvalidasi account, executable, working directory, release identity, database location, configuration, permission, dan required non-trading secret sebelum startup.
2. WHEN preflight lulus, THE system SHALL memulai Backend Service sebelum Edge Service.
3. THE Backend Service SHALL mencapai Backend Readiness atau terminal startup failure dalam waktu paling lama 120 detik.
4. Backend Readiness SHALL memerlukan application startup selesai, runtime lease tunggal, database read probe, dan loopback request yang berhasil.
5. Backend Readiness SHALL dapat berstatus ready ketika MT5 disconnected, Demo Execution stopped, dan paper engine stopped.
6. Readiness probe SHALL NOT memanggil MT5 connect, Demo/Paper start, `order_check`, `order_send`, close, modify, atau cancel broker.
7. IF Backend Readiness gagal, THEN cold-start Edge Service SHALL tidak dipublikasikan dan operational alert SHALL dihasilkan.
8. BEFORE Edge Service start/reload, THE system SHALL memvalidasi Vite dist, Nginx config, certificate chain, private-key access, dan proxy target.
9. IF Nginx candidate validation gagal, THEN konfigurasi terakhir yang valid SHALL dipertahankan dan candidate SHALL tidak dipublikasikan.
10. WHEN dependency sehat, end-to-end cold boot SHALL mencapai Backend Readiness dan Edge Liveness dalam waktu paling lama 300 detik.
11. Static Nginx `/healthz` SHALL NOT digunakan sebagai Backend Readiness, update success, rollback success, reboot success, atau release completion gate.
12. Startup Gate SHALL memverifikasi backend melalui loopback dan melalui proxy Nginx sebelum release dinyatakan available.

### Requirement 4: Trading-safe autostart

**User Story:** Sebagai operator trading, saya ingin backend dan Nginx dapat autostart tanpa mengaktifkan trading.

#### Acceptance Criteria

1. EVERY backend startup, reboot, crash recovery, update, rollback, dan post-restore manual start SHALL menghasilkan Trading-Safe State.
2. Persisted Demo Execution state SHALL diinisialisasi menjadi stopped tanpa broker call.
3. Persisted paper engine state SHALL diinisialisasi menjadi stopped dan scheduler SHALL tidak berjalan.
4. MT5 SHALL tetap disconnected sampai operator terautentikasi dengan permission existing meminta connect secara eksplisit.
5. Demo Execution dan paper engine SHALL tetap stopped sampai operator terautentikasi dengan permission existing meminta start secara eksplisit.
6. Service definitions, scripts, probes, monitoring, update, rollback, dan restore SHALL NOT memanggil mutation endpoint trading.
7. Lifecycle-only operation SHALL menghasilkan nol `order_check`, `order_send`, broker close, modify, dan cancel.
8. IF Trading-Safe State tidak dapat dibuktikan, THEN Backend Readiness SHALL gagal.
9. Operator action setelah startup SHALL tetap tunduk pada authentication, CSRF, RBAC, demo-account guard, idempotency, connector mutation gate, dan Safety Layer existing.
10. THE system SHALL NOT menyimpan atau replay operator mutation melewati restart atau reboot.

### Requirement 5: Planned shutdown dan reboot

**User Story:** Sebagai operator, saya ingin shutdown/reboot tidak meninggalkan runtime atau trading state ambigu.

#### Acceptance Criteria

1. Planned shutdown runbook SHALL meminta operator menghentikan Demo/Paper dan disconnect MT5 melalui flow existing sebelum service stop.
2. THE workflow SHALL memverifikasi Trading-Safe State sebelum melanjutkan planned shutdown.
3. THE Edge Service SHALL berhenti menerima traffic baru sebelum Backend Service dihentikan.
4. THE Edge Service SHALL diberi bounded drain timeout maksimum 30 detik.
5. THE Backend Service SHALL diberi graceful shutdown timeout maksimum 120 detik untuk WebSocket, backtest lifecycle, scheduler, MT5 disconnect, database connection, dan runtime lease cleanup.
6. IF timeout terlewati, THEN shutdown SHALL dicatat unclean dan full Startup Gate SHALL diwajibkan pada start berikutnya.
7. Shutdown lifecycle SHALL NOT mengirim order, auto-close position, modify stop, atau cancel broker order.
8. WHEN shutdown selesai, tidak boleh ada stale backend process, listener, atau runtime lease owner.
9. AFTER planned atau forced reboot, startup ordering dan Trading-Safe State SHALL diterapkan kembali.
10. Reboot evidence SHALL mencatat timing, process count, service state, readiness, dan zero broker mutation.

### Requirement 6: Crash recovery dan restart-loop containment

**User Story:** Sebagai operator, saya ingin crash dipulihkan secara bounded tanpa loop atau aktivasi trading otomatis.

#### Acceptance Criteria

1. AFTER unexpected exit, Process Manager SHALL menunggu sekurang-kurangnya 30 detik sebelum restart attempt.
2. Automatic restart SHALL dibatasi maksimum 3 attempt dalam rolling window 10 menit per service.
3. IF restart limit tercapai, THEN automatic restart SHALL berhenti dan critical alert SHALL dikirim dalam maksimum 5 menit.
4. EVERY backend restart SHALL mengulang full Backend Readiness dan Trading-Safe State checks.
5. A second Backend Service SHALL gagal memperoleh runtime lease dan SHALL tidak menjadi ready.
6. Edge-up/backend-down dan edge-down/backend-up SHALL dilaporkan sebagai kondisi berbeda.
7. IF Edge Service gagal, Backend Service SHALL tetap loopback-only dan tidak menjadi publik.
8. Invalid Nginx configuration SHALL tidak pernah direload.
9. Automatic recovery SHALL NOT menjalankan update, rollback, migration, restore, MT5 connect, Demo/Paper start, atau broker mutation.
10. AFTER restart-loop containment, service SHALL memerlukan operator start eksplisit dan full Startup Gate.

### Requirement 7: Native update dan rollback

**User Story:** Sebagai release operator, saya ingin update dan rollback native yang terukur dan fail-closed.

#### Acceptance Criteria

1. Update SHALL memerlukan change record, release identity, reviewer, maintenance window, rollback decision point, dan Last-Known-Good Release.
2. Update preflight SHALL memverifikasi release tests, venv, Vite dist, Nginx config, configuration availability, certificate, process count, readiness baseline, Trading-Safe State, recovery status, dan capacity.
3. IF migration diperlukan, update SHALL memerlukan backup `VALID`, off-host `VERIFIED`, `rpo_met=true`, dan writer offline.
4. IF migration tidak diperlukan, recovery status `AVAILABLE` dan `rpo_met=true` SHALL tetap diwajibkan.
5. ANY failed preflight SHALL menghentikan update sebelum active release, database, atau service definition diubah.
6. Update SHALL memakai planned shutdown ordering dan migration SHALL hanya berjalan ketika seluruh SQLite writer offline.
7. IF migration gagal, Backend Service SHALL tetap offline dan automatic downgrade SHALL dilarang.
8. Backend dan Vite dist SHALL berasal dari release identity yang sama.
9. Candidate release SHALL lulus Backend Readiness, Trading-Safe State, Nginx validation, HTTPS/static/API read-only/WebSocket smoke checks sebelum completion.
10. Smoke checks SHALL tidak menghubungkan MT5 atau memanggil broker mutation.
11. IF candidate gagal dalam bounded acceptance window 10 menit, THEN rollback SHALL dimulai atau service SHALL tetap offline sesuai change record.
12. Rollback SHALL mengembalikan backend, Vite dist, Nginx config, dan service definitions sebagai satu Last-Known-Good release set.
13. Rollback SHALL mengulang seluruh Startup Gate dan smoke checks.
14. IF database revision tidak kompatibel, THEN rollback SHALL berhenti offline dan meminta keputusan recovery manual; automatic restore/downgrade SHALL dilarang.
15. IF rollback gagal, Backend Service SHALL tetap offline dan critical alert SHALL dikirim.
16. Update/rollback evidence SHALL mencatat release lama/baru, backup, migration, gates, service state, timing, rollback, dan Trading-Safe State.

### Requirement 8: Restore hold dan boundary Milestone 10.7

**User Story:** Sebagai recovery operator, saya ingin orchestration tidak melanggar restore manual dan offline.

#### Acceptance Criteria

1. Restore SHALL tetap manual, offline, backup-ID-driven, dan operator-side sesuai Milestone 10.7.
2. Boot, crash, health failure, update failure, rollback failure, monitoring, dan scheduled task SHALL NOT memicu Restore.
3. Restore maintenance SHALL mengaktifkan Restore Hold yang mencegah automatic backend restart.
4. BEFORE recovery tooling berjalan, Backend Service dan seluruh SQLite writer SHALL terbukti berhenti.
5. WHILE Restore Hold aktif, Edge Service SHALL tidak mem-proxy ke backend offline dan Process Manager SHALL tidak memulai backend atau recovery writer lain.
6. IF dry-run, forensic preservation, replacement, integrity, revision, smoke, atau post-check gagal, THEN Restore Hold SHALL tetap aktif.
7. Successful maupun failed Restore SHALL NOT start/restart/reload Backend Service atau Edge Service.
8. Restore Hold SHALL hanya dilepas setelah evidence dan two-person sign-off Milestone 10.7 selesai.
9. First start setelah Restore SHALL manual dan SHALL menjalankan full Startup Gate serta Trading-Safe State.
10. Successful Restore SHALL NOT dianggap authorization untuk MT5 connect, Demo/Paper start, atau broker mutation.

### Requirement 9: External monitoring dan liveness/readiness separation

**User Story:** Sebagai operator on-call, saya ingin kegagalan host, edge, backend, dan recovery terdeteksi terpisah tanpa mutation.

#### Acceptance Criteria

1. External Monitoring SHALL memantau host, Process Manager, process count, Edge Liveness, Backend Readiness melalui Nginx, certificate, capacity, log rotation, backup status, dan scheduled tasks.
2. Edge dan backend checks SHALL berjalan sekurang-kurangnya setiap 60 detik dengan timeout maksimum 5 detik.
3. IF tiga check berturut-turut gagal, alert SHALL dikirim dalam maksimum 5 menit sejak kegagalan pertama.
4. `/healthz` SHALL dilabeli Edge Liveness saja.
5. Externally observed Backend Readiness SHALL melewati Nginx dan membuktikan respons berasal dari backend.
6. Monitoring payload SHALL dibatasi pada state, release identity, dan timestamp tersanitasi tanpa secret, account data, path sensitif, atau stack trace.
7. Backend disconnected/stopped trading subsystems SHALL tidak dianggap degraded ketika Trading-Safe State memang diharapkan.
8. Backup watchdog SHALL berjalan sekurang-kurangnya setiap 15 menit dengan warning pada umur 20 jam dan critical pada `rpo_met=false` atau umur 24 jam.
9. Monitoring delivery heartbeat yang hilang selama 10 menit SHALL menghasilkan integration-unavailable state.
10. Monitoring SHALL read-only dan SHALL NOT memiliki permission trading, service mutation, rollback, retention deletion, atau Restore.
11. Synthetic warning dan critical alert SHALL diuji setiap 30 hari dengan target delivery maksimum 5 menit.
12. IF monitoring mengekspos secret atau session data, THEN monitoring security gate SHALL gagal.

### Requirement 10: Certificate monitoring dan renewal

**User Story:** Sebagai operator, saya ingin kegagalan atau expiry HTTPS diketahui dan dapat di-rollback dengan aman.

#### Acceptance Criteria

1. Production certificate SHALL diperiksa dari host dan external HTTPS observation sekurang-kurangnya setiap 24 jam.
2. Certificate checks SHALL memverifikasi hostname, validity, chain, candidate private-key pairing, dan Nginx config test.
3. Remaining validity `<=30` hari SHALL menghasilkan warning dan `<=14` hari SHALL menghasilkan critical alert.
4. Expired, hostname mismatch, invalid chain, key mismatch, atau unreadable certificate SHALL menghasilkan critical alert.
5. Renewal runbook SHALL mencakup preflight, active-certificate backup, candidate install, ACL, chain validation, `nginx -t`, graceful reload, external verification, OCSP observation, dan rollback.
6. Failed candidate validation SHALL mempertahankan certificate set aktif yang valid dan SHALL tidak reload candidate.
7. Post-reload external verification SHALL membuktikan approved fingerprint, hostname, chain, dan expiry baru dalam maksimum 5 menit.
8. IF post-reload check gagal, THEN last validated certificate set SHALL dipulihkan dan diuji sebelum rollback reload.
9. Private key SHALL NOT masuk repository, arguments, logs, monitoring payload, atau evidence.
10. Renewal evidence SHALL mencatat old/new fingerprint, expiry UTC, validation/reload/external-check/rollback result tanpa private key.

### Requirement 11: Disk, log, dan recovery capacity

**User Story:** Sebagai operator, saya ingin kapasitas diketahui sebelum mengganggu database, log, update, atau recovery.

#### Acceptance Criteria

1. THE system SHALL menginventarisasi volume release, venv, Vite dist, Nginx/backend logs, SQLite DB/WAL/SHM, backup local/work/forensic, dan mounted off-host.
2. Capacity SHALL diperiksa sekurang-kurangnya setiap 5 menit.
3. Warning SHALL terjadi pada free space `<=20%` atau `<=10 GiB`, mana yang tercapai lebih dahulu.
4. Critical SHALL terjadi pada free space `<=10%` atau `<=5 GiB`, mana yang tercapai lebih dahulu.
5. Update preflight SHALL gagal ketika release atau database volume berada pada critical capacity.
6. Backup/restore capacity semantics Milestone 10.7 SHALL tetap fail-closed dan tidak boleh dilonggarkan.
7. Managed log rotation SHALL dipantau setiap 24 jam dengan retention 30 hari atau aggregate quota 5 GiB per host, kecuali audit retention terpisah.
8. Log usage `>=80%` quota SHALL warning; quota penuh atau dua rotation failure berturut-turut SHALL critical.
9. Rotation SHALL NOT menghapus active log, DB/WAL/SHM, backup, forensic evidence, certificate, secret, atau unowned file.
10. Monitoring SHALL membedakan local `VALID`, off-host `VERIFIED`, backup/copy failure, RPO breach, dan restore-drill failure.
11. Off-host belum verified setelah 24 jam atau restore drill lebih tua dari 31 hari/gagal SHALL menghasilkan critical recovery alert.
12. Critical capacity SHALL NOT memicu automatic deletion di luar managed log rotation dan GFS retention Milestone 10.7.
13. Disk-full runbook SHALL melarang ad-hoc deletion DB/WAL/SHM, unknown backup, forensic evidence, certificate, atau secret.

### Requirement 12: Windows host hardening dan network exposure

**User Story:** Sebagai security operator, saya ingin host mempunyai baseline hardening yang dapat diaudit.

#### Acceptance Criteria

1. Hardening checklist SHALL dijalankan sebelum production acceptance dan sekurang-kurangnya setiap 90 hari.
2. Windows SHALL berada pada supported security-update lifecycle.
3. Critical security update SHALL dinilai dalam 24 jam dan diterapkan atau memperoleh approved exception dalam 7 hari.
4. Windows Firewall SHALL default-deny inbound dan hanya membuka HTTP/HTTPS serta approved administrative access.
5. Backend port, Nginx status, database, Process Manager control, dan recovery tooling SHALL tidak dapat diakses publik.
6. Listener inventory SHALL mencatat owner/purpose/approval untuk setiap port; unapproved listener SHALL diblokir.
7. Host SHALL memiliki malware protection, firewall, security audit policy, dan UTC time synchronization aktif.
8. Clock drift lebih dari 60 detik SHALL menghasilkan warning dan SHALL memblokir update completion.
9. Production SHALL memakai debug off serta tidak menjalankan OpenAPI/docs, Vite dev, build watcher, Uvicorn reload, container runtime, atau external queue.
10. Event Log untuk service, Task Scheduler, authentication, dan administrative change SHALL memiliki retention sekurang-kurangnya 30 hari.
11. Remote administration SHALL dibatasi pada named operator accounts dan approved source network.
12. Failed hardening item SHALL memblokir production gate kecuali exception memiliki owner, compensating control, reviewer, dan expiry maksimum 30 hari.

### Requirement 13: Least privilege dan secret handling

**User Story:** Sebagai security operator, saya ingin account dan secret memiliki akses minimum dan tidak bocor melalui orchestration.

#### Acceptance Criteria

1. Backend, Edge, recovery task, monitoring reader, dan human operator SHALL memakai identity terpisah sesuai fungsi.
2. Backend dan Edge service accounts SHALL bukan local administrator, tidak memiliki interactive logon, dan hanya memiliki service/file permissions minimum.
3. Recovery account SHALL mempertahankan least-privilege boundaries Milestone 10.7 dan tidak memperoleh automatic service-start authority.
4. Monitoring identity SHALL read-only dan tidak memiliki permission trading, service reconfiguration, rollback, atau Restore.
5. Service accounts SHALL tidak dapat mengubah service definition sendiri.
6. NTFS ACL SHALL mencegah ordinary users membaca secret/private key atau mengubah release, config, database, backup catalog, recovery work, dan operational logs.
7. Production secrets SHALL NOT disimpan dalam repository, example values, release artifact, command arguments, service-manager command line, Task Scheduler arguments, logs, monitoring, crash output, atau evidence.
8. Runtime secret source SHALL dibatasi hanya untuk identity yang memerlukan dan tidak disalin ke release/evidence.
9. Missing startup secret SHALL menghasilkan sanitized not-ready; missing MT5 credential SHALL mempertahankan MT5 disconnected.
10. Missing backup key SHALL tetap fail-closed untuk recovery tetapi SHALL tidak menghalangi safe backend autostart.
11. Secret inventory SHALL mencatat owner, consumer, lifecycle timestamp, dan status tanpa nilai secret.
12. Secret rotation/revocation SHALL mempunyai runbook dan evidence tersanitasi.
13. Secret/ACL access review SHALL dijalankan setiap 90 hari.
14. IF output, log, status, error, artifact, atau evidence mengandung secret/session/environment dump, THEN security gate SHALL gagal.

### Requirement 14: Runbook dan Operator Evidence Package

**User Story:** Sebagai operator dan auditor, saya ingin operasi production dapat diulang dan dibuktikan.

#### Acceptance Criteria

1. Documentation SHALL menyediakan native Windows runbook untuk setup, start, stop, planned reboot, unplanned restart, update, rollback, crash loop, backend failure, Nginx failure, certificate renewal, disk full, log failure, monitoring failure, secret rotation, hardening review, dan restore handoff.
2. Runbooks SHALL memakai venv, single-worker Uvicorn, Vite dist, Nginx, selected NSSM/PM2, Task Scheduler existing, dan PowerShell native.
3. Runbooks SHALL NOT memperkenalkan container, external queue, cloud-specific workflow, second proxy, atau multi-worker Uvicorn.
4. EACH runbook SHALL menyatakan precondition, authorized role, expected state, timeout, pass/fail criteria, rollback/escalation, Trading-Safe State, dan evidence.
5. Start/reboot/update/rollback SHALL membedakan Edge Liveness dan Backend Readiness.
6. Restore handoff SHALL menegaskan manual/offline restore, Restore Hold, no auto-start, dan manual Startup Gate setelah sign-off.
7. EACH operational event SHALL menghasilkan Operator Evidence Package dengan UTC timing, operator/reviewer, release/revision, process manager, process/listener state, gates, readiness, Trading-Safe State, certificate, capacity, recovery, monitoring, tests, rollback, dan final decision.
8. Lifecycle evidence SHALL mencatat zero broker mutation.
9. Evidence SHALL tidak memuat secret, token, cookie, credential, key, environment dump, atau unrestricted sensitive path.
10. Evidence SHALL disimpan dengan ACL terbatas selama sekurang-kurangnya 180 hari.
11. Production update, rollback, certificate renewal, hardening exception, dan post-restore release SHALL memerlukan operator dan reviewer berbeda.
12. Missing, stale, inconsistent, atau secret-bearing evidence SHALL memblokir sign-off.
13. Isolated runbook drill SHALL mencakup cold boot, backend crash, edge crash, failed-update rollback, monitoring alert, dan certificate failure setiap 90 hari.
14. Drill SHALL NOT memakai production database, production secret, MT5 connection, Demo Execution, atau broker order.

### Requirement 15: Mandatory testing

**User Story:** Sebagai maintainer, saya ingin seluruh lifecycle dan failure mode dibuktikan tanpa production atau broker mutation.

#### Acceptance Criteria

1. Tests SHALL memverifikasi native topology, selected Process Manager contract, venv, single-worker Uvicorn, Vite dist, dan Nginx proxy tanpa prohibited tooling.
2. Tests SHALL memverifikasi tepat satu backend process dan second process ditolak runtime lease.
3. Tests SHALL memverifikasi backend-before-edge ordering, 120-second readiness bound, dan 300-second cold-boot bound.
4. Tests SHALL memverifikasi database unavailable, lease failure, missing secret, invalid release, dan startup timeout fail closed.
5. Tests SHALL membuktikan `/healthz` bukan backend readiness ketika backend down.
6. Tests SHALL membuktikan readiness dapat lulus dalam Trading-Safe State.
7. Tests SHALL memverifikasi startup, planned/forced reboot, crash restart, update, rollback, dan post-restore manual start tidak melanjutkan persisted trading state.
8. Tests SHALL membuktikan lifecycle/probe/monitoring/update/rollback/restore menghasilkan nol MT5 connect, Demo/Paper start, `order_check`, `order_send`, close, modify, dan cancel.
9. Tests SHALL memverifikasi planned shutdown ordering, drain timeout, graceful timeout, dan unclean-shutdown recovery.
10. Tests SHALL memverifikasi restart delay/limit, restart-loop containment, dan operator restart requirement.
11. Tests SHALL memverifikasi edge/backend failure dipisahkan, Nginx invalid config ditolak, dan backend tidak menjadi publik.
12. Tests SHALL memverifikasi update success/preflight failure/migration failure/readiness failure/smoke failure serta rollback success/failure/incompatible revision.
13. Tests SHALL memverifikasi backend/Vite release mismatch ditolak dan rollback tidak menjalankan downgrade/Restore.
14. Tests SHALL memverifikasi Restore Hold mencegah auto-restart dan Restore tidak memulai service.
15. Tests SHALL memverifikasi monitoring cadence, timeout, consecutive-failure alert, delivery time, heartbeat loss, serta payload redaction.
16. Tests SHALL memverifikasi edge-up/backend-down dan edge-down/backend-up alerts.
17. Tests SHALL memverifikasi certificate warning/critical thresholds, invalid candidate, reload success, dan rollback.
18. Tests SHALL memverifikasi disk thresholds, update block, log quota/rotation failure, backup age/RPO/off-host/drill alerts, dan safe deletion boundaries.
19. Tests SHALL memverifikasi firewall/listener exposure, service-account rights, NTFS ACL matrix, and separation of duties.
20. Tests SHALL memverifikasi secret tidak muncul di repository additions, release artifact, process arguments, service/task definitions, logs, monitoring, errors, atau evidence.
21. Tests SHALL memverifikasi API dan WebSocket proxy setelah startup/reboot/update/rollback tanpa broker mutation.
22. PowerShell dan Process Manager contract tests SHALL memakai isolated non-production paths dan fake services.
23. Tests SHALL memverifikasi mandatory evidence fields, reviewer separation, retention metadata, dan redaction.
24. ALL existing backend, frontend, auth, MT5 isolation, Safety, Nginx, WebSocket, backup, restore, dan recovery regression tests SHALL tetap lulus.
25. Mandatory tests SHALL NOT memakai production DB, production secret, real broker account/order, atau production VPS deployment.

### Requirement 16: Completion gate

**User Story:** Sebagai release reviewer, saya ingin milestone ditolak bila operational safety atau evidence belum terbukti.

#### Acceptance Criteria

1. Completion SHALL memerlukan seluruh requirements, mandatory tests, runbooks, monitoring, hardening, least privilege, dan evidence lulus.
2. Final evidence SHALL mencantumkan files changed, selected Process Manager, service ordering, restart policy, timeout, release identity, commands/results, dan limitations.
3. Final evidence SHALL membuktikan exactly one worker/process, loopback backend, Nginx-only public exposure, Vite dist, dan API/WebSocket proxy.
4. Final evidence SHALL membuktikan cold boot, reboot, crash, update, rollback, dan post-restore manual start memenuhi readiness dan Trading-Safe State.
5. Final evidence SHALL membuktikan `/healthz` tidak dipakai sebagai backend readiness.
6. Final evidence SHALL membuktikan alert delivery, certificate/capacity thresholds, hardening, ACL, secret redaction, dan evidence retention.
7. Final evidence SHALL membuktikan Restore tetap manual/offline dan tidak menyebabkan auto-start.
8. Final safety audit SHALL membuktikan lifecycle-only validation menghasilkan nol MT5 connect, Demo/Paper start, dan broker mutation.
9. IF any mandatory gate gagal, THEN Milestone 10.8 SHALL NOT dinyatakan complete.
10. Milestone SHALL berhenti pada operational readiness dan SHALL NOT melanjutkan trading redesign, high availability, database replacement, atau cloud deployment automation.

## Out of Scope

- Perubahan behavior atau redesign Milestone 10.1–10.7.
- Perubahan Strategy, Risk, Backtest, Paper rules, Demo rules, Safety decisions, atau broker semantics.
- Automatic MT5 connect, trading resume, broker replay, atau engine auto-start setelah restart.
- Automatic/scheduled Restore, automatic database downgrade/failover, atau auto-start setelah Restore.
- Docker, container orchestration, external queue, atau cloud-provider-specific deployment/monitoring.
- Multi-worker/multi-instance backend, active-active, additional load balancer, database clustering, atau penggantian SQLite.
- Public direct access ke Uvicorn, Nginx status, database, Process Manager control, atau recovery tooling.
- Pemilihan vendor VPS, certificate authority, monitoring, atau secret manager tertentu.
- Production deployment execution pada tahap specification.
