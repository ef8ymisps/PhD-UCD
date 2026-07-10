from __future__ import annotations

from dataclasses import replace

from own_models.src.data.case import Branch, Generator, Load, PowerCase


def with_case_name(case: PowerCase, name: str) -> PowerCase:
    return replace(case, name=name)


def make_relaxed_case(
    case: PowerCase,
    *,
    branch_rate_multiplier: float = 10.0,
    generator_limit_multiplier: float = 10.0,
) -> PowerCase:
    branches = [
        replace(branch, rate_a=branch.rate_a * branch_rate_multiplier if branch.rate_a > 0.0 else 0.0)
        for branch in case.branches
    ]
    generators = [
        replace(
            gen,
            pmax=gen.pmax * generator_limit_multiplier if gen.pmax > 0.0 else gen.pmax,
        )
        for gen in case.generators
    ]
    return replace(case, name=f"{case.name}_relaxed", branches=branches, generators=generators)


def make_congested_case(
    case: PowerCase,
    *,
    branch_id: str | None = None,
    rate_factor: float = 0.8,
    synthetic_rate_a: float = 1.0,
) -> PowerCase:
    target_id = branch_id
    if target_id is None:
        active = [branch for branch in case.branches if branch.status == 1]
        target_id = active[0].id if active else None
    if target_id is None:
        return replace(case, name=f"{case.name}_congested")

    branches: list[Branch] = []
    for branch in case.branches:
        if branch.id == target_id:
            rate = branch.rate_a * rate_factor if branch.rate_a > 0.0 else synthetic_rate_a
            branches.append(replace(branch, rate_a=rate))
        else:
            branches.append(branch)
    return replace(case, name=f"{case.name}_congested_{target_id}", branches=branches)


def make_generator_limited_case(
    case: PowerCase,
    *,
    generator_id: str | None = None,
    pmax_factor: float = 0.5,
) -> PowerCase:
    target_id = generator_id
    if target_id is None:
        active = [gen for gen in case.generators if gen.status == 1]
        target_id = active[0].id if active else None
    if target_id is None:
        return replace(case, name=f"{case.name}_generator_limited")

    generators: list[Generator] = []
    for gen in case.generators:
        if gen.id == target_id:
            if gen.pmax > 10.0 * max(gen.pg0, 1.0):
                new_pmax = max(gen.pmin, pmax_factor * max(gen.pg0, 1e-6))
            else:
                new_pmax = gen.pmin + pmax_factor * max(0.0, gen.pmax - gen.pmin)
            generators.append(replace(gen, pmax=new_pmax))
        else:
            generators.append(gen)
    return replace(case, name=f"{case.name}_generator_limited_{target_id}", generators=generators)


def make_load_scaled_case(case: PowerCase, *, load_factor: float) -> PowerCase:
    loads: list[Load] = [
        replace(load, pd=load.pd * load_factor, qd=load.qd * load_factor)
        for load in case.loads
    ]
    return replace(case, name=f"{case.name}_load_x{load_factor}", loads=loads)


def build_default_dc_opf_validation_cases(case: PowerCase, *, load_factor: float = 1.2):
    return {
        "base": case,
        "relaxed": make_relaxed_case(case),
        "congested": make_congested_case(case),
        "generator_limited": make_generator_limited_case(case),
        f"load_x{load_factor}": make_load_scaled_case(case, load_factor=load_factor),
    }
