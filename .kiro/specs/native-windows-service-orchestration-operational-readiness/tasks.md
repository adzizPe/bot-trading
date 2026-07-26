# Implementation Plan: Native Windows Service Orchestration and Operational Readiness

## Overview

Implementasikan Milestone 10.8 secara bertahap di atas kontrak Milestone 10.1–10.7: fondasi metadata operasional, readiness backend read-only, kontrak process manager native, Operational Controller one-shot, lifecycle/restart containment, update/rollback, Restore Hold, monitoring/certificate/capacity, hardening, PowerShell, runbook, dan completion evidence.

Seluruh implementasi dan validasi wajib memakai path, database, identity, service, certificate, serta secret synthetic/non-production. Jangan membaca `.env`, menginstal atau mengubah service production, deploy ke VPS, menghubungkan MT5 nyata, mengirim order, atau mengubah semantics trading/recovery existing. Native Windows, venv, tepat satu Uvicorn process/worker pada loopback, Vite `frontend/dist`, Nginx-only public edge, dan NSSM canonical wajib dipertahankan; PM2 hanya alternatif mutually exclusive. Setiap validation task harus berhenti dan tidak melanjutkan dependency berikutnya bila gate gagal.

## Tasks

- [x] 1. Bangun fondasi metadata dan policy operasional yang fail-closed
  - [x] 1.1 Buat package operasional dan konfigurasi path/policy tervalidasi
    - Definisikan root release, state, evidence, log, certificate, Nginx, recovery, dan active-reference sebagai path role terpisah; canonicalize dan tolak traversal, alias, reparse/symlink ambiguity, release mutable, serta overlap dengan Active SQLite.
    - Tambahkan timeout, restart window, monitoring cadence, retention, threshold, dan selected process manager dengan default aman; jangan menambahkan secret value/default atau database migration.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.4, 2.6, 11.1, 13.7, 13.8_
  - [x] 1.2 Implementasikan model `ReleaseManifest` dan validasi immutable release set
    - Modelkan schema/version, release/application/source/Alembic/frontend identity, hash backend/frontend/Nginx, timestamp UTC, dan lifecycle status; gunakan parsing ketat serta deterministic serialization.
    - Pastikan manifest tidak memuat secret, environment dump, credential, private key, atau mutable data path.
    - _Requirements: 3.1, 7.1, 7.5, 7.8, 13.7, 14.9_
  - [x] 1.3 Implementasikan durable `RestoreHoldRecord` store
    - Gunakan restricted sidecar di luar Active SQLite, `.partial` + flush + atomic replace, versioning, dan fail-closed read; missing state hanya berarti tidak ada hold bila absence dapat dibuktikan, sedangkan malformed/partial/ambiguous state berarti `HELD`.
    - Catat change ID, reason, hashed operator/reviewer identity, restore ID, dan UTC tanpa memberi recovery tooling authority untuk memulai service.
    - _Requirements: 8.1, 8.2, 8.3, 8.6, 8.8, 13.3, 13.6_
  - [x] 1.4 Implementasikan `ServiceGateResult` dan allowlisted `OperatorEvidencePackage`
    - Sediakan schema/version, UTC timing, release, gate/process/listener/lease/trading-safe state, zero-mutation counters, certificate/capacity/recovery/monitoring summary, decision, reviewer separation, dan retention metadata.
    - Terapkan canonical ordering, atomic signed-off publication, immutability after sign-off, bounded fields, identity hashing, path categorization, dan secret-canary redaction scanner.
    - _Requirements: 5.10, 7.16, 10.10, 14.7, 14.8, 14.9, 14.10, 14.11, 14.12, 16.2_
  - [x] 1.5 Tulis unit dan property tests untuk fondasi operasional
    - Uji path containment/alias/reparse simulation, malformed manifests, immutable identity, interrupted atomic writes, ambiguous Restore Hold, reviewer separation, evidence determinism, retention metadata, secret-value non-interference, dan canary quarantine.
    - Implementasikan Design Properties 8, 9, dan 11; gunakan temporary directories dan generated identities saja.
    - _Requirements: 8.3, 8.6, 8.8, 13.7, 13.9, 13.10, 13.11, 13.14, 14.7, 14.9, 14.10, 14.11, 14.12, 15.20, 15.23_
