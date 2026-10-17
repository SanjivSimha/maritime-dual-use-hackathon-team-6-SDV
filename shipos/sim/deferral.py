"""Strict consumer of Track A's exact D4 schedule identity, with no time rounding."""

import math
from copy import deepcopy

from shipos.contracts import Action


class DeferralError(ValueError):
    """Unsafe DEFER input: reject the whole tick and preserve its canonical evidence."""


def _whole(value, name):
    try:
        valid = (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and value >= 0
            and float(value).is_integer()
            and int(value) == float(value)
        )
    except OverflowError:
        valid = False
    if not valid:
        raise DeferralError(f"DEFER {name} must be finite, exact whole seconds/index")
    return int(value)


def capture_deferral(action: Action, schedule: list[dict], due_s: int) -> dict:
    """Capture the producer's original list index, exact times and full planned identity."""
    required = {"schedule_index", "from_t_s", "to_t_s"}
    if not required <= action.facts.keys():
        raise DeferralError("DEFER requires exact schedule_index/from_t_s/to_t_s metadata")
    index = _whole(action.facts["schedule_index"], "schedule_index")
    source = _whole(action.facts["from_t_s"], "from_t_s")
    destination = _whole(action.facts["to_t_s"], "to_t_s")
    if index >= len(schedule):
        raise DeferralError("DEFER schedule_index is outside the planned schedule")
    entry = schedule[index]
    if entry["t_s"] != source or entry["mode"] != action.to_mode:
        raise DeferralError("DEFER original timestamp/mode does not match schedule_index")
    if source <= due_s:
        raise DeferralError("DEFER target starts by the action execution time")
    if destination <= source:
        raise DeferralError("DEFER to_t_s must be strictly later than from_t_s")
    delay = destination - source
    shifted = []
    for entry in schedule[index:]:
        original = _whole(entry["t_s"], "suffix timestamp")
        if original <= due_s:
            raise DeferralError("DEFER affected suffix starts by the action execution time")
        # Validate representability of every resulting timestamp, including overflow.
        target = _whole(original + delay, "shifted suffix timestamp")
        shifted.append({**entry, "t_s": target})
    return {
        "schedule_index": index,
        "planned_schedule": deepcopy(schedule),
        "shifted_suffix": shifted,
        "delay_s": delay,
    }


def apply_deferral(plan: dict, schedule: list[dict], now: float) -> list[dict]:
    """Return a replacement only if the entire still-future planned identity matches.

    Normal consumption of earlier prefix entries is allowed. Insertions, replacements,
    reordering and edits to still-future prefix/suffix entries require a new plan.
    """
    planned = plan["planned_schedule"]
    index = plan["schedule_index"]
    if planned[index]["t_s"] <= now:
        raise DeferralError("DEFER target already started; replan required")
    expected = [e for e in planned if e["t_s"] > now]
    if schedule != expected:
        raise DeferralError("DEFER planned schedule identity changed; replan required")
    prefix = [e for e in planned[:index] if e["t_s"] > now]
    return deepcopy(prefix + plan["shifted_suffix"])
