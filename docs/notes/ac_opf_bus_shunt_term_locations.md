# AC OPF Bus Shunt Term Locations

This note pinpoints where bus shunt conductance/susceptance enters each AC OPF
formulation. It is meant to answer the validation question: are bus-level shunt
terms missing from the power-balance constraints?

## Julia Current Bus-Injection Model

The current Julia AC OPF uses a Ybus bus-injection balance.

- `julia/own_models/src/network/admittance_matrix.jl`
- `build_ybus(case)`

Bus shunts are added directly to the Ybus diagonal:

```julia
accumulate!(i, i, shunt.gs + im * shunt.bs)
```

The power balance then uses `data.ybus_row_terms[bus_id]` in:

- `julia/own_models/src/opf/ac/constraints.jl`
- `add_ac_power_balance!`

Therefore the shunt terms are present implicitly through the diagonal Ybus
entry. The active diagonal contribution contains `Gs * Vm^2`; the reactive
diagonal contribution contains `-Bs * Vm^2`.

Parallel branches are also accounted for correctly in this formulation.
Multiple physical branches between the same two buses are accumulated into the
same off-diagonal Ybus entry for the nodal injection calculation. This is the
correct network equivalent for bus power balance.

This does not remove the individual physical branch constraints. Thermal,
angle, and branch-flow quantities are still built per physical `branch_id` in:

- `julia/own_models/src/opf/ac/constraints.jl`
- `add_ac_branch_constraints!`

So the current Julia model uses the collapsed admittance only where nodal
balance requires it, while preserving individual parallel lines for branch
limits.

## Julia PM-Like Branch-Flow Model

The PM-like Julia model uses directed branch-flow variables and does not write
the balance as a full Ybus injection.

- `julia/own_models/src/opf/ac/powermodels_like.jl`
- `add_ac_powermodels_like_power_balance!`

Bus shunts are included explicitly:

```julia
p balance: pgen - pd - gs_by_bus[bus_id] * vm[bus_id]^2
q balance: qgen - qd + bs_by_bus[bus_id] * vm[bus_id]^2
```

## Python Current Bus-Injection Model

The current Python AC OPF follows the same Ybus bus-injection structure.

- `python/own_models/src/network/admittance.py`
- `build_admittance_matrix(case)`

Bus shunts are added to the Ybus diagonal:

```python
add(i, i, complex(shunt.gs, shunt.bs))
```

The power balance uses `data.ybus_terms_by_bus[bus_id]` in:

- `python/own_models/src/opf/ac/constraints.py`
- `add_ac_power_balance_constraints`

Therefore bus shunts are included implicitly through Ybus.

## Python PM-Like Branch-Flow Model

The Python PM-like model uses directed branch-flow variables and explicit shunt
terms in the bus balance.

- `python/own_models/src/opf/ac/powermodels_like.py`
- `_add_pm_like_power_balance_constraints`

Bus shunts are included explicitly:

```python
p balance: generation - demand - gs_by_bus[bus_id] * vm[bus_id] ** 2
q balance: generation - demand + bs_by_bus[bus_id] * vm[bus_id] ** 2
```

## Validation Conclusion

The bus shunt terms are not omitted. They are represented in two equivalent
ways:

- Bus-injection models: implicitly through the diagonal of Ybus.
- PM-like branch-flow models: explicitly in the bus power balance.

Adding explicit shunt terms to the current bus-injection balances without
removing them from Ybus would double-count bus shunts.

The parallel-line audit table is:

```text
results/own_vs_powermodels/_organized_for_meeting/05_shunt_and_formulation/julia_parallel_branch_ybus_audit.csv
```

For example, in the `1888_rte` base case the audit finds 2,531 physical branch
rows, 2,308 unique undirected bus pairs, 197 bus-pair groups with parallel
branches, and 2,531 thermal constraint instances. This confirms that parallel
lines are aggregated in Ybus for nodal balance but retained individually for
branch constraints.
