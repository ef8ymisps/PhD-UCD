from __future__ import annotations

from dataclasses import dataclass, replace

from own_models.src.data.case import Branch, Generator, PowerCase


@dataclass(frozen=True)
class ContingencyElement:
    """One piece of equipment removed by a contingency."""
    element_type: str
    element_id: str


@dataclass(frozen=True)
class Contingency:
    """A branch or generator outage definition used by N-1 screening."""
    id: str
    k: int
    elements: tuple[ContingencyElement, ...]

    @property
    def outaged_branch_ids(self) -> tuple[str, ...]:
        """Return branch ids removed by this contingency."""
        return tuple(element.element_id for element in self.elements if element.element_type == "branch")

    @property
    def outaged_generator_ids(self) -> tuple[str, ...]:
        """Return generator ids removed by this contingency."""
        return tuple(element.element_id for element in self.elements if element.element_type == "generator")


def generate_branch_contingencies(case: PowerCase, *, k: int = 1) -> list[Contingency]:
    """Create one branch-outage contingency for every active branch."""
    if k != 1:
        raise NotImplementedError("Only N-1 active branch contingencies are implemented in v1.")

    contingencies = []
    for branch in case.branches:
        if branch.status != 1:
            continue
        element = ContingencyElement(element_type="branch", element_id=branch.id)
        contingencies.append(
            Contingency(
                id=f"branch_{branch.id}",
                k=1,
                elements=(element,),
            )
        )
    return contingencies


def generate_contingencies(
    case: PowerCase,
    *,
    k: int = 1,
    include_branches: bool = True,
    include_generators: bool = False,
) -> list[Contingency]:
    """Create branch and/or generator N-1 contingencies for active equipment."""
    if k != 1:
        raise NotImplementedError("Only N-1 contingencies are implemented in v1.")
    contingencies = []
    if include_branches:
        contingencies.extend(generate_branch_contingencies(case, k=k))
    if include_generators:
        contingencies.extend(generate_generator_contingencies(case, k=k))
    return contingencies


def generate_generator_contingencies(case: PowerCase, *, k: int = 1) -> list[Contingency]:
    """Create one generator-outage contingency for every active generator."""
    if k != 1:
        raise NotImplementedError("Only N-1 active generator contingencies are implemented in v1.")

    contingencies = []
    for gen in case.generators:
        if gen.status != 1:
            continue
        element = ContingencyElement(element_type="generator", element_id=gen.id)
        contingencies.append(
            Contingency(
                id=f"generator_{gen.id}",
                k=1,
                elements=(element,),
            )
        )
    return contingencies


def apply_contingency(case: PowerCase, contingency: Contingency) -> PowerCase:
    """Return a copied case with the contingency equipment marked out of service."""
    outaged_branches = set(contingency.outaged_branch_ids)
    outaged_generators = set(contingency.outaged_generator_ids)
    unsupported = [
        element for element in contingency.elements if element.element_type not in {"branch", "generator"}
    ]
    if unsupported:
        raise NotImplementedError("Only branch and generator outage application is implemented in v1.")

    branches: list[Branch] = [
        replace(branch, status=0) if branch.id in outaged_branches else branch
        for branch in case.branches
    ]
    generators: list[Generator] = [
        replace(gen, status=0, pg0=0.0, qg0=0.0) if gen.id in outaged_generators else gen
        for gen in case.generators
    ]
    return replace(case, name=f"{case.name}_{contingency.id}_outage", branches=branches, generators=generators)


def branch_outage_contingency(branch_id: str) -> Contingency:
    """Create a single-branch outage contingency definition."""
    return Contingency(
        id=f"branch_{branch_id}",
        k=1,
        elements=(ContingencyElement(element_type="branch", element_id=branch_id),),
    )


def generator_outage_contingency(gen_id: str) -> Contingency:
    """Create a single-generator outage contingency definition."""
    return Contingency(
        id=f"generator_{gen_id}",
        k=1,
        elements=(ContingencyElement(element_type="generator", element_id=gen_id),),
    )


def apply_branch_outage(case: PowerCase, branch_id: str) -> PowerCase:
    """Return a copied case with one branch out of service."""
    return apply_contingency(case, branch_outage_contingency(branch_id))


def apply_generator_outage(case: PowerCase, gen_id: str) -> PowerCase:
    """Return a copied case with one generator out of service."""
    return apply_contingency(case, generator_outage_contingency(gen_id))
