$ErrorActionPreference = "Stop"

$Repo = Resolve-Path "."
$Root = Join-Path $Repo "results\own_vs_powermodels"
$JuliaCurrentSummaryPath = Join-Path $Root "julia\summary.csv"
$JuliaPmLikeSummaryPath = Join-Path $Root "julia_pm_like\summary.csv"
$PmLikeFullSweepPath = Join-Path $Repo "results\own_julia\opf_validation_sweeps_pm_like_full\compact_opf_validation_summary.csv"
$PmLikeLimitRowsPath = Join-Path $Repo "results\own_julia\opf_validation_sweeps_pm_like_limit_rows_maxiter500\compact_opf_validation_summary.csv"

$SolvedStatuses = @("optimal", "LOCALLY_SOLVED", "ALMOST_LOCALLY_SOLVED", "SOLVED_FEASIBLE")
$SummaryColumns = @(
    "case", "scenario", "own_status", "pm_status", "outcome_match",
    "own_objective", "pm_objective", "objective_diff", "objective_rel_diff",
    "own_solve_time_sec", "pm_solve_time_sec", "own_solve_iterations", "pm_solve_iterations",
    "pm_like_coverage_status", "pm_like_coverage_note"
)
$DiffColumns = @(
    "max_pg_diff", "mean_pg_diff", "max_qg_diff", "mean_qg_diff",
    "max_vm_diff", "mean_vm_diff", "max_va_diff", "mean_va_diff",
    "max_pf_diff", "mean_pf_diff", "max_qf_diff", "mean_qf_diff",
    "max_loading_pct_diff", "mean_loading_pct_diff"
)

function Backup-File {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Copy-Item -LiteralPath $Path -Destination "$Path.pre_6470_coverage_fix.bak" -Force
    }
}

function Status-IsSolved {
    param([object]$Value)
    if ($null -eq $Value) { return $false }
    return $SolvedStatuses -contains [string]$Value
}

function To-DoubleOrNull {
    param([object]$Value)
    $parsed = 0.0
    if ([double]::TryParse([string]$Value, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$parsed)) {
        return $parsed
    }
    return $null
}

function Mean {
    param([object[]]$Values)
    $nums = @($Values | ForEach-Object { To-DoubleOrNull $_ } | Where-Object { $null -ne $_ })
    if ($nums.Count -eq 0) { return "" }
    return ($nums | Measure-Object -Average).Average
}

function Median {
    param([object[]]$Values)
    $nums = @($Values | ForEach-Object { To-DoubleOrNull $_ } | Where-Object { $null -ne $_ } | Sort-Object)
    if ($nums.Count -eq 0) { return "" }
    $mid = [int]($nums.Count / 2)
    if ($nums.Count % 2 -eq 1) { return $nums[$mid] }
    return ($nums[$mid - 1] + $nums[$mid]) / 2.0
}

function Max-Value {
    param([object[]]$Values)
    $nums = @($Values | ForEach-Object { To-DoubleOrNull $_ } | Where-Object { $null -ne $_ })
    if ($nums.Count -eq 0) { return "" }
    return ($nums | Measure-Object -Maximum).Maximum
}

function Canonical-Scenario {
    param([object]$Row)
    $case = [string]$Row.case_name
    $scenarioId = [string]$Row.scenario_id
    if ($case -eq "" -or $case -eq $null) { $case = [string]$Row.case }
    if ($scenarioId -match "__base__none__none__none__none$") { return "base" }
    if ($scenarioId -ne "" -and $scenarioId -ne $null) { return $scenarioId }
    return [string]$Row.scenario
}

