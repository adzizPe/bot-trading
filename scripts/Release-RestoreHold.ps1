[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$ChangeId,
    [Parameter(Mandatory = $true)][string]$OperatorId,
    [Parameter(Mandatory = $true)][string]$ReviewerId,
    [Parameter(Mandatory = $true)][string]$RestoreId,
    [string]$Root = (Join-Path $PSScriptRoot '..\operations-state'),
    [ValidateSet('NSSM','PM2')][string]$ProcessManager = 'NSSM',
    [ValidateRange(1, 300)][int]$TimeoutSeconds = 120,
    [string]$EvidenceId = 'restore-hold-release-plan', [string]$EnvironmentSource = '',
    [string]$PythonPath = '', [switch]$Execute
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Operations.Common.ps1')
$operationArguments = @('--change-id',$ChangeId,'--operator-id',$OperatorId,'--reviewer-id',$ReviewerId,'--restore-id',$RestoreId)
Invoke-OperationsCli -Operation 'restore-hold-release' -Root $Root -ProcessManager $ProcessManager -TimeoutSeconds $TimeoutSeconds -EvidenceId $EvidenceId -EnvironmentSource $EnvironmentSource -PythonPath $PythonPath -OperationArguments $operationArguments -Execute:$Execute -WhatIfMode ([bool]$WhatIfPreference)
