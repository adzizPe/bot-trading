# Native Windows SQLite Backup and Recovery

This runbook covers Milestone 10.7 operator-side recovery on native Windows. It does not deploy anything and it never starts or stops the backend automatically. Run every command from an approved maintenance session as the dedicated recovery account.

Milestone 10.8 orchestration handoff, durable Restore Hold, service ordering, readiness, and Operator Evidence Package are documented in the [primary native Windows service operations runbook](windows-service-operations.md). That cross-reference does not change this runbook's restore semantics: restore remains manual, backup-ID-driven, offline, and no-auto-start on success or failure. Keep Restore Hold active until all post-checks and two-person sign-off pass; the first post-restore start is separately approved and must prove Backend Readiness via loopback and Nginx proxy. Static `/healthz` is Edge Liveness only.

## Non-negotiable safety rules

- **NEVER raw-copy the active database as a backup.** A file copy while SQLite is active can omit committed WAL state or capture an inconsistent point in time. Daily backup must use `Backup-Database.ps1`, which uses SQLite's online backup mechanism.
- Keep exactly one Uvicorn worker. Multiple workers violate the single-owner SQLite/runtime-lease model.
- Backup may run while the application writes. Restore, restore dry-run, and final forensic preservation require the backend and every other SQLite writer to be offline.
- Select a backup by backup ID only. Never pass or replace an arbitrary database path.
- Never schedule restore. Never place an encryption key, account credential, or secret in this repository, a PowerShell script, Task Scheduler arguments, logs, or command history.
- A failed restore, failed forensic copy, failed replacement, or failed post-restore check is fail-closed: do not restart the backend, demo subsystem, or MT5. After a successful restore, also do not restart anything until all post-restore checks and operator sign-off are complete.
- Do not delete, rename, checkpoint, or copy the active DB, `-wal`, or `-shm` files while a writer may be running.

## Recovery contract and ownership

The default objectives are RPO **24 hours** and RTO **2 hours**. Daily backup interval is **24 hours**. GFS retention keeps **7 daily**, **4 weekly**, and **3 monthly** recovery points. These are objectives, not guarantees; `Get-BackupStatus.ps1` supplies the measured evidence.

Each managed backup directory contains exactly `artifact.btbak` and `manifest.json`, plus `offhost-receipt.json` after a verified copy. The per-backup `manifest.json` is the source of truth. `status.json` is only a sanitized, rebuildable cache and must never be used to override a manifest.

Required configuration names are:

```text
BACKUP_RPO_HOURS=24
BACKUP_RTO_HOURS=2
BACKUP_INTERVAL_HOURS=24
BACKUP_RETENTION_DAILY=7
BACKUP_RETENTION_WEEKLY=4
BACKUP_RETENTION_MONTHLY=3
BACKUP_LOCAL_DIRECTORY=<absolute managed local path>
BACKUP_OFFHOST_DIRECTORY=<absolute secondary-host or secondary-volume path>
BACKUP_ENCRYPTION_REQUIRED=true
BACKUP_ENCRYPTION_KEY_ENV=BACKUP_ENCRYPTION_KEY
BACKUP_COMPRESSION=gzip
BACKUP_BUSY_TIMEOUT_SECONDS=30
BACKUP_OPERATION_TIMEOUT_SECONDS=3600
```

`BACKUP_ENCRYPTION_KEY` intentionally has no default. Blank destinations and missing key material fail closed when an operation needs them. The active `DATABASE_URL`, local catalog, off-host destination, work directory, and forensic directory must not alias each other.

## Account, filesystem, and key prerequisites

1. Use a dedicated, non-interactive, least-privilege Windows recovery account. Grant only Log on as a batch job, read access to the active SQLite files for backup, and modify access to explicitly managed backup/off-host/work/forensic roots.
2. Deny ordinary users access to those roots. Apply restrictive NTFS ACLs to the account, administrators, and the approved monitoring identity only; verify inheritance before first use.
3. Place active data, recovery work, and backup storage on encrypted Windows volumes. Temporary plaintext snapshot/candidate deletion is best effort: NTFS, SSD wear levelling, snapshots, and storage remanence mean unlinking cannot guarantee secure erasure. Encryption at rest and restrictive ACLs are mandatory compensating controls.
4. Confirm adequate free space for the source/WAL estimate, encrypted artifact, verification round-trip, forensic DB/WAL/SHM, and restore staging. Do not rely on retention to rescue an already full volume.
5. Supply the 32-byte random key as base64 through the protected runtime environment named by `BACKUP_ENCRYPTION_KEY_ENV`, or through the restore command's secure interactive prompt. Never use a task argument or command-line key.

## Daily backup, verification, off-host copy, and retention

