"""
Parse announced product facts from Wizards / TCGPlayer product descriptions.
"""

import re
from typing import Optional

NEW_CARD_PATTERNS = [
    r"(\d+)\s+new-to-Magic cards",
    r"introduces\s+(\d+)\s+never-before-seen",
    r"(\d+)\s+Magic cards make their debut",
    r"(\d+)\s+brand-new cards",
    r"(\d+)\s+new cards",
    r"(\d+)\s+new-to-the-game cards",
    r"including\s+(\d+)\s+new cards",
]


def parse_announced_new_cards(text: str) -> Optional[int]:
    """Extract the announced count of new cards from product description text."""
    if not text:
        return None

    matches = []
    for pattern in NEW_CARD_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if 0 < value <= 60:
                matches.append(value)

    if not matches:
        return None

    # Prefer the smallest plausible deck-level count when set + deck copy both match.
    return min(matches)
