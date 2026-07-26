# Requirements Document

## Introduction

Milestone 10.7 menutup blocker kritis hasil Release Candidate Review dengan menyediakan backup, verifikasi, off-host copy, retention, restore, dan restore drill SQLite yang aman. Seluruh operasi bersifat operator-side melalui CLI native Python dan wrapper PowerShell; tidak ada endpoint publik untuk mengambil backup dan tidak ada restore terjadwal.

Milestone ini tidak menambah fitur trading, tidak mengubah domain trading existing, tidak mengirim order MT5, tidak melakukan deployment VPS, dan tidak melanjutkan service orchestration. Raw copy database aktif bukan backup yang valid.

## Glossary

- **Active Database**: File SQLite yang dipakai backend, termasuk WAL dan SHM yang mungkin terkait.
- **Consistent Snapshot**: Salinan logis SQLite yang dihasilkan melalui SQLite Online Backup API atau mekanisme SQLite konsisten setara dan lulus seluruh verifikasi.
- **Backup Artifact**: File backup final yang telah dikompresi dan/atau dienkripsi bersama metadata terkait.
- **Backup Metadata**: Record non-secret yang mendeskripsikan lifecycle, identitas, checksum, revision, dan hasil verifikasi backup.
- **VALID Backup**: Backup yang selesai dibuat, dapat didekripsi bila perlu, checksum cocok, `PRAGMA integrity_check` menghasilkan `ok`, dan revision Alembic kompatibel.
- **Off-host Destination**: Shared network folder, mounted drive, atau filesystem destination terkonfigurasi yang terpisah dari lokasi database utama.
- **Forensic Copy**: Salinan database lama beserta WAL/SHM setelah writer berhenti, dipertahankan untuk investigasi dan bukan dinyatakan sebagai backup valid.
- **Restore Lease**: Penanda eksklusif bahwa suatu backup sedang dipakai proses restore sehingga tidak boleh dihapus retention.
- **RPO**: Batas umur maksimum backup terverifikasi yang dapat diterima.
- **RTO**: Batas durasi pemulihan dari awal restore sampai integrity, migration, repository smoke test, dan data comparison selesai.
- **Daily/Weekly/Monthly Class**: Kelas grandfather-father-son untuk retention backup terverifikasi.
- **Partial Artifact**: File sementara atau transfer yang belum selesai dan tidak boleh dianggap sebagai backup/off-host copy valid.
- **Structured Log**: Record machine-readable tanpa secret, key, plaintext credential, atau path sensitif yang tidak diperlukan.

## Requirements

### Requirement 1: Scope dan safety boundaries

**User Story:** Sebagai operator, saya ingin mekanisme recovery ditambahkan tanpa mengubah atau menjalankan fungsi trading.

#### Acceptance Criteria

1. THE system SHALL menyediakan backup, verification, off-host copy, retention, restore, restore drill, status, dan dokumentasi sebagai operasi operator native.
2. THE implementation SHALL NOT mengubah Strategy Engine, Risk Management, Paper Trading, Backtesting, Demo Execution, Safety Layer decision, atau order execution behavior.
3. THE implementation SHALL NOT memanggil MT5 connect, `order_check`, `order_send`, close/modify/cancel broker operation, atau mengaktifkan Demo Execution.
4. THE implementation SHALL NOT menambah Docker, container, cloud-provider-specific storage, external queue, atau service deployment baru.
5. THE system SHALL NOT menyediakan endpoint publik untuk download, upload, backup, restore, retention, atau key management.
6. THE normal backup and restore workflows SHALL dijalankan melalui operator CLI atau Windows PowerShell wrapper, bukan browser.
7. IF status administratif kelak diekspos melalui API, THEN access SHALL dibatasi ke `SUPER_ADMIN`, CSRF-protected untuk mutation, audit logged, dan SHALL NOT mengekspos key atau raw filesystem path.
8. THE documentation SHALL menyatakan secara eksplisit bahwa raw copy Active Database bukan backup valid.

### Requirement 2: RPO, RTO, schedule, retention, dan lokasi

**User Story:** Sebagai operator, saya ingin recovery objectives dan policy backup dapat dikonfigurasi serta diukur.

#### Acceptance Criteria