Run from the repository root. The wrappers locate `backend\.venv\Scripts\python.exe`, switch to the backend working directory, emit one sanitized JSON result, and preserve the CLI exit code.

```powershell
Set-Location -LiteralPath D:\bot-trading
.\scripts\Backup-Database.ps1
.\scripts\Verify-Backup.ps1 -BackupId '<backup-uuid-from-backup-output>'
.\scripts\Copy-BackupOffHost.ps1 -BackupId '<same-backup-uuid>'
.\scripts\Invoke-BackupRetention.ps1 -DryRun
.\scripts\Invoke-BackupRetention.ps1
.\scripts\Get-BackupStatus.ps1
```

Stop immediately on any non-zero exit. Do not infer "latest" from directory timestamps: carry the exact `backup_id` returned by the successful backup into verify and off-host copy. Verification must end with `VALID`; off-host copy must report a verified result before retention may consider that copy satisfied. Review every retention dry-run before deletion. Retention protects active/incomplete/unverified items, the newest valid backup, restore leases, and required backups not yet verified off-host; unknown files are not operator cleanup targets.

Record the JSON output, task exit code, backup ID, verification result, off-host receipt result, retention summary, free-space observation, and operator identity in the recovery log. Logs must not contain raw paths, environment dumps, keys, or credentials.

## Recovery status and alerts

```powershell
Set-Location -LiteralPath D:\bot-trading
.\scripts\Get-BackupStatus.ps1
```

The JSON `recovery` object contains `availability`, `last_successful_backup_at`, `last_verified_backup_at`, `backup_age_seconds`, `rpo_target_seconds`, `rpo_met`, `offhost_status`, `last_offhost_verified_at`, `next_scheduled_backup_at`, `latest_restore_drill_at`, `latest_restore_status`, `latest_restore_seconds`, `rto_target_seconds`, `rto_met`, and `latest_failure_category`.

`NEVER` means no manifest exists; `UNAVAILABLE` means manifests exist but no verified valid recovery point is available. Alert if the command exits non-zero, `availability` is not `AVAILABLE`, `rpo_met` is false, `backup_age_seconds` approaches or exceeds 86400, off-host status is not verified after the daily window, a latest failure exists, or the next scheduled time has passed. A local `VALID` artifact and an off-host `VERIFIED` artifact are distinct evidence.

## Restore dry-run (required before normal restore)

Both dry-run and normal restore are offline maintenance operations. Obtain incident/change approval, identify a verified backup ID, and have a second operator observe.

1. Manually place the native backend in its approved offline maintenance state. Confirm the Uvicorn process and every other SQLite writer are stopped; do not manipulate DB/WAL/SHM files.
2. Confirm the correct historical key is available through the protected environment or secure prompt. Confirm free space and that no other recovery operation is active.
3. Run:

```powershell
Set-Location -LiteralPath D:\bot-trading
.\scripts\Restore-Database.ps1 -BackupId '<verified-backup-uuid>' -DryRun
```

4. Require exit code `0`, `success=true`, `dry_run=true`, and status `VALIDATED`. Save the sanitized output for sign-off.
5. A dry-run authenticates/decrypts/decompresses a temporary candidate, verifies identity and SHA-256, runs full SQLite integrity checks, validates/migrates only the candidate for repository revision compatibility, and runs read-only repository smoke checks. It does **not** create a final forensic copy, clean active WAL/SHM, replace the active database, or modify active DB/WAL/SHM bytes.
6. If dry-run fails, leave the service offline. Resolve key, artifact, checksum, integrity, revision, smoke, lock, or space failures; do not proceed to normal restore.

## Normal restore and mandatory post-restore hold

Normal restore is destructive to the active target and must follow a passing dry-run for the same backup ID and key.

```powershell
Set-Location -LiteralPath D:\bot-trading
.\scripts\Restore-Database.ps1 -BackupId '<same-verified-backup-uuid>'
```

Use `-FirstRestore` only for an approved initial installation where no active database exists. It is not a bypass for a failed forensic copy or damaged active database.

The restore must acquire operation/runtime exclusion, revalidate the artifact and candidate, preserve the stopped original DB/WAL/SHM as forensic evidence, stage on the same volume, atomically replace once, and repeat integrity/revision/smoke checks. There is no overwrite or delete-copy fallback.

Do not restart the backend, demo subsystem, or MT5 after the command returns—even on success—until all of these are true:

- exit code is `0`, `success=true`, and status is `RESTORED`;
- the output names the expected backup ID and a restore ID;
- the forensic directory contains its manifest and checksums for every original DB/WAL/SHM file that existed;
- the built-in post-replacement integrity, repository revision, and smoke checks passed;
- `Get-BackupStatus.ps1` succeeds and existing backup evidence remains readable;
- the incident/change record includes elapsed time against the 2-hour RTO, evidence locations, and two-person sign-off.