- [x] 2. Tambahkan authoritative backend readiness yang minimal dan read-only
  - [x] 2.1 Wire release identity dan startup-state observations ke application lifespan
    - Ekspos hanya observation internal untuk startup complete, acquired `DatabaseRuntimeLease`, database read probe, release identity, MT5 disconnected, Demo stopped, paper stopped, dan scheduler stopped.
    - Pertahankan connector child backend-owned, auth/RBAC, Safety, dan initialization existing; jangan memanggil MT5 initialize/connect atau mutation dari observation.
    - _Requirements: 3.1, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4, 4.5, 4.9_
  - [x] 2.2 Implementasikan readiness evaluator dengan bounded database probe
    - Hasilkan hanya `READY|NOT_READY`, fixed service identity, version, release ID, timestamp UTC, lease/database categories, dan `trading_safe`; kategorikan missing required non-trading secret secara tersanitasi.
    - Fail closed untuk startup incomplete, lease/database/release mismatch, trading state unsafe, atau timeout; backup-key dan MT5 credential yang tidak dibutuhkan tidak boleh menghalangi safe startup.
    - _Requirements: 3.3, 3.4, 3.5, 3.6, 4.8, 13.9, 13.10_
  - [x] 2.3 Tambahkan exact readiness route dan exact Nginx proxy contract
    - Tambahkan satu GET read-only yang rate-limited/no-store, tanpa account/position/path/hostname/stack trace/secret; tambahkan exact-match Nginx location yang mempertahankan forwarded-header trust boundary.
    - Pertahankan static `/healthz` sebagai edge-only, `/health/full` sebagai authenticated domain health, docs denial, API/WebSocket proxy, TLS, limits, dan loopback upstream existing.
    - _Requirements: 2.2, 2.3, 3.6, 3.11, 3.12, 9.4, 9.5, 9.6, 12.5_
  - [x] 2.4 Tulis readiness unit, route, database, dan safety tests
    - Uji ready saat MT5 disconnected/Demo-Paper stopped; not-ready untuk lease, DB, startup, release, secret, dan trading-safe failure; uji schema/redaction, timeout, rate/cache contract, serta `/healthz` sukses ketika backend down.
    - Pasang spies yang mewajibkan nol MT5 connect, Demo/Paper start, `order_check`, `order_send`, close, modify, dan cancel; implementasikan Design Properties 1, 3, dan 4 bagian readiness.
    - _Requirements: 3.3, 3.4, 3.5, 3.6, 3.11, 4.7, 4.8, 9.5, 15.2, 15.4, 15.5, 15.6, 15.8_

- [x] 3. Definisikan single-owner native process-manager contracts
  - [x] 3.1 Implementasikan service-definition model dan fake SCM/process adapter
    - Modelkan executable, arguments, working directory, protected environment source, startup mode, identity, dependency, shutdown timeout, restart policy, dan log destination tanpa memasukkan secret ke argv.
    - Sediakan discovery process/listener/owner serta adapter fake untuk test; model tidak boleh menginstal service, menjalankan production executable, atau menjadi supervisor kedua.
    - _Requirements: 2.1, 2.4, 2.5, 2.6, 12.5, 13.1, 13.5, 15.22_
  - [x] 3.2 Buat canonical NSSM definition templates dan validator
    - Kunci backend ke venv Python/Uvicorn, `--host 127.0.0.1 --port 8000 --workers 1`, satu process, explicit working directory, bounded stop/restart/log settings, dan coarse Edge-to-Backend dependency.
    - Kunci Nginx ke native binary/config dan satu owner; larang MT5 terminal/connector, Demo, paper, restore, atau drill sebagai autostart service.
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 3.2, 4.6_
  - [x] 3.3 Buat mutually exclusive PM2 alternative contract validator
    - Izinkan hanya explicit host decision dengan fork mode, `instances=1`, no cluster/watch/reload, explicit venv interpreter, bounded restart, dan ownership/log/security contract setara.
    - Tolak PM2 dan NSSM yang mengelola process sama, mixed ownership dengan Task Scheduler/startup folder, atau Node/PM2 yang dianggap fallback otomatis.
    - _Requirements: 2.4, 2.5, 2.6, 6.9, 14.2_
  - [x] 3.4 Tulis process-manager, topology, ownership, dan exposure contract tests
    - Generate definitions ke temporary path dan uji exact worker/process/loopback arguments, Vite dist, API/WebSocket proxy, dependency, timeout/restart/log fields, no-secret argv, prohibited autostart, dual-owner rejection, dan PM2 equivalence.
    - Uji second file-backed backend ditolak runtime lease dan edge failure tidak membuka backend ke publik; implementasikan Design Properties 1 dan 10.
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.7, 2.8, 6.5, 12.4, 12.5, 15.1, 15.2, 15.11, 15.19, 15.20, 15.22_
- [x] 4. Implementasikan preflight dan cold-start Operational Controller one-shot
  - [x] 4.1 Bangun controller state machine dengan bounded adapter interfaces
    - Definisikan one-shot operations untuk preflight, SCM action, readiness polling, Nginx validation, process/listener checks, alert, dan evidence; seluruh side effect harus injectable dan setiap transition fail-closed/idempotent.
    - Controller tidak boleh bertahan sebagai supervisor, memanggil trading endpoint, menjalankan migration/restore, atau membaca/menyalin raw secret.
    - _Requirements: 1.2, 1.3, 1.4, 3.1, 4.6, 6.9, 13.7_
  - [x] 4.2 Implementasikan host/release/startup preflight
    - Validasi selected identity, executable, working directory, complete release hashes/pairing, Active SQLite location, protected config availability, ACL, required non-trading secret status, recovery status, capacity, certificate, process count, listener, dan Restore Hold.
    - Kembalikan stable sanitized failure category dan ubah nol active release/database/service definition bila satu check gagal.
    - _Requirements: 3.1, 7.2, 7.3, 7.4, 7.5, 8.3, 11.5, 13.9_
  - [x] 4.3 Implementasikan backend-before-edge cold-start gate
    - Pastikan edge candidate belum published, start backend, poll loopback readiness maksimum 120 detik, validasi dist/release/Nginx/certificate/proxy target, lalu start edge dan probe edge + proxied readiness dalam total maksimum 300 detik.
    - Pada failure, tahan edge candidate, contain/stop failed backend sesuai policy, emit alert/evidence, dan jangan gunakan `/healthz` sebagai backend/release success.
    - _Requirements: 3.2, 3.3, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12_
  - [x] 4.4 Implementasikan final startup process/listener/trading-safe gate
    - Buktikan tepat satu backend process/worker, loopback-only listener, Nginx-only public listeners, acquired lease, release pairing, Trading-Safe State, dan zero broker-mutation counters sebelum available.
    - Edge-up/backend-down dan edge-down/backend-up harus tetap menjadi kategori berbeda.
    - _Requirements: 2.8, 4.1, 4.4, 4.7, 4.8, 6.6, 6.7_
  - [x] 4.5 Tulis startup controller unit/property/integration tests
    - Gunakan fake clock/SCM/NSSM/process/listener/Nginx/certificate dan temporary SQLite untuk success, every preflight failure, ordering, 120/300-second boundaries, edge validation preservation, second process, stale listener, dan exact alert/evidence.
    - Implementasikan Design Property 2 dan lifecycle safety guards; stop suite pada nonzero result sebelum task lifecycle berikutnya.
    - _Requirements: 3.1, 3.2, 3.3, 3.7, 3.8, 3.9, 3.10, 3.12, 15.3, 15.4, 15.5, 15.8_

