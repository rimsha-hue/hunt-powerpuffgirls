"""Writing rules checked by code (spec section 8): banned phrases and dashes.

Generated text that breaks a rule gets one rewrite; if it still breaks it, the user is told
(or, for a CV bullet, the user's original wording is kept).
"""

import re

BANNED_PHRASES = (
    "i am writing to express",
    "passionate",
    "leverage",
    "synergy",
    "delve",
    "thrilled",
    "dynamic",
    "fast-paced",
    "esteemed",
    "i believe i would be a great fit",
)
_BANNED_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(p) for p in BANNED_PHRASES) + r")\w*", re.I
)
_DASHES = ("—", "–")  # em dash, en dash


def style_problems(text: str) -> list[str]:
    """Human-readable problems, empty when the text follows the rules."""
    problems = [f'uses "{m.group(0)}"' for m in _BANNED_RE.finditer(text)]
    if any(dash in text for dash in _DASHES):
        problems.append("uses an em or en dash")
    return problems