Any sharing violation, forensic failure, replacement failure, or post-check failure keeps the application offline. Preserve all output and files, escalate, and make an explicit rollback/remediation decision; never retry by manually replacing files.

## Forensic DB/WAL/SHM handling

The original DB, WAL, and SHM are one incident evidence set. Preservation occurs only after all writers stop and before publication of the replacement. The restore service copies each file that exists, calculates checksums, and writes `forensic-manifest.json` with classification `FORENSIC_NOT_VERIFIED_BACKUP`.

That classification means evidence, not a valid restore source. Do not open it read-write, checkpoint it, merge WAL manually, rename it into service, or feed it to retention. Restrict ACLs, keep it on encrypted storage, record chain of custody, and copy it to approved incident evidence storage only after the source is offline. If preservation fails and this is not a genuine first restore with no active DB, fail closed and keep the service offline.

## Failure procedures

### Suspected corruption

1. Declare an incident and keep or place every writer offline. Do not raw-copy the active database while it is active and do not delete DB/WAL/SHM.
2. Capture sanitized application/recovery outputs, timestamps, free space, and host/storage symptoms. Preserve original DB/WAL/SHM through the restore forensic stage.
3. Select the newest local `VALID` backup with matching off-host `VERIFIED` evidence, obtain its historical key, run dry-run, then normal restore.
4. If any check fails, remain offline and escalate. Do not attempt manual WAL repair or restart to "test" the database.

### Disk full or low space

- A failed or partial backup is not valid. Do not publish, copy off-host, or restore it.
- Free capacity using the approved storage process outside active DB/WAL/SHM and outside unknown/unowned catalog files. Inspect retention with `Invoke-BackupRetention.ps1 -DryRun`; only run retention when its plan is safe.
- Check local, work, forensic, same-volume staging, and off-host capacity separately. Retry the original operation only after capacity and filesystem health are confirmed.
- If a write failed mid-operation, retain sanitized evidence; managed partials are reconciled by the recovery subsystem, not by ad-hoc deletion.

### Off-host unavailable or checksum mismatch

- Local validity is unchanged, but disaster-recovery posture is degraded. Alert immediately and prevent retention from removing recovery points required off-host.
- Restore connectivity/capacity/permissions, then retry `Copy-BackupOffHost.ps1` with the same backup ID. Publication is idempotent only when identity and checksum match.
- Quarantine a mismatched destination through the approved incident process. Never bless it by editing a receipt or manifest.

## Encryption-key lifecycle

- **Generate:** use an approved cryptographic random generator to create exactly 32 random bytes and base64-encode them. Generate outside this repository and outside task command lines; never derive from a password.
- **Store:** keep the active key in an approved Windows-protected secret facility accessible only to the dedicated recovery account. Runtime injection uses the environment name configured by `BACKUP_ENCRYPTION_KEY_ENV`; the key value never belongs in `.env`, `.env.example`, source, scripts, manifests, receipts, logs, or task arguments.
- **Escrow:** maintain two controlled, tested escrow copies under separate custodians. Record key version, activation/retirement time, and covered backup IDs in the external secret inventory—not in the backup catalog.
- **Rotate:** generate a new key, update protected runtime injection, create/verify/copy a new backup, and test restore dry-run. Retain old keys while any retained local/off-host/forensic artifact depends on them; rotation does not re-encrypt old artifacts.
- **Revoke/retire:** only destroy an old key after GFS expiration, legal/incident holds, off-host copies, and escrow inventory all confirm no required artifact depends on it.
- **Loss:** there is no key reset or bypass. A lost key makes its encrypted artifacts unrecoverable. Keep affected files unchanged for investigation, mark the recovery gap, alert RPO/RTO owners, and establish a new verified recovery point with a new key.

## GFS retention, drill, and sign-off

Always inspect before deleting:

```powershell
.\scripts\Invoke-BackupRetention.ps1 -DryRun
.\scripts\Invoke-BackupRetention.ps1
```

The deterministic UTC plan keeps the newest eligible backup for 7 daily buckets, 4 ISO-week buckets, and 3 year-month buckets; overlapping selections form one keep set. Do not manually delete backup directories to imitate GFS. Place legal/incident holds outside the automated deletion flow and verify them before approval.

Run an isolated restore drill at least monthly and after material recovery changes:

```powershell
.\scripts\Invoke-RestoreDrill.ps1
```

The drill uses generated non-production data and a random non-production key. Require exit code `0`, status `PASS`, integrity `ok`, repository head compatibility, matching fingerprints, verified simulated off-host checksum, and measured restore time at or below 7200 seconds. A failed drill is an operational alert; it never authorizes testing against the active database.

