Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Invoke-OperationsCli {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('setup','preflight','start','stop','restart','reboot','update','rollback','crash-loop','restore-hold-status','recovery-handoff','restore-hold-release','monitoring-check','certificate-check','capacity-check','log-check','hardening-check')]
        [string]$Operation,
        [Parameter(Mandatory = $true)][string]$Root,
        [ValidateSet('NSSM','PM2')][string]$ProcessManager = 'NSSM',
        [ValidateRange(1, 300)][int]$TimeoutSeconds = 120,
        [string]$EvidenceId = '',
        [string]$EnvironmentSource = '',
        [string]$PythonPath = '',
        [string[]]$OperationArguments = @(),
        [switch]$Execute,
        [bool]$WhatIfMode = $false
    )

    $projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $backendRoot = Join-Path $projectRoot 'backend'
    if ([string]::IsNullOrWhiteSpace($PythonPath)) {
        $PythonPath = Join-Path $backendRoot '.venv\Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        Write-Output ('{{"category":"INPUT","evidence_id":"wrapper-failed","exit_code":2,"mode":"PLAN","operation":"{0}","status":"PYTHON_UNAVAILABLE","success":false}}' -f $Operation)
        exit 2
    }

    $cliArguments = @(
        '-m', 'app.operations.cli', $Operation,
        '--root', [System.IO.Path]::GetFullPath($Root),
        '--process-manager', $ProcessManager,
        '--timeout-seconds', ([string]$TimeoutSeconds)
    )
    if (-not [string]::IsNullOrWhiteSpace($EvidenceId)) {
        $cliArguments += @('--evidence-id', $EvidenceId)
    }
    if (-not [string]::IsNullOrWhiteSpace($EnvironmentSource)) {
        $cliArguments += @('--environment-source', [System.IO.Path]::GetFullPath($EnvironmentSource))
    }
    $cliArguments += $OperationArguments
    if ($Execute -and -not $WhatIfMode) {
        $cliArguments += '--execute'
    }

    Push-Location -LiteralPath $backendRoot
    try {
        $output = @(& $PythonPath @cliArguments 2>$null)
        $code = $LASTEXITCODE
    } catch {
        $output = @()
        $code = 5
    } finally {
        Pop-Location
    }
    if ($output.Count -ne 1) {
        Write-Output ('{{"category":"OUTPUT","evidence_id":"wrapper-failed","exit_code":7,"mode":"PLAN","operation":"{0}","status":"MALFORMED","success":false}}' -f $Operation)
        exit 7
    }
    try {
        $summary = $output[0] | ConvertFrom-Json -ErrorAction Stop
        if (
            $null -eq $summary.category -or
            $null -eq $summary.evidence_id -or
            $null -eq $summary.exit_code -or
            $null -eq $summary.mode -or
            $null -eq $summary.operation -or
            $null -eq $summary.status -or
            $null -eq $summary.success -or
            [string]$summary.operation -ne $Operation -or
            [int]$summary.exit_code -ne $code -or
            [bool]$summary.success -ne ($code -eq 0) -or
            @('PLAN', 'EXECUTE') -notcontains [string]$summary.mode
        ) {
            throw 'invalid exit contract'
        }
    } catch {
        Write-Output ('{{"category":"OUTPUT","evidence_id":"wrapper-failed","exit_code":7,"mode":"PLAN","operation":"{0}","status":"MALFORMED","success":false}}' -f $Operation)
        exit 7
    }
    Write-Output $output[0]
    exit $code
}
