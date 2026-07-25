from pathlib import Path
import json
import shutil

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "own_vs_powermodels"
ORGANIZED_OUT = OUT / "_organized_for_meeting" / "04_loadflow_validation"
REFERENCE_DIR = ROOT / "results" / "reference" / "powermodels" / "ac_opf"
TOL = 1e-6
VARIANT_ROOTS = {
    "current": OUT / "julia",
    "pm_like": OUT / "julia_pm_like",
}


def load_objective_context() -> pd.DataFrame:
    frames = []
    current_path = OUT / "objective_mismatch_details_julia_current.csv"
    if current_path.exists():
        df = pd.read_csv(current_path)
        df["variant"] = "current"
        frames.append(df)

    pm_like_path = OUT / "objective_mismatch_details_julia_pm_like.csv"
    if pm_like_path.exists():
        df = pd.read_csv(pm_like_path)
        df["variant"] = "pm_like"
        if "own_pm_like_objective" in df.columns:
            df["own_objective"] = df["own_pm_like_objective"]
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["variant", "case", "scenario"])

    keep = [
        "variant",
        "case",
        "scenario",
        "own_objective",
        "pm_objective",
        "objective_diff_own_minus_pm",
        "objective_rel_diff_pct",
        "cheaper_solution",
        "cheaper_by_abs",
    ]
    return pd.concat(frames, ignore_index=True)[keep]


def family_of(constraint_type: str) -> str:
    if constraint_type.startswith("pg_"):
        return "pg"
    if constraint_type.startswith("qg_"):
        return "qg"
    if constraint_type.startswith("vm_"):
        return "vm"
    if constraint_type == "thermal":
        return "thermal"
    if constraint_type.startswith("angle_"):
        return "angle"
    return constraint_type


def scenario_output_dir(variant: str, case: str, scenario: str) -> Path | None:
    root = VARIANT_ROOTS.get(str(variant))
    if root is None:
        return None
    case_root = root / str(case)
    direct = case_root / str(scenario)
    if direct.exists():
        return direct
    if not case_root.exists():
        return None
    for child in case_root.iterdir():
        if not child.is_dir():
            continue
        if child.name == str(scenario):
            return child
        label_path = child / "scenario_label.txt"
        if label_path.exists() and label_path.read_text(encoding="utf-8").strip() == str(scenario):
            return child
    return None


_GENERATOR_BUS_CACHE: dict[tuple[str, str, str], set[str]] = {}
_BUS_TYPE_CACHE: dict[tuple[str, str], dict[str, str]] = {}


def bus_type_label(value: object) -> str:
    text = str(value)
    if text == "1":
        return "PQ"
    if text == "2":
        return "PV"
    if text == "3":
        return "slack"
    return "unknown"


