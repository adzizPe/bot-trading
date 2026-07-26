# Implementation Plan: SQLite Backup, Restore, and Recovery Readiness

## Overview

Implementasikan recovery subsystem operator-side secara bertahap: fondasi konfigurasi/catalog/lease, artifact encryption, SQLite Online Backup API, verification, off-host copy, GFS retention, offline restore + dry-run, restore drill, CLI/PowerShell, monitoring, dan runbook. Seluruh test pada rencana ini wajib; tidak ada test task opsional karena milestone merupakan production release blocker.

Jangan membaca `.env`, memakai production database, menjalankan MT5/order, mengubah domain trading, menambah endpoint backup publik, membuat scheduled restore, melakukan service orchestration, atau deployment VPS. Exactly one Uvicorn worker dan deployment native tetap dipertahankan.

## Tasks

- [ ] 1. Siapkan dependency, version, konfigurasi, dan domain recovery
  - [ ] 1.1 Tambahkan authenticated-encryption dependency dengan exact pin
    - Pilih release `cryptography` yang mendukung Python 3.10+, Windows wheel, dan API AES-GCM streaming; pin exact di `backend/requirements.txt` dan catat alasan/version evidence.
    - Jangan menambah cloud SDK, queue, container, atau open version range.
    - _Requirements: 5.1, 18.1, 18.2_
  - [ ] 1.2 Tambahkan konfigurasi recovery tervalidasi
    - Tambahkan RPO 24 jam, RTO 2 jam, interval 24 jam, retention 7/4/3, local/off-host destination, encryption requirement/key source, compression, busy timeout, dan operation timeout ke Settings serta `.env.example` dengan placeholder aman.
    - Tolak key/default destination tidak aman, non-SQLite source, retention/duration invalid, source alias, dan ambiguous relative path sebelum I/O.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.9, 2.10, 5.2, 5.3, 5.4_
  - [ ] 1.3 Sentralisasikan application version untuk manifest dan health metadata
    - Buat single backend version constant yang digunakan FastAPI/HealthMonitor dan backup manifest; database revision tetap authority terpisah.
    - Jangan mengubah frontend behavior atau domain trading.
    - _Requirements: 4.1, 4.11, 10.8_
  - [ ] 1.4 Buat package recovery dan immutable domain models
    - Definisikan enums/frozen dataclasses untuk manifest schema, lifecycle status, verification, off-host receipt, restore result, RPO class, recovery status, stable errors, metrics, dan exit codes.
    - Pastikan setiap backup directory memiliki file tepat `manifest.json` dengan `backup_format_version` dan seluruh metadata wajib.
    - _Requirements: 4.1, 4.2, 4.3, 4.6, 4.7, 4.9, 4.11, 14.1, 14.2_

- [ ] 2. Implementasikan path safety, durable catalog, structured logging, dan leases
  - [ ] 2.1 Implementasikan `SQLitePathResolver` dan managed-path policy
    - Parse SQLAlchemy SQLite URL, canonicalize source/local/off-host/work/forensic paths, enforce containment, dan tolak memory DB, traversal, alias, symlink/reparse point, arbitrary overwrite, serta destination di source directory.
    - Simpan hanya sanitized source basename pada manifest/status.
    - _Requirements: 4.4, 4.5, 7.8, 15.1, 15.2, 15.8, 15.9_
  - [ ] 2.2 Implementasikan `FilesystemCatalog` dengan per-backup `manifest.json`
    - Gunakan layout `backups/<backup_id>/artifact.btbak`, `manifest.json`, dan optional `offhost-receipt.json`; gunakan `.partial`, flush/fsync, atomic replace, schema version, rebuild status, dan interrupted-operation reconciliation.
    - Unknown/unowned files tidak boleh dihapus atau dimodifikasi.
    - _Requirements: 3.7, 3.8, 4.1, 4.2, 4.7, 4.8, 4.10, 4.12, 15.9, 15.10_
  - [ ] 2.3 Implementasikan allowlisted structured JSON logging dan redaction
    - Tulis bounded JSONL event/result tanpa key, credential, environment dump, raw URL/path, traceback, atau exception mentah.
    - Tambahkan secret-canary scanner test helper.
    - _Requirements: 4.4, 4.9, 5.3, 5.12, 12.9, 12.10, 15.7_
  - [ ] 2.4 Implementasikan recovery `OperationLease`
    - Gunakan kernel file lock Windows `msvcrt` dan non-Windows test fallback, bounded acquisition, atomic owner metadata, safe stale-content handling, serta deterministic serialization.
    - _Requirements: 3.12, 8.5, 8.6, 9.4, 15.5, 15.6, 15.10_
  - [ ] 2.5 Implementasikan dan wire `DatabaseRuntimeLease`
    - FastAPI lifespan memegang lease untuk seluruh waktu backend file-backed aktif; restore wajib memperoleh lease yang sama dan SQLite exclusive preflight.
    - Pastikan shutdown melepas lease, second owner gagal, existing in-memory tests tetap terisolasi, dan tidak ada startup MT5/demo behavior baru.
    - _Requirements: 9.2, 9.3, 12.11, 16.19, 18.1_
  - [ ] 2.6 Tulis tests path, catalog, logging, dan lease
    - Uji traversal/alias/reparse simulation, atomic manifest, exact `manifest.json`, interrupted metadata, stale/active lock, concurrent invocation, runtime lease, secret redaction, dan unowned-file preservation.
    - _Requirements: 4.1, 4.9, 4.10, 15.1, 15.2, 15.5, 15.6, 15.7, 15.9_