Daily sign-off records: backup ID; UTC start/end; backup, verify, copy, retention, and status exit codes; manifest status; off-host receipt; RPO; free space; alerts; and operator. Drill/restore sign-off additionally records stage evidence, integrity/revision/fingerprint results, RTO, forensic evidence, approver, and second operator.

## Windows PowerShell 5.1 Task Scheduler flow

Create two native Task Scheduler tasks under the dedicated recovery account: a daily backup pipeline and a periodic isolated drill. **Never create a scheduled restore task.** Task Scheduler may store the account credential using Windows-protected task storage; the credential and encryption key must not appear in the repository, driver script, action arguments, or logs.

Daily task settings:

- trigger every 24 hours with a start time that leaves alert/remediation margin before the 24-hour RPO;
- `Run whether user is logged on or not`, dedicated least privilege account, no interactive logon, and highest privileges disabled unless a documented ACL requirement proves otherwise;
- action `powershell.exe` with `-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File D:\Ops\Invoke-SqliteBackupPipeline.ps1`;
- **Start in** `D:\bot-trading` (the working directory is mandatory);
- multiple-instance policy `IgnoreNew` (no overlap), a four-hour Task Scheduler execution timeout, bounded retry, and no parallel recovery task;
- protected runtime environment supplies the key to the task process; do not add it to the action or driver.

The operator-owned driver lives outside the repository, has restrictive ACLs, contains no key/credential, and performs exactly backup -> verify -> off-host copy -> retention. Its PowerShell 5.1-compatible logic is:

```powershell
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath 'D:\bot-trading'
function Invoke-RecoveryStep([string]$Script, [string[]]$Arguments) {
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $processArguments = @(
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'RemoteSigned',
        '-File', $Script
    ) + $Arguments
    $lines = @(& $shell @processArguments)
    $code = $LASTEXITCODE
    $lines | Write-Output
    if ($code -ne 0) { exit $code }
    return ($lines[-1] | ConvertFrom-Json)
}
$backup = Invoke-RecoveryStep '.\scripts\Backup-Database.ps1' @()
$id = [string]$backup.backup_id
if ([string]::IsNullOrWhiteSpace($id)) { exit 1 }
$null = Invoke-RecoveryStep '.\scripts\Verify-Backup.ps1' @('-BackupId', $id)
$null = Invoke-RecoveryStep '.\scripts\Copy-BackupOffHost.ps1' @('-BackupId', $id)
$null = Invoke-RecoveryStep '.\scripts\Invoke-BackupRetention.ps1' @()
exit 0
```

Before scheduling it, execute retention dry-run manually and validate the driver using generated/non-production paths. Do not add service start/stop actions.
Periodic drill task settings mirror the same account, working directory, `IgnoreNew`, and non-interactive PowerShell policy. Run `scripts\Invoke-RestoreDrill.ps1` monthly with a two-hour timeout, never concurrently with the daily pipeline.

Enable and monitor the `Microsoft-Windows-TaskScheduler/Operational` Event Log. Alert on task start/action failures, non-zero action result/`LastTaskResult`, timeout/termination, ignored launch caused by overlap, missing expected successful completion, and any disabled task. Forward only sanitized task metadata and wrapper JSON.

Create a separate watchdog task every 15 minutes that runs `scripts\Get-BackupStatus.ps1`, parses the single JSON object, and raises an alert without changing recovery state. Warning threshold is 20 hours since `last_verified_backup_at`; critical threshold is `rpo_met=false`, `backup_age_seconds >= 86400`, unavailable recovery, stale `next_scheduled_backup_at`, off-host not verified after the daily pipeline, non-null `latest_failure_category`, drill failure, or `rto_met=false`. The watchdog must return non-zero when it raises a critical alert so Task Scheduler and Event Log monitoring both observe it.

Test all task definitions with generated paths and no production database. Export task XML to the protected operations evidence location for review, not to this repository. Review task account rights, ACLs, timeout, no-overlap policy, working directory, protected key injection, Event Log subscription, watchdog delivery, and restore-task absence quarterly.

## Operator completion checklist

- [ ] Correct host, maintenance/change record, backup ID, and historical key version confirmed.
- [ ] Exactly one Uvicorn worker policy confirmed; restore has all writers offline.
- [ ] Daily backup is `VALID`, separately verified, and copied off-host with checksum evidence.
- [ ] RPO status and free-space checks pass; retention dry-run was reviewed before retention.
- [ ] Restore dry-run passed before normal restore; original DB/WAL/SHM forensic evidence is accounted for.
- [ ] Post-restore integrity, revision, smoke, status, and 2-hour RTO evidence pass before any restart.
- [ ] Drill evidence is current; Event Log, exit-code, watchdog, overlap, and stale-RPO alerts are healthy.
- [ ] Two-person sign-off is recorded and contains no key, credential, raw path, or environment dump.
