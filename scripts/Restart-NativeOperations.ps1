[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Root = (Join-Path $PSScriptRoot '..\operations-state'),
    [ValidateSet('NSSM','PM2')][string]$ProcessManager = 'NSSM',
    [ValidateRange(1, 300)][int]$TimeoutSeconds = 120,
    [string]$ReleaseId = 'current-release',
    [string]$EvidenceId = 'restart-plan',
    [string]$EnvironmentSource = '',
    [string]$PythonPath = '',
    [switch]$Execute
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Operations.Common.ps1')
$operationArguments = @('--release-id', $ReleaseId)
Invoke-OperationsCli -Operation 'restart' -Root $Root -ProcessManager $ProcessManager -TimeoutSeconds $TimeoutSeconds -EvidenceId $EvidenceId -EnvironmentSource $EnvironmentSource -PythonPath $PythonPath -OperationArguments $operationArguments -Execute:$Execute -WhatIfMode ([bool]$WhatIfPreference)