function New-SummaryRow {
    param(
        [string]$Case,
        [string]$Scenario,
        [object]$SourceRow,
        [string]$CoverageStatus,
        [string]$CoverageNote
    )
    $values = [ordered]@{}
    foreach ($col in $SummaryColumns) { $values[$col] = "" }
    $values["case"] = $Case
    $values["scenario"] = $Scenario
    $values["pm_like_coverage_status"] = $CoverageStatus
    $values["pm_like_coverage_note"] = $CoverageNote
    if ($null -ne $SourceRow) {
        foreach ($pair in @(
            @("own_status", "own_status"), @("pm_status", "pm_status"), @("outcome_match", "outcome_match"),
            @("own_objective", "own_objective"), @("pm_objective", "pm_objective"),
            @("objective_diff", "objective_diff"), @("objective_rel_diff", "objective_rel_diff"),
            @("own_solve_time_sec", "own_solve_time_sec"), @("pm_solve_time_sec", "pm_solve_time_sec"),
            @("own_solve_iterations", "own_solve_iterations"), @("pm_solve_iterations", "pm_solve_iterations")
        )) {
            $target = $pair[0]
            $source = $pair[1]
            if ($SourceRow.PSObject.Properties.Name -contains $source) {
                $values[$target] = $SourceRow.$source
            }
        }
    } else {
        $values["own_status"] = "NOT_RUN"
        $values["pm_status"] = "NOT_RUN"
    }
    if ([string]$values["own_status"] -eq "NOT_RUN" -and [string]$values["pm_status"] -eq "NOT_RUN") {
        $values["pm_like_coverage_status"] = "not_run"
        $values["pm_like_coverage_note"] = "No Julia PM-like solve metrics are available for this validation-plan row."
    }
    return [pscustomobject]$values
}

function Read-CsvSafe {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        return @(Import-Csv -LiteralPath $Path)
    }
    return @()
}

function Safe-Segment {
    param([string]$Label)
    if ($Label.Length -le 80) { return $Label }
    $sha = [System.Security.Cryptography.SHA1]::Create()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Label)
    $hash = ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") }) -join ""
    return $Label.Substring(0, [Math]::Min(60, $Label.Length)) + "__" + $hash.Substring(0, 10)
}

function Diff-Stats {
    param([string]$Case, [string]$Scenario)
    $dir = Join-Path (Join-Path (Join-Path $Root "julia_pm_like") $Case) (Safe-Segment $Scenario)
    $missing = New-Object System.Collections.Generic.List[string]
    $stats = [ordered]@{}
    foreach ($col in $DiffColumns) { $stats[$col] = "" }

    $generators = Join-Path $dir "generators.csv"
    if (Test-Path -LiteralPath $generators) {
        $rows = @(Import-Csv -LiteralPath $generators)
        $stats["max_pg_diff"] = Max-Value ($rows | ForEach-Object { $_.abs_pg_diff })
        $stats["mean_pg_diff"] = Mean ($rows | ForEach-Object { $_.abs_pg_diff })
        $stats["max_qg_diff"] = Max-Value ($rows | ForEach-Object { $_.abs_qg_diff })
        $stats["mean_qg_diff"] = Mean ($rows | ForEach-Object { $_.abs_qg_diff })
    } else { $missing.Add("generators.csv") }

    $buses = Join-Path $dir "buses.csv"
    if (Test-Path -LiteralPath $buses) {
        $rows = @(Import-Csv -LiteralPath $buses)
        $stats["max_vm_diff"] = Max-Value ($rows | ForEach-Object { $_.abs_vm_diff })
        $stats["mean_vm_diff"] = Mean ($rows | ForEach-Object { $_.abs_vm_diff })
        $stats["max_va_diff"] = Max-Value ($rows | ForEach-Object { $_.abs_va_diff })
        $stats["mean_va_diff"] = Mean ($rows | ForEach-Object { $_.abs_va_diff })
    } else { $missing.Add("buses.csv") }

    $branches = Join-Path $dir "branches.csv"
    if (Test-Path -LiteralPath $branches) {
        $rows = @(Import-Csv -LiteralPath $branches)
        $stats["max_pf_diff"] = Max-Value ($rows | ForEach-Object { $_.abs_pf_diff })
        $stats["mean_pf_diff"] = Mean ($rows | ForEach-Object { $_.abs_pf_diff })
        $stats["max_qf_diff"] = Max-Value ($rows | ForEach-Object { $_.abs_qf_diff })
        $stats["mean_qf_diff"] = Mean ($rows | ForEach-Object { $_.abs_qf_diff })
        $stats["max_loading_pct_diff"] = Max-Value ($rows | ForEach-Object { $_.abs_loading_pct_diff })
        $stats["mean_loading_pct_diff"] = Mean ($rows | ForEach-Object { $_.abs_loading_pct_diff })
    } else { $missing.Add("branches.csv") }

    $stats["detailed_export_tables_available"] = ($missing.Count -eq 0)
    $stats["missing_detail_tables"] = ($missing -join ";")
    return $stats
}

