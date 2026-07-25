$ErrorActionPreference = "Stop"

$repo = Resolve-Path "."
$sourceRoot = Join-Path $repo "results\own_vs_powermodels\_organized_for_meeting"
$outRoot = Join-Path $repo "results\own_vs_powermodels\_drive_upload_ac_opf_discussion_slides"

function Ensure-Dir {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path | Out-Null
    }
}

function Copy-Table {
    param(
        [string]$Source,
        [string]$DestDir,
        [string]$DestName = $null
    )
    Ensure-Dir $DestDir
    $src = Join-Path $repo $Source
    if (-not (Test-Path -LiteralPath $src)) {
        Write-Warning "Missing source table: $Source"
        return
    }
    if ($null -eq $DestName) {
        $DestName = Split-Path -Leaf $src
    }
    Copy-Item -LiteralPath $src -Destination (Join-Path $DestDir $DestName) -Force
}

function Normalize-Scenario {
    param([string]$Scenario)
    if ($Scenario -like "*qg_limits_x0p6*" -or $Scenario -like "*qg_limit_multiplier__0p6*" -or $Scenario -like "*qg_limits_x0.6*") {
        return "qg_limits_x0p6"
    }
    return (($Scenario -replace "\.", "p") -replace "[^A-Za-z0-9_]+", "_").Trim("_")
}

function Scenario-Matches {
    param(
        [string]$RowScenario,
        [string]$TargetSlug
    )
    if ($TargetSlug -eq "qg_limits_x0p6") {
        return ($RowScenario -like "*qg_limits_x0p6*" -or $RowScenario -like "*qg_limit_multiplier__0p6*" -or $RowScenario -like "*qg_limits_x0.6*")
    }
    return ((Normalize-Scenario $RowScenario) -eq $TargetSlug)
}

function Export-Rows {
    param(
        [object[]]$Rows,
        [string]$Path
    )
    Ensure-Dir (Split-Path -Parent $Path)
    if ($Rows.Count -eq 0) {
        "" | Set-Content -LiteralPath $Path
    } else {
        $Rows | Export-Csv -LiteralPath $Path -NoTypeInformation
    }
}

function To-Double {
    param([object]$Value)
    $parsed = 0.0
    if ([double]::TryParse([string]$Value, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$parsed)) {
        return $parsed
    }
    return $null
}

function Max-Abs-Variables {
    param($Row)
    $vals = @(
        To-Double $Row.max_abs_pg_deviation
        To-Double $Row.max_abs_qg_deviation
        To-Double $Row.max_abs_vm_deviation
        To-Double $Row.max_abs_va_deviation
    ) | Where-Object { $null -ne $_ }
    if ($vals.Count -eq 0) { return $null }
    return ($vals | ForEach-Object { [Math]::Abs($_) } | Measure-Object -Maximum).Maximum
}

Ensure-Dir $outRoot

$formulationDir = Join-Path $outRoot "01_formulation"
$perfDir = Join-Path $outRoot "02_pm_like_vs_current"
$devRoot = Join-Path $outRoot "03_deviation_minimization"
$eqRoot = Join-Path $outRoot "04_equality_only_deviation"
$lfDir = Join-Path $outRoot "05_loadflow_workflow"

Copy-Table "results\own_vs_powermodels\_organized_for_meeting\05_shunt_and_formulation\power_balance_bus_shunt_term_locations.csv" $formulationDir
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\05_shunt_and_formulation\julia_parallel_branch_ybus_audit.csv" $formulationDir
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\05_shunt_and_formulation\shunt_toggle_solution_summary_julia.csv" $formulationDir

Copy-Table "results\own_vs_powermodels\_organized_for_meeting\03_performance\solve_performance_summary_julia_models.csv" $perfDir
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\03_performance\solve_performance_by_case_julia_models.csv" $perfDir
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\03_performance\solve_performance_scenario_details_julia_models.csv" $perfDir

