"""
Final opportunity score calculation.

Combines inclusion, reprint, and scarcity into a single ranking metric.
"""


def opportunity_score(
    p_included: float,
    p_reprinted: float,
    scarcity_score: float,
    demand_score: float,
) -> float:
    """Calculate final opportunity score (0-100)."""
    omission = p_included * (1 - p_reprinted)
    return omission * scarcity_score * demand_score * 100