def reference_case_path(case: str, scenario: str) -> Path | None:
    stem = f"pglib_opf_case{case}"
    direct = REFERENCE_DIR / f"{stem}_{scenario}_case.json"
    if direct.exists():
        return direct

    reference_direct = REFERENCE_DIR / f"{stem}_{scenario}_reference.json"
    if reference_direct.exists():
        case_path = reference_direct.with_name(reference_direct.name.replace("_reference.json", "_case.json"))
        if case_path.exists():
            return case_path

    for reference_path in sorted(REFERENCE_DIR.glob(f"{stem}_*_reference.json")):
        suffix = reference_path.name[len(stem) + 1 : -len("_reference.json")]
        case_path = REFERENCE_DIR / f"{stem}_{suffix}_case.json"
        if not case_path.exists():
            continue
        if suffix == scenario:
            return case_path
        try:
            reference = json.loads(reference_path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if str(reference.get("scenario", "")) == str(scenario):
            return case_path
    return None


def bus_types(case: str, scenario: str) -> dict[str, str]:
    key = (str(case), str(scenario))
    if key in _BUS_TYPE_CACHE:
        return _BUS_TYPE_CACHE[key]
    path = reference_case_path(*key)
    if path is None:
        _BUS_TYPE_CACHE[key] = {}
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    mapping = {
        str(bus_id): bus_type_label(bus.get("bus_type"))
        for bus_id, bus in data.get("bus", {}).items()
    }
    _BUS_TYPE_CACHE[key] = mapping
    return mapping


def generator_buses(variant: str, case: str, scenario: str) -> set[str]:
    key = (str(variant), str(case), str(scenario))
    if key in _GENERATOR_BUS_CACHE:
        return _GENERATOR_BUS_CACHE[key]
    out_dir = scenario_output_dir(*key)
    if out_dir is None:
        _GENERATOR_BUS_CACHE[key] = set()
        return set()
    path = out_dir / "generators.csv"
    if not path.exists():
        _GENERATOR_BUS_CACHE[key] = set()
        return set()
    frame = pd.read_csv(path, dtype=str)
    buses = set(frame["bus_id"].astype(str)) if "bus_id" in frame.columns else set()
    _GENERATOR_BUS_CACHE[key] = buses
    return buses


def classify_pf_check(row: pd.Series) -> dict[str, object]:
    constraint_type = str(row["constraint_type"])
    family = family_of(constraint_type)

    if family == "pg":
        return {
            "pf_check_independence": "specified_input_not_rederived",
            "pf_value_source": "OPF endpoint Pg used as fixed PF active-power injection",
            "bus_voltage_check_class": "not_voltage",
            "independently_rederived_after_pf": False,
        }

    if family == "qg":
        return {
            "pf_check_independence": "pf_rederived_bus_q_requirement_then_allocated_to_generators",
            "pf_value_source": "PF Qcalc plus load gives aggregate bus Qg requirement; generator-level Qg is allocated/clipped across units for reporting",
            "bus_voltage_check_class": "not_voltage",
            "independently_rederived_after_pf": False,
        }

    if family == "vm":
        bus_type = str(row.get("bus_type", "")).strip().lower()
        if bus_type in {"pv", "slack"}:
            return {
                "pf_check_independence": "opf_endpoint_voltage_setpoint_pinned_in_pf",
                "pf_value_source": "PV/slack voltage magnitude retained from OPF endpoint seed",
                "bus_voltage_check_class": "pinned_pv_or_slack_vm",
                "independently_rederived_after_pf": False,
            }
        if bus_type == "pq":
            return {
                "pf_check_independence": "pf_rederived_pq_bus_vm",
                "pf_value_source": "Newton-solved PQ-bus voltage magnitude",
                "bus_voltage_check_class": "rederived_pq_bus_vm",
                "independently_rederived_after_pf": True,
            }

        # Existing level CSVs were generated before bus_type was emitted.  The
        # preferred recovery path is the PowerModels case JSON used to build
        # the scenario; fallback to generator-bus proxy only if that JSON cannot
        # be located.
        exact_bus_type = bus_types(row["case"], row["scenario"]).get(str(row["element_id"]), "")
        if exact_bus_type == "PQ":
            return {
                "pf_check_independence": "pf_rederived_pq_bus_vm",
                "pf_value_source": "Newton-solved PQ-bus voltage magnitude",
                "bus_voltage_check_class": "rederived_pq_bus_vm",
                "independently_rederived_after_pf": True,
            }
        if exact_bus_type in {"PV", "slack"}:
            return {
                "pf_check_independence": "opf_endpoint_voltage_setpoint_pinned_in_pf",
                "pf_value_source": "PV/slack voltage magnitude retained from OPF endpoint seed",
                "bus_voltage_check_class": "pinned_pv_or_slack_vm",
                "independently_rederived_after_pf": False,
            }

        gen_buses = generator_buses(row["variant"], row["case"], row["scenario"])
        if str(row["element_id"]) in gen_buses:
            return {
                "pf_check_independence": "opf_endpoint_voltage_setpoint_pinned_in_pf",
                "pf_value_source": "Generator-bus voltage magnitude retained from OPF endpoint seed",
                "bus_voltage_check_class": "pinned_generator_bus_vm",
                "independently_rederived_after_pf": False,
            }
        return {
            "pf_check_independence": "pf_rederived_non_generator_bus_vm",
            "pf_value_source": "Newton-solved non-generator bus voltage magnitude",
            "bus_voltage_check_class": "rederived_non_generator_bus_vm",
            "independently_rederived_after_pf": True,
        }

    if family == "thermal":
        return {
            "pf_check_independence": "pf_rederived_branch_flow_from_pf_voltage",
            "pf_value_source": "Branch apparent flow recomputed from endpoint PF Vm/Va",
            "bus_voltage_check_class": "not_voltage",
            "independently_rederived_after_pf": True,
        }

    if family == "angle":
        return {
            "pf_check_independence": "pf_rederived_angle_from_pf_voltage",
            "pf_value_source": "Branch angle difference recomputed from endpoint PF Va",
            "bus_voltage_check_class": "not_voltage",
            "independently_rederived_after_pf": True,
        }

    return {
        "pf_check_independence": "unknown",
        "pf_value_source": "unknown",
        "bus_voltage_check_class": "unknown",
        "independently_rederived_after_pf": False,
    }


def bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def add_pf_check_classification(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if {
        "pf_check_independence",
        "pf_value_source",
        "bus_voltage_check_class",
        "independently_rederived_after_pf",
    }.issubset(df.columns):
        df["independently_rederived_after_pf"] = bool_series(df["independently_rederived_after_pf"])
        return df

    classifications = pd.DataFrame([classify_pf_check(row) for _, row in df.iterrows()])
    return pd.concat([df.reset_index(drop=True), classifications], axis=1)


def add_flags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["constraint_family"] = df["constraint_type"].map(family_of)
    df = add_pf_check_classification(df)

    for endpoint in ("own_opf", "pm_opf", "own_pf", "pm_pf"):
        df[f"{endpoint}_violation"] = df[f"{endpoint}_margin"] < -TOL
        df[f"{endpoint}_violation_amount"] = (-df[f"{endpoint}_margin"]).clip(lower=0.0)

    df["own_pf_less_slack_than_pm_pf"] = df["own_pf_margin"] < df["pm_pf_margin"] - TOL
    df["pm_pf_less_slack_than_own_pf"] = df["pm_pf_margin"] < df["own_pf_margin"] - TOL
    df["own_pf_margin_minus_pm_pf_margin"] = df["own_pf_margin"] - df["pm_pf_margin"]
    df["own_pf_binding_pm_pf_slack"] = df["pf_binding_relation"] == "only_own_pf_binding"
    df["pm_pf_binding_own_pf_slack"] = df["pf_binding_relation"] == "only_pm_pf_binding"
    df["not_independently_rederived_after_pf"] = ~df["independently_rederived_after_pf"]
    df["specified_or_pinned_after_pf"] = df["pf_check_independence"].isin(
        {
            "specified_input_not_rederived",
            "powermodels_compute_ac_pf_specified_pg_dispatch",
            "opf_endpoint_voltage_setpoint_pinned_in_pf",
            "powermodels_compute_ac_pf_voltage_setpoint_pinned",
        }
    )
    return df


def _max_or_zero(series: pd.Series) -> float:
    return 0.0 if series.empty else float(series.max())


def _min_or_nan(series: pd.Series) -> float:
    return float("nan") if series.empty else float(series.min())


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    group_cols = ["variant", "case", "scenario"]
    for keys, g in df.groupby(group_cols, dropna=False):
        independent = g[g["independently_rederived_after_pf"]]
        not_independent = g[~g["independently_rederived_after_pf"]]
        specified_or_pinned = g[g["specified_or_pinned_after_pf"]]
        row = dict(zip(group_cols, keys))
        row.update(
            n_constraints=len(g),
            n_independently_rederived_constraints=len(independent),
            n_not_independently_rederived_constraints=len(not_independent),
            n_specified_or_pinned_constraints=len(specified_or_pinned),
            n_own_pf_violations=int(g["own_pf_violation"].sum()),
            n_pm_pf_violations=int(g["pm_pf_violation"].sum()),
            n_independent_own_pf_violations=int(independent["own_pf_violation"].sum()),
            n_independent_pm_pf_violations=int(independent["pm_pf_violation"].sum()),
            max_own_pf_violation=float(g["own_pf_violation_amount"].max()),
            max_pm_pf_violation=float(g["pm_pf_violation_amount"].max()),
            max_independent_own_pf_violation=_max_or_zero(independent["own_pf_violation_amount"]),
            max_independent_pm_pf_violation=_max_or_zero(independent["pm_pf_violation_amount"]),
            min_own_pf_margin=float(g["own_pf_margin"].min()),
            min_pm_pf_margin=float(g["pm_pf_margin"].min()),
            min_independent_own_pf_margin=_min_or_nan(independent["own_pf_margin"]),
            min_independent_pm_pf_margin=_min_or_nan(independent["pm_pf_margin"]),
            n_pf_binding_mismatches=int((g["pf_binding_relation"] != "same").sum()),
            n_own_pf_binding_pm_pf_slack=int(g["own_pf_binding_pm_pf_slack"].sum()),
            n_pm_pf_binding_own_pf_slack=int(g["pm_pf_binding_own_pf_slack"].sum()),
            n_own_pf_less_slack_than_pm_pf=int(g["own_pf_less_slack_than_pm_pf"].sum()),
            max_abs_pf_margin_diff=float(g["abs_pf_margin_diff"].max()),
        )
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_by_family(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    group_cols = ["variant", "case", "scenario", "constraint_family"]
    for keys, g in df.groupby(group_cols, dropna=False):
        independent = g[g["independently_rederived_after_pf"]]
        not_independent = g[~g["independently_rederived_after_pf"]]
        specified_or_pinned = g[g["specified_or_pinned_after_pf"]]
        row = dict(zip(group_cols, keys))
        row.update(
            n_constraints=len(g),
            n_independently_rederived_constraints=len(independent),
            n_not_independently_rederived_constraints=len(not_independent),
            n_specified_or_pinned_constraints=len(specified_or_pinned),
            n_own_pf_violations=int(g["own_pf_violation"].sum()),
            n_pm_pf_violations=int(g["pm_pf_violation"].sum()),
            n_independent_own_pf_violations=int(independent["own_pf_violation"].sum()),
            n_independent_pm_pf_violations=int(independent["pm_pf_violation"].sum()),
            max_own_pf_violation=float(g["own_pf_violation_amount"].max()),
            max_pm_pf_violation=float(g["pm_pf_violation_amount"].max()),
            max_independent_own_pf_violation=_max_or_zero(independent["own_pf_violation_amount"]),
            max_independent_pm_pf_violation=_max_or_zero(independent["pm_pf_violation_amount"]),
            n_pf_binding_mismatches=int((g["pf_binding_relation"] != "same").sum()),
            n_own_pf_binding_pm_pf_slack=int(g["own_pf_binding_pm_pf_slack"].sum()),
            n_pm_pf_binding_own_pf_slack=int(g["pm_pf_binding_own_pf_slack"].sum()),
            max_abs_pf_margin_diff=float(g["abs_pf_margin_diff"].max()),
        )
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_by_independence(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    group_cols = ["variant", "case", "scenario", "constraint_family", "pf_check_independence"]
    for keys, g in df.groupby(group_cols, dropna=False):
        row = dict(zip(group_cols, keys))
        row.update(
            n_constraints=len(g),
            independently_rederived_after_pf=bool(g["independently_rederived_after_pf"].iloc[0]),
            n_own_pf_violations=int(g["own_pf_violation"].sum()),
            n_pm_pf_violations=int(g["pm_pf_violation"].sum()),
            max_own_pf_violation=float(g["own_pf_violation_amount"].max()),
            max_pm_pf_violation=float(g["pm_pf_violation_amount"].max()),
            n_pf_binding_mismatches=int((g["pf_binding_relation"] != "same").sum()),
            max_abs_pf_margin_diff=float(g["abs_pf_margin_diff"].max()),
            pf_value_source=str(g["pf_value_source"].iloc[0]),
        )
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_vm_by_bus_class(df: pd.DataFrame) -> pd.DataFrame:
    vm = df[df["constraint_family"] == "vm"].copy()
    rows = []
    group_cols = ["variant", "case", "scenario", "bus_voltage_check_class", "pf_check_independence"]
    for keys, g in vm.groupby(group_cols, dropna=False):
        row = dict(zip(group_cols, keys))
        row.update(
            n_constraints=len(g),
            n_buses=int(len(g["element_id"].astype(str).unique())),
            independently_rederived_after_pf=bool(g["independently_rederived_after_pf"].iloc[0]),
            n_own_pf_violations=int(g["own_pf_violation"].sum()),
            n_pm_pf_violations=int(g["pm_pf_violation"].sum()),
            max_own_pf_violation=float(g["own_pf_violation_amount"].max()),
            max_pm_pf_violation=float(g["pm_pf_violation_amount"].max()),
            n_pf_binding_mismatches=int((g["pf_binding_relation"] != "same").sum()),
            max_abs_pf_margin_diff=float(g["abs_pf_margin_diff"].max()),
            pf_value_source=str(g["pf_value_source"].iloc[0]),
        )
        rows.append(row)
    return pd.DataFrame(rows)


def copy_outputs(paths: list[Path]) -> None:
    ORGANIZED_OUT.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if path.exists():
            shutil.copy2(path, ORGANIZED_OUT / path.name)


def main() -> None:
    levels_path = OUT / "opf_endpoint_pf_inequality_levels_julia.csv"
    df = pd.read_csv(levels_path)
    obj = load_objective_context()
    df = add_flags(df)
    df = df.merge(obj, how="left", on=["variant", "case", "scenario"])

    summary = summarize(df)
    summary = summary.merge(obj, how="left", on=["variant", "case", "scenario"])

    by_family = summarize_by_family(df)
    by_family = by_family.merge(obj, how="left", on=["variant", "case", "scenario"])

    by_independence = summarize_by_independence(df)
    by_independence = by_independence.merge(obj, how="left", on=["variant", "case", "scenario"])

    vm_by_bus_class = summarize_vm_by_bus_class(df)
    vm_by_bus_class = vm_by_bus_class.merge(obj, how="left", on=["variant", "case", "scenario"])

    violations = df[df["own_pf_violation"] | df["pm_pf_violation"]].copy()
    violations = violations.sort_values(
        ["variant", "case", "scenario", "own_pf_violation_amount", "pm_pf_violation_amount"],
        ascending=[True, True, True, False, False],
    )

    binding_diffs = df[df["pf_binding_relation"] != "same"].copy()
    binding_diffs = binding_diffs.sort_values(
        ["variant", "case", "scenario", "abs_pf_margin_diff"],
        ascending=[True, True, True, False],
    )

    own_cheaper_focus = df[df["cheaper_solution"].isin(["own_julia", "own_pm_like", "own"])].copy()
    own_cheaper_focus = own_cheaper_focus[
        own_cheaper_focus["own_pf_violation"]
        | own_cheaper_focus["own_pf_binding_pm_pf_slack"]
        | own_cheaper_focus["own_pf_less_slack_than_pm_pf"]
    ].sort_values(["variant", "case", "scenario", "abs_pf_margin_diff"], ascending=[True, True, True, False])

    not_independent = df[~df["independently_rederived_after_pf"]].copy()
    not_independent = not_independent.sort_values(
        ["variant", "case", "scenario", "constraint_family", "abs_pf_margin_diff"],
        ascending=[True, True, True, True, False],
    )

    specified_or_pinned = df[df["specified_or_pinned_after_pf"]].copy()
    specified_or_pinned = specified_or_pinned.sort_values(
        ["variant", "case", "scenario", "constraint_family", "abs_pf_margin_diff"],
        ascending=[True, True, True, True, False],
    )

    outputs = {
        "opf_endpoint_pf_inequality_levels_classified_julia.csv": df,
        "opf_endpoint_pf_inequality_feasibility_summary_julia.csv": summary,
        "opf_endpoint_pf_inequality_feasibility_by_family_julia.csv": by_family,
        "opf_endpoint_pf_inequality_feasibility_by_independence_julia.csv": by_independence,
        "opf_endpoint_pf_inequality_vm_by_bus_class_julia.csv": vm_by_bus_class,
        "opf_endpoint_pf_inequality_not_independently_rederived_rows_julia.csv": not_independent,
        "opf_endpoint_pf_inequality_specified_or_pinned_rows_julia.csv": specified_or_pinned,
        "opf_endpoint_pf_inequality_violations_julia.csv": violations,
        "opf_endpoint_pf_inequality_binding_differences_julia.csv": binding_diffs,
        "opf_endpoint_pf_inequality_own_cheaper_focus_julia.csv": own_cheaper_focus,
    }
    output_paths = []
    for name, frame in outputs.items():
        path = OUT / name
        frame.to_csv(path, index=False)
        output_paths.append(path)

    copy_outputs(output_paths)

    print("Wrote:")
    for path in output_paths:
        print(path)
    print(f"Copied outputs to {ORGANIZED_OUT}")


if __name__ == "__main__":
    main()