$perfRows = Import-Csv -LiteralPath (Join-Path $sourceRoot "03_performance\solve_performance_by_case_julia_models.csv")
$cases = $perfRows | Select-Object -ExpandProperty case -Unique | Sort-Object
$perfSlide = foreach ($case in $cases) {
    $current = $perfRows | Where-Object { $_.case -eq $case -and $_.model -eq "current_julia" } | Select-Object -First 1
    $pmLike = $perfRows | Where-Object { $_.case -eq $case -and $_.model -eq "pm_like_julia" } | Select-Object -First 1
    if ($null -eq $current -or $null -eq $pmLike) { continue }
    $currentTime = To-Double $current.mean_own_time_sec
    $pmLikeTime = To-Double $pmLike.mean_own_time_sec
    $pmTime = To-Double $pmLike.mean_pm_time_sec
    $speedup = if ($null -ne $pmLikeTime -and $pmLikeTime -ne 0.0) { $currentTime / $pmLikeTime } else { $null }
    [pscustomobject]@{
        case = $case
        current_solved_over_rows = "$($current.both_solved_rows)/$($current.rows)"
        pm_like_solved_over_rows = "$($pmLike.both_solved_rows)/$($pmLike.rows)"
        pm_like_attempted_over_rows = if ($pmLike.PSObject.Properties.Name -contains "attempted_rows" -and $pmLike.attempted_rows -ne "") { "$($pmLike.attempted_rows)/$($pmLike.rows)" } else { "$($pmLike.rows)/$($pmLike.rows)" }
        pm_like_not_run_rows = if ($pmLike.PSObject.Properties.Name -contains "not_run_rows") { $pmLike.not_run_rows } else { "0" }
        current_mean_time_sec = "{0:N3}" -f $currentTime
        pm_like_mean_time_sec = "{0:N3}" -f $pmLikeTime
        powermodels_mean_time_sec = "{0:N3}" -f $pmTime
        current_mean_iterations = "{0:N1}" -f (To-Double $current.mean_own_iterations)
        pm_like_mean_iterations = "{0:N1}" -f (To-Double $pmLike.mean_own_iterations)
        powermodels_mean_iterations = "{0:N1}" -f (To-Double $pmLike.mean_pm_iterations)
        current_to_pm_like_time_speedup = if ($null -ne $speedup) { "{0:N2}x" -f $speedup } else { "" }
    }
}
Export-Rows $perfSlide (Join-Path $perfDir "solve_performance_summary_for_slide.csv")

$objectiveDetails = Import-Csv -LiteralPath (Join-Path $sourceRoot "06_discrepancy_diagnosis\diagnostic_target_scenarios_details_selected_models.csv")
$juliaTargets = $objectiveDetails | Where-Object { $_.model_variant -in @("julia_current", "julia_pm_like") }
$currentTargets = $juliaTargets | Where-Object { $_.model_variant -eq "julia_current" }
$pmLikeTargets = $juliaTargets | Where-Object { $_.model_variant -eq "julia_pm_like" }

$currentDev = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\current_constrained_deviation_minimization_julia.csv")
$currentBind = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\current_constrained_deviation_binding_summary_julia.csv")
$pmLikeDev = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\pm_like_constrained_deviation_minimization_julia.csv")
$pmLikeBind = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\pm_like_constrained_deviation_binding_summary_julia.csv")
$variantDetails = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\deviation_minimization_variant_details_selected_models.csv")
$variantSummary = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\deviation_minimization_variant_summary_selected_models.csv")

Copy-Table "results\own_vs_powermodels\_organized_for_meeting\07_deviation_minimization\deviation_minimization_variant_summary_selected_models.csv" $devRoot
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\07_deviation_minimization\deviation_minimization_variant_details_selected_models.csv" $devRoot