- [ ] 3. Implementasikan versioned encrypted artifact dan consistent SQLite backup
  - [ ] 3.1 Implementasikan `.btbak` container dan streaming AES-256-GCM
    - Buat authenticated header dengan magic/version, backup identity, application/migration version, timestamps, compression, nonce, dan size; gunakan header sebagai AAD.
    - Stream gzip lalu AES-256-GCM ke `.partial`, append tag, fsync, hitung SHA-256 seluruh container, dan atomic rename tanpa memuat database penuh ke memory.
    - _Requirements: 4.6, 5.1, 5.5, 5.6, 5.7, 5.8_
  - [ ] 3.2 Implementasikan secure key input dan temporary plaintext lifecycle
    - Terima tepat random 32-byte key dalam base64 dari environment atau `getpass`; jangan menerima key melalui argv, log, manifest, atau default.
    - Tempatkan snapshot plaintext di restricted `.work`, cleanup pada success/failure/interruption, dan dokumentasikan best-effort deletion.
    - _Requirements: 5.2, 5.3, 5.4, 5.9, 5.10, 5.11, 5.12_
  - [ ] 3.3 Implementasikan page-batched SQLite Online Backup API adapter
    - Gunakan `sqlite3.Connection.backup` dengan bounded page chunks, busy timeout, sleep/retry, progress, operation timeout, cancellation, dan injected filesystem fault interface.
    - Jangan raw-copy DB/WAL/SHM atau force live WAL checkpoint; writer committed state harus masuk snapshot konsisten.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.9, 3.10, 3.11_
  - [ ] 3.4 Implementasikan disk preflight dan backup orchestrator
    - Estimasi source/WAL/page-size serta ruang untuk snapshot, artifact, dan round-trip verify; tangani preflight/mid-stream disk-full.
    - Orkestrasi IN_PROGRESS → VALIDATING → artifact publication → round-trip verification → VALID/INVALID/FAILED dan update manifest/status atomik.
    - _Requirements: 3.5, 3.6, 3.7, 3.8, 4.3, 4.7, 4.8, 4.10_
  - [ ] 3.5 Tulis backup consistency dan failure tests
    - Uji normal DB, WAL aktif tanpa checkpoint, concurrent writer, persistent exclusive lock, preflight/mid-stream disk-full, cancellation/interruption, operation timeout, partial cleanup, bounded memory, dan no raw-copy behavior.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.28_
  - [ ] 3.6 Tulis artifact encryption and format tests
    - Uji encrypt/decrypt, random nonce non-determinism, wrong key, tampered header/ciphertext/tag, truncation, unsupported version, header-manifest mismatch, compression, cleanup, checksum scope, dan key absence dari output.
    - _Requirements: 5.1, 5.3, 5.5, 5.6, 5.7, 5.8, 5.10, 16.7, 16.8, 16.9, 16.10, 16.25_
  - [ ] 3.7 Tulis property tests online consistency dan failed-publication invariant
    - Implementasikan Design Property 1 dan Property 2 dengan file-backed generated transaction/failure schedules.
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 4.8, 4.9, 4.10**
  - [ ] 3.8 Tulis property test authenticated encryption
    - Implementasikan Design Property 3 untuk nonce, key, dan byte mutations.
    - **Validates: Requirements 5.1, 5.5, 5.7, 5.8**