- [x] 5. Implementasikan planned shutdown, reboot, dan restart-loop containment
  - [x] 5.1 Implementasikan planned shutdown gate dan reverse ordering
    - Require explicit operator-established Demo/Paper stop dan MT5 disconnect melalui flow existing, lalu verify read-only Trading-Safe State; drain/stop edge maksimum 30 detik sebelum graceful backend stop maksimum 120 detik.
    - Verifikasi tidak ada stale process/listener/runtime lease; jangan auto-close, modify, cancel, atau mengubah broker position saat shutdown.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.7, 5.8_
  - [x] 5.2 Implementasikan unclean marker dan reboot workflow state
    - Tandai timeout/forced exit sebagai unclean secara durable, catat timing, dan wajibkan full Startup Gate pada start berikutnya; clean maupun forced reboot harus kembali ke Trading-Safe State.
    - Jangan menyimpan/replay mutation intent dan jangan menganggap process exit sebagai bukti trading-safe.
    - _Requirements: 4.10, 5.6, 5.9, 5.10_
  - [x] 5.3 Implementasikan restart attempt window dan quarantine
    - Terapkan delay minimum 30 detik, maksimum 3 attempt per rolling 10 menit/service, critical alert maksimum 5 menit, durable attempt state, dan explicit operator release + full gate setelah quarantine.
    - Backend restart menjalankan full readiness/trading-safe checks; edge restart wajib memvalidasi config/certificate dan tidak mengubah backend exposure.
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.7, 6.8, 6.10_
  - [x] 5.4 Tulis shutdown/reboot/restart property dan lifecycle tests
    - Uji clean/unsafe/timeout/forced stop, reverse order, cleanup lease/listener, restart delay/window boundaries, crash quarantine, operator restart, backend/edge split failure, dan invalid Nginx candidate.
    - Implementasikan Design Properties 3 dan 5 dengan fake time; semua connect/start/order/close/modify/cancel spies wajib nol.
    - _Requirements: 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 6.1, 6.2, 6.3, 6.4, 6.8, 15.7, 15.9, 15.10, 15.11_
- [x] 6. Implementasikan native release update dan application rollback
  - [x] 6.1 Implementasikan immutable release staging/activation transaction
    - Stage backend + per-release venv + Vite dist + Nginx application config/snippets + service manifest sebagai satu hashed identity; pertahankan mutable DB/secrets/certificate/log/evidence/recovery di luar release.
    - Active-reference switch hanya boleh terjadi saat maintenance/offline gate lulus dan harus recoverable dari interruption tanpa mixed backend/frontend.
    - _Requirements: 7.1, 7.5, 7.8, 7.12, 13.6_
  - [x] 6.2 Implementasikan update preflight dan change-record policy
    - Require operator/reviewer, maintenance window, rollback decision, Last-Known-Good, test evidence, venv/dist/Nginx/config/certificate/process/readiness/trading-safe/capacity checks.
    - Require recovery `AVAILABLE` + `rpo_met=true`; migration juga memerlukan backup `VALID`, off-host `VERIFIED`, dan seluruh writer offline.
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 11.5, 14.11_
  - [x] 6.3 Implementasikan offline migration orchestration boundary
    - Jalankan migration existing hanya setelah edge/backend/all SQLite writers berhenti dan runtime exclusion terbukti; record revision/result tanpa mengubah migration semantics.
    - Migration failure mempertahankan backend offline dan dilarang memicu downgrade, Restore, release publication, atau trading operation.
    - _Requirements: 7.3, 7.6, 7.7, 7.14_
  - [x] 6.4 Implementasikan candidate acceptance dan read-only smoke gate
    - Setelah candidate start, require readiness/trading-safe, Nginx validation, HTTPS/static/API read-only/WebSocket smoke, proxied release identity, dan bounded 10-minute decision.
    - Smoke tidak boleh login/connect MT5 atau memanggil mutation endpoint; failure memulai approved rollback atau tetap offline sesuai change record.
    - _Requirements: 7.9, 7.10, 7.11, 15.21_
  - [x] 6.5 Implementasikan complete Last-Known-Good rollback
    - Restore satu complete release set, check expected Alembic compatibility, ulang Startup Gate/smoke, dan emit critical alert bila gagal.
    - Incompatible revision wajib berhenti offline untuk keputusan recovery manual; jangan menjalankan Alembic downgrade atau Milestone 10.7 Restore otomatis.
    - _Requirements: 7.12, 7.13, 7.14, 7.15, 7.16_
  - [x] 6.6 Tulis update/rollback unit, property, fault, dan integration tests
    - Uji success serta every preflight/migration/activation/readiness/Nginx/smoke/interruption failure, 10-minute boundary, mixed identity rejection, LKG success/failure, incompatible revision, dan unchanged Active SQLite bytes on rollback.
    - Implementasikan Design Properties 6 dan 7; lifecycle spies wajib membuktikan nol MT5 connect, engine start, downgrade, Restore, dan broker mutation.
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.7, 7.8, 7.9, 7.10, 7.11, 7.12, 7.13, 7.14, 7.15, 15.12, 15.13_