function Build-Dev-Slide-Summary {
    param(
        [string]$ModelVariant,
        [object[]]$Targets,
        [object[]]$Rows
    )
    foreach ($target in $Targets) {
        $slug = Normalize-Scenario $target.scenario
        $details = $variantDetails | Where-Object {
            $_.model_variant -eq $ModelVariant -and
            $_.diagnostic_type -eq "constrained" -and
            (Scenario-Matches $_.scenario $slug)
        }
        $full = $details | Where-Object { $_.objective_variant -eq "full" } | Select-Object -First 1
        $largestAblation = $details |
            Where-Object { $_.objective_variant -ne "full" } |
            Sort-Object { [Math]::Abs((Max-Abs-Variables $_)) } -Descending |
            Select-Object -First 1
        [pscustomobject]@{
            case = $target.case
            scenario = $slug
            objective_diff_own_minus_pm = $target.objective_diff_own_minus_pm
            cheaper_solution = $target.cheaper_solution
            full_l1_deviation_nonnegative = if ($full) { $full.l1_deviation } else { "" }
            full_max_abs_variable_deviation = if ($full) { "{0:E3}" -f (Max-Abs-Variables $full) } else { "" }
            full_cost_diff_vs_pm = if ($full) { $full.cost_diff_vs_pm } else { "" }
            largest_ablation_variant = if ($largestAblation) { $largestAblation.objective_variant } else { "" }
            largest_ablation_l1_deviation = if ($largestAblation) { $largestAblation.l1_deviation } else { "" }
            largest_ablation_max_abs_variable_deviation = if ($largestAblation) { "{0:E3}" -f (Max-Abs-Variables $largestAblation) } else { "" }
            detail_link_macro = "\linkDevMin" + ($(if ($ModelVariant -eq "julia_current") { "Current" } else { "PMlike" })) + ($slug -replace "_", "")
        }
    }
}

$currentSlide = Build-Dev-Slide-Summary "julia_current" $currentTargets $currentDev
$pmLikeSlide = Build-Dev-Slide-Summary "julia_pm_like" $pmLikeTargets $pmLikeDev
Export-Rows $currentSlide (Join-Path $devRoot "deviation_minimization_current_summary_for_slide.csv")
Export-Rows $pmLikeSlide (Join-Path $devRoot "deviation_minimization_pm_like_summary_for_slide.csv")

