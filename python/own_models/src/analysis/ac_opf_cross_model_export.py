"""Export compact AC OPF validation scenarios for PowerModels.jl."""

from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import re

import pandas as pd

from own_models.src.analysis.contingencies import apply_branch_outage, apply_generator_outage
from own_models.src.data.case import Branch, Bus, Generator, Load, PowerCase


SCENARIO_KEY_COLUMNS = [
    "case_name",
    "scenario",
    "target_type",
    "target_id",
    "parameter_name",
    "parameter_value",
]


def export_powermodels_ac_scenarios(
    compact_sweep_csv: str | Path,
    *,
    output_dir: str | Path,
    case_loader,
    cases: set[str] | None = None,
    case_labels: set[str] | None = None,
    include_no_reference_slack: bool = False,
) -> pd.DataFrame:
    """Export compact AC OPF scenarios as MATPOWER files and return a manifest."""
    compact_sweep_csv = Path(compact_sweep_csv)
    output_dir = Path(output_dir)
    scenario_dir = output_dir / "cases"
    scenario_dir.mkdir(parents=True, exist_ok=True)

    frame = pd.read_csv(compact_sweep_csv, dtype=str, keep_default_na=False)
    known_case_labels = {_case_label(case) for case in (case_labels or cases or set())}
    if cases:
        case_names = {_case_label(case) for case in cases}
        frame = frame[
            frame["case_name"].map(lambda name: _infer_base_case_label(str(name), known_case_labels)).isin(case_names)
        ]

    base_cases: dict[str, PowerCase] = {}
    rows: list[dict[str, object]] = []
    for _, source_row in frame.iterrows():
        row = source_row.to_dict()
        if row.get("scenario") == "slack_without_reference" and not include_no_reference_slack:
            continue

        case_name = str(row["case_name"])
        base_case_name = _infer_base_case_label(case_name, known_case_labels)
        if base_case_name not in base_cases:
            base_cases[base_case_name] = replace(case_loader(base_case_name), name=base_case_name)

        scenario_case = build_scenario_case_from_compact_row(base_cases[base_case_name], row)
        scenario_id = compact_ac_reference_scenario_id(row)
        scenario_path = scenario_dir / f"{scenario_id}.m"
        write_matpower_case(scenario_path, scenario_case, function_name=scenario_id)

        rows.append(
            {
                **{column: row.get(column, "") for column in SCENARIO_KEY_COLUMNS},
                "scenario_id": scenario_id,
                "scenario_file": str(scenario_path),
            }
        )

    manifest = pd.DataFrame(rows)
    manifest_path = output_dir / "compact_ac_opf_powermodels_manifest.csv"
    manifest.to_csv(manifest_path, index=False)
    return manifest


def build_scenario_case_from_compact_row(base_case: PowerCase, row: dict[str, object]) -> PowerCase:
    """Recreate the scenario case described by one compact sweep row."""
    scenario = str(row.get("scenario", ""))
    parameter_name = str(row.get("parameter_name", ""))
    parameter_value = str(row.get("parameter_value", ""))
    target_id = str(row.get("target_id", ""))

    if scenario == "base":
        return base_case
    if scenario == "relaxed_limits":
        case = relax_limits(base_case, rate_multiplier=float(parameter_value), angle_limit_deg=180.0)
        return with_voltage_bounds(case, relaxed_voltage_bounds(base_case, (0.90, 1.10)))
    if scenario == "branch_congestion":
        return scale_all_branch_rates(base_case, float(parameter_value))
    if scenario == "tight_voltage_limits":
        lower, upper = _parse_bounds(parameter_value)
        return with_voltage_bounds(base_case, restricted_voltage_bounds(base_case, (lower, upper)))
    if scenario in ("generator_capacity_reduction", "generator_capacity_expansion"):
        return scale_generator_pmax(base_case, target_id, float(parameter_value))
    if scenario == "load_scaling":
        return scale_loads(base_case, float(parameter_value), scale_p=True, scale_q=True)
    if scenario == "system_angle_limits":
        return set_system_angle_limit(base_case, float(parameter_value))
    if scenario == "reactive_power_stress":
        return scale_loads(base_case, float(parameter_value), scale_p=False, scale_q=True)
    if scenario == "reactive_generation_limits":
        return tighten_reactive_generation_limits(base_case, float(parameter_value))
    if scenario == "slack_consistency":
        return with_slack_bus(base_case, str(parameter_value))
    if scenario == "branch_outage":
        return apply_branch_outage(base_case, target_id)
    if scenario == "generator_outage":
        return apply_generator_outage(base_case, target_id)
    if scenario == "slack_without_reference" and parameter_name == "fix_reference_angle":
        return base_case

    raise ValueError(f"Unsupported compact AC OPF scenario row: {row!r}")