1. THE configuration SHALL menyediakan RPO target, RTO target, backup interval, daily retention, weekly retention, monthly retention, local backup destination, dan off-host destination.
2. THE staging defaults SHALL menetapkan RPO 24 jam, RTO 2 jam, daily retention 7, weekly retention 4, dan monthly retention 3.
3. THE defaults SHALL menggunakan backup interval tidak lebih besar dari RPO target.
4. THE configuration SHALL menerima override melalui environment atau explicit CLI operator input tanpa menyimpan secret ke repository.
5. IF duration, interval, retention count, atau destination invalid, ambiguous, non-positive ketika wajib positif, atau menunjuk Active Database sebagai destination, THEN startup operasi SHALL fail closed sebelum membuat artifact.
6. THE system SHALL menghitung backup age dari backup terakhir yang VALID dan SHALL membandingkannya dengan RPO target.
7. THE system SHALL mengukur RTO aktual dari awal restore drill sampai seluruh post-restore verification selesai.
8. THE status SHALL membedakan target, actual, met/not-met, serta timestamp UTC yang dipakai dalam calculation.
9. THE implementation SHALL menggunakan timestamp UTC untuk schedule, classification, metadata, dan filename.
10. THE local dan off-host destination SHALL configurable dan SHALL NOT hard-code provider cloud tertentu.

### Requirement 3: Consistent SQLite backup

**User Story:** Sebagai operator, saya ingin snapshot database tetap konsisten ketika WAL atau writer aktif.

#### Acceptance Criteria

1. WHEN backup dimulai, THE system SHALL menggunakan SQLite Online Backup API atau mekanisme SQLite konsisten setara, bukan raw filesystem copy Active Database.
2. THE backup SHALL menangkap state commit konsisten dari database utama termasuk perubahan committed yang berada pada WAL tanpa menyalin WAL/SHM sebagai artifact backup logis.
3. WHEN normal writer melakukan transaksi selama backup, THE backup SHALL menghasilkan snapshot pada boundary transaksi yang valid tanpa menghentikan writer lebih lama dari bounded SQLite coordination.
4. THE backup SHALL menggunakan bounded busy timeout, retry, dan progress handling agar lock tidak menyebabkan hang tanpa batas.
5. IF database tetap locked melewati policy timeout, THEN backup SHALL gagal, status SHALL `FAILED`, failure reason SHALL sanitized, dan tidak ada artifact yang ditandai valid.
6. IF disk space tidak memadai sebelum atau selama backup, THEN operation SHALL fail closed, mencatat failure category tanpa secret, dan membersihkan atau mengarantina Partial Artifact.
7. IF backup terinterupsi, THEN final filename SHALL tidak diterbitkan sebagai completed dan Partial Artifact SHALL dibersihkan pada invocation berikutnya atau ditandai aman untuk cleanup.
8. THE backup SHALL menulis ke temporary path pada destination dan SHALL mempublikasikan artifact dengan atomic rename hanya setelah snapshot selesai.
9. THE backup SHALL tidak menahan seluruh database dalam memory dan SHALL mendukung progress/cancellation boundary yang aman.
10. THE snapshot SHALL dibuka terpisah dari Active Database untuk verifikasi.
11. IF source bukan SQLite file-backed database yang didukung, THEN operation SHALL fail closed dengan error tersanitasi.
12. Concurrent backup, restore, off-host copy, verification, atau retention operations SHALL dikoordinasikan agar tidak merusak artifact atau Active Database.

### Requirement 4: Backup metadata dan lifecycle

**User Story:** Sebagai operator, saya ingin setiap backup dapat diaudit tanpa membocorkan secret.

#### Acceptance Criteria

1. EACH backup SHALL disimpan dalam managed backup directory yang memiliki file bernama tepat `manifest.json`; manifest tersebut SHALL berisi `backup_id`, `application_version`, `database_revision`, `created_at`, `completed_at`, `source_database`, `backup_filename`, `backup_size`, `checksum_sha256`, `encrypted`, `compression`, `backup_format_version`, `rpo_class`, `status`, dan `failure_reason`.
2. THE metadata SHALL menggunakan stable schema version dan backup ID yang collision-resistant.
3. `created_at` dan `completed_at` SHALL berupa UTC timestamps; `completed_at` SHALL kosong sampai lifecycle selesai.
4. `source_database` SHALL berupa identifier tersanitasi dan SHALL NOT menyimpan credential-bearing URL atau secret.
5. `backup_filename` SHALL berupa nama artifact terkontrol dan SHALL NOT menerima arbitrary path traversal.
6. `checksum_sha256` SHALL mengidentifikasi byte artifact final yang disimpan atau ditransfer; metadata SHALL menjelaskan checksum scope secara deterministik.
7. THE status lifecycle SHALL sekurang-kurangnya membedakan in-progress, failed, validating, valid, invalid, dan off-host verification state.
8. A backup SHALL NOT berstatus `VALID` sebelum snapshot, encryption bila enabled, checksum, integrity check, dan Alembic verification semuanya lulus.
9. IF operation gagal, THEN failure reason SHALL stabil, bounded, sanitized, dan SHALL NOT memuat encryption key, password, environment dump, atau traceback mentah.
10. Metadata update SHALL atomic sehingga interruption tidak menghasilkan record completed palsu.
11. Application version dan database revision SHALL direkam terpisah; application version SHALL NOT digunakan sendiri untuk keputusan compatibility.
12. THE system SHALL preserve metadata failure history yang diperlukan untuk monitoring dan audit sesuai retention policy.

