# Convert a physical value to per-unit by dividing by its base.
function to_per_unit(value, base)
    return value / base
end

# Convert a per-unit value back to the physical unit defined by base.
function from_per_unit(value_pu, base)
    return value_pu * base
end