foreach ($target in $currentTargets) {
    $slug = Normalize-Scenario $target.scenario
    $dir = Join-Path $devRoot ("current\" + $slug)
    Ensure-Dir $dir
    Export-Rows ($currentDev | Where-Object { Scenario-Matches $_.scenario $slug }) (Join-Path $dir "current_deviation_minimization_variants.csv")
    Export-Rows ($currentBind | Where-Object { Scenario-Matches $_.scenario $slug }) (Join-Path $dir "current_deviation_binding_summary.csv")
    Export-Rows ($variantDetails | Where-Object { $_.model_variant -eq "julia_current" -and (Scenario-Matches $_.scenario $slug) }) (Join-Path $dir "current_deviation_variant_details.csv")
}

foreach ($target in $pmLikeTargets) {
    $slug = Normalize-Scenario $target.scenario
    $dir = Join-Path $devRoot ("pm_like\" + $slug)
    Ensure-Dir $dir
    Export-Rows ($pmLikeDev | Where-Object { Scenario-Matches $_.scenario $slug }) (Join-Path $dir "pm_like_deviation_minimization_variants.csv")
    Export-Rows ($pmLikeBind | Where-Object { Scenario-Matches $_.scenario $slug }) (Join-Path $dir "pm_like_deviation_binding_summary.csv")
    Export-Rows ($variantDetails | Where-Object { $_.model_variant -eq "julia_pm_like" -and (Scenario-Matches $_.scenario $slug) }) (Join-Path $dir "pm_like_deviation_variant_details.csv")
}

$eqMin = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\equality_only_deviation_minimization_julia.csv")
$eqLevels = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\equality_only_deviation_inequality_levels_julia.csv")
$eqFamily = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\equality_only_deviation_inequality_level_by_family_julia.csv")
$eqBinding = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\equality_only_deviation_binding_summary_julia.csv")
$eqRecon = Import-Csv -LiteralPath (Join-Path $sourceRoot "07_deviation_minimization\equality_only_vs_original_binding_reconciliation.csv")

Copy-Table "results\own_vs_powermodels\_organized_for_meeting\07_deviation_minimization\equality_only_vs_original_binding_reconciliation.csv" $eqRoot
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\07_deviation_minimization\equality_only_deviation_inequality_level_by_family_julia.csv" $eqRoot
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\07_deviation_minimization\equality_only_deviation_minimization_julia.csv" $eqRoot

$eqSlide = foreach ($row in $eqMin) {
    $slug = Normalize-Scenario $row.scenario
    $recon = $eqRecon | Where-Object { $_.model_variant -eq ("julia_" + $row.variant) -and (Scenario-Matches $_.scenario $slug) } | Select-Object -First 1
    [pscustomobject]@{
        model_variant = "julia_" + $row.variant
        case = $row.case
        scenario = $slug
        status = $row.equality_only_status
        l1_deviation_nonnegative = $row.equality_only_l1_deviation_nonnegative
        max_abs_variable_deviation = "{0:E3}" -f (Max-Abs-Variables $row)
        h_binding_mismatches = if ($recon) { $recon.equality_only_binding_mismatches } else { "" }
        h_violations = if ($recon) { $recon.equality_only_violations } else { "" }
        h_max_abs_margin_diff = if ($recon) { "{0:E3}" -f (To-Double $recon.equality_only_max_abs_margin_diff) } else { "" }
    }
}
Export-Rows $eqSlide (Join-Path $eqRoot "equality_only_deviation_summary_for_slide.csv")

$eqSlugs = $eqMin | ForEach-Object { Normalize-Scenario $_.scenario } | Sort-Object -Unique
foreach ($slug in $eqSlugs) {
    $dir = Join-Path $eqRoot $slug
    Ensure-Dir $dir
    Export-Rows ($eqMin | Where-Object { Scenario-Matches $_.scenario $slug }) (Join-Path $dir "equality_only_deviation_minimization.csv")
    Export-Rows ($eqFamily | Where-Object { Scenario-Matches $_.scenario $slug }) (Join-Path $dir "equality_only_h_level_summary_by_family.csv")
    Export-Rows ($eqBinding | Where-Object { Scenario-Matches $_.scenario $slug }) (Join-Path $dir "equality_only_binding_summary.csv")
    Export-Rows ($eqRecon | Where-Object { Scenario-Matches $_.scenario $slug }) (Join-Path $dir "equality_only_vs_original_binding_reconciliation.csv")
}

Copy-Table "results\own_vs_powermodels\_organized_for_meeting\04_loadflow_validation\loadflow_validation_scope_and_interpretation.csv" $lfDir
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\04_loadflow_validation\opf_endpoint_loadflow_validation_julia.csv" $lfDir
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\04_loadflow_validation\opf_endpoint_pf_inequality_feasibility_summary_julia.csv" $lfDir
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\04_loadflow_validation\opf_endpoint_pf_inequality_feasibility_by_independence_julia.csv" $lfDir
Copy-Table "results\own_vs_powermodels\_organized_for_meeting\04_loadflow_validation\opf_endpoint_pf_inequality_levels_classified_julia.csv" $lfDir

$filterReport = @(
    [pscustomobject]@{ model_variant = "julia_current"; included_scenarios = (($currentTargets | ForEach-Object { Normalize-Scenario $_.scenario }) -join ";"); n_included = ($currentTargets | Measure-Object).Count }
    [pscustomobject]@{ model_variant = "julia_pm_like"; included_scenarios = (($pmLikeTargets | ForEach-Object { Normalize-Scenario $_.scenario }) -join ";"); n_included = ($pmLikeTargets | Measure-Object).Count }
    [pscustomobject]@{ model_variant = "python_pm_like_zero"; included_scenarios = (($objectiveDetails | Where-Object { $_.model_variant -eq "python_pm_like_zero" } | ForEach-Object { Normalize-Scenario $_.scenario }) -join ";"); n_included = (($objectiveDetails | Where-Object { $_.model_variant -eq "python_pm_like_zero" }) | Measure-Object).Count }
)
Export-Rows $filterReport (Join-Path $outRoot "objective_gap_filter_report.csv")

Write-Host "Prepared Drive upload staging folder:"
Write-Host $outRoot
