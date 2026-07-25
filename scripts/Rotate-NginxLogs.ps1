[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$NginxRoot = 'C:\nginx',
    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 30
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($NginxRoot)
$testScript = Join-Path $PSScriptRoot 'Test-NginxConfig.ps1'
& $testScript -NginxRoot $root

$logDirectory = Join-Path $root 'logs'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$names = @('access.log', 'websocket-access.log', 'error.log')
foreach ($name in $names) {
    $source = Join-Path $logDirectory $name
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        $archive = Join-Path $logDirectory ("{0}-{1}.log" -f [IO.Path]::GetFileNameWithoutExtension($name), $stamp)
        if ($PSCmdlet.ShouldProcess($source, "Rotate to $archive")) {
            Move-Item -LiteralPath $source -Destination $archive
        }
    }
}

$executable = Join-Path $root 'nginx.exe'
if ($PSCmdlet.ShouldProcess($executable, 'Reopen Nginx logs')) {
    & $executable -p ($root.TrimEnd('\', '/') + '/') -s reopen
    if ($LASTEXITCODE -ne 0) { throw "Nginx log reopen failed: $LASTEXITCODE" }
}

$cutoff = (Get-Date).AddDays(-$RetentionDays)
Get-ChildItem -LiteralPath $logDirectory -File -Filter '*.log' |
    Where-Object { $_.Name -match '^(access|websocket-access|error)-\d{8}-\d{6}\.log$' -and $_.LastWriteTime -lt $cutoff } |
    ForEach-Object {
        if ($PSCmdlet.ShouldProcess($_.FullName, 'Delete expired rotated log')) {
            Remove-Item -LiteralPath $_.FullName
        }
    }