- [x] 7. Integrasikan Restore Hold dengan boundary recovery Milestone 10.7
  - [x] 7.1 Terapkan Restore Hold pada seluruh automatic-start/restart paths
    - Startup, crash recovery, reboot task, update, rollback, dan monitor harus menolak backend start ketika hold `HELD` atau ambiguous; disable automatic restart tanpa memberi monitor/recovery tool service-mutation permission.
    - Edge tidak boleh mem-proxy backend offline sebagai ready dan recovery writer lain harus tetap berhenti.
    - _Requirements: 8.2, 8.3, 8.4, 8.5, 8.6_
  - [x] 7.2 Implementasikan offline recovery handoff tanpa mengubah Restore semantics
    - Controller hanya memasuki hold, menghentikan Edge lalu Backend, membuktikan all writers/process/listener/lease offline, dan menghasilkan handoff evidence untuk script Milestone 10.7 existing.
    - Successful maupun failed dry-run/restore tidak boleh memanggil SCM start/restart/reload atau melepaskan hold otomatis.
    - _Requirements: 8.1, 8.4, 8.5, 8.6, 8.7_
  - [x] 7.3 Implementasikan two-person hold release dan manual first-start gate
    - Release hanya setelah evidence valid, operator/reviewer berbeda, restore ID/result terikat, dan sign-off complete; first start harus explicit manual dan menjalankan full Startup Gate.
    - Restore success tidak memberi authorization untuk MT5 connect, Demo/Paper start, atau broker mutation.
    - _Requirements: 8.8, 8.9, 8.10, 14.6, 14.11_
  - [x] 7.4 Tulis Restore Hold, recovery handoff, dan no-auto-start tests
    - Uji durable/ambiguous hold, every auto-start path, active writer rejection, failed/success restore result, controller interruption, reviewer separation, manual release/start, serta unchanged Milestone 10.7 CLI contract.
    - Implementasikan Design Property 8; fake recovery dan service adapters harus menunjukkan nol service start setelah restore dan nol trading call.
    - _Requirements: 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 15.14_
- [x] 8. Implementasikan read-only monitoring, alerting, dan managed logging
  - [x] 8.1 Bangun vendor-neutral monitor state machine dan split probes
    - Probe host/process manager/process count, static edge liveness, proxied backend readiness, certificate, capacity, log rotation, recovery, dan scheduled task dengan category state terpisah.
    - Edge/backend cadence maksimum 60 detik, timeout maksimum 5 detik, tiga consecutive failures, dan alert delivery maksimum 5 menit; monitor tidak boleh melakukan remediation/service/trading mutation.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.7, 9.10_
  - [x] 8.2 Implementasikan recovery/task watchdog, delivery heartbeat, dan alert adapter
    - Konsumsi sanitized recovery status setiap maksimum 15 menit; warning umur 20 jam, critical RPO/24 jam, off-host, failure, next schedule, dan drill rules existing.
    - Modelkan alert deduplication, synthetic warning/critical monthly test, delivery heartbeat 10 menit, dan integration-unavailable tanpa menaruh secret/account/session/raw path pada payload.
    - _Requirements: 9.6, 9.8, 9.9, 9.11, 9.12, 11.10, 11.11_
  - [x] 8.3 Implementasikan structured operational logs dan rotation observations
    - Gunakan UTC, event/change correlation, allowlisted fields, stable categories, bounded output, dan references ke log event/time range alih-alih menyalin raw log ke evidence.
    - Pantau Nginx split logs, NSSM backend stdout/stderr, Event Log, Task Scheduler, dan recovery JSONL tanpa mengubah ownership/format Milestone sebelumnya.
    - _Requirements: 11.7, 11.8, 13.7, 14.7, 14.9_
  - [x] 8.4 Tulis monitoring cadence, alert, payload, dan zero-mutation tests
    - Gunakan fake clock/probes/sink untuk success/failure recovery, edge-up/backend-down, edge-down/backend-up, consecutive failures, timeout, delivery target, heartbeat loss, deduplication, and synthetic delivery.
    - Uji disconnected/stopped trading state tetap healthy, payload redaction, read-only permissions, dan semua MT5/service/trading spies nol.
    - _Requirements: 9.2, 9.3, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12, 15.15, 15.16_

