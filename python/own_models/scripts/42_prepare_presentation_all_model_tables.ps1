$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$Results = Join-Path $Root "results\own_vs_powermodels"
$Organized = Join-Path $Results "_organized_for_meeting"
$CaseOrder = @("5_pjm", "14_ieee", "30_ieee", "118_ieee", "300_ieee", "1354_pegase", "1888_rte", "6470_rte")
$CaseRank = @{}
for ($i = 0; $i -lt $CaseOrder.Count; $i++) {
    $CaseRank[$CaseOrder[$i]] = $i
}

function To-Double($value) {
    if ($null -eq $value -or "$value" -eq "") { return $null }
    $parsed = 0.0
    if ([double]::TryParse("$value", [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$parsed)) {
        return $parsed
    }
    return $null
}

function To-Bool($value) {
    return "$value".Trim().ToLowerInvariant() -in @("true", "1", "yes")
}

function Family-FromConstraint($name) {
    $n = "$name".ToLowerInvariant()
    if ($n.StartsWith("angle")) { return "angle" }
    if ($n.StartsWith("pg")) { return "pg" }
    if ($n.StartsWith("qg")) { return "qg" }
    if ($n.StartsWith("vm") -or $n.StartsWith("voltage")) { return "vm" }
    if ($n.StartsWith("sf") -or $n.StartsWith("st") -or $n.StartsWith("thermal")) { return "thermal" }
    if ($n -eq "") { return "unknown" }
    return $n
}

function Model-FromJuliaVariant($variant) {
    $v = "$variant".ToLowerInvariant()
    if ($v -eq "current") { return "julia_current" }
    if ($v -eq "pm_like" -or $v -eq "pmlike") { return "julia_pm_like" }
    if ($v -eq "") { return "julia_unknown" }
    return "julia_$v"
}

function Format-Number($value) {
    if ($null -eq $value) { return "" }
    if ($value -is [string]) { return $value }
    return ([double]$value).ToString("G12", [System.Globalization.CultureInfo]::InvariantCulture)
}

function New-Bucket($source) {
    [ordered]@{
        n_constraints = 0
        n_binding_mismatches = 0
        n_equality_only_violations = 0
        n_pm_violations = 0
        max_equality_only_violation = 0.0
        max_abs_value_diff = 0.0
        max_abs_margin_diff = 0.0
        max_abs_equality_only_margin = 0.0
        max_abs_pm_margin = 0.0
        source = $source
    }
}

function Add-Max($bucket, $key, $value) {
    $v = To-Double $value
    if ($null -ne $v -and $v -gt $bucket[$key]) {
        $bucket[$key] = $v
    }
}

function Median($values) {
    $xs = @($values | Where-Object { $null -ne $_ } | Sort-Object)
    if ($xs.Count -eq 0) { return $null }
    $mid = [int]($xs.Count / 2)
    if ($xs.Count % 2 -eq 1) { return [double]$xs[$mid] }
    return ([double]$xs[$mid - 1] + [double]$xs[$mid]) / 2.0
}

function Mean($values) {
    $xs = @($values | Where-Object { $null -ne $_ })
    if ($xs.Count -eq 0) { return $null }
    return (($xs | Measure-Object -Average).Average)
}

$EqualityBuckets = @{}
$JuliaHPath = Join-Path $Results "equality_only_deviation_inequality_level_by_family_julia.csv"
$PythonHPath = Join-Path $Results "python_pm_like\equality_only_deviation_inequality_levels.csv"

foreach ($row in Import-Csv -LiteralPath $JuliaHPath) {
    $model = Model-FromJuliaVariant $row.variant
    $family = Family-FromConstraint $row.constraint_type
    $key = "$model|$($row.case)|$($row.scenario)|$family"
    if (-not $EqualityBuckets.ContainsKey($key)) {
        $EqualityBuckets[$key] = New-Bucket (Split-Path $JuliaHPath -Leaf)
    }
    $bucket = $EqualityBuckets[$key]
    $n = To-Double $row.n_constraints
    if ($null -ne $n) { $bucket.n_constraints += [int]$n }
    $m = To-Double $row.n_binding_mismatches
    if ($null -ne $m) { $bucket.n_binding_mismatches += [int]$m }
    $v = To-Double $row.n_equality_only_violations
    if ($null -ne $v) { $bucket.n_equality_only_violations += [int]$v }
    Add-Max $bucket "max_equality_only_violation" $row.max_violation
    Add-Max $bucket "max_abs_value_diff" $row.max_abs_value_diff
    Add-Max $bucket "max_abs_margin_diff" $row.max_abs_margin_diff
    Add-Max $bucket "max_abs_equality_only_margin" $row.max_abs_equality_only_margin
    Add-Max $bucket "max_abs_pm_margin" $row.max_abs_pm_margin
}

foreach ($row in Import-Csv -LiteralPath $PythonHPath) {
    if ($row.diagnostic_mode -ne "equality_only" -or $row.variant -ne "full") { continue }
    $model = if ($row.model_variant) { $row.model_variant } else { "python_pm_like_zero" }
    $family = Family-FromConstraint $row.constraint_type
    $key = "$model|$($row.case)|$($row.scenario)|$family"
    if (-not $EqualityBuckets.ContainsKey($key)) {
        $EqualityBuckets[$key] = New-Bucket (Split-Path $PythonHPath -Leaf)
    }
    $bucket = $EqualityBuckets[$key]
    $bucket.n_constraints += 1
    if ((To-Bool $row.equality_only_binding) -ne (To-Bool $row.pm_binding)) { $bucket.n_binding_mismatches += 1 }
    if (To-Bool $row.equality_only_violates_limit) { $bucket.n_equality_only_violations += 1 }
    if (To-Bool $row.pm_violates_limit) { $bucket.n_pm_violations += 1 }
    Add-Max $bucket "max_equality_only_violation" $row.equality_only_violation
    Add-Max $bucket "max_abs_value_diff" $row.abs_value_diff
    Add-Max $bucket "max_abs_margin_diff" $row.abs_margin_diff
    $eqMargin = To-Double $row.equality_only_margin
    if ($null -ne $eqMargin) { Add-Max $bucket "max_abs_equality_only_margin" ([math]::Abs($eqMargin)) }
    $pmMargin = To-Double $row.pm_margin
    if ($null -ne $pmMargin) { Add-Max $bucket "max_abs_pm_margin" ([math]::Abs($pmMargin)) }
}

$EqualityRows = foreach ($key in $EqualityBuckets.Keys) {
    $parts = $key.Split("|")
    $bucket = $EqualityBuckets[$key]
    [pscustomobject]@{
        model = $parts[0]
        case = $parts[1]
        scenario = $parts[2]
        constraint_family = $parts[3]
        n_constraints = $bucket.n_constraints
        n_binding_mismatches = $bucket.n_binding_mismatches
        n_equality_only_violations = $bucket.n_equality_only_violations
        n_pm_violations = $bucket.n_pm_violations
        max_equality_only_violation = Format-Number $bucket.max_equality_only_violation
        max_abs_value_diff = Format-Number $bucket.max_abs_value_diff
        max_abs_margin_diff = Format-Number $bucket.max_abs_margin_diff
        max_abs_equality_only_margin = Format-Number $bucket.max_abs_equality_only_margin
        max_abs_pm_margin = Format-Number $bucket.max_abs_pm_margin
        source = $bucket.source
    }
}

$EqualityRows = $EqualityRows | Sort-Object `
    @{ Expression = { if ($CaseRank.ContainsKey($_.case)) { $CaseRank[$_.case] } else { 999 } } }, `
    scenario, model, constraint_family

$EqualityOutputs = @(
    (Join-Path $Results "equality_only_deviation_inequality_level_by_family_all_models.csv"),
    (Join-Path $Organized "09_constraint_mismatch_evidence\equality_only_constraint_level_summary_by_family_all_models.csv")
)
foreach ($path in $EqualityOutputs) {
    New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
    $EqualityRows | Export-Csv -LiteralPath $path -NoTypeInformation -Encoding UTF8
}

$JuliaPerf = Import-Csv -LiteralPath (Join-Path $Results "solve_performance_by_case_julia_models.csv")
$PythonSummary = Import-Csv -LiteralPath (Join-Path $Results "python_pm_like\summary.csv")
$PythonIterationBackfillPath = Join-Path $Results "python_pm_like\iteration_backfill.csv"
$PythonIterationBackfill = @{}
if (Test-Path -LiteralPath $PythonIterationBackfillPath) {
    foreach ($row in Import-Csv -LiteralPath $PythonIterationBackfillPath) {
        $it = To-Double $row.solve_iterations
        if ($null -ne $it) {
            $PythonIterationBackfill["$($row.case)|$($row.scenario)"] = $it
        }
    }
}
$PythonStatus = @{}
foreach ($row in Import-Csv -LiteralPath (Join-Path $Results "python_pm_like\status_by_case.csv")) {
    $PythonStatus[$row.case] = $row
}

$PerformanceRows = foreach ($case in $CaseOrder) {
    $cur = $JuliaPerf | Where-Object { $_.case -eq $case -and $_.model -eq "current_julia" } | Select-Object -First 1
    $jpm = $JuliaPerf | Where-Object { $_.case -eq $case -and $_.model -eq "pm_like_julia" } | Select-Object -First 1
    $py = @($PythonSummary | Where-Object { $_.case -eq $case -and (To-Bool $_.own_success) })
    $pyTimes = @($py | ForEach-Object { To-Double $_.own_solve_time_sec } | Where-Object { $null -ne $_ })
    $pyIterations = @($py | ForEach-Object {
        $it = To-Double $_.own_solve_iterations
        if ($null -eq $it) { $it = To-Double $_.solve_iterations }
        if ($null -eq $it) {
            $key = "$($_.case)|$($_.scenario)"
            if ($PythonIterationBackfill.ContainsKey($key)) {
                $it = $PythonIterationBackfill[$key]
            }
        }
        $it
    } | Where-Object { $null -ne $_ })
    $status = $PythonStatus[$case]
    $pmTime = if ($jpm) { $jpm.mean_pm_time_sec } elseif ($cur) { $cur.mean_pm_time_sec } else { "" }
    $pmIt = if ($jpm) { $jpm.mean_pm_iterations } elseif ($cur) { $cur.mean_pm_iterations } else { "" }
    [pscustomobject]@{
        case = $case
        julia_current_mean_time_sec = if ($cur) { Format-Number (To-Double $cur.mean_own_time_sec) } else { "" }
        julia_pm_like_mean_time_sec = if ($jpm) { Format-Number (To-Double $jpm.mean_own_time_sec) } else { "" }
        python_pm_like_mean_time_sec = Format-Number (Mean $pyTimes)
        powermodels_mean_time_sec = Format-Number (To-Double $pmTime)
        julia_current_mean_iterations = if ($cur) { Format-Number (To-Double $cur.mean_own_iterations) } else { "" }
        julia_pm_like_mean_iterations = if ($jpm) { Format-Number (To-Double $jpm.mean_own_iterations) } else { "" }
        python_pm_like_mean_iterations = Format-Number (Mean $pyIterations)
        powermodels_mean_iterations = Format-Number (To-Double $pmIt)
        python_pm_like_solved_rows = $py.Count
        python_pm_like_total_rows = if ($status) { $status.total } else { "" }
        python_pm_like_optimal_rows = if ($status) { $status.optimal } else { "" }
        python_pm_like_max_iteration_rows = if ($status) { $status.maxIterations } else { "" }
        python_iteration_note = if ($pyIterations.Count -eq 0) {
            "not exported in full Python PM-like sweep"
        } elseif ($pyIterations.Count -lt $py.Count) {
            "partial iteration backfill: $($pyIterations.Count)/$($py.Count) solved rows"
        } else {
            "iteration backfill"
        }
    }
}

$PerformanceOutputs = @(
    (Join-Path $Results "solve_performance_by_case_all_models_with_python_pm_like.csv"),
    (Join-Path $Organized "02_pm_like_vs_current\solve_performance_by_case_all_models_with_python_pm_like.csv")
)
foreach ($path in $PerformanceOutputs) {
    New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
    $PerformanceRows | Export-Csv -LiteralPath $path -NoTypeInformation -Encoding UTF8
}

Write-Host "Wrote $($EqualityRows.Count) equality-only family rows"
Write-Host "Wrote $($PerformanceRows.Count) performance rows"
foreach ($path in ($EqualityOutputs + $PerformanceOutputs)) {
    Write-Host $path
}