function Binding-Mismatch-Map {
    $path = Join-Path $Root "binding_constraint_mismatch_tolerance_summary_julia_pm_like.csv"
    $map = @{}
    foreach ($row in (Read-CsvSafe $path)) {
        $map["$($row.case)|$($row.scenario)"] = $row.total_mismatches
    }
    return $map
}

function Build-Final-PmLikeSummary {
    param([object[]]$PmRows)
    $bindingMap = Binding-Mismatch-Map
    $rows = foreach ($row in $PmRows) {
        $stats = Diff-Stats ([string]$row.case) ([string]$row.scenario)
        $values = [ordered]@{
            model_variant = "julia_pm_like"
        }
        foreach ($col in $SummaryColumns) { $values[$col] = $row.$col }
        foreach ($col in $DiffColumns) { $values[$col] = $stats[$col] }
        $values["detailed_export_tables_available"] = $stats["detailed_export_tables_available"]
        $values["missing_detail_tables"] = $stats["missing_detail_tables"]
        $key = "$($row.case)|$($row.scenario)"
        $values["binding_mismatches_total"] = if ($bindingMap.ContainsKey($key)) { $bindingMap[$key] } else { "" }
        $values["summary_scope"] = "Julia PM-like formulation; 6470 coverage rows explicitly distinguish attempted, solved, and not-run scenarios"
        [pscustomobject]$values
    }
    return @($rows)
}

function Build-ScenarioDetails {
    param([object[]]$Rows, [string]$Model)
    return @(
        foreach ($row in $Rows) {
            $ownSolved = Status-IsSolved $row.own_status
            $pmSolved = Status-IsSolved $row.pm_status
            $bothSolved = $ownSolved -and $pmSolved
            $ownTime = To-DoubleOrNull $row.own_solve_time_sec
            $pmTime = To-DoubleOrNull $row.pm_solve_time_sec
            $ownIt = To-DoubleOrNull $row.own_solve_iterations
            $pmIt = To-DoubleOrNull $row.pm_solve_iterations
            [pscustomobject]@{
                model = $Model
                case = $row.case
                scenario = $row.scenario
                own_status = $row.own_status
                pm_status = $row.pm_status
                both_solved = [string]$bothSolved
                attempted = [string]([string]$row.own_status -ne "NOT_RUN" -or [string]$row.pm_status -ne "NOT_RUN")
                not_run = [string]([string]$row.own_status -eq "NOT_RUN" -and [string]$row.pm_status -eq "NOT_RUN")
                own_solve_time_sec = $row.own_solve_time_sec
                pm_solve_time_sec = $row.pm_solve_time_sec
                solve_time_ratio_own_vs_pm = if ($bothSolved -and $null -ne $ownTime -and $null -ne $pmTime -and $pmTime -ne 0.0) { $ownTime / $pmTime } else { "" }
                own_solve_iterations = $row.own_solve_iterations
                pm_solve_iterations = $row.pm_solve_iterations
                iteration_ratio_own_vs_pm = if ($bothSolved -and $null -ne $ownIt -and $null -ne $pmIt -and $pmIt -ne 0.0) { $ownIt / $pmIt } else { "" }
                coverage_status = $row.pm_like_coverage_status
            }
        }
    )
}