- [x] 9. Implementasikan certificate, capacity, dan safe managed-log workflows
  - [x] 9.1 Implementasikan local/external certificate policy checks
    - Parse hostname, validity UTC, chain, approved fingerprint, candidate key pairing, private-key readability category, Nginx config result, dan external observation; daily cadence dengan warning `<=30` hari dan critical `<=14` hari.
    - Private key bytes/path sensitif tidak boleh masuk model, output, log, monitoring, atau evidence.
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.9_
  - [x] 9.2 Implementasikan certificate candidate renewal/rollback transaction
    - Backup active set secara restricted, validate candidate/ACL/chain/key + `nginx -t`, graceful reload, external fingerprint/hostname/chain/expiry/OCSP check maksimum 5 menit, lalu commit evidence.
    - Validation failure mempertahankan active set tanpa reload; post-reload failure memulihkan last validated set, retest, dan rollback reload tanpa restart backend.
    - _Requirements: 10.5, 10.6, 10.7, 10.8, 10.10_
  - [x] 9.3 Implementasikan volume inventory dan capacity thresholds
    - Inventaris release/venv/dist/log/DB-WAL-SHM/local-work-forensic-backup/off-host roles; check maksimum 5 menit dengan warning `<=20%` atau `<=10 GiB`, critical `<=10%` atau `<=5 GiB`.
    - Critical memblokir update pada affected release/database volume tetapi tidak memicu ad-hoc deletion atau melonggarkan capacity semantics recovery existing.
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.12_
  - [x] 9.4 Implementasikan managed-log quota/rotation safety policy
    - Terapkan retention 30 hari atau aggregate 5 GiB/host, warning 80%, critical full/two failures, allowlisted owned archives, active-log preservation, dan deterministic dry-run/recheck.
    - Jangan pernah delete DB/WAL/SHM, backup, forensic, certificate, secret, active log, atau unknown/unowned files.
    - _Requirements: 11.7, 11.8, 11.9, 11.12, 11.13_
  - [x] 9.5 Tulis certificate/capacity/rotation policy, fault, dan property tests
    - Uji date/space exact boundaries, invalid hostname/chain/key/config, active-set preservation, reload/external success, rollback, volume update block, backup/off-host/drill distinctions, quota/rotation failures, and unowned-file preservation.
    - Implementasikan Design Property 12 dengan temporary certificates/filesystems dan fake Nginx; jangan memakai production certificate atau menghapus file di luar sandbox.
    - _Requirements: 10.2, 10.3, 10.4, 10.6, 10.7, 10.8, 11.3, 11.4, 11.5, 11.8, 11.9, 11.10, 11.11, 15.17, 15.18_
- [x] 10. Implementasikan host-hardening, least-privilege, ACL, dan secret audit contracts
  - [x] 10.1 Bangun Windows host/network hardening auditor
    - Periksa supported update state, firewall default-deny + approved 80/443/admin access, listener ownership, malware/firewall/audit/time services, clock drift, Event Log retention, remote-admin scope, dan production debug/docs/dev/watcher/reload/container/queue prohibitions.
    - Critical update assessment/apply-exception windows dan 90-day review harus dapat dibuktikan; exception memerlukan owner/control/reviewer dan expiry maksimum 30 hari.
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8, 12.9, 12.10, 12.11, 12.12_
  - [x] 10.2 Implementasikan identity/right/ACL matrix validator
    - Validasi identity backend, Edge, recovery, monitoring, operator, reviewer terpisah; no local-admin/interactive-logon/self-service-reconfiguration untuk service accounts; monitoring read-only dan recovery tanpa auto-start.
    - Validasi least-privilege ACL atas release/config/data/log/recovery/evidence/certificate/secret serta service-definition immutability tanpa menerapkan ACL production saat test.
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.13_
  - [x] 10.3 Implementasikan secret inventory metadata dan artifact/process scanner
    - Simpan hanya owner, consumer, lifecycle timestamp, dan availability/status; scan repository additions, release artifacts, argv, service/task definitions, logs, errors, monitoring, crash output, dan evidence untuk secret canaries/session/environment dumps.
    - Secret-bearing output harus fail security gate dan dikarantina; jangan membaca `.env` atau nilai secret sebenarnya.
    - _Requirements: 13.7, 13.8, 13.9, 13.10, 13.11, 13.12, 13.14_
  - [x] 10.4 Tulis hardening, exposure, ACL, separation, dan secret tests
    - Gunakan synthetic Windows command outputs/ACL descriptors/listeners/artifacts untuk approved/failure/exception cases, clock drift, public port 8000/status/recovery denial, rights matrix, quarterly review, rotation/revocation evidence, dan canary leak.
    - Pastikan prohibited-tool scan tidak menghasilkan network call, service change, atau production host mutation.
    - _Requirements: 12.4, 12.5, 12.6, 12.8, 12.9, 12.12, 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.12, 13.13, 13.14, 15.19, 15.20_

