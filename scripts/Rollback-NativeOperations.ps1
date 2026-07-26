[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$ChangeId,
    [Parameter(Mandatory = $true)][string]$OperatorId,
    [Parameter(Mandatory = $true)][string]$ReviewerId,
    [Parameter(Mandatory = $true)][string]$ReleaseId,
    [Parameter(Mandatory = $true)][string]$DatabaseRevision,
    [string]$Root = (Join-Path $PSScriptRoot '..\operations-state'),
    [ValidateSet('NSSM','PM2')][string]$ProcessManager = 'NSSM',
    [ValidateRange(1, 300)][int]$TimeoutSeconds = 300,
    [string]$EvidenceId = 'rollback-plan', [string]$EnvironmentSource = '',
    [string]$PythonPath = '', [switch]$Execute
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Operations.Common.ps1')
$operationArguments = @('--change-id',$ChangeId,'--operator-id',$OperatorId,'--reviewer-id',$ReviewerId,'--release-id',$ReleaseId,'--database-revision',$DatabaseRevision)
Invoke-OperationsCli -Operation 'rollback' -Root $Root -ProcessManager $ProcessManager -TimeoutSeconds $TimeoutSeconds -EvidenceId $EvidenceId -EnvironmentSource $EnvironmentSource -PythonPath $PythonPath -OperationArguments $operationArguments -Execute:$Execute -WhatIfMode ([bool]$WhatIfPreference)
