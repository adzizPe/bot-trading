[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('HOST','PROCESS_MANAGER','PROCESS_COUNT','EDGE','BACKEND','CERTIFICATE','CAPACITY','LOG_ROTATION','RECOVERY','SCHEDULED_TASK','DELIVERY')][string]$Category = 'HOST',
    [string]$Root = (Join-Path $PSScriptRoot '..\operations-state'),
    [ValidateSet('NSSM','PM2')][string]$ProcessManager = 'NSSM',
    [ValidateRange(1, 5)][int]$TimeoutSeconds = 5,
    [string]$EvidenceId = 'monitoring-check-plan', [string]$EnvironmentSource = '',
    [string]$PythonPath = '', [switch]$Execute
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Operations.Common.ps1')
$operationArguments = @('--category',$Category)
Invoke-OperationsCli -Operation 'monitoring-check' -Root $Root -ProcessManager $ProcessManager -TimeoutSeconds $TimeoutSeconds -EvidenceId $EvidenceId -EnvironmentSource $EnvironmentSource -PythonPath $PythonPath -OperationArguments $operationArguments -Execute:$Execute -WhatIfMode ([bool]$WhatIfPreference)
