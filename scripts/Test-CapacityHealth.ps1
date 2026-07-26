[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Root = (Join-Path $PSScriptRoot '..\operations-state'),
    [ValidateSet('NSSM','PM2')][string]$ProcessManager = 'NSSM',
    [ValidateRange(1, 5)][int]$TimeoutSeconds = 5,
    [string]$EvidenceId = 'capacity-check-plan', [string]$EnvironmentSource = '',
    [string]$PythonPath = '', [switch]$Execute
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Operations.Common.ps1')
Invoke-OperationsCli -Operation 'capacity-check' -Root $Root -ProcessManager $ProcessManager -TimeoutSeconds $TimeoutSeconds -EvidenceId $EvidenceId -EnvironmentSource $EnvironmentSource -PythonPath $PythonPath -Execute:$Execute -WhatIfMode ([bool]$WhatIfPreference)
