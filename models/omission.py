"""
Model 3: P(Omitted Despite Synergy) — Derived, Not Trained

Core of the spec thesis: high synergy card + not reprinted = players must buy it.
"""


def omission_probability(p_included: float, p_reprinted: float) -> float:
    """
    Calculate omission probability.
    
    High synergy card (p_included) + not reprinted (1 - p_reprinted) = spike target.
    """
    return p_included * (1 - p_reprinted)