### Requirement 5: Encryption dan key handling

**User Story:** Sebagai operator, saya ingin backup terenkripsi dengan key eksternal dan kegagalan autentikasi yang fail-closed.

#### Acceptance Criteria

1. THE system SHALL mendukung authenticated encryption dengan algorithm modern dan versioned artifact format.
2. THE encryption key atau passphrase SHALL berasal dari environment atau secure interactive operator input.
3. THE repository, default configuration, test fixture production-like, metadata, filename, structured log, exception message, dan process argument SHALL NOT berisi default atau real encryption key.
4. IF encryption diwajibkan tetapi key tidak tersedia, malformed, atau tidak memenuhi policy, THEN backup SHALL fail sebelum artifact final dibuat.
5. WHEN restore menggunakan key salah atau ciphertext dimodifikasi, THE authentication check SHALL gagal dan Active Database SHALL tetap tidak berubah.
6. Compression, bila enabled, SHALL dilakukan sebelum encryption dan nilai compression SHALL dicatat pada metadata.
7. Encryption metadata MAY memuat non-secret algorithm/version/KDF parameters dan random salt/nonce tetapi SHALL NOT memuat key.
8. The same plaintext encrypted twice SHALL menggunakan randomness yang memadai sehingga ciphertext tidak deterministik.
9. Plaintext temporary backup SHALL dihindari melalui streaming bila feasible.
10. IF plaintext temporary file harus digunakan, THEN file SHALL berada di restricted temporary directory, SHALL tidak diterbitkan sebagai artifact, dan SHALL dihapus secara best-effort pada success, failure, atau interruption.
11. THE documentation SHALL menjelaskan bahwa secure deletion absolut pada SSD/filesystem tidak dapat dijamin dan SHALL merekomendasikan encrypted volume serta ACL sebagai defense utama.
12. Restore SHALL tidak menulis key ke stdout, stderr, PowerShell transcript, command history, atau structured log.

### Requirement 6: Post-backup verification

**User Story:** Sebagai operator, saya ingin backup dinyatakan valid hanya jika benar-benar dapat dibuka dan cocok dengan schema aplikasi.

#### Acceptance Criteria

1. AFTER artifact dibuat, THE verifier SHALL menghitung ulang SHA-256 dan membandingkannya dengan metadata.
2. WHEN artifact encrypted, THE verifier SHALL mengautentikasi dan mendekripsinya ke isolated temporary location menggunakan key yang diberikan.
3. THE verifier SHALL membuka snapshot SQLite dalam read-only mode untuk pemeriksaan yang tidak memerlukan migration.
4. THE verifier SHALL menjalankan `PRAGMA integrity_check` dan SHALL menerima backup hanya jika hasil tunggalnya `ok`.
5. THE verifier SHALL membaca Alembic revision dari snapshot dan membandingkannya dengan revision repository yang diizinkan.
6. IF checksum mismatch, decryption gagal, integrity check gagal, Alembic revision hilang/unknown/divergent/lebih baru, atau metadata tidak valid, THEN backup SHALL ditandai `INVALID` dan SHALL NOT dipakai restore normal.
7. A revision ancestor SHALL dianggap compatible hanya jika policy restore menyatakan migration path tersedia dan migration pada temporary database berhasil.
8. THE verifier SHALL menjalankan bounded read-only repository smoke checks terhadap tabel/record representatif tanpa memulai aplikasi, scheduler, MT5, atau trading engine.
9. Temporary plaintext hasil verification SHALL dibersihkan sesuai encryption temporary-file policy.
10. Verification SHALL dapat dijalankan ulang secara idempotent dan SHALL memperbarui last-verified timestamp tanpa mengubah snapshot.

### Requirement 7: Verified off-host copy

**User Story:** Sebagai operator, saya ingin salinan backup berada di lokasi terpisah dan diverifikasi setelah transfer.

#### Acceptance Criteria

