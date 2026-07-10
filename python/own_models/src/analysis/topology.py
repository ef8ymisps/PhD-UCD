from __future__ import annotations

from dataclasses import dataclass

from own_models.src.data.case import PowerCase


@dataclass(frozen=True)
class IslandDiagnostics:
    """Summary of connected components and islanded load/generation."""
    islanded: bool
    n_islands: int
    n_energized_islands: int
    n_load_only_islands: int
    n_generation_only_islands: int
    n_balanced_islands: int
    islanded_load_p: float
    islanded_generation_p: float
    components: tuple[tuple[str, ...], ...]


def _active_load_p_by_bus(case: PowerCase) -> dict[str, float]:
    """Aggregate active load by bus for energized-island diagnostics."""
    values = {bus.id: 0.0 for bus in case.buses}
    for load in case.loads:
        if load.status == 1:
            values[load.bus] += load.pd
    return values


def _active_generation_p_by_bus(case: PowerCase) -> dict[str, float]:
    """Aggregate online active generation by bus for island diagnostics."""
    values = {bus.id: 0.0 for bus in case.buses}
    for gen in case.generators:
        if gen.status == 1:
            values[gen.bus] += gen.pg0
    return values


def connected_components(case: PowerCase) -> list[tuple[str, ...]]:
    """Find connected bus components using active branches only."""
    adjacency = {bus.id: set() for bus in case.buses}
    for branch in case.branches:
        if branch.status != 1:
            continue
        adjacency[branch.from_bus].add(branch.to_bus)
        adjacency[branch.to_bus].add(branch.from_bus)

    seen = set()
    components = []
    for bus in case.buses:
        if bus.id in seen:
            continue
        stack = [bus.id]
        component = []
        seen.add(bus.id)
        while stack:
            bus_id = stack.pop()
            component.append(bus_id)
            for neighbor in adjacency[bus_id]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(tuple(sorted(component, key=lambda item: case.bus_id_to_index[item])))
    return components


def island_diagnostics(case: PowerCase) -> IslandDiagnostics:
    """Classify connected components relative to the slack component."""
    components = connected_components(case)
    load_p = _active_load_p_by_bus(case)
    gen_p = _active_generation_p_by_bus(case)
    slack_component = next(
        component for component in components if case.slack_bus_id in component
    )

    n_energized = 0
    n_load_only = 0
    n_generation_only = 0
    n_balanced = 0
    islanded_load_p = 0.0
    islanded_generation_p = 0.0

    for component in components:
        component_load = sum(load_p[bus_id] for bus_id in component)
        component_gen = sum(gen_p[bus_id] for bus_id in component)
        energized = component_load > 1e-12 or component_gen > 1e-12
        if energized:
            n_energized += 1
        if component != slack_component:
            islanded_load_p += component_load
            islanded_generation_p += component_gen
        if component_load > 1e-12 and component_gen <= 1e-12:
            n_load_only += 1
        elif component_load <= 1e-12 and component_gen > 1e-12:
            n_generation_only += 1
        elif component_load > 1e-12 and component_gen > 1e-12:
            n_balanced += 1

    return IslandDiagnostics(
        islanded=n_energized > 1,
        n_islands=len(components),
        n_energized_islands=n_energized,
        n_load_only_islands=n_load_only,
        n_generation_only_islands=n_generation_only,
        n_balanced_islands=n_balanced,
        islanded_load_p=float(islanded_load_p),
        islanded_generation_p=float(islanded_generation_p),
        components=tuple(components),
    )
