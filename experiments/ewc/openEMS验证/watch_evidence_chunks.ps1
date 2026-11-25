param(
    [int]$MainTarget = 21,
    [int]$ParallelTarget = 6,
    [int]$NewCaseTarget = 1,
    [int]$Hours = 6
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$mainDir = "G:\ewc-evidence\gauss8us\111_loss0_2.5ps"
$parDirs = @(
    "E:\ewc-evidence\gauss_parallel\111_loss0_1.25ps",
    "E:\ewc-evidence\gauss_parallel\111_loss1_1.25ps",
    "E:\ewc-evidence\gauss_parallel\mixed_history_1.25ps"
)
$newDirs = @(
    "E:\ewc-evidence\gauss_parallel\111_loss1_2.5ps",
    "E:\ewc-evidence\gauss_parallel\mixed_history_2.5ps"
)

function Count-Chunks([string]$path) {
    return (Get-ChildItem $path -Filter "steps_*.npz" -ErrorAction SilentlyContinue).Count
}

function Log([string]$text) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $text
    Add-Content -Path (Join-Path $root "watch_next_chunks_2.log") -Value $line -Encoding utf8
}

$deadline = (Get-Date).AddHours($Hours)
Log ("WATCH2_START main_target={0} parallel_target={1} new_case_target={2} hours={3}" -f $MainTarget, $ParallelTarget, $NewCaseTarget, $Hours)

while ((Get-Date) -lt $deadline) {
    $nMain = Count-Chunks $mainDir
    $nPar = ($parDirs | ForEach-Object { Count-Chunks $_ } | Measure-Object -Minimum).Minimum
    $nNew = ($newDirs | ForEach-Object { Count-Chunks $_ } | Measure-Object -Minimum).Minimum
    Log ("POLL main={0} parallel_min={1} new_min={2}" -f $nMain, $nPar, $nNew)

    if ($nMain -ge $MainTarget) {
        $marker = Join-Path $root ("verify_gauss_partial_{0}.log" -f $MainTarget)
        if (-not (Test-Path $marker)) {
            Log ("RUN gauss_longtime_partial target={0}" -f $MainTarget)
            $out = python -B (Join-Path $PSScriptRoot "verify_copper_gauss_longtime.py") --partial 2>&1
            Set-Content -Path $marker -Value ($out -join "`n") -Encoding utf8
            Log ("DONE gauss_longtime_partial exit={0} tail={1}" -f $LASTEXITCODE, ($out | Select-Object -Last 1))
        }
        $MainTarget = $nMain + 1
    }

    if ($nPar -ge $ParallelTarget) {
        $marker = Join-Path $root ("verify_parallel_partial_{0}.log" -f $ParallelTarget)
        if (-not (Test-Path $marker)) {
            Log ("RUN gauss_parallel_partial target={0}" -f $ParallelTarget)
            $out = python -B (Join-Path $PSScriptRoot "verify_copper_gauss_parallel.py") --partial 2>&1
            Set-Content -Path $marker -Value ($out -join "`n") -Encoding utf8
            Log ("DONE gauss_parallel_partial exit={0} tail={1}" -f $LASTEXITCODE, ($out | Select-Object -Last 1))
        }
        $ParallelTarget = $nPar + 1
    }

    if ($nNew -ge $NewCaseTarget) {
        $stage = "{0}_{1}_{2}" -f $MainTarget, $ParallelTarget, $NewCaseTarget
        $marker = Join-Path $root ("verify_all_histories_partial_{0}.log" -f $stage)
        if (-not (Test-Path $marker)) {
            Log ("RUN all_histories_partial stage={0}" -f $stage)
            $out = python -B (Join-Path $PSScriptRoot "verify_copper_gauss_all_histories.py") --partial 2>&1
            Set-Content -Path $marker -Value ($out -join "`n") -Encoding utf8
            Log ("DONE all_histories_partial exit={0} tail={1}" -f $LASTEXITCODE, ($out | Select-Object -Last 1))
        }
        $NewCaseTarget = $nNew + 1
    }

    Start-Sleep -Seconds 120
}

Log "WATCH2_END"
