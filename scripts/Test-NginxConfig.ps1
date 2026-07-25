[CmdletBinding()]
param(
    [string]$NginxRoot = 'C:\nginx',
    [string]$ConfigPath = 'conf/nginx.conf'
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($NginxRoot)
$executable = Join-Path $root 'nginx.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "nginx.exe was not found at $executable"
}

$prefix = $root.TrimEnd('\', '/') + '/'
& $executable -p $prefix -c $ConfigPath -t
if ($LASTEXITCODE -ne 0) {
    throw "Nginx configuration test failed with exit code $LASTEXITCODE"
}
Write-Output 'Nginx configuration test passed.'