- [ ] 4. Implementasikan backup verification, Alembic compatibility, dan repository smoke checks
  - [ ] 4.1 Implementasikan `BackupVerifier`
    - Validate manifest schema/artifact identity, recompute size/SHA-256, authenticate/decrypt/decompress ke temp, buka read-only, jalankan full `PRAGMA integrity_check`, dan update verification atomik.
    - Terima hanya single `ok`; cleanup plaintext pada seluruh branch.
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.6, 6.9, 6.10_
  - [ ] 4.2 Implementasikan `AlembicCompatibilityService`
    - Temukan repository head/lineage tanpa menjalankan app; accept exact head, klasifikasikan ancestor migratable, dan tolak missing/unknown/divergent/newer.
    - Migrasikan hanya temporary candidate dengan explicit database URL, lalu ulang integrity/revision check; jangan automatic downgrade.
    - _Requirements: 6.5, 6.7, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.9_
  - [ ] 4.3 Implementasikan read-only repository smoke checker
    - Periksa revision row, allowlisted critical tables, foreign keys, bounded counts/fingerprints tanpa mengimpor app main atau memulai background/trading subsystem.
    - _Requirements: 6.8, 10.7, 18.7_
  - [ ] 4.4 Tulis verifier and compatibility tests
    - Uji valid checksum, mismatch, wrong key, integrity failure, exact head, compatible ancestor, migration success/failure, missing/unknown/newer/divergent revision, smoke failure, idempotent reverify, dan temp cleanup.
    - _Requirements: 6.1, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 16.7, 16.8, 16.10, 16.11, 16.12_
  - [ ] 4.5 Tulis property test VALID transition
    - Implementasikan Design Property 4: semua verification gates wajib pass dan satu failure pun mencegah `VALID`.
    - **Validates: Requirements 4.8, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8**

- [ ] 5. Implementasikan verified off-host copy dan safe GFS retention
  - [ ] 5.1 Implementasikan `OffHostCopyService`
    - Pilih hanya local VALID backup; stream ke destination `.partial`, flush/hash, compare source checksum, atomic publish artifact/`manifest.json`/receipt, dan update local status hanya setelah verified.
    - Tangani retry idempotent, destination full/unavailable/interrupted/mismatch, alias, dan cleanup/quarantine tanpa menurunkan local validity.
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10_
  - [ ] 5.2 Tulis off-host copy tests dan checksum property
    - Uji success, source not VALID, unavailable/full/interrupted destination, mismatched existing file, retry, partial cleanup, checksum receipt, local preservation, dan simulated network path.
    - Implementasikan Design Property 5.
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 16.13, 16.14_
  - [ ] 5.3 Implementasikan deterministic GFS retention planner/executor
    - Pilih newest per UTC day/ISO week/year-month; union class; protect latest VALID, Restore Lease, in-progress/validating, active copy, unverified, dan required-not-offhost backup.
    - Sediakan dry-run, recheck before delete, managed trash transaction/reconciliation, symlink defense, dan structured summary.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12_
  - [ ] 5.4 Tulis retention unit/property tests
    - Uji 7/4/3 defaults, overlapping classes, UTC/ISO boundaries, latest valid, active lease, pending verify/copy, required off-host, malformed/unknown files, interruption, and byte-identical dry-run.
    - Implementasikan Design Property 6 dan retention portion Property 7.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 16.15, 16.16, 16.29_
