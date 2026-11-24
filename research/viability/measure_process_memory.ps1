# Sample the Lyrica GUI process during playback. The packaged executable also
# has a small launcher process. Select the GUI process by its larger working set.
# $app = Get-Process Lyrica | Sort-Object WorkingSet64 -Descending | Select-Object -First 1
# ./research/viability/measure_process_memory.ps1 -ProcessId $app.Id | Export-Csv ram.csv -NoTypeInformation
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateRange(1, 2147483647)]
    [int] $ProcessId,
    [ValidateRange(1, 86400)]
    [int] $DurationSeconds = 10800,
    [ValidateRange(1, 3600)]
    [int] $IntervalSeconds = 30
)

$process = Get-Process -Id $ProcessId -ErrorAction Stop
$timer = [Diagnostics.Stopwatch]::StartNew()
try {
    while (-not $process.HasExited) {
        $process.Refresh()
        [pscustomobject]@{
            Time = [DateTimeOffset]::Now.ToString('o')
            ProcessId = $ProcessId
            ElapsedSeconds = [int]$timer.Elapsed.TotalSeconds
            WorkingSetMiB = [math]::Round($process.WorkingSet64 / 1MB, 1)
            PrivateCommitMiB = [math]::Round($process.PrivateMemorySize64 / 1MB, 1)
            Threads = $process.Threads.Count
            Handles = $process.HandleCount
        }
        $remaining = $DurationSeconds - $timer.Elapsed.TotalSeconds
        if ($remaining -le 0) { break }
        $delay = [math]::Min($IntervalSeconds, $remaining)
        if ($process.WaitForExit([int][math]::Ceiling($delay * 1000))) { break }
    }
}
finally {
    $process.Dispose()
}
