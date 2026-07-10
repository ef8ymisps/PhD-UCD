"""Compatibility shim for the repo-root PyPowSyBl replacement sweep script.

This file exists because some notebooks add ``python/scripts`` to ``sys.path``
before the repo-root ``scripts`` directory.  Keep imports working by loading the
canonical implementation and re-exporting its public names.
"""

from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path


ROOT_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pypowsybl_replacement_experiment_matrix.py"


def _load_root_module():
    spec = importlib.util.spec_from_file_location("_root_pypowsybl_replacement_experiment_matrix", ROOT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load canonical script at {ROOT_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_ROOT_MODULE = _load_root_module()

for _name, _value in vars(_ROOT_MODULE).items():
    if _name.startswith("__") and _name not in {"__doc__", "__all__"}:
        continue
    globals()[_name] = _value

__all__ = [
    name
    for name in globals()
    if not name.startswith("_") or name in {"_case_paths", "_balance", "_feasibility", "_is_converged_status"}
]


if __name__ == "__main__":
    runpy.run_path(str(ROOT_SCRIPT), run_name="__main__")