- [ ] 6. Implementasikan safe offline restore dan explicit Restore Dry Run Mode
  - [ ] 6.1 Implementasikan restore preflight dan backup selection
    - Acquire operation/runtime leases, SQLite exclusive preflight, Restore Lease, disk capacity, managed `manifest.json`, artifact identity, checksum, and key checks; reject backend/writer active.
    - Pilih backup melalui backup ID, bukan arbitrary path; jangan menyentuh Active Database pada failure.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_
  - [ ] 6.2 Implementasikan Restore Dry Run Mode
    - Decrypt/authenticate/decompress ke temporary candidate lalu jalankan manifest identity, SHA-256, `PRAGMA integrity_check`, Alembic lineage/migration compatibility, dan repository smoke check.
    - Hasilkan replacement plan/status tetapi jangan membuat final Forensic Copy, membersihkan Active WAL/SHM, menjalankan `os.replace`, atau mengubah byte Active Database.
    - _Requirements: 9.5, 9.6, 9.7, 9.8, 12.7, 12.8_
  - [ ] 6.3 Implementasikan candidate migration dan repeated validation
    - Untuk compatible ancestor, migrate hanya candidate; ulang integrity/revision/smoke checks sebelum publication.
    - _Requirements: 9.8, 10.2, 10.3, 10.4, 10.5, 10.6_
  - [ ] 6.4 Implementasikan forensic DB/WAL/SHM preservation
    - Setelah writer berhenti dan candidate valid, copy/checksum exact-state DB/WAL/SHM ke forensic directory dengan `FORENSIC_NOT_VERIFIED_BACKUP` manifest; fail closed jika copy gagal kecuali explicit first-restore mode.
    - _Requirements: 9.9, 9.10, 9.11, 9.14, 9.17_
  - [ ] 6.5 Implementasikan same-volume atomic replacement dan post-check
    - Stage candidate sebagai sibling Active Database, preserve stale WAL/SHM dahulu, gunakan satu `os.replace`, jangan fallback overwrite/delete-copy, lalu ulang integrity/revision/smoke.
    - Pada sharing violation/replace/post-check failure, tetap fail closed, jangan start backend/demo/MT5, dan simpan safe diagnostic/rollback instruction.
    - _Requirements: 9.7, 9.12, 9.13, 9.15, 9.16, 9.17, 9.18_
  - [ ] 6.6 Tulis restore success/failure tests
    - Uji restore success, corrupt backup, wrong key, checksum mismatch, active backend/runtime lease, external writer, forensic DB/WAL/SHM, no-active first restore, compatible migration, atomic replace, sharing violation, post-check failure, dan target preservation.
    - _Requirements: 16.17, 16.18, 16.19, 16.20, 16.21, 16.28_
  - [ ] 6.7 Tulis dry-run mutation-free tests
    - Snapshot Active DB/WAL/SHM/catalog bytes sebelum/sesudah successful dan failing dry-runs; buktikan decrypt/checksum/integrity/migration compatibility/smoke dijalankan tetapi forensic/cleanup/replace tidak.
    - Implementasikan restore portion Design Property 7.
    - _Requirements: 12.8, 16.17, 16.18, 16.19_
  - [ ] 6.8 Tulis restore safety property tests
    - Implementasikan Design Properties 8–11 untuk runtime exclusion, pre-replace rejection, forensic-before-publication, dan candidate-only migration.
    - **Validates: Requirements 9.2, 9.3, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12, 9.13, 9.14, 10.2, 10.3, 10.4, 10.5, 10.6**

- [ ] 7. Implementasikan recovery status, RPO/RTO, dan automated offline restore drill
  - [ ] 7.1 Implementasikan `StatusService`
    - Rebuild sanitized durable status dari per-backup `manifest.json`, receipts, dan drill results; hitung last successful/verified, age, RPO met, off-host, next schedule, latest drill/RTO, dan latest failure.
    - Bedakan NEVER/UNAVAILABLE, local VALID, off-host VERIFIED, dan latest failure tanpa raw paths/key.
    - _Requirements: 2.6, 2.7, 2.8, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9, 14.10_
  - [ ] 7.2 Implementasikan isolated `RestoreDrillRunner`
    - Buat current-head file DB, seed representative records, baseline counts/fingerprints, backup/encrypt/verify, simulated off-host copy, delete/corrupt source, restore, post-check, data comparison, and cleanup.
    - Gunakan random non-production key, production-equivalent services, no app main, dan explicit MT5/order import/call guard.
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 11.12, 11.13, 11.14, 11.15_
  - [ ] 7.3 Tambahkan monotonic backup/restore timing dan RPO/RTO evidence
    - Catat backup duration, end-to-end restore duration, exact target comparison, boundary behavior, and structured drill result.
    - _Requirements: 2.6, 2.7, 2.8, 11.10, 11.11, 18.5_
  - [ ] 7.4 Tulis status dan RPO/RTO unit/property tests
    - Uji no backup/drill, valid old backup + latest failure, exact 24h/2h boundaries, breaches, next schedule, deterministic unordered manifest rebuild, and sanitized allowlist.
    - Implementasikan Design Properties 12 dan 13.
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 16.23, 16.24_
  - [ ] 7.5 Tulis end-to-end restore drill test
    - Verifikasi seluruh 13 drill stages, integrity `ok`, revision head, fingerprints equal, off-host checksum verified, RPO/RTO report, non-zero exit on injected stage failure, no production DB path, dan zero MT5/order.
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11, 11.12, 11.13, 11.14, 11.15, 16.22, 16.26_
  - [ ] 7.6 Tulis secret and trading-boundary property tests
    - Implementasikan Design Properties 15 dan 16; scan manifest/status/receipt/stdout/stderr/JSONL serta dependency imports/counters.
    - **Validates: Requirements 1.2, 1.3, 1.4, 4.9, 5.3, 5.12, 11.15, 12.10, 15.7, 16.25, 16.26**

