$ErrorActionPreference = "Stop"

$Root = Join-Path (Get-Location) "results\own_vs_powermodels"
$OutRoot = Join-Path $Root "_organized_for_meeting"

$Categories = [ordered]@{
    "00_overview" = @(
        "final_summary_julia_current.csv",
        "final_summary_julia_pm_like.csv",
        "final_summary_python_current.csv",
        "final_summary_python_pm_like.csv",
        "final_summary_all_models_combined.csv",
        "final_summary_model_coverage.csv",
        "final_summary_julia.csv",
        "final_summary_python.csv",
        "significant_discrepancies.csv",
        "julia_pm_like_coverage_audit.csv"
    )
    "01_objective_and_solver" = @(
        "objective_mismatch_summary_selected_models.csv",
        "objective_mismatch_details_selected_models.csv",
        "objective_mismatch_summary_julia_current.csv",
        "objective_mismatch_details_julia_current.csv",
        "objective_mismatch_summary_julia_pm_like.csv",
        "objective_mismatch_details_julia_pm_like.csv",
        "solver_diagnostics_objective_mismatch_julia.csv",
        "solver_diagnostics_objective_mismatch_ipopt_iterations_julia.csv",
        "solver_diagnostics_ipopt_notes_julia.csv"
    )
    "02_binding_constraints" = @(
        "binding_constraint_mismatches_julia.csv",
        "binding_constraint_mismatches_python.csv",
        "binding_constraint_mismatch_summary_julia_current.csv",
        "binding_constraint_mismatch_details_julia_current.csv",
        "binding_constraint_mismatch_tolerance_summary_julia_current.csv",
        "binding_constraint_mismatch_tolerance_by_family_julia_current.csv",
        "binding_constraint_mismatch_details_julia_pm_like.csv",
        "binding_constraint_mismatch_tolerance_summary_julia_pm_like.csv",
        "binding_constraint_mismatch_tolerance_by_family_julia_pm_like.csv"
    )
    "03_performance" = @(
        "solve_performance_summary_julia_current.csv",
        "solve_performance_by_case_julia_current.csv",
        "solve_performance_scenario_details_julia_current.csv",
        "solve_performance_summary_julia_pm_like.csv",
        "solve_performance_by_case_julia_pm_like.csv",
        "solve_performance_scenario_details_julia_pm_like.csv",
        "solve_performance_summary_julia_models.csv",
        "solve_performance_by_case_julia_models.csv",
        "solve_performance_scenario_details_julia_models.csv",
        "solve_performance_summary_julia_pm_like_validation_source.csv",
        "solve_performance_by_case_julia_pm_like_validation_source.csv"
    )
    "04_loadflow_validation" = @(
        "loadflow_validation_scope_and_interpretation.csv",
        "opf_solution_loadflow_validation_julia.csv",
        "opf_vs_pf_variable_differences_julia.csv",
        "opf_vs_pf_binding_comparison_julia.csv",
        "opf_endpoint_loadflow_validation_julia.csv",
        "opf_endpoint_pf_variable_comparison_julia.csv",
        "opf_endpoint_pf_inequality_levels_julia.csv",
        "opf_endpoint_pf_inequality_levels_classified_julia.csv",
        "opf_endpoint_pf_inequality_summary_julia.csv",
        "opf_endpoint_pf_inequality_feasibility_summary_julia.csv",
        "opf_endpoint_pf_inequality_feasibility_by_family_julia.csv",
        "opf_endpoint_pf_inequality_feasibility_by_independence_julia.csv",
        "opf_endpoint_pf_inequality_vm_by_bus_class_julia.csv",
        "opf_endpoint_pf_inequality_not_independently_rederived_rows_julia.csv",
        "opf_endpoint_pf_inequality_specified_or_pinned_rows_julia.csv",
        "opf_endpoint_pf_inequality_violations_julia.csv",
        "opf_endpoint_pf_inequality_binding_differences_julia.csv",
        "opf_endpoint_pf_inequality_own_cheaper_focus_julia.csv"
    )
    "05_shunt_and_formulation" = @(
        "power_balance_bus_shunt_term_locations.csv",
        "shunt_toggle_solution_summary_julia.csv",
        "shunt_toggle_variable_differences_julia.csv",
        "shunt_toggle_binding_differences_julia.csv",
        "shunt_toggle_binding_summary_julia.csv"
    )
    "06_discrepancy_diagnosis" = @(
        "diagnostic_target_scenarios_summary_selected_models.csv",
        "diagnostic_target_scenarios_details_selected_models.csv",
        "julia_discrepancy_diagnosis.csv",
        "julia_discrepancy_diagnosis_top_cases.csv"
    )
    "07_deviation_minimization" = @(
        "deviation_minimization_variant_summary_selected_models.csv",
        "deviation_minimization_variant_details_selected_models.csv",
        "equality_only_vs_original_binding_reconciliation.csv",
        "pm_like_constrained_deviation_minimization_julia.csv",
        "pm_like_constrained_deviation_variable_differences_julia.csv",
        "pm_like_constrained_deviation_binding_differences_julia.csv",
        "pm_like_constrained_deviation_binding_summary_julia.csv",
        "current_constrained_deviation_minimization_julia.csv",
        "current_constrained_deviation_variable_differences_julia.csv",
        "current_constrained_deviation_binding_differences_julia.csv",
        "current_constrained_deviation_binding_summary_julia.csv",
        "equality_only_deviation_minimization_julia.csv",
        "equality_only_deviation_variable_differences_julia.csv",
        "equality_only_deviation_inequality_levels_julia.csv",
        "equality_only_deviation_binding_summary_julia.csv"
    )
    "08_python_ac_opf_debugging" = @(
        "python_ac_opf_debug_recommended_run_queue.csv",
        "python_ac_opf_debug_target_inventory.csv",
        "python_ac_opf_debug_target_summary.csv",
        "python_deviation_binding_summary.csv",
        "python_deviation_inequality_levels.csv",
        "python_deviation_minimization_diagnostics.csv",
        "python_deviation_variable_differences.csv",
        "python_pm_like_meaningful_validation_summary.csv",
        "python_power_balance_bus_shunt_term_locations.csv",
        "python_solver_start_diagnostics.csv",
        "python_start_validation_summary.csv",
        @{ source = "python_pm_like\agreement_audit.csv"; file = "python_pm_like_full_sweep_agreement_audit.csv" },
        @{ source = "python_pm_like\agreement_counts.csv"; file = "python_pm_like_full_sweep_agreement_counts.csv" },
        @{ source = "python_pm_like\backfilled_powermodels_references.csv"; file = "python_pm_like_full_sweep_backfilled_powermodels_references.csv" },
        @{ source = "python_pm_like\nonoptimal_status_details.csv"; file = "python_pm_like_full_sweep_nonoptimal_status_details.csv" },
        @{ source = "python_pm_like\solver_diagnostics.csv"; file = "python_pm_like_full_sweep_solver_diagnostics.csv" },
        @{ source = "python_pm_like\status_by_case.csv"; file = "python_pm_like_full_sweep_status_by_case.csv" },
        @{ source = "python_pm_like\summary.csv"; file = "python_pm_like_full_sweep_summary.csv" },
        @{ source = "python_pm_like\equality_only_deviation_targets.csv"; file = "python_pm_like_equality_only_deviation_targets.csv" },
        @{ source = "python_pm_like\equality_only_deviation_minimization.csv"; file = "python_pm_like_equality_only_deviation_minimization.csv" },
        @{ source = "python_pm_like\equality_only_deviation_variable_differences.csv"; file = "python_pm_like_equality_only_deviation_variable_differences.csv" },
        @{ source = "python_pm_like\equality_only_deviation_inequality_levels.csv"; file = "python_pm_like_equality_only_deviation_inequality_levels.csv" },
        @{ source = "python_pm_like\equality_only_deviation_binding_summary.csv"; file = "python_pm_like_equality_only_deviation_binding_summary.csv" },
        "python_endpoint_pypowsybl_loadflow_validation.csv",
        "python_endpoint_pypowsybl_lf_variable_comparison.csv",
        "python_endpoint_pypowsybl_lf_inequality_levels.csv",
        "python_endpoint_pypowsybl_lf_binding_summary.csv",
        "python_endpoint_pypowsybl_lf_violations.csv",
        "python_endpoint_pypowsybl_lf_README.md"
    )
}

