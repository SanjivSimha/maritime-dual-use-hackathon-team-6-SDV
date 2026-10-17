"""One plain sentence per decision, generated from the constraint that actually bound it.

Nothing here is a canned explanation: every clause is assembled from a ConstraintResult's
resource, capacity, demand, unit, corner, contributors and facts, or from the chosen actions and
the stated costs. Change a number in a card and the sentence changes with it.
"""

from __future__ import annotations

from shipos.contracts import (
    Action,
    ActionKind,
    ConstraintResult,
    Decision,
    DecisionKind,
    Verdict,
)


def fmt(x: float) -> str:
    """Two decimals below 100, one above; trailing zeros dropped."""
    digits = 1 if abs(x) >= 100 else 2  # literal-ok: display precision
    s = f"{x:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def _who(contributors: dict[str, float], unit: str) -> str:
    top = sorted(contributors.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{g} {fmt(v)}" for g, v in top) + f" {unit}" if top else ""


def _corner(b: ConstraintResult) -> str:
    """ "slow, 30 °C, one genset down" → "at slow speed in 30 °C water with one genset down, "."""
    if not b.corner:
        return ""
    parts = b.corner.split(", ")
    if len(parts) < 3:  # literal-ok: band, water, gensets
        return f"in {b.corner}, "
    band, water, gensets, *rest = parts
    loop = " and the loop degraded" if rest else ""
    return f"at {band} speed in {water} water with {gensets}{loop}, "


def _paren(*items: str) -> str:
    kept = [i for i in items if i]
    return f" ({'; '.join(kept)})" if kept else ""


def describe(b: ConstraintResult) -> str:
    """The binding constraint as a clause a captain would accept."""
    r, cap, dem, u = b.resource, fmt(b.capacity), fmt(b.demand), b.unit
    over, spare = fmt(b.demand - b.capacity), fmt(b.margin)
    head, _, tail = r.partition(".")
    f = b.facts

    if r == "power.total":
        if b.ok:
            return f"{_corner(b)}the {cap} kW reserve keeps {spare} kW free at {dem} kW worst case"
        who = _paren(f"{over} kW short", _who(b.contributors, "kW"))
        return f"{_corner(b)}guests need {dem} kW against {cap} kW usable{who}"
    if r.startswith("power.line."):
        return f"the {tail.split('.')[-1]} mission line would carry {dem} kW of its {cap} kW"
    if r == "power.peak":
        return f"{_corner(b)}peaks reach {dem} kW against {cap} kW for the peak window"
    if r == "heat.loop":
        state = ", degraded," if str(f.get("loop_degraded")) == "True" else ""
        water = f"at {fmt(float(f['seawater_c']))} °C" if "seawater_c" in f else ""
        if b.ok:
            return f"the cooling loop {water}{state} has {spare} kW spare of {cap} kW"
        who = _paren(f"{over} kW over", _who(b.contributors, "kW"))
        return f"the cooling loop {water}{state} rejects {cap} kW but guests put in {dem} kW{who}"
    if r.startswith("heat.mast_air"):
        return f"mast positions are air-cooled to {cap} kW and the guest draws {dem} kW"
    if r.startswith("heat.manifold."):
        return f"{tail.split('.')[-1]} needs a chilled-water manifold and {f.get('slot')} has none"
    if r == "mass.margin":
        if b.ok:
            return f"mass reaches {dem} t of the {cap} t margin ({spare} t spare)"
        return f"mass would reach {dem} t against the {cap} t margin ({over} t over)"
    if r == "mass.stability":
        if b.ok:
            return f"stability reaches {dem} t-eq of {cap} t-eq ({spare} t-eq spare)"
        return f"stability would reach {dem} t-eq against {cap} t-eq ({over} t-eq over)"
    if r.startswith(("deck.allowed.", "mast.allowed.")):
        who = r.removeprefix("deck.allowed.").removeprefix("mast.allowed.")
        return f"{who} cannot be mounted on {f.get('slot')} ({f.get('kind')})"
    if r.startswith("deck.unplaced."):
        return f"{r.removeprefix('deck.unplaced.').replace('.', ' ')} has no position left"
    if r.startswith(("deck.", "mast.")) and r.endswith((".mass", ".area")):
        slot = r.split(".")[1]
        return f"{slot} takes {cap} {u} and would carry {dem} {u}{_paren(_who(b.contributors, u))}"
    if r.startswith("deck.hangar_roof.clearance"):
        return f"the hangar-roof antenna is {dem} m tall against {cap} m drone clearance"
    if head in ("deck", "mast"):
        if "occupied_by" in f:
            return f"{tail} is taken by the {f['occupied_by']}"
        return f"{tail} has room for {cap} {u} but {_who(b.contributors, u)} need {dem}"
    if r.startswith("relation.excludes."):
        return f"{tail.split('.')[-1]} excludes {f.get('target')}: {f.get('reason')}"
    if r.startswith("relation.requires."):
        return f"{tail.split('.')[-1]} requires {f.get('target')}: {f.get('reason')}"
    if r == "time.landing_deadline":
        return (
            f"{f.get('guest')} must land within {cap} min and waiting for "
            f"{f.get('waiting_for')} takes {dem} min"
        )
    if r == "time.ready":
        return f"readying the ship for {f.get('guest')} takes {dem} min of the {cap} min it has"
    if r.startswith("time.safety_command."):
        return (
            f"a safety command over the {f.get('link')} link takes {dem} s against {cap} s"
            f" (fallback: {f.get('fallback')})"
        )
    if head == "time":
        return f"{tail} needs {dem} s against a {cap} s ceiling"
    if head == "data":
        return f"{tail} offers {cap} Mbit/s against {dem} Mbit/s ({f.get('basis', 'current')})"
    if head == "compute":
        return f"the rack has {cap} {u} of {tail} for guests and {dem} are requested"
    if r.startswith("install."):
        return _install(b)
    return f"{r} needs {dem} {u} of {cap} {u}"


def _install(b: ConstraintResult) -> str:
    f = b.facts
    if b.resource == "install.port_only":
        return (
            f"{f.get('guest')} installs only in port ({fmt(float(f['people']))} people, "
            f"{fmt(float(f['hours']))} h)"
        )
    if b.resource == "install.crew":
        need, have = fmt(b.demand), fmt(b.capacity)
        return f"the installation needs {need} people on deck and {have} are aboard"
    if b.resource == "install.window":
        return f"no installation window: {f.get('reason')}"
    return f"{b.resource}: {f}"


def _when(a: Action) -> str:
    return " with one genset down" if a.when == "degraded" else ""


def action_phrase(a: Action) -> str:
    g, k, kw = a.guest, a.kind, f" (−{fmt(a.kw_recovered)} kW)" if a.kw_recovered else ""
    if k == ActionKind.THROTTLE:
        return f"{g} drops to {a.to_mode}{_when(a)}{kw}"
    if k == ActionKind.STANDBY:
        return f"{g} goes to standby{_when(a)}{kw}"
    if k == ActionKind.SHED:
        return f"{g} is switched off{_when(a)}{kw}"
    if k == ActionKind.REMOVE:
        return f"{g} is displaced"
    if k == ActionKind.MOVE:
        return f"{g} moves to {a.facts.get('slot')}"
    if k == ActionKind.INTERLOCK:
        return f"{g}'s {a.to_mode} waits while {(a.other or '').replace('.', ' ')}{_when(a)}"
    if k == ActionKind.DEFER:
        return f"{g} {a.to_mode} is deferred {fmt(a.delay_min)} min{kw}"
    if k == ActionKind.PAUSE:
        return f"{g} holds ({a.to_mode}) for {fmt(float(a.facts.get('window_min', 0)))} min"
    if k == ActionKind.SUSPEND:
        return f"{g} suspends its {a.facts.get('activity', 'activity')}"
    if k == ActionKind.PROCEED:
        return f"{g} proceeds to {a.to_mode}"
    if k == ActionKind.DENY:
        return f"{g} is denied ({a.facts.get('outcome', 'refused')})"
    if k == ActionKind.RESERVE:
        return f"{fmt(a.kw_recovered)} kW is reserved for {g}"
    if k == ActionKind.RETASK:
        return f"{g} is re-tasked: {a.facts.get('detail')}"
    return f"{g} keeps its {a.to_mode or 'mode'}"


def _actions(actions: list[Action], sep: str = " and ") -> str:
    return sep.join(action_phrase(a) for a in actions if a.kind != ActionKind.KEEP)


def _costs(d: Decision, exclude_card: bool = True) -> str:
    items = [c for c in d.costs if not (exclude_card and c.guest == "card")]
    if not items:
        return ""
    return "; cost: " + ", ".join(
        f"{c.guest} loses {fmt(c.amount)} {c.unit} ({c.what})" for c in items
    )


def _gain(d: Decision) -> float | None:
    return next((c.amount for c in d.costs if c.guest == "card"), None)


def render(d: Decision) -> str:
    b = d.binding
    clause = describe(b) if b is not None else "no constraint is close to its limit"
    s = d.subject

    if d.kind == DecisionKind.ADMISSION:
        if d.verdict == Verdict.YES:
            tail = "; holds with one genset down" if d.holds_one_genset_down else ""
            return f"Admit {s}: every envelope corner holds; tightest, {clause}{tail}."
        if d.verdict == Verdict.YES_IF:
            fix = _actions(d.chosen.actions) if d.chosen else "conditions are met"
            gain = _gain(d)
            price = ""
            if d.chosen is not None and gain is not None:
                total = d.chosen.cost + d.chosen.cost_contingent
                price = f"; this costs {fmt(total)} capability against {fmt(gain)} returned"
            return f"Admit {s} if {fix}: {clause}{price}{_costs(d)}."
        if d.verdict == Verdict.NO:
            why = ""
            if d.chosen is not None and d.chosen.actions:
                why = (
                    f"; the cheapest fix ({_actions(d.chosen.actions)}) costs "
                    f"{fmt(d.chosen.cost)} capability"
                )
                gain = _gain(d)
                if gain is not None:
                    why += f", more than the {fmt(gain)} {s} returns"
            return f"Refuse {s}: {clause}{why}."
        when = f"; next window: {d.next_window}" if d.next_window else ""
        alt = f"; instead {_actions(d.chosen.actions)}" if d.chosen and d.chosen.actions else ""
        return f"Not now: {clause}{when}{alt}."

    if d.kind == DecisionKind.PLACEMENT:
        p = d.placement.get(s)
        where = (
            ", ".join(f"{k} on {v}" for k, v in p.slots.items()) + f", {p.line.value} line"
            if p
            else "nowhere"
        )
        n = len(d.alternatives)
        return f"Place {s}: {where}; tightest, {clause}; {n} alternatives rejected."

    if d.kind == DecisionKind.ARBITRATION:
        plan = _actions(d.chosen.actions, sep=", ") if d.chosen else ""
        return f"{d.winner} goes first: {clause}; {plan}{_costs(d)}."

    event = s.replace("_", " ").capitalize()
    if d.verdict == Verdict.NO_ACTION:
        return f"{event}: no shedding needed; {clause}{_costs(d)}."
    steps = _actions(d.chosen.actions, sep="; then ") if d.chosen else ""
    return f"{event}: {clause}; shed in order: {steps}{_costs(d)}."