- [ ] 8. Implementasikan operator CLI dan Windows PowerShell wrappers
  - [ ] 8.1 Implementasikan `python -m app.recovery.cli`
    - Tambahkan commands `backup`, `verify`, `copy-offhost`, `retention`, `restore`, `drill`, dan `status`; map stable exit codes, JSON summary, dry-run, secure prompt, and sanitized unexpected errors.
    - Jangan menerima key lewat argv atau start/stop service/MT5/demo.
    - _Requirements: 12.1, 12.5, 12.6, 12.7, 12.8, 12.9, 12.10, 12.11, 12.12_
  - [ ] 8.2 Buat seluruh PowerShell scripts
    - Tambahkan `Backup-Database.ps1`, `Verify-Backup.ps1`, `Restore-Database.ps1`, `Copy-BackupOffHost.ps1`, `Invoke-RestoreDrill.ps1`, `Invoke-BackupRetention.ps1`, dan `Get-BackupStatus.ps1`.
    - Gunakan PowerShell 5.1-compatible syntax, typed params, literal path, root/python discovery, argument arrays, fail-closed handling, and exact exit-code propagation.
    - _Requirements: 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8, 12.9, 12.10, 12.11, 12.12_
  - [ ] 8.3 Tulis CLI and PowerShell contract tests
    - Uji commands/parameters, dry-run, missing/wrong key, non-interactive environment, key absent from process args/output, malformed IDs/paths, stable exit codes, project-root independence, and no production database access.
    - _Requirements: 12.3, 12.4, 12.5, 12.6, 12.7, 12.8, 12.9, 12.10, 16.30_
- [ ] 9. Dokumentasikan native Windows backup/recovery operations
  - [ ] 9.1 Buat complete SQLite recovery runbook
    - Dokumentasikan daily backup, verification, normal/dry-run restore, corruption, disk full, off-host failure, key generation/storage/rotation/escrow/loss, GFS retention, restore drill, and operator sign-off.
    - Tegaskan raw copy active DB bukan valid backup; preserve original DB/WAL/SHM; jangan start backend/demo/MT5 setelah restore.
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.9, 17.10, 17.11, 17.12_
  - [ ] 9.2 Dokumentasikan Windows Task Scheduler dan generic failure alerts
    - Berikan task backup → verify → off-host copy → retention, periodic drill, dedicated least-privilege account, no overlap, timeout, working directory, exit-code/Event Log/watchdog alerts, dan RPO stale alert.
    - Jangan menjadwalkan restore atau memasukkan key/credential ke script/repository.
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8_
  - [ ] 9.3 Perbarui README dan configuration reference
    - Tambahkan ringkasan Milestone 10.7, command aman, manifest per backup, dry-run semantics, status fields, limitations, exact-one-worker reminder, dan link runbook.
    - _Requirements: 1.8, 12.11, 14.1, 17.2, 18.1, 18.8_
  - [ ] 9.4 Tulis documentation/config contract tests
    - Pastikan seluruh scripts/runbook/config names tersedia, defaults 24h/2h/7/4/3 benar, no default key, no cloud/container/service-orchestration instruction, and active raw copy prohibition present.
    - _Requirements: 2.2, 5.3, 13.1, 13.2, 17.1, 17.2, 17.12_

