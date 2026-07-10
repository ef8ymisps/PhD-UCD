"""Stored OPF solution containers for warm starts and benchmarks.

The OPF models should not know where a previous solution came from. This module
keeps solved dispatch/state values in a small source-agnostic format that AC OPF,
DC OPF, and later SCOPF can reuse as initialization data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StoredOPFSolution:
    """Solved OPF state that can be reused as a model warm start."""

    case_name: str
    model_type: str
    source: str
    status: str
    objective: float | None
    pg: dict[str, float]
    qg: dict[str, float] | None = None
    vm: dict[str, float] | None = None
    va: dict[str, float] | None = None
    theta: dict[str, float] | None = None
    branch_flows: dict[str, Any] | None = None


def stored_ac_solution_from_result(case_name: str, result, *, source: str = "own_ac_opf") -> StoredOPFSolution:
    """Convert an AC OPF result object into a reusable stored solution."""
    return StoredOPFSolution(
        case_name=case_name,
        model_type="ac",
        source=source,
        status=str(result.status),
        objective=float(result.objective),
        pg={str(key): float(value) for key, value in result.pg.items()},
        qg={str(key): float(value) for key, value in result.qg.items()},
        vm={str(key): float(value) for key, value in result.vm.items()},
        va={str(key): float(value) for key, value in result.va.items()},
        branch_flows=result.branch_flows,
    )


def stored_dc_solution_from_result(case_name: str, result, *, source: str = "own_dc_opf") -> StoredOPFSolution:
    """Convert a DC OPF result object into a reusable stored solution."""
    return StoredOPFSolution(
        case_name=case_name,
        model_type="dc",
        source=source,
        status=str(result.status),
        objective=float(result.objective),
        pg={str(key): float(value) for key, value in result.pg.items()},
        theta={str(key): float(value) for key, value in result.theta.items()},
        branch_flows=result.branch_flows,
    )


def save_stored_opf_solution(solution: StoredOPFSolution, path: str | Path) -> None:
    """Write a stored OPF solution as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(asdict(solution), handle, indent=2, sort_keys=True)


def load_stored_opf_solution(path: str | Path) -> StoredOPFSolution:
    """Read a stored OPF solution from JSON."""
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return StoredOPFSolution(**data)