New-Item -ItemType Directory -Path $OutRoot -Force | Out-Null

$manifestRows = New-Object System.Collections.Generic.List[object]

foreach ($category in $Categories.Keys) {
    $categoryDir = Join-Path $OutRoot $category
    New-Item -ItemType Directory -Path $categoryDir -Force | Out-Null

    foreach ($entry in $Categories[$category]) {
        if ($entry -is [hashtable]) {
            $fileName = $entry.file
            $source = Join-Path $Root $entry.source
        } else {
            $fileName = $entry
            $source = Join-Path $Root $fileName
        }
        $destination = Join-Path $categoryDir $fileName
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination $destination -Force
            $item = Get-Item -LiteralPath $source
            $manifestRows.Add([pscustomobject]@{
                category = $category
                file = $fileName
                copied = $true
                source = $source
                organized_path = $destination
                bytes = $item.Length
                last_write_time = $item.LastWriteTime
            })
        } else {
            $manifestRows.Add([pscustomobject]@{
                category = $category
                file = $fileName
                copied = $false
                source = $source
                organized_path = $destination
                bytes = $null
                last_write_time = $null
            })
        }
    }
}

$manifestPath = Join-Path $OutRoot "manifest.csv"
$manifestRows | Export-Csv -LiteralPath $manifestPath -NoTypeInformation

$readme = @'
# Organized Own-vs-PowerModels Tables

Generated by `scripts/organize_own_vs_powermodels_tables.ps1`.

This folder contains copied presentation tables from `results/own_vs_powermodels`.
The original files are left in place so existing scripts and open IDE tabs do not
break.

## Folders

- `00_overview`: global Julia/Python summaries and significant discrepancies.
- `01_objective_and_solver`: objective mismatch tables and Ipopt diagnostics.
- `02_binding_constraints`: binding mismatch details and tolerance summaries.
- `03_performance`: solve time and iteration comparisons.
- `04_loadflow_validation`: OPF endpoint load-flow validation and inequality levels.
- `05_shunt_and_formulation`: bus-shunt location/toggle diagnostics.
- `06_discrepancy_diagnosis`: automated Julia discrepancy diagnosis tables.
- `07_deviation_minimization`: constrained deviation-minimization diagnostics.
- `08_python_ac_opf_debugging`: Python current/PM-like debugging, solver starts, full sweep, equality-only deviation diagnostics, and PyPowSyBl endpoint load-flow validation.

Use `manifest.csv` to see exactly which source file was copied into each folder.
'@

Set-Content -LiteralPath (Join-Path $OutRoot "README.md") -Value $readme -Encoding UTF8

Write-Host "Organized tables written to $OutRoot"
Write-Host "Manifest written to $manifestPath"