def relax_limits(case: PowerCase, *, rate_multiplier: float, angle_limit_deg: float) -> PowerCase:
    limit = math.radians(angle_limit_deg)
    branches = [
        replace(
            branch,
            rate_a=branch.rate_a * rate_multiplier if branch.rate_a > 0.0 else 0.0,
            angmin=-limit,
            angmax=limit,
        )
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_relaxed", branches=branches)


def scale_all_branch_rates(case: PowerCase, multiplier: float) -> PowerCase:
    branches: list[Branch] = [
        replace(branch, rate_a=branch.rate_a * multiplier if branch.rate_a > 0.0 else branch.rate_a)
        for branch in case.branches
    ]
    return replace(case, name=f"{case.name}_all_branch_rates_x{multiplier}", branches=branches)


def relaxed_voltage_bounds(case: PowerCase, target_bounds: tuple[float, float]) -> dict[str, tuple[float, float]]:
    lower, upper = target_bounds
    return {bus.id: (min(float(bus.vmin), float(lower)), max(float(bus.vmax), float(upper))) for bus in case.buses}


def restricted_voltage_bounds(case: PowerCase, target_bounds: tuple[float, float]) -> dict[str, tuple[float, float]]:
    lower, upper = target_bounds
    half_width = max(0.0, (float(upper) - float(lower)) / 2.0)
    if half_width == 0.0:
        raise ValueError(f"Voltage tightening bounds must have positive width, got {target_bounds!r}.")
    return {bus.id: tight_voltage_bounds_for_bus(bus, half_width) for bus in case.buses}


def tight_voltage_bounds_for_bus(bus: Bus, half_width: float) -> tuple[float, float]:
    case_lower = float(bus.vmin)
    case_upper = float(bus.vmax)
    if case_lower > case_upper:
        raise ValueError(f"Bus {bus.id} has invalid voltage limits: {case_lower} > {case_upper}.")
    center = min(max(float(bus.vm0), case_lower), case_upper)
    return (max(case_lower, center - half_width), min(case_upper, center + half_width))


def scale_generator_pmax(case: PowerCase, gen_id: str, multiplier: float) -> PowerCase:
    generators: list[Generator] = [
        replace(gen, pmax=max(gen.pmin, gen.pmax * multiplier)) if gen.id == gen_id else gen
        for gen in case.generators
    ]
    return replace(case, name=f"{case.name}_gen_{gen_id}_pmax_x{multiplier}", generators=generators)


def scale_loads(case: PowerCase, multiplier: float, *, scale_p: bool, scale_q: bool) -> PowerCase:
    loads: list[Load] = [
        replace(
            load,
            pd=load.pd * multiplier if scale_p else load.pd,
            qd=load.qd * multiplier if scale_q else load.qd,
        )
        for load in case.loads
    ]
    return replace(case, name=f"{case.name}_load_x{multiplier}", loads=loads)


def set_system_angle_limit(case: PowerCase, angle_limit_deg: float) -> PowerCase:
    limit = math.radians(angle_limit_deg)
    branches: list[Branch] = [replace(branch, angmin=-limit, angmax=limit) for branch in case.branches]
    return replace(case, name=f"{case.name}_angle_{angle_limit_deg}deg", branches=branches)


def tighten_reactive_generation_limits(case: PowerCase, multiplier: float) -> PowerCase:
    generators: list[Generator] = []
    for gen in case.generators:
        if gen.status != 1:
            generators.append(gen)
            continue
        center = min(max(gen.qg0, gen.qmin), gen.qmax)
        lower_width = max(0.0, center - gen.qmin) * multiplier
        upper_width = max(0.0, gen.qmax - center) * multiplier
        generators.append(replace(gen, qmin=center - lower_width, qmax=center + upper_width, qg0=center))
    return replace(case, name=f"{case.name}_qg_limits_x{multiplier}", generators=generators)


def compact_ac_reference_scenario_id(row: dict[str, object]) -> str:
    """Build a filesystem-safe id from the stable compact scenario key."""
    parts = [str(row.get(column, "")) for column in SCENARIO_KEY_COLUMNS]
    text = "__".join(part if part else "none" for part in parts)
    text = text.replace(".", "p").replace("-", "m")
    return re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")


def with_voltage_bounds(case: PowerCase, bounds: dict[str, tuple[float, float]]) -> PowerCase:
    buses = [
        replace(bus, vmin=float(bounds[bus.id][0]), vmax=float(bounds[bus.id][1]))
        if bus.id in bounds
        else bus
        for bus in case.buses
    ]
    return replace(case, buses=buses)


def with_slack_bus(case: PowerCase, slack_bus_id: str) -> PowerCase:
    gen_buses = {gen.bus for gen in case.generators if gen.status == 1}
    buses: list[Bus] = []
    for bus in case.buses:
        if bus.id == slack_bus_id:
            buses.append(replace(bus, bus_type=3))
        elif bus.bus_type == 3:
            buses.append(replace(bus, bus_type=2 if bus.id in gen_buses else 1))
        else:
            buses.append(bus)
    return replace(case, name=f"{case.name}_slack_{slack_bus_id}", buses=buses, slack_bus_id=slack_bus_id)


def write_matpower_case(path: str | Path, case: PowerCase, *, function_name: str) -> None:
    """Write a normalized ``PowerCase`` as a MATPOWER v2 ``.m`` file."""
    path = Path(path)
    load_by_bus = {bus.id: [0.0, 0.0] for bus in case.buses}
    for load in case.loads:
        if load.status != 1:
            continue
        load_by_bus.setdefault(load.bus, [0.0, 0.0])
        load_by_bus[load.bus][0] += load.pd * case.base_mva
        load_by_bus[load.bus][1] += load.qd * case.base_mva

    shunt_by_bus = {bus.id: [0.0, 0.0] for bus in case.buses}
    for shunt in case.shunts:
        if shunt.status != 1:
            continue
        shunt_by_bus.setdefault(shunt.bus, [0.0, 0.0])
        shunt_by_bus[shunt.bus][0] += shunt.gs * case.base_mva
        shunt_by_bus[shunt.bus][1] += shunt.bs * case.base_mva

    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"function mpc = {function_name}\n")
        handle.write("mpc.version = '2';\n")
        handle.write(f"mpc.baseMVA = {_fmt(case.base_mva)};\n\n")

        handle.write("%% bus data\n")
        handle.write("% bus_i type Pd Qd Gs Bs area Vm Va baseKV zone Vmax Vmin\n")
        handle.write("mpc.bus = [\n")
        for bus in case.buses:
            pd, qd = load_by_bus.get(bus.id, [0.0, 0.0])
            gs, bs = shunt_by_bus.get(bus.id, [0.0, 0.0])
            handle.write(
                "\t"
                + "\t".join(
                    [
                        bus.id,
                        str(bus.bus_type),
                        _fmt(pd),
                        _fmt(qd),
                        _fmt(gs),
                        _fmt(bs),
                        "1",
                        _fmt(bus.vm0),
                        _fmt(math.degrees(bus.va0)),
                        _fmt(bus.nominal_v_kv),
                        "1",
                        _fmt(bus.vmax),
                        _fmt(bus.vmin),
                    ]
                )
                + ";\n"
            )
        handle.write("];\n\n")

        handle.write("%% generator data\n")
        handle.write("% bus Pg Qg Qmax Qmin Vg mBase status Pmax Pmin\n")
        handle.write("mpc.gen = [\n")
        for gen in case.generators:
            bus = next(bus for bus in case.buses if bus.id == gen.bus)
            handle.write(
                "\t"
                + "\t".join(
                    [
                        gen.bus,
                        _fmt(gen.pg0 * case.base_mva),
                        _fmt(gen.qg0 * case.base_mva),
                        _fmt(gen.qmax * case.base_mva),
                        _fmt(gen.qmin * case.base_mva),
                        _fmt(bus.vm0),
                        _fmt(case.base_mva),
                        str(gen.status),
                        _fmt(gen.pmax * case.base_mva),
                        _fmt(gen.pmin * case.base_mva),
                    ]
                )
                + ";\n"
            )
        handle.write("];\n\n")

        handle.write("%% generator cost data\n")
        handle.write("% 2 startup shutdown n c2 c1 c0\n")
        handle.write("mpc.gencost = [\n")
        for gen in case.generators:
            handle.write(
                "\t2\t0\t0\t3\t"
                + "\t".join([_fmt(gen.cost_c2), _fmt(gen.cost_c1), _fmt(gen.cost_c0)])
                + ";\n"
            )
        handle.write("];\n\n")

        handle.write("%% branch data\n")
        handle.write("% fbus tbus r x b rateA rateB rateC ratio angle status angmin angmax\n")
        handle.write("mpc.branch = [\n")
        for branch in case.branches:
            rate = branch.rate_a * case.base_mva
            tap = 0.0 if abs(branch.tap - 1.0) <= 1e-12 else branch.tap
            handle.write(
                "\t"
                + "\t".join(
                    [
                        branch.from_bus,
                        branch.to_bus,
                        _fmt(branch.r),
                        _fmt(branch.x),
                        _fmt(branch.b),
                        _fmt(rate),
                        _fmt(rate),
                        _fmt(rate),
                        _fmt(tap),
                        _fmt(math.degrees(branch.shift)),
                        str(branch.status),
                        _fmt(math.degrees(branch.angmin)),
                        _fmt(math.degrees(branch.angmax)),
                    ]
                )
                + ";\n"
            )
        handle.write("];\n")


def _case_label(case: str) -> str:
    return str(case).replace("pglib_opf_case", "").replace(".mat", "").replace(".m", "")


def _infer_base_case_label(case_name: str, known_case_labels: set[str]) -> str:
    label = _case_label(case_name)
    for candidate in sorted(known_case_labels, key=len, reverse=True):
        if label == candidate or label.startswith(candidate + "_"):
            return candidate
    return label


def _parse_bounds(text: str) -> tuple[float, float]:
    lower, upper = str(text).split("-", 1)
    return float(lower), float(upper)


def _fmt(value: float) -> str:
    return f"{float(value):.12g}"