function Build-ByCase {
    param([object[]]$Rows, [string]$Model)
    return @(
        foreach ($group in ($Rows | Group-Object case | Sort-Object Name)) {
            $items = @($group.Group)
            $both = @($items | Where-Object { [string]$_.both_solved -eq "True" -or [string]$_.both_solved -eq "true" })
            [pscustomobject]@{
                model = $Model
                case = $group.Name
                rows = $items.Count
                attempted_rows = @($items | Where-Object { [string]$_.attempted -eq "True" -or [string]$_.attempted -eq "true" }).Count
                not_run_rows = @($items | Where-Object { [string]$_.not_run -eq "True" -or [string]$_.not_run -eq "true" }).Count
                both_solved_rows = $both.Count
                own_solved_rows = @($items | Where-Object { Status-IsSolved $_.own_status }).Count
                pm_solved_rows = @($items | Where-Object { Status-IsSolved $_.pm_status }).Count
                mean_own_time_sec = Mean ($both | ForEach-Object { $_.own_solve_time_sec })
                median_own_time_sec = Median ($both | ForEach-Object { $_.own_solve_time_sec })
                mean_pm_time_sec = Mean ($both | ForEach-Object { $_.pm_solve_time_sec })
                median_pm_time_sec = Median ($both | ForEach-Object { $_.pm_solve_time_sec })
                mean_time_ratio_own_vs_pm = Mean ($both | ForEach-Object { $_.solve_time_ratio_own_vs_pm })
                median_time_ratio_own_vs_pm = Median ($both | ForEach-Object { $_.solve_time_ratio_own_vs_pm })
                max_time_ratio_own_vs_pm = Max-Value ($both | ForEach-Object { $_.solve_time_ratio_own_vs_pm })
                mean_own_iterations = Mean ($both | ForEach-Object { $_.own_solve_iterations })
                median_own_iterations = Median ($both | ForEach-Object { $_.own_solve_iterations })
                mean_pm_iterations = Mean ($both | ForEach-Object { $_.pm_solve_iterations })
                median_pm_iterations = Median ($both | ForEach-Object { $_.pm_solve_iterations })
                mean_iteration_ratio_own_vs_pm = Mean ($both | ForEach-Object { $_.iteration_ratio_own_vs_pm })
                median_iteration_ratio_own_vs_pm = Median ($both | ForEach-Object { $_.iteration_ratio_own_vs_pm })
                max_iteration_ratio_own_vs_pm = Max-Value ($both | ForEach-Object { $_.iteration_ratio_own_vs_pm })
                own_iteration_limit_rows = @($items | Where-Object { [string]$_.own_status -eq "ITERATION_LIMIT" }).Count
                pm_iteration_limit_rows = @($items | Where-Object { [string]$_.pm_status -eq "ITERATION_LIMIT" }).Count
                coverage_note = if ($items.Count -ne $both.Count) { "Metrics use both-solved rows only; row counts include not-run/failed coverage rows." } else { "" }
            }
        }
    )
}

