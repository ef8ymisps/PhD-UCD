module OwnPowerModels

include("data/case_structs.jl")
include("data/branch_classification.jl")
include("adapters/powermodels_adapter.jl")
include("data/case_loader.jl")

include("network/dc_matrices.jl")
include("network/admittance_matrix.jl")

include("pf/dc_power_flow.jl")
include("pf/ac_power_flow.jl")

include("opf/solver_selection.jl")
include("opf/dc/dc.jl")
include("opf/ac/ac.jl")

include("analysis/compare_with_powermodels.jl")
include("analysis/dc_to_ac_feasibility.jl")
include("analysis/feasibility_metrics.jl")
include("analysis/benchmarking.jl")
include("analysis/validation_cases.jl")
include("analysis/validation_runner.jl")
include("analysis/opf_solution_inspection.jl")
include("analysis/base_opf_cross_model.jl")
include("analysis/ac_opf_validation.jl")
include("analysis/ac_pf_compute_validation.jl")
include("analysis/n1_screening.jl")
include("analysis/dc_n1_screening.jl")

# PTDF/LODF (network/ptdf.jl) must load after opf/dc/dc.jl (uses DCOPFData
# as a type annotation).
include("network/ptdf.jl")

# Monolithic preventive DC-SCOPF: data structures + contingency enumeration
# (data.jl), then the model builder (model.jl) -- must load after
# n1_screening.jl (reuses N1Contingency/generate_*_n1_contingencies),
# opf/dc/dc.jl (reuses DCOPFData/build_dc_opf_data and the base DC-OPF's
# own variable/constraint/objective builders), and network/ptdf.jl (reuses
# compute_ptdf/compute_lodf). See docs/formulations/dc_scopf.md.
include("opf/dc_scopf/data.jl")
include("opf/dc_scopf/automatic_response.jl")
include("opf/dc_scopf/model.jl")

# Reusable SCOPF measurement/evidence collection -- must load after
# opf_solution_inspection.jl (reuses append_rows_csv!), n1_screening.jl
# (N1Contingency), and opf/dc_scopf/model.jl (DCSCOPFSkippedContingency).
include("analysis/scopf_evidence.jl")

# Reusable per-contingency isolation diagnosis -- must load after
# scopf_evidence.jl (append_rows_csv!) and opf/dc_scopf/model.jl
# (build_dc_scopf_model's anchor_pg/contingencies kwargs).
include("analysis/scopf_contingency_diagnosis.jl")

export PowerCase
export load_powercase_from_powermodels
export solve_dc_power_flow
export solve_ac_power_flow_own
export solve_ac_power_flow_newton
export solve_ac_power_flow_with_q_limits
export solve_dc_opf_own
export solve_ac_opf_own
export build_dc_opf_model
export build_ac_opf_model
export get_powermodels_branch_flows
export compare_branch_flows_with_powermodels
export compare_with_powermodels
export check_dc_opf_dispatch_with_ac_pf
export BenchmarkResult
export OPFSpeedComparisonResult
export benchmark_call
export benchmark_opf_speed_comparison
export opf_speed_comparison_as_named_tuple
export print_opf_speed_summary
export ValidationCaseSummary
export make_relaxed_case
export make_congested_case
export make_generator_limited_case
export make_infeasible_load_case
export first_active_branch_id
export first_active_generator_id
export build_default_validation_cases
export summarize_validation_case
export validation_case_summary_as_named_tuple
export validation_cases_as_named_tuples
export ValidationOPFStudyRow
export run_opf_validation_case
export validation_opf_study_row_as_named_tuple
export run_validation_opf_study
export own_dc_opf_generator_rows
export powermodels_dc_opf_generator_rows
export compare_dc_opf_generator_rows
export own_dc_opf_branch_rows
export powermodels_dc_opf_branch_rows
export compare_dc_opf_branch_rows
export dc_opf_summary_row
export own_ac_opf_generator_rows
export powermodels_ac_opf_generator_rows
export compare_ac_opf_generator_rows
export own_ac_opf_bus_rows
export powermodels_ac_opf_bus_rows
export compare_ac_opf_bus_rows
export own_ac_opf_branch_rows
export powermodels_ac_opf_branch_rows
export compare_ac_opf_branch_rows
export inspect_branch_angle_limits
export ac_opf_summary_row
export ACOPFValidationTolerances
export ACOPFValidationResult
export default_ac_opf_validation_tolerances
export run_ac_opf_validation_case
export run_ac_opf_validation_suite
export run_powermodels_ac_pf_compute
export compare_own_fixed_ac_pf_with_powermodels_compute
export BaseOPFCrossModelResult
export run_base_dc_opf_cross_model_case
export run_base_ac_opf_cross_model_case
export run_base_opf_cross_model_suite
export write_base_opf_cross_model_csvs
export classify_binding
export classify_mismatch_reason
export binding_set_comparison
export classify_dc_opf_generator_rows
export classify_dc_opf_branch_rows
export classify_ac_opf_generator_rows
export classify_ac_opf_bus_rows
export classify_ac_opf_branch_rows
export ac_branch_flow_convention_check
export append_rows_csv!
export write_dc_opf_detail_rows!
export write_ac_opf_detail_rows!
export is_transformer
export branch_equipment_type
export N1Contingency
export generate_branch_n1_contingencies
export generate_line_n1_contingencies
export generate_transformer_n1_contingencies
export generate_generator_n1_contingencies
export apply_proportional_reserve_balancing
export run_n1_branch_screening
export run_n1_generator_screening
export run_n1_screening
export run_dc_n1_screening
export dc_contingency_violations
export case_with_generator_pg_dispatch
export case_with_base_dispatch_own_ac
export DCSCOPFContingencyData
export DCSCOPFData
export build_dc_scopf_contingency_data
export build_dc_scopf_data
export compute_ptdf
export LODFResult
export compute_lodf
export generator_response_weights
export generator_response_weights_by_bus
export DCSCOPFSkippedContingency
export build_dc_scopf_model
export solve_dc_scopf_own
export generate_non_islanding_line_n1_contingencies
export generate_non_islanding_branch_n1_contingencies
export scopf_summary_row
export scopf_contingency_rows
export scopf_stage_timing_rows
export write_scopf_evidence!
export total_constraint_count
export peak_memory_mb
export peak_working_set_bytes
export diagnose_scopf_contingencies
export print_scopf_contingency_diagnosis_summary

end