- [x] 11. Tambahkan native Windows PowerShell operator workflows dan offline service artifacts
  - [x] 11.1 Tambahkan setup, preflight, start, stop, restart, dan reboot wrappers
    - Gunakan PowerShell 5.1-compatible strict mode, explicit working directory, native venv/Uvicorn/Nginx/NSSM, bounded timeout, exact exit propagation, WhatIf/non-production mode, sanitized JSON result, dan evidence ID.
    - Setup hanya menghasilkan/validasi offline definition artifacts secara default; jangan menginstal/start service production tanpa explicit deployment tahap terpisah.
    - _Requirements: 2.6, 3.1, 3.2, 5.1, 5.3, 5.4, 5.5, 14.1, 14.2, 14.4_
  - [x] 11.2 Tambahkan update, rollback, crash-loop, dan Restore Hold wrappers
    - Bungkus controller policy tanpa reimplementasi safety; require change/reviewer/release identifiers, preserve offline state on failure, dan larang secret/key pada parameter.
    - Restore handoff hanya enter/check/release hold dan stop verification; jangan menambah scheduled/automatic Restore atau service start sesudah recovery.
    - _Requirements: 6.3, 6.10, 7.1, 7.11, 7.14, 8.3, 8.7, 8.8, 8.9, 14.6_
  - [x] 11.3 Tambahkan read-only monitoring, certificate, capacity, log, dan hardening wrappers
    - Sediakan one-shot checks yang cocok Task Scheduler, non-overlap, bounded timeout, stable exit/category, protected environment source, dan no remediation by monitor.
    - Certificate renewal/rollback tetap explicit operator workflow; monitoring checks tidak memperoleh private key output, service mutation, retention delete, rollback, Restore, atau trading permission.
    - _Requirements: 9.1, 9.2, 9.8, 9.10, 10.1, 10.5, 11.2, 11.7, 12.1, 13.4_
  - [x] 11.4 Tulis PowerShell AST/contract dan fake-process-manager tests
    - Uji strict mode, quoting/path-with-spaces, exact arguments, selected-manager exclusivity, worker/host constraints, timeout/exit propagation, no-secret argv, no trading endpoints, no restore auto-start, idempotency, WhatIf, dan fail-closed malformed output.
    - Jalankan hanya dengan fake SCM/NSSM/PM2/Nginx executables dan temporary paths; tidak boleh menyentuh Windows service nyata.
    - _Requirements: 4.6, 8.7, 13.7, 14.2, 14.3, 15.8, 15.20, 15.22, 15.25_
- [x] 12. Tulis native Windows runbook dan evidence procedures
  - [x] 12.1 Dokumentasikan topology, setup, service ownership, dan routine lifecycle
    - Dokumentasikan canonical NSSM dan approved PM2 alternative, exact venv/Uvicorn single-worker loopback contract, Vite dist, Nginx-only edge, service dependencies, protected config, setup/preflight/start/stop/restart/planned reboot, expected state, timeout, pass/fail, escalation, dan evidence.
    - Nyatakan bahwa `/healthz` hanya Edge Liveness dan backend readiness harus melalui exact backend/proxied contract.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.11, 14.1, 14.2, 14.3, 14.4, 14.5_
  - [x] 12.2 Dokumentasikan update, rollback, crash, backend, Nginx, dan Windows Update flows
    - Sertakan precondition/authorized role/change/reviewer, clean/unclean shutdown, recovery limits, candidate/LKG identity, schema incompatibility stop, smoke, rollback/escalation, Trading-Safe State, dan evidence.
    - Larang automatic migration recovery, downgrade, Restore, public Uvicorn fallback, MT5 reconnect, dan engine resume.
    - _Requirements: 5.1, 5.6, 6.1, 6.2, 6.3, 6.10, 7.1, 7.7, 7.14, 14.1, 14.4_
  - [x] 12.3 Dokumentasikan certificate, disk/log, monitoring, hardening, dan secret operations
    - Sertakan renewal/rollback, disk-full safe boundaries, log failure/quota, delivery failure/synthetic alert, listener/firewall/clock/update review, ACL review, secret rotation/revocation, exception, cadence, criteria, dan sanitized evidence.
    - Jangan menganjurkan ad-hoc delete, secret CLI/service argument, private-key logging, cloud-specific monitoring, atau production credential di repository.
    - _Requirements: 9.11, 10.5, 10.8, 10.9, 11.13, 12.1, 12.12, 13.7, 13.12, 13.13, 14.1_
  - [x] 12.4 Dokumentasikan Restore handoff dan disaster recovery native
    - Referensikan runbook Milestone 10.7 tanpa menggandakan/mengubah semantics: Restore Hold, all writers offline, dry-run, forensic/post-check, two-person sign-off, no auto-start, manual Startup Gate, dan trading remains stopped.
    - Disaster recovery memakai clean supported Windows VPS, verified LKG release/off-host backup/certificate/secrets via approved channel, manual cutover, tanpa deployment cloud/container.
    - _Requirements: 1.5, 8.1, 8.4, 8.7, 8.8, 8.9, 8.10, 14.1, 14.3, 14.6_
  - [x] 12.5 Dokumentasikan Operator Evidence Package, retention, dan drill checklist
    - Tentukan mandatory fields, references bukan raw logs, ACL, 180-day retention, reviewer separation, stale/inconsistent/secret-bearing rejection, dan final decision template.
    - Definisikan isolated 90-day drill untuk cold boot, backend crash, edge crash, failed-update rollback, monitoring alert, dan certificate failure dengan generated DB/secret/certificate serta tanpa MT5/Demo/order.
    - _Requirements: 14.7, 14.8, 14.9, 14.10, 14.11, 14.12, 14.13, 14.14_
  - [x] 12.6 Validasi runbook commands, links, boundaries, dan traceability
    - Uji/parse contoh PowerShell secara offline, cek seluruh required flow dan role/gate/evidence field, referensi recovery valid, terminology readiness/liveness konsisten, dan prohibited technology/production deployment tidak muncul sebagai instruction.
    - Hentikan progression bila dokumentasi dapat memulai trading/Restore otomatis, mengekspos secret, atau melewati gate.
    - _Requirements: 1.5, 1.6, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.9, 15.20, 15.25_

