"""Feasibility kernel shared by D1–D4: one physics check, four search strategies on top.

`Kernel.evaluate(config, corner)` returns one ConstraintResult per resource (and per rule) for a
configuration of guests in one envelope corner. Demand is the *worst coincident* draw: the
highest-power combination of modes the guests could be in at the same time, where two modes can
coincide only if no exclusion, interlock or ship-state conflict forbids it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from shipos.contracts import (
    ConstraintResult,
    Corner,
    Envelope,
    GuestManifest,
    Placement,
    ResourceClass,
    ShipSpec,
)
from shipos.physics import compute, cooling, links, mass, positions, power, timing
from shipos.statemachine import conflicts

SILENCE = "emission:silent"


@dataclass(frozen=True)
class GuestConfig:
    id: str
    modes: tuple[str, ...]  # modes this guest may be in (throttling removes the expensive ones)
    placement: Placement | None = None


@dataclass(frozen=True)
class Config:
    guests: tuple[GuestConfig, ...]
    interlocks: frozenset[tuple[str, str]] = field(default_factory=frozenset)  # "G2.winch" pairs

    @property
    def ids(self) -> list[str]:
        return [g.id for g in self.guests]

    @property
    def placed(self) -> bool:
        return all(g.placement is not None for g in self.guests)

    def placements(self) -> dict[str, Placement]:
        return {g.id: g.placement for g in self.guests if g.placement is not None}


def corners(ship: ShipSpec, envelope: Envelope) -> list[Corner]:
    """A.10: every speed band × genset count at the hottest seawater (× loop degraded if asked)."""
    loop_states = [False, True] if envelope.include_cooling_degraded else [False]
    return [
        Corner(
            speed_band=b, seawater_c=envelope.seawater_c_max, gensets_online=n, cooling_degraded=d
        )
        for b, n, d in product(envelope.speed_bands, envelope.gensets_online, loop_states)
    ]


def contingency_corner(ship: ShipSpec, envelope: Envelope) -> Corner:
    """Outside A.10 by default: chilled-water loop degraded in the hottest water."""
    return Corner(
        speed_band=envelope.speed_bands[0],
        seawater_c=envelope.seawater_c_max,
        gensets_online=power.full_gensets(ship),
        cooling_degraded=True,
    )


def binding(results: list[ConstraintResult]) -> ConstraintResult | None:
    """Most violated hard constraint (normalised margin); if none is violated, the tightest one."""
    hard = [r for r in results if r.hard]
    violated = [r for r in hard if not r.ok]
    if violated:
        return min(violated, key=lambda r: (r.norm_margin, r.margin, r.resource))
    quantities = [r for r in hard if r.capacity > 0]
    return min(quantities, key=lambda r: (r.norm_margin, r.resource), default=None)


class Kernel:
    def __init__(self, ship: ShipSpec, guests: dict[str, GuestManifest]):
        self.ship = ship
        self.guests = guests
        self._worst_cache: dict = {}

    # ------------------------------------------------------------------ coincidence

    def compatible(self, a: tuple[str, str], b: tuple[str, str], interlocks=frozenset()) -> bool:
        (ga, ma), (gb, mb) = a, b
        if ga == gb:
            return ma == mb
        if (f"{ga}.{ma}", f"{gb}.{mb}") in interlocks or (f"{gb}.{mb}", f"{ga}.{ma}") in interlocks:
            return False
        A, B = self.guests[ga], self.guests[gb]
        for src, smode, other, omode in ((A, ma, gb, mb), (B, mb, ga, ma)):
            for ex in src.excludes:
                if ex.kind != "conditional" or ex.target != other:
                    continue
                if (ex.mode in (None, smode)) and (ex.target_mode in (None, omode)):
                    return False
        mA, mB = A.mode(ma), B.mode(mb)
        return not conflicts(mA.conditions, mB.conditions, mA.deck_machinery, mB.deck_machinery)

    def _contribution(self, gid: str, mode: str, objective: str) -> float:
        kw = power.mode_kw(self.guests[gid], mode)
        return cooling.heat_kw(self.guests[gid], kw) if objective == "heat" else kw

    def worst(
        self, config: Config, objective: str, only: frozenset[str] | None = None
    ) -> tuple[float, dict[str, str]]:
        """Highest coincident sum of `objective` ("power" | "heat") over guests in `only`.

        Every guest is always in some mode, so guests outside `only` still constrain which
        modes can coincide. Branch-and-bound over ≤ 6 guests × ≤ 7 modes: milliseconds.
        """
        key = (tuple((g.id, g.modes) for g in config.guests), config.interlocks, objective, only)
        if key in self._worst_cache:
            return self._worst_cache[key]
        counted = only if only is not None else frozenset(config.ids)

        def value(gid: str, mode: str) -> float:
            return self._contribution(gid, mode, objective) if gid in counted else 0

        order = sorted(config.guests, key=lambda g: (-max(value(g.id, m) for m in g.modes), g.id))
        options = [sorted(g.modes, key=lambda m, gid=g.id: (-value(gid, m), m)) for g in order]
        remaining_max = [0.0] * (len(order) + 1)
        for i in range(len(order) - 1, -1, -1):
            remaining_max[i] = remaining_max[i + 1] + max(value(order[i].id, m) for m in options[i])

        best: list = [-1.0, {}]
        chosen: list[tuple[str, str]] = []

        def dfs(i: int, total: float) -> None:
            if total + remaining_max[i] <= best[0]:
                return
            if i == len(order):
                best[0], best[1] = total, dict(chosen)
                return
            gid = order[i].id
            for m in options[i]:
                if all(self.compatible((gid, m), c, config.interlocks) for c in chosen):
                    chosen.append((gid, m))
                    dfs(i + 1, total + value(gid, m))
                    chosen.pop()

        dfs(0, 0.0)
        result = (max(best[0], 0.0), best[1])
        self._worst_cache[key] = result
        return result

    # ------------------------------------------------------------------ evaluation

    def evaluate(
        self, config: Config, corner: Corner, silence_expected: bool = False
    ) -> list[ConstraintResult]:
        ship, label = self.ship, corner.label
        manifests = [self.guests[g] for g in config.ids]
        results: list[ConstraintResult] = []

        kw, combo = self.worst(config, "power")
        results.append(
            ConstraintResult(
                cls=ResourceClass.POWER,
                resource="power.total",
                capacity=power.usable_kw(ship, corner.speed_band, corner.gensets_online),
                demand=kw,
                unit="kW",
                corner=label,
                contributors={g: power.mode_kw(self.guests[g], m) for g, m in combo.items()},
                facts={f"mode.{g}": m for g, m in combo.items()},
            )
        )
        peaks = [self.guests[g].mode(m).peak_kw for g, m in combo.items()]
        if any(peaks):
            results.append(
                ConstraintResult(
                    cls=ResourceClass.POWER,
                    resource="power.peak",
                    capacity=power.usable_kw(ship, corner.speed_band, corner.gensets_online)
                    + power.peak_extra_kw(ship, corner.gensets_online),
                    demand=kw + max(peaks),
                    unit="kW",
                    corner=label,
                )
            )
        hkw, hcombo = self.worst(config, "heat")
        loop_cap = cooling.loop_capacity_kw(ship, corner.seawater_c, corner.cooling_degraded)
        results.append(
            ConstraintResult(
                cls=ResourceClass.HEAT,
                resource="heat.loop",
                capacity=loop_cap,
                demand=hkw,
                unit="kW",
                corner=label,
                contributors={
                    g: cooling.heat_kw(self.guests[g], power.mode_kw(self.guests[g], m))
                    for g, m in hcombo.items()
                    if cooling.heat_kw(self.guests[g], power.mode_kw(self.guests[g], m))
                },
                facts={
                    "seawater_c": corner.seawater_c,
                    "loop_degraded": str(corner.cooling_degraded),
                },
            )
        )
        results.append(mass.raw_check(ship, manifests, label))
        results += compute.checks(ship, manifests, label)
        results += links.checks(ship, manifests, None, None, worst=True, corner=label)
        for g in manifests:
            sc = timing.safety_command_check(ship, g)
            if sc is not None:
                results.append(sc.model_copy(update={"corner": label}))
        results += self._relations(config, silence_expected, label)
        if config.placed and config.guests:
            results += self._placement(config, corner)
        return results

    def _relations(self, config: Config, silence_expected: bool, label: str):
        present = set(config.ids)
        out = []
        for gid in config.ids:
            g = self.guests[gid]
            for ex in g.excludes:
                if ex.kind != "absolute":
                    continue
                hit = (ex.target == SILENCE and silence_expected) or ex.target in present
                if hit:
                    out.append(
                        ConstraintResult(
                            cls=ResourceClass.DATA if ex.target == SILENCE else ResourceClass.DECK,
                            resource=f"relation.excludes.{gid}",
                            capacity=0,
                            demand=1,
                            unit="rule",
                            corner=label,
                            contributors={gid: 1},
                            facts={"target": ex.target, "reason": ex.reason},
                        )
                    )
            for req in g.requires:
                if not req.target.startswith("slot:") and req.target not in present:
                    out.append(
                        ConstraintResult(
                            cls=ResourceClass.DECK,
                            resource=f"relation.requires.{gid}",
                            capacity=0,
                            demand=1,
                            unit="rule",
                            corner=label,
                            contributors={gid: 1},
                            facts={"target": req.target, "reason": req.reason},
                        )
                    )
        return out

    def _placement(self, config: Config, corner: Corner) -> list[ConstraintResult]:
        ship, label = self.ship, corner.label
        placements = config.placements()
        out = [
            r.model_copy(update={"corner": label})
            for r in positions.check(ship, self.guests, placements)
        ]
        out.append(mass.stability_check(ship, self.guests, placements, label))
        lines = power.surviving_lines(ship, corner.gensets_online)
        for line in lines:
            # after a trip the bus-tie puts every guest on the surviving line
            on_line = frozenset(
                g for g, p in placements.items() if p.line == line or len(lines) == 1
            )
            if not on_line:
                continue
            kw, combo = self.worst(config, "power", only=on_line)
            out.append(
                ConstraintResult(
                    cls=ResourceClass.POWER,
                    resource=f"power.line.{line.value}",
                    capacity=power.line_capacity_kw(ship, line, corner.gensets_online),
                    demand=kw,
                    unit="kW",
                    corner=label,
                    contributors={
                        g: power.mode_kw(self.guests[g], m)
                        for g, m in combo.items()
                        if g in on_line
                    },
                )
            )
        return out
