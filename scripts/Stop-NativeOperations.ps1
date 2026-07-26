[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Root = (Join-Path $PSScriptRoot '..\operations-state'),
    [ValidateSet('NSSM','PM2')][string]$ProcessManager = 'NSSM',
    [ValidateRange(1, 300)][int]$TimeoutSeconds = 120,
    [string]$EvidenceId = 'stop-plan',
    [string]$EnvironmentSource = '',
    [string]$PythonPath = '',
    [switch]$Execute
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Operations.Common.ps1')
Invoke-OperationsCli -Operation 'stop' -Root $Root -ProcessManager $ProcessManager -TimeoutSeconds $TimeoutSeconds -EvidenceId $EvidenceId -EnvironmentSource $EnvironmentSource -PythonPath $PythonPath -Execute:$Execute -WhatIfMode ([bool]$WhatIfPreference)