- [x] 13. Jalankan focused lifecycle, security, dan operational integration validation
  - [x] 13.1 Jalankan complete lifecycle matrix dengan fake Windows services
    - Uji clean cold boot, readiness failure, edge validation failure, backend/edge crash, clean/forced reboot, update success/failure, rollback, Restore Hold, dan post-restore manual start dengan bounded fake clock.
    - Assert ordering, exact process/worker/listener/release identity, split statuses, evidence, alerts, and zero MT5 connect/Demo-Paper start/order-check/send/close/modify/cancel pada setiap scenario.
    - _Requirements: 3.10, 4.1, 5.9, 6.4, 7.9, 8.9, 15.3, 15.7, 15.8, 15.9, 15.10, 15.12, 15.14_
  - [x] 13.2 Jalankan loopback Uvicorn dan Nginx sandbox contract validation
    - Start hanya temporary one-worker local backend dan sandbox Nginx/fake equivalent; buktikan second runtime lease ditolak, backend loopback-only, static `/healthz` terpisah, proxied readiness, static Vite asset, API read-only, dan exact/prefix WebSocket handshake.
    - Uji invalid config/certificate candidate tidak reload; jangan memakai public network, production hostname/certificate, auth mutation, atau real broker.
    - _Requirements: 2.1, 2.2, 2.3, 3.11, 3.12, 9.5, 15.2, 15.5, 15.11, 15.17, 15.21, 15.25_
  - [x] 13.3 Jalankan security and prohibited-tool audit atas seluruh addition
    - Scan service/task arguments, artifacts, logs/evidence, listeners, docs/scripts/dependencies untuk secret canary, public backend, multi-worker, dual owner, restore autostart, Docker/Kubernetes/container/external queue/cloud tooling, dev server/watcher/reload, dan production paths/actions.
    - Audit tidak boleh membaca `.env`; setiap leak/prohibition atau failed separation-of-duties memblokir completion.
    - _Requirements: 1.3, 1.4, 1.5, 1.6, 12.4, 12.5, 12.9, 13.1, 13.7, 13.14, 15.19, 15.20, 15.25_
  - [x] 13.4 Jalankan isolated operational drill dan hasilkan synthetic evidence
    - Jalankan cold boot, crash containment, edge failure, failed-update rollback, monitoring warning/critical delivery, certificate candidate failure/rollback, capacity block, dan Restore Hold handoff hanya pada generated sandbox.
    - Verifikasi timing objectives, no production service/DB/secret/VPS, evidence redaction/reviewer/retention, no auto-start after restore, dan all trading counters zero.
    - _Requirements: 9.11, 10.7, 10.8, 11.5, 14.11, 14.13, 14.14, 15.23, 15.25_
