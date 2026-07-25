[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [uri]$BaseUri,
    [ValidateRange(1, 1000000)]
    [int]$Requests = 1000,
    [ValidateRange(1, 500)]
    [int]$Concurrency = 20,
    [ValidateSet('/healthz')]
    [string]$Path = '/healthz'
)

if ($BaseUri.Scheme -ne 'https') { throw 'Benchmark BaseUri must use HTTPS.' }
Add-Type -AssemblyName System.Net.Http
$handler = [System.Net.Http.HttpClientHandler]::new()
$methods = [System.Net.DecompressionMethods]::GZip -bor [System.Net.DecompressionMethods]::Deflate
if ([Enum]::GetNames([System.Net.DecompressionMethods]) -contains 'Brotli') {
    $methods = $methods -bor [Enum]::Parse([System.Net.DecompressionMethods], 'Brotli')
}
$handler.AutomaticDecompression = $methods
$client = [System.Net.Http.HttpClient]::new($handler)
$client.Timeout = [TimeSpan]::FromSeconds(15)
$target = [uri]::new($BaseUri, $Path)
$completed = 0
$failed = 0
$watch = [System.Diagnostics.Stopwatch]::StartNew()
try {
    while ($completed + $failed -lt $Requests) {
        $count = [Math]::Min($Concurrency, $Requests - $completed - $failed)
        $tasks = @()
        for ($index = 0; $index -lt $count; $index++) {
            $tasks += $client.GetAsync($target)
        }
        foreach ($task in $tasks) {
            try {
                $response = $task.GetAwaiter().GetResult()
                if ($response.IsSuccessStatusCode) { $completed++ } else { $failed++ }
                $response.Dispose()
            } catch {
                $failed++
            }
        }
    }
} finally {
    $watch.Stop()
    $client.Dispose()
    $handler.Dispose()
}

[ordered]@{
    url = $target.AbsoluteUri
    requests = $Requests
    concurrency = $Concurrency
    successful = $completed
    failed = $failed
    elapsed_seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 6)
    requests_per_second = [Math]::Round($Requests / $watch.Elapsed.TotalSeconds, 2)
} | ConvertTo-Json -Compress