function Build-OverallSummary {
    param([object[]]$Rows, [string]$Model)
    $both = @($Rows | Where-Object { [string]$_.both_solved -eq "True" -or [string]$_.both_solved -eq "true" })
    return [pscustomobject]@{
        model = $Model
        rows = $Rows.Count
        attempted_rows = @($Rows | Where-Object { [string]$_.attempted -eq "True" -or [string]$_.attempted -eq "true" }).Count
        not_run_rows = @($Rows | Where-Object { [string]$_.not_run -eq "True" -or [string]$_.not_run -eq "true" }).Count
        both_solved_rows = $both.Count
        own_solved_rows = @($Rows | Where-Object { Status-IsSolved $_.own_status }).Count
        pm_solved_rows = @($Rows | Where-Object { Status-IsSolved $_.pm_status }).Count
        mean_own_time_sec = Mean ($both | ForEach-Object { $_.own_solve_time_sec })
        median_own_time_sec = Median ($both | ForEach-Object { $_.own_solve_time_sec })
        mean_pm_time_sec = Mean ($both | ForEach-Object { $_.pm_solve_time_sec })
        median_pm_time_sec = Median ($both | ForEach-Object { $_.pm_solve_time_sec })
        mean_time_ratio_own_vs_pm = Mean ($both | ForEach-Object { $_.solve_time_ratio_own_vs_pm })
        median_time_ratio_own_vs_pm = Median ($both | ForEach-Object { $_.solve_time_ratio_own_vs_pm })
        max_time_ratio_own_vs_pm = Max-Value ($both | ForEach-Object { $_.solve_time_ratio_own_vs_pm })
        mean_own_iterations = Mean ($both | ForEach-Object { $_.own_solve_iterations })
        median_own_iterations = Median ($both | ForEach-Object { $_.own_solve_iterations })
        mean_pm_iterations = Mean ($both | ForEach-Object { $_.pm_solve_iterations })
        median_pm_iterations = Median ($both | ForEach-Object { $_.pm_solve_iterations })
        mean_iteration_ratio_own_vs_pm = Mean ($both | ForEach-Object { $_.iteration_ratio_own_vs_pm })
        median_iteration_ratio_own_vs_pm = Median ($both | ForEach-Object { $_.iteration_ratio_own_vs_pm })
        max_iteration_ratio_own_vs_pm = Max-Value ($both | ForEach-Object { $_.iteration_ratio_own_vs_pm })
        own_iteration_limit_rows = @($Rows | Where-Object { [string]$_.own_status -eq "ITERATION_LIMIT" }).Count
        pm_iteration_limit_rows = @($Rows | Where-Object { [string]$_.pm_status -eq "ITERATION_LIMIT" }).Count
        coverage_note = "Rows include explicit not-run coverage rows; timing/iteration aggregates use both-solved rows only."
    }
}

function Merge-CsvTables {
    param([string[]]$Paths, [string]$OutputPath)
    $tables = @($Paths | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object { Import-Csv -LiteralPath $_ })
    $allRows = @($tables | ForEach-Object { $_ })
    if ($allRows.Count -eq 0) { return }
    $columns = @()
    foreach ($row in $allRows) {
        foreach ($name in $row.PSObject.Properties.Name) {
            if ($columns -notcontains $name) { $columns += $name }
        }
    }
    $merged = foreach ($row in $allRows) {
        $values = [ordered]@{}
        foreach ($col in $columns) {
            $values[$col] = if ($row.PSObject.Properties.Name -contains $col) { $row.$col } else { "" }
        }
        [pscustomobject]$values
    }
    $merged | Export-Csv -LiteralPath $OutputPath -NoTypeInformation
}

if (-not (Test-Path -LiteralPath $JuliaCurrentSummaryPath)) {
    throw "Missing current Julia summary: $JuliaCurrentSummaryPath"
}
if (-not (Test-Path -LiteralPath $JuliaPmLikeSummaryPath)) {
    throw "Missing Julia PM-like summary: $JuliaPmLikeSummaryPath"
}

$currentSummary = @(Import-Csv -LiteralPath $JuliaCurrentSummaryPath)
$expected6470 = @($currentSummary | Where-Object { $_.case -eq "6470_rte" })
if ($expected6470.Count -ne 41) {
    throw "Expected 41 current Julia 6470 rows, found $($expected6470.Count). Refusing to infer validation-plan coverage."
}

Backup-File $JuliaPmLikeSummaryPath

$existingPmLike = @(Import-Csv -LiteralPath $JuliaPmLikeSummaryPath)
$non6470 = @($existingPmLike | Where-Object { $_.case -ne "6470_rte" } | ForEach-Object {
    New-SummaryRow -Case $_.case -Scenario $_.scenario -SourceRow $_ -CoverageStatus "exported" -CoverageNote "PM-like row present in exported comparison summary."
})

$actual6470 = @{}
foreach ($row in @($existingPmLike | Where-Object { $_.case -eq "6470_rte" })) {
    $actual6470[[string]$row.scenario] = New-SummaryRow -Case "6470_rte" -Scenario ([string]$row.scenario) -SourceRow $row -CoverageStatus "exported" -CoverageNote "PM-like row present in exported comparison summary."
}