1. THE system SHALL menyalin hanya backup local yang berstatus `VALID` ke shared network folder, mounted drive, atau configurable filesystem destination.
2. THE copy SHALL menggunakan temporary/partial filename pada destination dan atomic rename setelah transfer selesai.
3. AFTER copy, THE system SHALL menghitung SHA-256 pada destination dan SHALL membandingkannya dengan checksum source artifact.
4. A local backup SHALL NOT dianggap off-host verified sebelum destination checksum cocok dan metadata/status copy berhasil dipersist.
5. IF copy gagal, terinterupsi, destination penuh, destination unavailable, atau checksum mismatch, THEN partial destination SHALL dibersihkan atau dikarantina dan failure SHALL dicatat.
6. Off-host copy failure SHALL NOT menghapus atau menurunkan validitas local backup yang telah diverifikasi.
7. Retry SHALL bounded dan idempotent serta SHALL tidak membuat duplicate completed artifact untuk backup ID yang sama.
8. THE implementation SHALL mencegah source/destination alias, traversal, overwrite file tidak terkait, dan destination di dalam Active Database directory.
9. THE logs dan status SHALL tidak mengekspos UNC credential, mount secret, encryption key, atau raw sensitive path.
10. THE restore workflow SHALL dapat memilih verified local atau verified off-host artifact melalui identifier terkontrol.

### Requirement 8: Daily, weekly, dan monthly retention

**User Story:** Sebagai operator, saya ingin retention bounded tanpa menghapus satu-satunya recovery point yang aman.

#### Acceptance Criteria

1. THE retention policy SHALL mengklasifikasikan backup VALID dalam kelas daily, weekly, dan monthly menggunakan UTC serta aturan deterministik terdokumentasi.
2. THE staging defaults SHALL mempertahankan 7 daily, 4 weekly, dan 3 monthly recovery points.
3. A backup MAY memenuhi lebih dari satu class dan SHALL dipertahankan selama dibutuhkan oleh sedikitnya satu class.
4. THE retention SHALL tidak pernah menghapus backup VALID terakhir.
5. THE retention SHALL tidak menghapus backup yang memiliki Restore Lease aktif.
6. THE retention SHALL tidak menghapus backup in-progress, validating, belum selesai diverifikasi, atau partial transfer yang sedang aktif.
7. THE retention SHALL tidak menghapus local recovery point terakhir sebelum off-host policy yang diwajibkan terpenuhi.
8. THE retention SHALL menyediakan dry-run yang menampilkan identifier, class, age, dan planned action tanpa menghapus file atau metadata.
9. Non-dry-run deletion SHALL memverifikasi bahwa candidate masih memenuhi policy tepat sebelum delete untuk mencegah race.
10. THE retention SHALL menghapus hanya artifact dan metadata yang dimiliki sistem serta SHALL menolak symlink/reparse-point/path escape yang tidak aman.
11. Interrupted deletion SHALL dapat direkonsiliasi pada invocation berikutnya tanpa menghapus unrelated file.
12. Retention result SHALL dicatat sebagai structured summary termasuk kept, eligible, deleted, skipped, dan failure counts.

### Requirement 9: Safe offline restore dan forensic preservation

**User Story:** Sebagai operator, saya ingin restore tidak menimpa database aktif sebelum seluruh validasi lulus.

#### Acceptance Criteria

1. Restore SHALL merupakan operasi offline manual dan SHALL NOT dijadwalkan.
2. BEFORE restore, THE system SHALL membuktikan backend/writer telah berhenti dan SHALL memperoleh exclusive recovery guard terhadap Active Database.
3. IF backend masih aktif, handle masih terbuka, writer terdeteksi, atau exclusive guard tidak dapat diperoleh, THEN restore SHALL gagal tanpa mengubah Active Database.
4. Restore SHALL menerima backup melalui backup ID/metadata terkontrol dan SHALL menolak arbitrary unverified file kecuali explicit forensic verification workflow terpisah.
5. Restore SHALL memverifikasi metadata dan artifact SHA-256 sebelum decrypt atau replace.
6. Restore SHALL mengautentikasi/decrypt backup bila encrypted dan SHALL gagal pada wrong key tanpa mengubah Active Database.
7. Restore SHALL menempatkan candidate database di temporary path terisolasi pada filesystem/volume yang mendukung final atomic replace.
8. Restore SHALL menjalankan integrity check, Alembic compatibility verification, migration bila policy mengizinkan ancestor revision, dan repository smoke test pada temporary candidate.
9. BEFORE replace, restore SHALL membuat Forensic Copy Active Database lama beserta WAL dan SHM yang ada, metadata, timestamp, dan checksum.
10. IF Forensic Copy tidak dapat diselesaikan, THEN restore SHALL fail closed kecuali tidak ada Active Database sebelumnya dan operator secara eksplisit menjalankan first-restore mode.
11. Forensic Copy SHALL diberi label bahwa file tersebut bukan backup konsisten yang telah diverifikasi.
12. Final replacement SHALL memakai atomic filesystem operation bila didukung; kegagalan replace SHALL mempertahankan database lama dan candidate untuk diagnosis yang aman.
13. Restore SHALL NOT fallback ke overwrite in-place atau delete-then-copy yang dapat meninggalkan database setengah tertulis.
14. WAL/SHM stale SHALL ditangani hanya setelah writer berhenti dan SHALL dipreservasi dalam Forensic Copy sebelum cleanup/replacement.
15. AFTER replace, THE restored database SHALL diverifikasi ulang melalui integrity check, revision check, dan smoke test sebelum restore dinyatakan berhasil.
16. Restore SHALL tidak start backend, Nginx, MT5, Demo Execution, paper engine, backtest engine, atau process manager.
17. Restore result SHALL mencatat backup ID, forensic copy ID, start/end UTC, elapsed time, integrity result, revision, checksum result, dan sanitized status tanpa raw sensitive path atau key.
18. IF post-replace verification gagal, THEN workflow SHALL fail closed dan memberikan prosedur operator untuk rollback menggunakan forensic material; SHALL NOT membuka mutation secara otomatis.

