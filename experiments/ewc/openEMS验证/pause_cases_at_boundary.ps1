param(
    [string[]]$Cases = @(
        'E:\ewc-evidence\gauss_parallel\111_loss1_2.5ps',
        'E:\ewc-evidence\gauss_parallel\mixed_history_2.5ps'
    ),
    [int[]]$Pids = @(25484, 13708),
    [int]$Hours = 6
)

$log = Join-Path (Split-Path $PSScriptRoot -Parent) 'pause_cases_at_boundary.log'
function Write-Log([string]$m) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Add-Content -Path $log -Encoding UTF8
}

$baseline = @{}
for ($i = 0; $i -lt $Cases.Count; $i++) {
    $p = Join-Path $Cases[$i] 'progress.json'
    $baseline[$i] = if (Test-Path $p) { (Get-Item $p).LastWriteTime } else { Get-Date }
}

$deadline = (Get-Date).AddHours($Hours)
Write-Log "start target_pids=$($Pids -join ',') deadline=$($deadline.ToString('yyyy-MM-dd HH:mm:ss'))"

while ((Get-Date) -lt $deadline) {
    $alive = 0
    for ($i = 0; $i -lt $Cases.Count; $i++) {
        $proc = Get-Process -Id $Pids[$i] -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        $alive++
        $p = Join-Path $Cases[$i] 'progress.json'
        if ((Test-Path $p) -and (Get-Item $p).LastWriteTime -gt $baseline[$i]) {
            Start-Sleep -Seconds 15
            Write-Log "boundary case=$($Cases[$i]) progress=$(Get-Item $p).LastWriteTime pid=$($Pids[$i])"
            Stop-Process -Id $Pids[$i] -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            if (Get-Process -Id $Pids[$i] -ErrorAction SilentlyContinue) {
                Write-Log "stop_failed pid=$($Pids[$i])"
            } else {
                Write-Log "stopped pid=$($Pids[$i])"
            }
        }
    }
    if ($alive -eq 0) { Write-Log 'all target processes gone'; break }
    Start-Sleep -Seconds 60
}
Write-Log 'watcher exit'