- [x] 14. Jalankan regression, completion gate, dan final readiness evidence
  - [x] 14.1 Jalankan focused backend operational dan property suites
    - Jalankan seluruh unit/property/contract/integration tests Milestone 10.8 dengan real MT5 integration deselected; record exact command, duration, pass/fail/skip, property seed/counterexample, dan limitations.
    - Jika satu test/gate gagal, perbaiki hanya scope Milestone 10.8 atau critical compatibility bug dan ulang targeted suite; jangan melanjutkan completion evidence saat merah.
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8, 15.9, 15.10, 15.11, 15.12, 15.13, 15.14, 15.15, 15.16, 15.17, 15.18, 15.19, 15.20, 15.21, 15.22, 15.23, 15.25_
  - [x] 14.2 Jalankan full non-integration backend dan recovery regressions
    - Jalankan lint/type/compile checks yang berlaku, backend suite tanpa opt-in real MT5, serta backup/verify/off-host/retention/dry-run/restore/drill/offline-safety regressions Milestone 10.7.
    - Buktikan tidak ada migration baru yang tidak diperlukan dan recovery semantics, auth/RBAC, MT5 isolation, Safety, Strategy/Risk/Paper/Backtest/Demo tetap tidak berubah.
    - _Requirements: 1.1, 1.3, 1.4, 1.7, 15.24, 15.25_
  - [x] 14.3 Jalankan frontend, Nginx, API, dan WebSocket regressions
    - Jalankan frontend lint, typecheck, Vitest non-watch, Vite production build, Nginx config/contract tests, security headers/cache/upload boundaries, REST proxy, dan both exact/prefix WebSocket routes.
    - Gunakan dist/sandbox synthetic dan jangan menjalankan dev server, watcher, production reload, atau public deployment.
    - _Requirements: 2.3, 3.8, 12.9, 15.1, 15.21, 15.24, 15.25_
  - [x] 14.4 Susun final Milestone 10.8 evidence dan limitations
    - Catat files changed, selected Process Manager, topology/order/restart/timeouts, release identity, commands/results, cold boot/reboot/crash/update/rollback/post-restore outcomes, certificate/capacity/recovery/monitoring/hardening/ACL state, and limitations tanpa secret/raw sensitive path.
    - Buktikan exactly one Uvicorn process/worker, loopback-only backend, Nginx-only public edge, Vite dist, API/WebSocket proxy, `/healthz` edge-only, Restore manual/offline/no-start, alert delivery, evidence retention, dan zero trading/broker mutations.
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8_
  - [x] 14.5 Lakukan final scope/prohibited-technology/completion audit
    - Require seluruh requirement, mandatory test, runbook, monitoring, hardening, least privilege, drill, dan evidence PASS; sinkronkan task checkboxes hanya berdasarkan evidence aktual.
    - Nyatakan milestone incomplete bila gate gagal; jangan deploy production, commit otomatis, redesign trading, menambah HA/database replacement, atau melanjutkan cloud/container automation.
    - _Requirements: 1.5, 1.6, 16.1, 16.9, 16.10_

## Notes

- Semua leaf task bersifat wajib; tidak ada optional task karena Milestone 10.8 adalah operational-readiness gate.
- Task boleh berjalan paralel hanya di wave yang sama dan hanya bila tidak mengubah file/resource yang sama; setiap wave berikutnya menunggu seluruh task wajib pada wave sebelumnya lulus.
- Test memakai fake SCM/NSSM/PM2/Task Scheduler, temporary SQLite/path, synthetic certificate/ACL/secret canary, dan local sandbox. Real MT5 integration tetap opt-in dan deselected.
- Service-definition artifacts dan PowerShell hanya divalidasi offline; milestone tidak menginstal service, mengubah Task Scheduler production, reload Nginx production, deploy ke VPS, atau membuat commit otomatis.
- `.env` tidak boleh dibaca. Secret/private key tidak mempunyai default, tidak masuk repository, argv, service/task arguments, log, monitoring, crash output, atau evidence.
- Perubahan pada Milestone 10.1–10.7 hanya diizinkan untuk integration point minimum atau critical bug yang dibuktikan test; semantics auth, trading, Safety, MT5, dan recovery harus tetap dipertahankan.
- Static Nginx `/healthz` hanya Edge Liveness. Completion selalu memerlukan backend readiness via loopback dan Nginx proxy, tepat satu worker/process, loopback-only Uvicorn, serta zero broker mutation.
- Restore tetap backup-ID-driven, manual, offline, dan no-auto-start pada success maupun failure; Restore Hold tidak menggantikan Milestone 10.7 recovery checks.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["1.5", "2.1", "3.1"] },
    {
      "id": 3,
      "tasks": ["2.2", "3.2", "3.3", "9.1", "9.3", "10.1", "10.2", "10.3"]
    },
    { "id": 4, "tasks": ["2.3", "3.4", "9.2", "9.4", "10.4"] },
    { "id": 5, "tasks": ["2.4", "4.1", "8.1", "9.5"] },
    { "id": 6, "tasks": ["4.2", "8.2", "8.3"] },
    { "id": 7, "tasks": ["4.3", "4.4", "8.4"] },
    { "id": 8, "tasks": ["4.5", "5.1"] },
    { "id": 9, "tasks": ["5.2", "5.3", "6.1", "6.2", "7.1"] },
    { "id": 10, "tasks": ["5.4", "6.3", "6.4", "7.2"] },
    { "id": 11, "tasks": ["6.5", "7.3"] },
    { "id": 12, "tasks": ["6.6", "7.4"] },
    { "id": 13, "tasks": ["11.1", "11.2", "11.3"] },
    { "id": 14, "tasks": ["11.4", "12.1", "12.2", "12.3", "12.4", "12.5"] },
    { "id": 15, "tasks": ["12.6", "13.1", "13.2", "13.3"] },
    { "id": 16, "tasks": ["13.4"] },
    { "id": 17, "tasks": ["14.1"] },
    { "id": 18, "tasks": ["14.2", "14.3"] },
    { "id": 19, "tasks": ["14.4"] },
    { "id": 20, "tasks": ["14.5"] }
  ]
}
```
