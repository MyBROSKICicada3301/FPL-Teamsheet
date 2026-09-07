"""Probability bands.

BACKEND.md §7: `band` is computed server-side so the colour ramp thresholds
live in one place, not in both codebases. This module is that one place. The
frontend reads the band name off the API response and never re-derives it.
"""

# Upper bound (exclusive) -> band name. Ordered.
BANDS = (
    (0.20, "cold"),   # noise
    (0.50, "warm"),   # live but unresolved
    (0.85, "hot"),    # expect movement
    (1.01, "done"),   # effectively agreed
)

#: Below this, the product says "Not enough signal yet" rather than a number.
MIN_DISPLAY_P = 0.05


def band_for(p_exit: float) -> str:
    """Return the band name for a probability in [0, 1]."""
    for upper, name in BANDS:
        if p_exit < upper:
            return name
    return "done"


def round_p(p_exit: float) -> float:
    """Round to two decimals.

    BACKEND.md §7: a model trained on a few thousand transfers cannot support
    0.6783, and showing it invites a precision the model does not have.
    """
    return round(float(p_exit), 2)