### Requirement 10: Migration compatibility dan repository validation

**User Story:** Sebagai operator, saya ingin restored database hanya digunakan oleh versi aplikasi yang kompatibel.

#### Acceptance Criteria

1. THE system SHALL menentukan repository Alembic head tanpa memuat aplikasi runtime atau memulai engine trading.
2. IF backup revision sama dengan allowed target revision, THEN compatibility check MAY proceed.
3. IF backup revision merupakan ancestor yang memiliki migration path valid, THEN migrations SHALL dijalankan hanya pada temporary restore candidate sebelum Active Database diganti.
4. IF migration pada candidate gagal, THEN Active Database SHALL tetap tidak berubah.
5. IF backup revision lebih baru, unknown, missing, divergent, atau bukan bagian dari lineage yang diizinkan, THEN restore SHALL ditolak.
6. AFTER candidate migration, THE system SHALL menjalankan integrity check dan repository smoke test ulang.
7. THE smoke test SHALL sekurang-kurangnya memverifikasi koneksi read-only, revision table, schema/tabel kritis yang dikonfigurasi, dan data representative fingerprint/count.
8. THE compatibility decision SHALL memakai database revision sebagai authority dan application version sebagai audit context.
9. THE workflow SHALL tidak menjalankan destructive downgrade otomatis.

### Requirement 11: Automated offline restore drill

**User Story:** Sebagai operator, saya ingin bukti periodik bahwa backup dapat dipulihkan dalam target RTO tanpa menyentuh production.

#### Acceptance Criteria

1. THE drill SHALL membuat file-backed SQLite test database pada isolated temporary workspace dan SHALL NOT menggunakan Active Database production.
2. THE drill SHALL mengisi data representatif yang mencakup revision Alembic dan record penting untuk repository smoke/data comparison.
3. THE drill SHALL membuat Consistent Snapshot menggunakan workflow backup production-equivalent.
4. THE drill SHALL mengenkripsi artifact tanpa memakai production key fixture.
5. THE drill SHALL menyalin artifact ke simulated off-host destination dan memverifikasi checksum destination.
6. THE drill SHALL menghapus atau mengorupsi database test original setelah baseline fingerprint disimpan.
7. THE drill SHALL restore melalui workflow restore production-equivalent ke target test.
8. THE drill SHALL menjalankan integrity check, Alembic verification, dan repository smoke test.
9. THE drill SHALL membandingkan record count, deterministic fingerprint, dan nilai penting sebelum serta sesudah restore.
10. THE drill SHALL mengukur backup duration dan restore duration menggunakan monotonic timer.
11. THE drill SHALL membandingkan backup age/RPO dan restore duration/RTO terhadap target terkonfigurasi.
12. THE drill SHALL mencatat hasil structured yang mencakup pass/fail tiap tahap tanpa key atau raw sensitive path.
13. ANY failed stage SHALL menghasilkan non-zero exit code dan SHALL tidak ditandai sebagai successful drill.
14. THE latest drill result SHALL tersedia untuk status monitoring.
15. THE drill SHALL membuktikan zero MT5/order calls melalui test guard atau dependency isolation.

### Requirement 12: Native Python CLI dan Windows PowerShell scripts

**User Story:** Sebagai operator Windows, saya ingin command fail-closed yang dapat dipakai manual atau Task Scheduler.

