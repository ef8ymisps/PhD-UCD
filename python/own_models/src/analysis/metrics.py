from __future__ import annotations

import pandas as pd

from own_models.src.data.case import PowerCase
from own_models.src.network.topology import network_topology


def case_summary(case: PowerCase) -> pd.DataFrame:
    topology = network_topology(case)
    return pd.DataFrame(
        [
            {
                "case_name": case.name,
                "base_mva": case.base_mva,
                "n_buses": len(case.buses),
                "n_branches": len(case.branches),
                "n_active_branches": topology["n_active_branches"],
                "n_generators": len(case.generators),
                "n_active_generators": sum(1 for gen in case.generators if gen.status == 1),
                "n_loads": len(case.loads),
                "n_active_loads": sum(1 for load in case.loads if load.status == 1),
                "n_shunts": len(case.shunts),
                "slack_bus_id": case.slack_bus_id,
                "total_pd_pu": sum(load.pd for load in case.loads if load.status == 1),
                "total_qd_pu": sum(load.qd for load in case.loads if load.status == 1),
                "total_pg0_pu": sum(gen.pg0 for gen in case.generators if gen.status == 1),
                "n_components": topology["n_components"],
            }
        ]
    )


def solution_metrics(result) -> pd.DataFrame:
    data = {
        "converged": getattr(result, "converged", None),
        "mismatch_norm": getattr(result, "mismatch_norm", None),
        "iterations": getattr(result, "iterations", None),
    }
    return pd.DataFrame([data])