- [ ] 10. Focused checkpoint — validate recovery subsystem
  - [ ] 10.1 Jalankan backend lint/format checks pada file baru/diubah
  - [ ] 10.2 Jalankan focused recovery unit, integration, property, CLI, and PowerShell contract tests
  - [ ] 10.3 Jalankan offline restore drill pada generated test database
  - [ ] 10.4 Perbaiki seluruh failure sebelum full regression
  - _Requirements: 16.1–16.30, 18.3, 18.4, 18.9_

- [ ] 11. Full regression, evidence, dan completion audit
  - [ ] 11.1 Jalankan full backend regression tanpa opt-in actual MT5 tests
    - Existing expectations tidak boleh dilonggarkan untuk meluluskan milestone.
    - _Requirements: 16.27, 18.3_
  - [ ] 11.2 Jalankan relevant frontend regression, typecheck, dan build
    - Walau frontend tidak berubah secara fungsional, pastikan recovery wiring tidak menyebabkan regression repository.
    - _Requirements: 16.27, 18.3_
  - [ ] 11.3 Jalankan final offline restore drill dan capture evidence
    - Tampilkan backup duration, restore duration, `integrity_check=ok`, checksum PASS, off-host VERIFIED, actual/target RPO, actual/target RTO, migration revision, and data fingerprint match.
    - _Requirements: 11.10, 11.11, 18.4, 18.5_
  - [ ] 11.4 Audit security dan zero-trading boundary
    - Scan log/manifest/output untuk secret; audit dependency/call counters bahwa tidak ada MT5/order/demo activation; konfirmasi `.env` dan production DB tidak dibaca; konfirmasi no VPS deployment.
    - _Requirements: 16.25, 16.26, 18.7_
  - [ ] 11.5 Tampilkan changed files dan completion report
    - Catat exact dependency pin, tests/lint results, drill evidence, limitations, dan operator steps yang tetap manual.
    - Berhenti setelah Milestone 10.7; jangan lanjut ke service orchestration.
    - _Requirements: 18.2, 18.5, 18.6, 18.7, 18.8, 18.9_

## Notes

- Seluruh test tasks wajib; tanda `*` optional tidak digunakan.
- Property-based tests harus bounded dan diberi warning saat dijalankan.
- Backup normal boleh berjalan saat writer aktif; restore dan forensic copy wajib offline.
- `manifest.json` per backup adalah source of truth; `status.json` hanya sanitized rebuildable cache.
- Restore dry-run wajib melakukan decrypt, checksum, integrity, migration compatibility, dan smoke validation tetapi tidak boleh membuat forensic copy final atau mengubah Active Database/WAL/SHM.
- Key tidak pernah menjadi CLI argument, default, log, manifest, atau repository content.
- Recovery package tidak boleh mengimpor atau memanggil MT5, Strategy, Risk, Paper, Backtest, Demo, Safety mutation, API route, atau `app.main`.
- Tidak ada database migration baru untuk catalog, endpoint backup, container, cloud SDK, scheduled restore, service orchestration, atau deployment VPS.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "2.4"] },
    { "id": 2, "tasks": ["2.5", "2.6", "3.1", "3.2", "3.3"] },
    {
      "id": 3,
      "tasks": ["3.4", "3.5", "3.6", "3.7", "3.8", "4.1", "4.2", "4.3"]
    },
    { "id": 4, "tasks": ["4.4", "4.5", "5.1", "5.3", "6.1"] },
    { "id": 5, "tasks": ["5.2", "5.4", "6.2", "6.3", "6.4"] },
    { "id": 6, "tasks": ["6.5", "6.6", "6.7", "6.8", "7.1", "7.2", "7.3"] },
    { "id": 7, "tasks": ["7.4", "7.5", "7.6", "8.1"] },
    { "id": 8, "tasks": ["8.2", "8.3", "9.1", "9.2", "9.3", "9.4"] },
    { "id": 9, "tasks": ["10.1", "10.2", "10.3", "10.4"] },
    { "id": 10, "tasks": ["11.1", "11.2", "11.3", "11.4", "11.5"] }
  ]
}
```