#### Acceptance Criteria

1. THE implementation SHALL menyediakan testable native Python CLI/service sebagai source of truth untuk backup domain logic.
2. THE repository SHALL menyediakan `Backup-Database.ps1`, `Verify-Backup.ps1`, `Restore-Database.ps1`, `Copy-BackupOffHost.ps1`, `Invoke-RestoreDrill.ps1`, dan `Invoke-BackupRetention.ps1`.
3. EACH PowerShell script SHALL menggunakan strict/fail-closed error handling, typed parameters, literal path handling, dan propagation exit code dari Python CLI.
4. Scripts SHALL kompatibel dengan Windows PowerShell 5.1 sejauh dependency runtime memungkinkan dan SHALL tidak memerlukan interactive-only feature untuk scheduled operation.
5. Backup, verification, off-host copy, drill, dan retention SHALL mendukung non-interactive key retrieval dari environment; restore MAY mendukung secure operator input.
6. Encryption key SHALL tidak diteruskan sebagai plain command-line argument.
7. Dry-run SHALL tersedia untuk retention dan operation lain yang dapat menghapus/replace bila relevan.
8. Dry-run restore SHALL memverifikasi preconditions, manifest/artifact identity, decryption/authentication, SHA-256 checksum, `PRAGMA integrity_check`, Alembic migration compatibility, dan repository smoke test pada temporary candidate, tetapi SHALL NOT membuat Forensic Copy final, membersihkan WAL/SHM aktif, mengganti Active Database, atau mengubah database aktif dalam bentuk apa pun.
9. Scripts SHALL menghasilkan structured log/summary dan stable non-zero exit code pada validation, lock, disk, checksum, encryption, integrity, revision, copy, retention, atau restore failure.
10. stdout/stderr SHALL bounded dan SHALL tidak berisi key, credential, raw environment, sensitive path, atau traceback kecuali explicit local debug mode yang tetap redacted.
11. Scripts SHALL tidak start/stop service secara otomatis pada milestone ini; operator runbook SHALL menangani precondition backend stopped.
12. No script SHALL menjalankan MT5 atau broker operation.

### Requirement 13: Scheduled Task dan failure alerting

**User Story:** Sebagai operator, saya ingin backup otomatis dapat dijadwalkan dan kegagalannya terlihat.

#### Acceptance Criteria

1. THE documentation SHALL menyediakan Windows Task Scheduler setup untuk scheduled backup, off-host copy, verification, dan retention.
2. Restore SHALL NOT dikonfigurasi atau didokumentasikan sebagai scheduled task.
3. Scheduled tasks SHALL memakai dedicated least-privilege account, explicit working directory, hidden secret handling, dan non-overlapping execution policy.
4. Task order SHALL memastikan backup selesai dan VALID sebelum off-host copy, serta retention berjalan setelah verification/copy policy terpenuhi.
5. THE scheduled configuration SHALL menyimpan Last Run Result dan structured output pada restricted location.
6. THE documentation SHALL menyediakan mekanisme alert generik berbasis non-zero exit code, stale successful backup, failed verification, failed off-host copy, atau failed drill tanpa mengunci ke vendor cloud tertentu.
7. IF task terlewat atau backup age melampaui RPO, THEN status SHALL menunjukkan RPO breach sampai backup VALID baru tersedia.
8. Task Scheduler credential atau encryption key SHALL NOT disimpan dalam script atau repository.

### Requirement 14: Backup and recovery monitoring status

**User Story:** Sebagai operator, saya ingin mengetahui apakah recovery point masih memenuhi policy tanpa melihat path atau key.

#### Acceptance Criteria

1. THE system SHALL menyediakan status berisi last successful backup, last verified backup, backup age, off-host copy status, next scheduled backup, dan latest restore drill result.
2. Status SHALL mencakup RPO target/actual/met, RTO target/latest actual/met, latest failure category, dan timestamps UTC.
3. Status SHALL membedakan local VALID dari off-host VERIFIED dan SHALL tidak menyamakan copy attempted dengan copy verified.
4. Status SHALL berasal dari durable metadata, bukan process memory saja.
5. Status output SHALL tidak mengekspos encryption key, passphrase, environment value, credential, raw source/destination path, database URL, session data, atau stack trace.
6. Operator CLI SHALL dapat membaca status tanpa membuka atau memodifikasi Active Database.
7. IF no VALID backup atau no restore drill exists, THEN status SHALL melaporkan `NEVER`/`UNAVAILABLE` secara eksplisit dan SHALL NOT menganggap recovery ready.
8. IF latest backup invalid/failed tetapi valid backup lama masih ada, THEN status SHALL menampilkan kedua kondisi dan menghitung age dari last VALID backup.
9. Public health endpoint SHALL NOT mendapatkan detail backup sensitif atau download capability.
10. Any optional authenticated summary SHALL bersifat read-only, least-privilege, bounded, dan audit-safe.