foreach ($sourcePath in @($PmLikeFullSweepPath, $PmLikeLimitRowsPath)) {
    foreach ($row in (Read-CsvSafe $sourcePath | Where-Object { $_.case_name -eq "6470_rte" -or [string]$_.scenario_id -like "6470_rte*" })) {
        $scenario = Canonical-Scenario $row
        if ($scenario -eq "base" -and $actual6470.ContainsKey("base")) {
            continue
        }
        $actual6470[$scenario] = New-SummaryRow -Case "6470_rte" -Scenario $scenario -SourceRow $row -CoverageStatus "attempted_no_detail_export" -CoverageNote "PM-like compact sweep row exists, but detailed per-scenario export tables are unavailable."
    }
}

$rebuilt6470 = foreach ($expected in $expected6470) {
    $scenario = [string]$expected.scenario
    if ($actual6470.ContainsKey($scenario)) {
        $actual6470[$scenario]
    } else {
        New-SummaryRow -Case "6470_rte" -Scenario $scenario -SourceRow $null -CoverageStatus "not_run" -CoverageNote "No Julia PM-like result row found; retained as validation-plan coverage placeholder."
    }
}

$rebuiltSummary = @($non6470 + $rebuilt6470)
$rebuiltSummary | Export-Csv -LiteralPath $JuliaPmLikeSummaryPath -NoTypeInformation

$finalPmLike = Build-Final-PmLikeSummary $rebuiltSummary
$finalPmLikePath = Join-Path $Root "final_summary_julia_pm_like.csv"
Backup-File $finalPmLikePath
$finalPmLike | Export-Csv -LiteralPath $finalPmLikePath -NoTypeInformation

$coverageRows = foreach ($group in ($finalPmLike | Group-Object case | Sort-Object Name)) {
    $expectedRows = @($currentSummary | Where-Object { $_.case -eq $group.Name }).Count
    [pscustomobject]@{
        case = $group.Name
        expected_validation_plan_rows = $expectedRows
        pm_like_summary_rows = $group.Count
        attempted_rows = @($group.Group | Where-Object { $_.own_status -ne "NOT_RUN" -or $_.pm_status -ne "NOT_RUN" }).Count
        not_run_rows = @($group.Group | Where-Object { $_.own_status -eq "NOT_RUN" -and $_.pm_status -eq "NOT_RUN" }).Count
        both_solved_rows = @($group.Group | Where-Object { (Status-IsSolved $_.own_status) -and (Status-IsSolved $_.pm_status) }).Count
        rows_with_detail_exports = @($group.Group | Where-Object { [string]$_.detailed_export_tables_available -eq "True" -or [string]$_.detailed_export_tables_available -eq "true" }).Count
        coverage_comment = if ($group.Name -eq "6470_rte") { "Corrected: 41 validation-plan rows are represented; only attempted/available PM-like rows have solve metrics." } else { "" }
    }
}
$coveragePath = Join-Path $Root "julia_pm_like_coverage_audit.csv"
Backup-File $coveragePath
$coverageRows | Export-Csv -LiteralPath $coveragePath -NoTypeInformation

$currentDetailsPath = Join-Path $Root "solve_performance_scenario_details_julia_current.csv"
$currentDetails = @(Import-Csv -LiteralPath $currentDetailsPath | ForEach-Object {
    if ($_.PSObject.Properties.Name -notcontains "attempted") {
        $_ | Add-Member -NotePropertyName attempted -NotePropertyValue "true" -Force
        $_ | Add-Member -NotePropertyName not_run -NotePropertyValue "false" -Force
        $_ | Add-Member -NotePropertyName coverage_status -NotePropertyValue "exported" -Force
    }
    $_
})

$pmDetails = Build-ScenarioDetails $rebuiltSummary "pm_like_julia"
$pmDetailsPath = Join-Path $Root "solve_performance_scenario_details_julia_pm_like.csv"
Backup-File $pmDetailsPath
$pmDetails | Export-Csv -LiteralPath $pmDetailsPath -NoTypeInformation

