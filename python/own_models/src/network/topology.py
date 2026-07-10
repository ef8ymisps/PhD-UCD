from __future__ import annotations

from collections import defaultdict, deque

from own_models.src.data.case import PowerCase


def network_topology(case: PowerCase) -> dict[str, object]:
    """Return active-bus adjacency and connected components for the network."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    for bus in case.buses:
        adjacency[bus.id]
    for branch in case.branches:
        if branch.status == 1:
            adjacency[branch.from_bus].add(branch.to_bus)
            adjacency[branch.to_bus].add(branch.from_bus)

    visited: set[str] = set()
    components: list[list[str]] = []
    for bus_id in adjacency:
        if bus_id in visited:
            continue
        queue = deque([bus_id])
        visited.add(bus_id)
        component: list[str] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))

    return {
        "n_buses": len(case.buses),
        "n_branches": len(case.branches),
        "n_active_branches": sum(1 for branch in case.branches if branch.status == 1),
        "n_components": len(components),
        "components": components,
    }