### Requirement 15: Security, filesystem, dan concurrency hardening

**User Story:** Sebagai operator keamanan, saya ingin backup tooling tidak menjadi jalur pembacaan atau penimpaan file arbitrer.

#### Acceptance Criteria

1. THE system SHALL canonicalize dan validate seluruh source, destination, temporary, metadata, dan forensic paths sebelum I/O.
2. THE system SHALL menolak traversal, unsafe symlink/reparse point, destination alias, unsupported network semantics, dan overwrite unrelated file.
3. Backup artifact, metadata, logs, temporary plaintext, forensic copies, dan key source SHALL didokumentasikan untuk NTFS ACL least privilege.
4. THE system SHALL menggunakan restrictive creation permissions sejauh didukung Windows dan SHALL tidak memperlebar ACL existing.
5. THE operation lock SHALL memiliki bounded stale-lock recovery yang memverifikasi owner/process state sebelum mengambil alih.
6. Concurrent invocations SHALL fail or serialize deterministically tanpa deadlock dan tanpa valid status palsu.
7. Structured logs SHALL menggunakan allowlisted fields dan redaction; exception dari filesystem, SQLite, encryption, atau Alembic SHALL disanitasi.
8. Artifact filename dan backup ID SHALL tidak memuat username, credential, account number, atau secret.
9. Cleanup SHALL hanya menyentuh managed files yang dapat dibuktikan kepemilikannya melalui metadata/format.
10. THE implementation SHALL tidak mengandalkan process memory sebagai satu-satunya sumber lifecycle state.

### Requirement 16: Mandatory verification and regression tests

**User Story:** Sebagai maintainer, saya ingin seluruh success dan failure mode dibuktikan otomatis.

#### Acceptance Criteria

1. Tests SHALL memverifikasi backup database normal.
2. Tests SHALL memverifikasi backup saat WAL aktif.
3. Tests SHALL memverifikasi backup saat writer melakukan transaksi.
4. Tests SHALL memverifikasi database locked menghasilkan bounded fail-closed outcome.
5. Tests SHALL memverifikasi disk-full/preflight dan mid-operation failure tidak menghasilkan valid artifact.
6. Tests SHALL memverifikasi interruption tidak mempublikasikan completed artifact dan partial cleanup aman.
7. Tests SHALL memverifikasi checksum valid.
8. Tests SHALL memverifikasi checksum mismatch ditolak.
9. Tests SHALL memverifikasi encryption dan decryption round trip.
10. Tests SHALL memverifikasi wrong encryption key ditolak dan database target tidak berubah.
11. Tests SHALL memverifikasi integrity-check failure ditolak.
12. Tests SHALL memverifikasi Alembic revision mismatch/unknown/newer/divergent ditolak.
13. Tests SHALL memverifikasi off-host copy berhasil dan destination checksum cocok.
14. Tests SHALL memverifikasi off-host copy gagal/terinterupsi dan partial tidak dianggap verified.
15. Tests SHALL memverifikasi daily/weekly/monthly retention dan overlap class.
16. Tests SHALL memverifikasi retention dry-run tidak menghapus data.
17. Tests SHALL memverifikasi restore berhasil dari backup VALID.
18. Tests SHALL memverifikasi restore dari corrupt backup ditolak sebelum replace.
19. Tests SHALL memverifikasi restore saat backend/writer aktif ditolak.
20. Tests SHALL memverifikasi Forensic Copy database/WAL/SHM dibuat sebelum replace.
21. Tests SHALL memverifikasi atomic replace serta failure mempertahankan database lama.
22. Tests SHALL memverifikasi restore drill end-to-end pada database test terisolasi.
23. Tests SHALL memverifikasi RPO calculation pada boundary dan breach.
24. Tests SHALL memverifikasi RTO measurement dan target comparison.
25. Tests SHALL memverifikasi key/secret tidak muncul pada log, metadata, output, atau failure.
26. Tests SHALL memverifikasi zero MT5/order calls dan tidak ada Demo Execution activation.
27. ALL existing regression tests SHALL tetap lulus.
28. Tests SHALL menggunakan file-backed temporary SQLite untuk WAL, lock, corruption, atomic replace, dan restore behavior; in-memory SQLite saja SHALL tidak dianggap cukup.
29. Property/boundary tests SHALL mencakup UTC classification, retention counts, filename/path validation, size/checksum, dan interrupted lifecycle.
30. PowerShell wrapper contract tests SHALL memverifikasi parameter safety, dry-run, key transport, dan exit-code propagation tanpa menjalankan production database.