$pmByCase = Build-ByCase $pmDetails "pm_like_julia"
$pmByCasePath = Join-Path $Root "solve_performance_by_case_julia_pm_like.csv"
Backup-File $pmByCasePath
$pmByCase | Export-Csv -LiteralPath $pmByCasePath -NoTypeInformation

$pmSummary = @(Build-OverallSummary $pmDetails "pm_like_julia")
$pmSummaryPath = Join-Path $Root "solve_performance_summary_julia_pm_like.csv"
Backup-File $pmSummaryPath
$pmSummary | Export-Csv -LiteralPath $pmSummaryPath -NoTypeInformation

$currentByCase = @(Import-Csv -LiteralPath (Join-Path $Root "solve_performance_by_case_julia_current.csv"))
$modelsByCasePath = Join-Path $Root "solve_performance_by_case_julia_models.csv"
Backup-File $modelsByCasePath
Merge-CsvTables @(
    (Join-Path $Root "solve_performance_by_case_julia_current.csv"),
    $pmByCasePath
) $modelsByCasePath

$currentSummaryPerf = @(Import-Csv -LiteralPath (Join-Path $Root "solve_performance_summary_julia_current.csv"))
$modelsSummaryPath = Join-Path $Root "solve_performance_summary_julia_models.csv"
Backup-File $modelsSummaryPath
Merge-CsvTables @(
    (Join-Path $Root "solve_performance_summary_julia_current.csv"),
    $pmSummaryPath
) $modelsSummaryPath

$modelsDetailsPath = Join-Path $Root "solve_performance_scenario_details_julia_models.csv"
Backup-File $modelsDetailsPath
Merge-CsvTables @(
    $currentDetailsPath,
    $pmDetailsPath
) $modelsDetailsPath

Merge-CsvTables @(
    (Join-Path $Root "final_summary_julia_current.csv"),
    $finalPmLikePath,
    (Join-Path $Root "final_summary_python_current.csv"),
    (Join-Path $Root "final_summary_python_pm_like.csv")
) (Join-Path $Root "final_summary_all_models_combined.csv")

$combinedFinal = @(Import-Csv -LiteralPath (Join-Path $Root "final_summary_all_models_combined.csv"))
$coverageFinal = foreach ($group in ($combinedFinal | Group-Object model_variant | Sort-Object Name)) {
    [pscustomobject]@{
        model_variant = $group.Name
        n_rows = $group.Count
        n_own_solved_status = @($group.Group | Where-Object { Status-IsSolved $_.own_status }).Count
        n_pm_solved_status = @($group.Group | Where-Object { Status-IsSolved $_.pm_status }).Count
        n_not_run_rows = @($group.Group | Where-Object { $_.own_status -eq "NOT_RUN" -and $_.pm_status -eq "NOT_RUN" }).Count
        n_rows_with_detail_exports = @($group.Group | Where-Object { $_.detailed_export_tables_available -eq "True" -or $_.detailed_export_tables_available -eq "true" }).Count
    }
}
$coverageFinal | Export-Csv -LiteralPath (Join-Path $Root "final_summary_model_coverage.csv") -NoTypeInformation

Write-Host "Corrected Julia PM-like 6470 coverage:"
Write-Host "  expected 6470 rows: $($expected6470.Count)"
Write-Host "  PM-like 6470 rows now: $($rebuilt6470.Count)"
Write-Host "  attempted rows: $(@($rebuilt6470 | Where-Object { $_.own_status -ne 'NOT_RUN' -or $_.pm_status -ne 'NOT_RUN' }).Count)"
Write-Host "  both-solved rows: $(@($rebuilt6470 | Where-Object { (Status-IsSolved $_.own_status) -and (Status-IsSolved $_.pm_status) }).Count)"
Write-Host "  not-run rows: $(@($rebuilt6470 | Where-Object { $_.own_status -eq 'NOT_RUN' -and $_.pm_status -eq 'NOT_RUN' }).Count)"
