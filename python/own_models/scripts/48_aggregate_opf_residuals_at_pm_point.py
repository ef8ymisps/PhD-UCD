"""Aggregate the at_pm_point/ outputs from Julia script 41 and Python script
47 into root-level tables, mirroring script 45's own aggregate-root
convention.

Pure concatenation/summary -- no new computation. Reads the 7 scenario-combo
outputs already written under
results/own_vs_powermodels/_organized_for_meeting/10_opf_solution_inspection/
1888_rte_objective_gap/by_model_scenario/<model_variant>/<scenario>/at_pm_point/
and writes the same files, prefixed with model_variant/case/scenario, to the
root-level at_pm_point/ folder, plus a summary_by_family and a manifest.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BY_MODEL_SCENARIO_ROOT = (
    ROOT
    / "results"
    / "own_vs_powermodels"
    / "_organized_for_meeting"
    / "10_opf_solution_inspection"
    / "1888_rte_objective_gap"
    / "by_model_scenario"
)
OUT_ROOT = BY_MODEL_SCENARIO_ROOT.parent / "at_pm_point"
MATERIAL_TOL = 1e-4

SCENARIO_GRID = [
    ("julia_current", "load_x0.8"),
    ("julia_current", "load_x1.2_reactive_stress"),
    ("julia_pm_like", "load_x0.8"),
    ("julia_pm_like", "load_x1.2_reactive_stress"),
    ("julia_pm_like", "qg_limits_x0.6"),
    ("python_pm_like_zero", "load_x0.8"),
    ("python_pm_like_zero", "load_x1.2_reactive_stress"),
]

DATA_FILES = [
    "power_balance_p",
    "power_balance_q",
    "pf_ohms_at_pm_point",
    "qf_ohms_at_pm_point",
    "pt_ohms_at_pm_point",
    "qt_ohms_at_pm_point",
    "pg_bounds_at_pm_point",
    "qg_bounds_at_pm_point",
    "vm_bounds_at_pm_point",
    "angle_difference_at_pm_point",
    "thermal_at_pm_point",
]

# Per family: which column holds the residual/margin-gap metric, and which
# (already-computed) boolean column marks a "material"/violation row.
FAMILY_METRIC = {
    "power_balance_p": ("residual", "satisfied_1e4", False),
    "power_balance_q": ("residual", "satisfied_1e4", False),
    "pf_ohms_at_pm_point": ("residual", "satisfied_1e4", False),
    "qf_ohms_at_pm_point": ("residual", "satisfied_1e4", False),
    "pt_ohms_at_pm_point": ("residual", "satisfied_1e4", False),
    "qt_ohms_at_pm_point": ("residual", "satisfied_1e4", False),
    "pg_bounds_at_pm_point": ("margin_gap", None, None),
    "qg_bounds_at_pm_point": ("margin_gap", None, None),
    "vm_bounds_at_pm_point": ("margin_gap", None, None),
    "angle_difference_at_pm_point": ("margin_gap", None, None),
    "thermal_at_pm_point": ("margin_gap", "calc_violates_limit", True),
}


def disk_path(path: Path) -> Path:
    # Several of these scenario paths exceed Windows' legacy 260-char
    # MAX_PATH once nested this deep (confirmed: 261 chars for a real path
    # here) -- `.exists()` fails silently (returns False) rather than
    # raising, so without this prefix every file below would look "missing".
    # Same fix as script 45's own `disk_path` helper.
    if not path.is_absolute():
        path = path.resolve()
    if path.drive and not str(path).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(path))
    return path


def read_scenario_csv(model_variant: str, scenario: str, filename: str) -> pd.DataFrame | None:
    path = disk_path(BY_MODEL_SCENARIO_ROOT / model_variant / scenario / "at_pm_point" / f"{filename}.csv")
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return None
    if df.empty:
        return None
    df.insert(0, "scenario", scenario)
    df.insert(0, "case", "1888_rte")
    df.insert(0, "model_variant", model_variant)
    return df


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    aggregate_tables: dict[str, pd.DataFrame] = {}
    manifest_rows = []
    summary_rows = []

    for family in DATA_FILES:
        frames = []
        for model_variant, scenario in SCENARIO_GRID:
            df = read_scenario_csv(model_variant, scenario, family)
            if df is None:
                continue
            frames.append(df)

            metric_col, flag_col, flag_means_violation = FAMILY_METRIC[family]
            if flag_col is not None:
                if flag_means_violation:
                    n_material = int(df[flag_col].sum())
                else:
                    n_material = int((~df[flag_col]).sum())
            else:
                n_material = int((df[metric_col].abs() > MATERIAL_TOL).sum())

            summary_rows.append(
                {
                    "model_variant": model_variant,
                    "case": "1888_rte",
                    "scenario": scenario,
                    "family": family,
                    "n_rows": len(df),
                    "max_abs_residual_or_margin_gap": df[metric_col].abs().max(),
                    "n_material_violations": n_material,
                }
            )

        combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        aggregate_tables[family] = combined
        combined.to_csv(OUT_ROOT / f"{family}.csv", index=False)

    for model_variant, scenario in SCENARIO_GRID:
        manifest_path = disk_path(BY_MODEL_SCENARIO_ROOT / model_variant / scenario / "at_pm_point" / "at_pm_point_manifest.csv")
        if manifest_path.exists():
            manifest_rows.append(pd.read_csv(manifest_path).iloc[0].to_dict())
        else:
            manifest_rows.append(
                {"model_variant": model_variant, "case": "1888_rte", "scenario": scenario, "formulation": "", "status": "missing"}
            )

    pd.DataFrame(manifest_rows).to_csv(OUT_ROOT / "at_pm_point_manifest.csv", index=False)
    pd.DataFrame(summary_rows).sort_values(["model_variant", "scenario", "family"]).to_csv(
        OUT_ROOT / "at_pm_point_summary_by_family.csv", index=False
    )

    print(f"Wrote aggregate at_pm_point tables to {OUT_ROOT}")
    for family, df in aggregate_tables.items():
        print(f"  {family}.csv: {len(df)} rows")


if __name__ == "__main__":
    main()