### Requirement 17: Runbook dan operator sign-off

**User Story:** Sebagai operator, saya ingin prosedur yang dapat diikuti dan dibuktikan sebelum production.

#### Acceptance Criteria

1. THE documentation SHALL menyediakan runbook backup harian, verifikasi backup, restore normal, database corruption recovery, disk-full recovery, off-host copy failure, encryption-key handling, retention, restore drill, dan operator sign-off.
2. THE runbook SHALL menyatakan raw copy Active Database bukan backup valid.
3. THE restore runbook SHALL memerintahkan stop backend/writers, preserve original database/WAL/SHM, tidak delete/overwrite original, validate candidate, atomic replace, dan post-restore verification.
4. THE corruption runbook SHALL membedakan Forensic Copy dari VALID Backup.
5. THE disk-full runbook SHALL melarang penghapusan sembarang database/WAL/SHM dan SHALL memprioritaskan penghentian writer serta penambahan/pemulihan kapasitas.
6. THE key-handling runbook SHALL mencakup generation, storage di luar repository, rotation impact, recovery escrow, secure input, loss scenario, dan larangan log/history.
7. THE retention runbook SHALL mewajibkan dry-run review sebelum policy pertama kali diaktifkan atau diubah.
8. THE drill runbook SHALL menggunakan database test, simulated off-host destination, non-production key, dan SHALL melarang MT5/order.
9. Operator sign-off SHALL mencatat release/revision, backup ID, checksum verified, integrity result, off-host result, forensic behavior, measured RPO/RTO, drill timestamp, operator, reviewer, dan pass/fail.
10. THE documentation SHALL menjelaskan bahwa kehilangan encryption key membuat encrypted backup tidak dapat dipulihkan.
11. THE documentation SHALL menjelaskan limitasi best-effort plaintext deletion dan kebutuhan ACL/encrypted volume.
12. THE runbook SHALL memakai deployment native Windows/PowerShell dan SHALL NOT memperkenalkan container atau cloud-specific workflow.

### Requirement 18: Compatibility, performance evidence, dan completion gate

**User Story:** Sebagai release reviewer, saya ingin evidence akhir yang membuktikan milestone tanpa memperluas scope.

#### Acceptance Criteria

1. THE implementation SHALL tetap kompatibel dengan FastAPI, async SQLAlchemy, SQLite/Alembic, native Python virtual environment, dan Windows VPS workflow existing.
2. Dependency baru untuk encryption, jika diperlukan, SHALL dipin ke exact version dan SHALL digunakan hanya untuk kebutuhan backup/recovery yang terdokumentasi.
3. Lint, focused unit/integration tests, full backend regression, relevant frontend regression, dan PowerShell contract checks SHALL lulus.
4. An offline restore drill SHALL lulus tanpa membaca atau mengubah production database.
5. Final evidence SHALL menampilkan backup duration, restore duration, integrity-check result, checksum verification, simulated off-host status, actual RPO, target RPO, actual RTO, dan target RTO.
6. Final evidence SHALL mencantumkan seluruh file dibuat/diubah.
7. Final audit SHALL membuktikan tidak ada MT5/order invocation dan tidak ada deployment VPS.
8. Milestone SHALL berhenti setelah backup/recovery readiness dan SHALL NOT melanjutkan service orchestration.
9. IF any mandatory backup, restore, integrity, encryption, off-host, retention, drill, secret-redaction, or regression check fails, THEN Milestone 10.7 SHALL NOT dinyatakan complete.

## Out of Scope

- Fitur atau perubahan Strategy Engine, Risk Management, Paper Trading, Backtesting, Demo Execution, Safety Layer decision, atau execution broker.
- Pengiriman, modifikasi, penutupan, atau pembatalan order MT5.
- Automatic startup aplikasi, MT5, atau Demo Execution setelah restore.
- Public backup download/upload/restore endpoint.
- Browser-based key input atau key distribution.
- Cloud-provider-specific backup integration.
- Database replication, clustering, failover otomatis, atau perubahan dari SQLite ke DBMS lain.
- Service orchestration, NSSM/PM2 provisioning, Windows service dependency implementation, atau VPS deployment.
- Raw copy Active Database sebagai backup konsisten.
- Scheduled restore atau automatic destructive downgrade.
- Penggunaan database production nyata dalam automated restore drill.
