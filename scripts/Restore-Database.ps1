[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$BackupId,
    [switch]$DryRun,
    [switch]$FirstRestore
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backendRoot = Join-Path $projectRoot 'backend'
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Output '{"exit_code":2,"operation":"restore","reason":"PYTHON_UNAVAILABLE","status":"FAILED","success":false}'
    exit 2
}
$cliArguments = @('-m', 'app.recovery.cli', 'restore', '--backup-id', $BackupId)
if ($DryRun) { $cliArguments += '--dry-run' }
if ($FirstRestore) { $cliArguments += '--first-restore' }
Push-Location -LiteralPath $backendRoot
try {
    & $python @cliArguments
    $code = $LASTEXITCODE
} catch {
    Write-Output '{"exit_code":1,"operation":"restore","reason":"WRAPPER_FAILED","status":"FAILED","success":false}'
    $code = 1
} finally {
    Pop-Location
}
exit $code
