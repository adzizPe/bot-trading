[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backendRoot = Join-Path $projectRoot 'backend'
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Output '{"exit_code":2,"operation":"backup","reason":"PYTHON_UNAVAILABLE","status":"FAILED","success":false}'
    exit 2
}
$cliArguments = @('-m', 'app.recovery.cli', 'backup')
Push-Location -LiteralPath $backendRoot
try {
    & $python @cliArguments
    $code = $LASTEXITCODE
} catch {
    Write-Output '{"exit_code":1,"operation":"backup","reason":"WRAPPER_FAILED","status":"FAILED","success":false}'
    $code = 1
} finally {
    Pop-Location
}
exit $code
