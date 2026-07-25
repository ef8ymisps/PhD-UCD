$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")
$Results = Join-Path $Root "results\own_vs_powermodels"
$Organized = Join-Path $Results "_organized_for_meeting"
$Source = Join-Path $Organized "09_constraint_mismatch_evidence\loadflow_constraint_level_details.csv"
$LimitViolationTolerance = 1e-6

function To-Bool($value) {
    return "$value".Trim().ToLowerInvariant() -in @("true", "1", "yes")
}

function To-Double($value) {
    if ($null -eq $value -or "$value" -eq "") { return $null }
    $parsed = 0.0
    if ([double]::TryParse("$value", [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$parsed)) {
        return $parsed
    }
    return $null
}

function Pick($row, [string[]]$names) {
    foreach ($name in $names) {
        if ($row.PSObject.Properties.Name -contains $name) {
            $value = $row.$name
            if ($null -ne $value -and "$value" -ne "") {
                return $value
            }
        }
    }
    return ""
}

$rows = Import-Csv -LiteralPath $Source

$focused = foreach ($row in $rows) {
    $hasMismatch = To-Bool (Pick $row @("material_mismatch"))
    $leftViolationAmount = To-Double (Pick $row @("own_lf_violation_amount", "own_pf_violation_amount"))
    $rightViolationAmount = To-Double (Pick $row @("pm_lf_violation_amount", "pm_pf_violation_amount"))
    if ($null -eq $leftViolationAmount) { $leftViolationAmount = 0.0 }
    if ($null -eq $rightViolationAmount) { $rightViolationAmount = 0.0 }
    $leftViolates = $leftViolationAmount -gt $LimitViolationTolerance
    $rightViolates = $rightViolationAmount -gt $LimitViolationTolerance
    $hasEndpointViolation = $leftViolates -ne $rightViolates
    $hasSharedViolation = $leftViolates -and $rightViolates
    if (-not ($hasMismatch -or $hasEndpointViolation -or $hasSharedViolation -or $leftViolates -or $rightViolates)) {
        continue
    }

    $model = Pick $row @("model_variant", "variant")
    $pfEngine = Pick $row @("pf_engine")
    $pfMethod = Pick $row @("pf_method")
    if ($pfEngine -eq "" -and "$model".StartsWith("python")) {
        $pfEngine = "pypowsybl/OpenLoadFlow"
    }
    if ($pfMethod -eq "" -and "$model".StartsWith("python")) {
        $pfMethod = "AC load flow"
    }
    $stage = Pick $row @("comparison_stage")
    $diagnosticVariant = Pick $row @("diagnostic_variant")
    $leftValue = Pick $row @("own_lf_value", "own_pf_value")
    $rightValue = Pick $row @("pm_lf_value", "pm_pf_value")
    $leftMargin = Pick $row @("own_lf_margin", "own_pf_margin")
    $rightMargin = Pick $row @("pm_lf_margin", "pm_pf_margin")
    $leftBinding = Pick $row @("own_lf_binding", "own_pf_binding")
    $rightBinding = Pick $row @("pm_lf_binding", "pm_pf_binding")
    $leftSource = Pick $row @("own_lf_independence_note", "pf_value_source")
    $rightSource = Pick $row @("pm_lf_independence_note", "pf_value_source")

    [pscustomobject]@{
        model_variant = $model
        case = $row.case
        scenario = Pick $row @("canonical_scenario", "scenario")
        pf_engine = $pfEngine
        pf_method = $pfMethod
        comparison_stage = $stage
        diagnostic_variant = $diagnosticVariant
        constraint_family = Pick $row @("constraint_family")
        constraint_type = Pick $row @("constraint_type")
        element_id = Pick $row @("element_id")
        bound = Pick $row @("bound")
        own_endpoint_value = $leftValue
        powermodels_endpoint_value = $rightValue
        own_endpoint_margin = $leftMargin
        powermodels_endpoint_margin = $rightMargin
        abs_value_diff = Pick $row @("abs_value_diff", "abs_pf_value_diff")
        abs_margin_diff = Pick $row @("abs_margin_diff", "abs_pf_margin_diff")
        own_endpoint_binding = $leftBinding
        powermodels_endpoint_binding = $rightBinding
        binding_relation = Pick $row @("binding_relation", "pf_binding_relation")
        material_mismatch = $hasMismatch
        own_endpoint_violates_limit = $leftViolates
        powermodels_endpoint_violates_limit = $rightViolates
        endpoint_specific_violation = $hasEndpointViolation
        shared_violation = $hasSharedViolation
        own_endpoint_violation_amount = $leftViolationAmount.ToString("G12", [System.Globalization.CultureInfo]::InvariantCulture)
        powermodels_endpoint_violation_amount = $rightViolationAmount.ToString("G12", [System.Globalization.CultureInfo]::InvariantCulture)
        limit_violation_tolerance = $LimitViolationTolerance.ToString("G12", [System.Globalization.CultureInfo]::InvariantCulture)
        value_source_note = $leftSource
        powermodels_value_source_note = $rightSource
        objective_diff_own_minus_pm = Pick $row @("objective_diff_own_minus_pm")
        cheaper_solution = Pick $row @("cheaper_solution")
    }
}

$fieldOrder = @(
    "model_variant",
    "case",
    "scenario",
    "pf_engine",
    "pf_method",
    "comparison_stage",
    "diagnostic_variant",
    "constraint_family",
    "constraint_type",
    "element_id",
    "bound",
    "own_endpoint_value",
    "powermodels_endpoint_value",
    "own_endpoint_margin",
    "powermodels_endpoint_margin",
    "abs_value_diff",
    "abs_margin_diff",
    "own_endpoint_binding",
    "powermodels_endpoint_binding",
    "binding_relation",
    "material_mismatch",
    "own_endpoint_violates_limit",
    "powermodels_endpoint_violates_limit",
    "endpoint_specific_violation",
    "shared_violation",
    "own_endpoint_violation_amount",
    "powermodels_endpoint_violation_amount",
    "limit_violation_tolerance",
    "value_source_note",
    "powermodels_value_source_note",
    "objective_diff_own_minus_pm",
    "cheaper_solution"
)

$outputs = @(
    (Join-Path $Results "loadflow_constraint_mismatches_and_violations_focus.csv"),
    (Join-Path $Organized "04_loadflow_validation\loadflow_constraint_mismatches_and_violations_focus.csv"),
    (Join-Path $Organized "09_constraint_mismatch_evidence\loadflow_constraint_mismatches_and_violations_focus.csv")
)

foreach ($path in $outputs) {
    New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
    $focused | Select-Object $fieldOrder | Export-Csv -LiteralPath $path -NoTypeInformation -Encoding UTF8
}

$summary = $focused |
    Group-Object model_variant, case, scenario, pf_engine |
    ForEach-Object {
        $group = $_.Group
        [pscustomobject]@{
            model_variant = $group[0].model_variant
            case = $group[0].case
            scenario = $group[0].scenario
            pf_engine = $group[0].pf_engine
            n_rows = $group.Count
            n_material_mismatches = @($group | Where-Object { $_.material_mismatch }).Count
            n_endpoint_specific_violations = @($group | Where-Object { $_.endpoint_specific_violation }).Count
            n_shared_violations = @($group | Where-Object { $_.shared_violation }).Count
            n_own_endpoint_violations = @($group | Where-Object { $_.own_endpoint_violates_limit }).Count
            n_powermodels_endpoint_violations = @($group | Where-Object { $_.powermodels_endpoint_violates_limit }).Count
        }
    }

$summaryOutputs = @(
    (Join-Path $Results "loadflow_constraint_mismatches_and_violations_focus_summary.csv"),
    (Join-Path $Organized "04_loadflow_validation\loadflow_constraint_mismatches_and_violations_focus_summary.csv"),
    (Join-Path $Organized "09_constraint_mismatch_evidence\loadflow_constraint_mismatches_and_violations_focus_summary.csv")
)

foreach ($path in $summaryOutputs) {
    New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
    $summary | Export-Csv -LiteralPath $path -NoTypeInformation -Encoding UTF8
}

$pythonViolations = $focused | Where-Object {
    $_.model_variant -eq "python_pm_like_zero" -and
    ($_.own_endpoint_violates_limit -or $_.powermodels_endpoint_violates_limit)
}

function Max-Abs-Violation($row) {
    $own = 0.0
    $pm = 0.0
    [double]::TryParse("$($row.own_endpoint_violation_amount)", [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$own) | Out-Null
    [double]::TryParse("$($row.powermodels_endpoint_violation_amount)", [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$pm) | Out-Null
    return [math]::Max([math]::Abs($own), [math]::Abs($pm))
}

$pythonDiagnosis = foreach ($row in $pythonViolations) {
    $maxViolation = Max-Abs-Violation $row
    $interpretation = ""
    $needsFollowUp = $false
    if ($row.constraint_type -like "pg_*") {
        $interpretation = "tiny Pg setpoint overshoot; Pg is specified input in LF and excess is near numerical tolerance"
        $needsFollowUp = $false
    } elseif ($row.constraint_type -like "qg_*") {
        $interpretation = "shared OpenLoadFlow-reactive-power limit violation at PV generator; check Q-limit enforcement/import convention"
        $needsFollowUp = $true
    } elseif ($row.constraint_type -like "vm_*" -and $row.endpoint_specific_violation) {
        $interpretation = "endpoint-specific PQ-bus voltage violation after OpenLoadFlow; inspect Python endpoint/import convention"
        $needsFollowUp = $true
    } elseif ($row.constraint_type -like "vm_*") {
        $interpretation = "shared PQ-bus voltage violation after OpenLoadFlow; likely PF convention/tolerance but should be documented"
        $needsFollowUp = $true
    } else {
        $interpretation = "post-LF limit violation; inspect source rows"
        $needsFollowUp = $true
    }

    [pscustomobject]@{
        scenario = $row.scenario
        constraint_type = $row.constraint_type
        element_id = $row.element_id
        bound = $row.bound
        own_endpoint_value = $row.own_endpoint_value
        powermodels_endpoint_value = $row.powermodels_endpoint_value
        own_endpoint_margin = $row.own_endpoint_margin
        powermodels_endpoint_margin = $row.powermodels_endpoint_margin
        own_endpoint_violates_limit = $row.own_endpoint_violates_limit
        powermodels_endpoint_violates_limit = $row.powermodels_endpoint_violates_limit
        endpoint_specific_violation = $row.endpoint_specific_violation
        shared_violation = $row.shared_violation
        max_violation_amount = $maxViolation.ToString("G12", [System.Globalization.CultureInfo]::InvariantCulture)
        value_source_note = $row.value_source_note
        interpretation = $interpretation
        needs_follow_up = $needsFollowUp
    }
}

$diagnosisOutputs = @(
    (Join-Path $Results "python_pm_like_loadflow_violation_diagnosis.csv"),
    (Join-Path $Organized "04_loadflow_validation\python_pm_like_loadflow_violation_diagnosis.csv"),
    (Join-Path $Organized "09_constraint_mismatch_evidence\python_pm_like_loadflow_violation_diagnosis.csv")
)

foreach ($path in $diagnosisOutputs) {
    New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
    $pythonDiagnosis | Export-Csv -LiteralPath $path -NoTypeInformation -Encoding UTF8
}

Write-Host "Wrote $($focused.Count) focused load-flow detail rows"
Write-Host "Wrote $($summary.Count) focused load-flow summary rows"
Write-Host "Wrote $($pythonDiagnosis.Count) Python PM-like violation diagnosis rows"
foreach ($path in ($outputs + $summaryOutputs + $diagnosisOutputs)) {
    Write-Host $path
}
