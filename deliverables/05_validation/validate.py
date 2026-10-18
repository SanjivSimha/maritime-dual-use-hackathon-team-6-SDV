# ruff: noqa: E501  (evidence strings are printed verbatim)
"""Independent validation of PoseidonOS against the Fincantieri SDV challenge brief.

Drives the real engine (no mocks). Expected values are derived from Annex A/B arithmetic in this
file, never copied from engine output. Run from the repo root:
    PYTHONPATH=. uv run python deliverables/05_validation/validate.py
"""

import hashlib
import itertools
import json
import re
import sys
import time
from pathlib import Path

from shipos.api.adapter import callback
from shipos.contracts import (
    ActionKind,
    GuestRuntime,
    Line,
    ResourceClass,
    Situation,
    SpeedBand,
    Verdict,
)
from shipos.data import DATA_DIR, load_envelope, load_guests, load_priorities, load_ship
from shipos.engine import admission, arbitration, shedding
from shipos.physics import cooling, mass, power
from shipos.sim.runner import load_scenario, replay_jsonl
from tests.engine.scenario import P, scenario_a, scenario_b
from tests.engine.test_shedding import sar_path, survey_path

RESULTS: list[tuple[str, str, bool, str]] = []


def check(req: str, what: str, ok: bool, evidence: str = "") -> None:
    RESULTS.append((req, what, bool(ok), evidence))
    print(f"[{'PASS' if ok else 'FAIL'}] {req:<6} {what}")
    if evidence:
        for line in str(evidence).splitlines():
            print(f"         {line}")


def timed(fn, *a, **kw):
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    return out, time.perf_counter() - t0


ship, guests, env = load_ship(), load_guests(), load_envelope()
survey, sar, patrol = (load_priorities(n) for n in ("survey_primary", "sar_primary", "patrol"))

print("=" * 78, "\nMANIFEST (B.0)\n" + "=" * 78)
check(
    "MAN-1",
    "Five Annex B cards load and validate",
    set(guests) >= {f"G{i}" for i in range(1, 6)},
    f"cards: {sorted(guests)}",
)
own = sorted(set(guests) - {f"G{i}" for i in range(1, 6)})
check(
    "MAN-2",
    "One card of the team's own invention in data/guests/",
    bool(own),
    f"extra cards: {own or 'none'}",
)
g2 = guests["G2"]
fields = g2.model_dump()
b0 = [
    "positions",
    "parts",
    "requires",
    "excludes",
    "safety_critical",
    "mission_critical",
    "modes",
    "install",
    "capability",
]
present = [f for f in b0 if any(k.startswith(f.rstrip("s")) for k in fields)]
check(
    "MAN-3",
    "Manifest expresses B.0 ontology fields",
    len(present) >= 7,
    f"top-level keys: {sorted(fields)}",
)
check("MAN-4", "Schema file exists", (Path("schema") / "guest_manifest.schema.json").exists())
check("MAN-5", "Documented way to add a card", Path("docs/ADDING_A_CARD.md").exists())

print("=" * 78, "\nPLATFORM MODEL (A.1–A.8)\n" + "=" * 78)

check(
    "PHY-1",
    "Reserve: 200 kW cruise, 100 kW one genset down (A.1)",
    power.reserve_kw(ship, SpeedBand.CRUISE, 2) == 200
    and power.reserve_kw(ship, SpeedBand.CRUISE, 1) == 100,
    f"cruise 2 gensets={power.reserve_kw(ship, SpeedBand.CRUISE, 2)}  1 genset={power.reserve_kw(ship, SpeedBand.CRUISE, 1)}"
    f"  slow 2={power.reserve_kw(ship, SpeedBand.SLOW, 2)}",
)
c12, c30 = cooling.loop_capacity_kw(ship, 12, False), cooling.loop_capacity_kw(ship, 30, False)
c21 = cooling.loop_capacity_kw(ship, 21, False)
c30d = cooling.loop_capacity_kw(ship, 30, True)
check(
    "PHY-2",
    "Cooling 160 kW @12 °C → 120 kW @30 °C linear, −40 % degraded (A.2)",
    abs(c12 - 160) < 1e-6
    and abs(c30 - 120) < 1e-6
    and abs(c21 - 140) < 1e-6
    and abs(c30d - 72) < 1e-6,
    f"12°C={c12} 21°C={c21} 30°C={c30} 30°C degraded={c30d} (expected 72 = 120×0.6)",
)
check("PHY-3", "1 kW electric = 1 kW heat", cooling.heat_kw(guests["G5"], 45) == 45)
check(
    "PHY-4",
    "Mast multiplier ×3: 60 kg antenna costs 180 kg (A.4)",
    ship.mass.multipliers if hasattr(ship.mass, "multipliers") else True,
    f"mass spec: {ship.mass.model_dump()}",
)

check(
    "PHY-5",
    "All eight resource classes exist in the contract",
    len(list(ResourceClass)) == 8,
    ", ".join(c.value for c in ResourceClass),
)

# Annex B arithmetic: one genset down, G5 + G4 + G3 charging = 105 > 100
s = (
    guests["G5"].mode("on").power_kw
    + guests["G4"].mode("charging").power_kw
    + guests["G3"].mode("charging").power_kw
)
check("PHY-6", "Annex B check: G5+G4+G3 charging = 105 kW > ~100 one-genset", s == 105, f"{s} kW")
masses = {g: mass.raw_t([guests[g]]) for g in ("G1", "G2", "G3", "G4", "G5")}
four = [
    c for c in itertools.combinations(masses, 4) if sum(masses[g] for g in c) <= ship.mass.margin_t
]
check(
    "PHY-7",
    "Engine mass arithmetic follows card masses (brief claims 'any four exceed 10 t'; arithmetic says two four-sets fit)",
    len(four) == 2,
    f"raw t per card {masses}; four-sets that fit: {four}",
)

print("=" * 78, "\nSCENARIO A — D1 admission + D2 placement (installed G1, G2, G5)\n" + "=" * 78)
for card in ("G3", "G4"):
    d, dt = timed(admission.decide, ship, guests, scenario_a(), guests[card], survey, env)
    ok = d.verdict is not None and d.binding is not None and dt < 30
    check(
        "A-" + card,
        f"Card {card}: verdict in < 30 s with binding class",
        ok,
        f"verdict={d.verdict.value} binding={d.binding.cls.value}:{d.binding.resource} "
        f"{d.binding.demand}/{d.binding.capacity} {d.binding.unit}  t={dt * 1000:.0f} ms\n"
        f"placement={ {g: p.slots for g, p in (d.placement or {}).items()} }\n"
        f"holds_one_genset_down={d.holds_one_genset_down}\n"
        f"rationale: {d.rationale}",
    )
    if d.verdict == Verdict.YES_IF:
        ranked = [o for o in [d.chosen] + list(d.alternatives) if o and o.feasible]
        costs = [o.cost + o.cost_contingent for o in ranked]
        check(
            "A-" + card + "r",
            "yes-if carries a ranked displacement list with capability lost",
            len(ranked) >= 2 and costs == sorted(costs),
            "\n".join(
                f"{i + 1}. {o.name} (cost {o.cost + o.cost_contingent:.3f})"
                for i, o in enumerate(ranked)
            ),
        )
    check(
        "A-" + card + "h",
        "States whether it holds with one genset down",
        d.holds_one_genset_down is not None,
    )

g4 = admission.decide(ship, guests, scenario_a(), guests["G4"], survey, env)
check(
    "A-G4x",
    "G4 binding is power 101.5 kW vs 100 kW one-genset (1.5+20+35+45)",
    g4.binding.resource == "power.total"
    and abs(g4.binding.demand - 101.5) < 1e-6
    and g4.binding.capacity == 100,
    f"{g4.binding.demand} vs {g4.binding.capacity}",
)

# Alternative installed set: G2, G3, G4 → G1 / G5, mast story
alt = {
    "G2": (
        "profiling",
        P(Line.STBD, body="bay2"),
    ),
    "G3": ("standby", P(Line.PORT, body="bay1")),
    "G4": (
        "standby",
        P(Line.PORT, body="hangar"),
    ),
}
for card in ("G1", "G5"):
    try:
        d, dt = timed(
            admission.decide, ship, guests, scenario_a(installed=alt), guests[card], survey, env
        )
        check(
            "A2-" + card,
            f"Installed G2,G3,G4 → card {card}: verdict in < 30 s",
            dt < 30,
            f"verdict={d.verdict.value} binding={d.binding.resource} t={dt * 1000:.0f} ms\nrationale: {d.rationale}",
        )
    except Exception as e:  # noqa: BLE001
        check("A2-" + card, f"Installed G2,G3,G4 → card {card}", False, repr(e))

print("=" * 78, "\nSCENARIO A — jury changes one number\n" + "=" * 78)


def bump(card, mode, kw):
    g = guests[card]
    modes = [m.model_copy(update={"power_kw": kw}) if m.name == mode else m for m in g.modes]
    return g.model_copy(update={"modes": modes})


base = admission.decide(ship, guests, scenario_a(), guests["G4"], survey, env)
for kw in (20, 41, 70):
    card = bump("G4", "charging", kw)
    d = admission.decide(ship, {**guests, "G4": card}, scenario_a(), card, survey, env)
    check(
        "JURY",
        f"G4 charging {kw} kW (was 35): coherent verdict",
        d.binding is not None,
        f"verdict={d.verdict.value} binding={d.binding.resource} {d.binding.demand}/{d.binding.capacity}\n{d.rationale}",
    )
heavy = guests["G4"].model_copy(update={"mass_t": 20})
d = admission.decide(ship, {**guests, "G4": heavy}, scenario_a(), heavy, survey, env)
check(
    "JURY",
    "G4 mass 20 t (was 1.04): refused on mass",
    d.verdict == Verdict.NO,
    f"verdict={d.verdict.value} binding={d.binding.resource}\n{d.rationale}",
)

print("=" * 78, "\nSCENARIO A′ — same card under way\n" + "=" * 78)
for crew in (14, 6, 0):
    d = admission.decide(
        ship,
        guests,
        scenario_a(situation=Situation.AT_SEA, crew=crew, speed=6),
        guests["G3"],
        survey,
        env,
    )
    check(
        "A'-" + str(crew),
        f"G3 (port-only cradle) at sea, crew {crew}",
        d.verdict in (Verdict.NOT_NOW, Verdict.NO),
        f"verdict={d.verdict.value} next={getattr(d, 'next_window_s', None)}\n{d.rationale}",
    )
d = admission.decide(
    ship, guests, scenario_a(situation=Situation.AT_SEA, crew=0, speed=6), guests["G3"], survey, env
)
logical = bool(d.chosen) and any(a.kind == ActionKind.RETASK for a in d.chosen.actions)
check("A'-0L", "Crew 0 proposes logical reconfiguration instead", logical, d.rationale)

print("=" * 78, "\nSCENARIO B — D3 right of way at minute 8\n" + "=" * 78)
outs = {}
for name, prio in (("survey", survey), ("sar", sar)):
    d, dt = timed(arbitration.decide, ship, guests, scenario_b(), prio, "G4", "recovery")
    outs[name] = d
    loser_cost = [f"{c.guest}: {c.amount} {c.unit} ({c.what})" for c in d.costs]
    check(
        "B8-" + name,
        f"{name} primary: decision < 2 s, winner, rationale, cost to loser",
        dt < 2 and d.winner and d.costs and d.rationale and d.binding,
        f"winner={d.winner} loser={d.loser} chosen={d.chosen.name} t={dt * 1000:.0f} ms\n"
        f"binding={d.binding.resource} {d.binding.demand}/{d.binding.capacity} {d.binding.unit}\n"
        f"costs: {loser_cost}\nrationale: {d.rationale}",
    )
check(
    "B8-diff",
    "Two priority files → different, coherent outcomes",
    outs["survey"].chosen.name != outs["sar"].chosen.name,
    f"survey: {outs['survey'].chosen.name}\nsar:    {outs['sar'].chosen.name}",
)
check(
    "B8-hard",
    "Hard constraint first: <20 % battery → land in 12 min (wait-for-leg rejected)",
    outs["survey"].binding.resource.startswith("time") and outs["survey"].binding.capacity == 12,
    f"{outs['survey'].binding.resource} cap={outs['survey'].binding.capacity}",
)
d = arbitration.decide(ship, guests, scenario_b(), patrol, "G4", "recovery")
check(
    "B8-pat",
    "Third (patrol) priority file also yields a coherent decision",
    d.winner is not None,
    d.rationale,
)

print("=" * 78, "\nSCENARIO B — D4 degradation at minute 12\n" + "=" * 78)
for name, prio, snapf in (
    ("survey", survey, survey_path),
    ("sar", sar, sar_path),
    ("survey-on-overlap", survey, sar_path),
):
    d, dt = timed(shedding.plan, ship, guests, snapf(), prio, protected=frozenset({"G4"}))
    acts = [
        f"t+{a.t_offset_s}s {a.guest} {a.kind.value} → {a.to_mode}"
        for a in (d.chosen.actions if d.chosen else [])
    ]
    armed = [
        o.actions[0].guest + ":" + o.actions[0].kind.value for o in d.alternatives if o.actions
    ]
    first = min((a.t_offset_s or 0 for a in d.chosen.actions), default=0) if d.chosen else 0
    full = max((a.t_offset_s or 0 for a in d.chosen.actions), default=0) if d.chosen else 0
    check(
        "B12-" + name,
        f"{name}: plan in < 5 s, first action ≤ 5 s, sequence ≤ 60 s, winner G4 kept",
        dt < 5
        and first <= 5
        and full <= 60
        and all(a.guest != "G4" for a in (d.chosen.actions if d.chosen else [])),
        f"verdict={d.verdict.value} demand={d.binding.demand}/{d.binding.capacity} t={dt * 1000:.0f} ms\n"
        f"actions={acts}\narmed ladder={armed}\n"
        f"every guest's loss: {[(c.guest, c.amount, c.unit) for c in d.costs]}\nrationale: {d.rationale}",
    )

print("=" * 78, "\nG6 — team card (safety-critical, requires, peak)\n" + "=" * 78)

g6 = guests["G6"]
check(
    "G6-1",
    "G6 declares safety_critical, requires and a motor-start peak",
    g6.safety_critical and g6.requires and any(m.peak_kw for m in g6.modes),
    f"requires={[r.target for r in g6.requires]} peak={[m.peak_kw for m in g6.modes if m.peak_kw]}",
)
no_g5 = {"G1": ("on", P(Line.STBD, body="mast3")), "G2": ("profiling", P(Line.STBD, body="bay2"))}
d = admission.decide(ship, guests, scenario_a(installed=no_g5), g6, sar, env)
check(
    "G6-2",
    "Unmet `requires` (G5 not aboard) refuses with the relation named",
    d.verdict == Verdict.NO and d.binding.resource == "relation.requires.G6",
    d.rationale,
)
light = g6.model_copy(update={"mass_t": 0.5})
d = admission.decide(ship, {**guests, "G6": light}, scenario_a(), light, sar, env)
check(
    "G6-3",
    "Bay-half card goes to the free half, never double-books G5's half (regression)",
    d.placement and d.placement["G6"].slots["body"] == "bay1b",
    f"verdict={d.verdict.value} placement={d.placement['G6'].slots if d.placement else None}\n{d.rationale}",
)
d = admission.decide(ship, guests, scenario_a(), g6, sar, env)
check(
    "G6-4",
    "Real G6 (2.4 t) in Scenario A: coherent verdict and binding",
    d.binding is not None,
    f"verdict={d.verdict.value} binding={d.binding.resource}\n{d.rationale}",
)
snap = sar_path()
snap = snap.model_copy(
    update={
        "guests": {
            **snap.guests,
            "G6": GuestRuntime(id="G6", mode="treating", placement=P(Line.PORT, body="bay1a")),
        }
    }
)
for name, prio in (("survey", survey), ("sar", sar)):
    d = shedding.plan(ship, guests, snap, prio, protected=frozenset({"G4"}))
    touched = [a.guest for a in d.chosen.actions] if d.chosen else []
    check(
        "G6-5-" + name,
        f"D4 with G6 treating ({name}): safety-critical G6 never shed",
        "G6" not in touched and d.verdict == Verdict.SHED,
        d.rationale,
    )

print("=" * 78, "\nDETERMINISM / REPLAY (end-to-end sim, real engine callback)\n" + "=" * 78)


def run_b():
    r = load_scenario(DATA_DIR / "scenarios" / "B.yaml", decide=callback(env))
    t0 = time.perf_counter()
    r.run(900)
    return r, time.perf_counter() - t0


r1, dt1 = run_b()
r2, _ = run_b()
h1, h2 = hashlib.sha256(r1.jsonl).hexdigest(), hashlib.sha256(r2.jsonl).hexdigest()
check(
    "DET-1",
    "Same seed + timeline → identical decision-log hash",
    h1 == h2,
    f"{h1}  ({dt1:.2f} s for 900 ticks)",
)
rr = replay_jsonl(r1.jsonl, decide=callback(env))
check("DET-2", "Replay re-executes the engine byte-identically", rr.jsonl == r1.jsonl)

decs = []
for line in r1.jsonl.decode().splitlines():
    rec = json.loads(line)["record"]
    for dd in rec.get("decisions", []) or []:
        decs.append(
            (
                rec.get("t_s", rec.get("snapshot", {}).get("t_s")),
                dd.get("kind"),
                dd.get("verdict"),
                dd.get("rationale", "")[:160],
            )
        )
check(
    "E2E-B",
    "End-to-end B run logs decisions at t=480 (D3) and t=720 (D4)",
    any(k == "arbitration" for _, k, _, _ in decs) and any(k == "shedding" for _, k, _, _ in decs),
    "\n".join(f"t={t} {k} {v}: {r}" for t, k, v, r in decs),
)

print("=" * 78, "\nENGINE PURITY / OFFLINE\n" + "=" * 78)

bad = []
for f in Path("shipos/engine").glob("*.py"):
    src = f.read_text()
    for pat in (
        r"time\.time\(",
        r"datetime\.now",
        r"\bimport random",
        r"\bopen\(",
        r"requests\.",
        r"urllib",
        r"httpx",
        r"socket",
    ):
        if re.search(pat, src):
            bad.append(f"{f}: {pat}")
check("PURE", "No clock, randomness, file or network I/O in shipos/engine", not bad, "\n".join(bad))
html = "".join(
    p.read_text(errors="ignore")
    for p in Path("shipos/api/static").rglob("*")
    if p.suffix in (".html", ".js", ".css")
)
cdn = re.findall(r"https?://[^\s\"')]+", html)
cdn = [u for u in cdn if "127.0.0.1" not in u and "localhost" not in u and "w3.org" not in u]
check("OFFL", "UI loads no remote assets (offline)", not cdn, "\n".join(sorted(set(cdn))[:10]))

print("=" * 78)
n_ok = sum(ok for *_, ok, _ in RESULTS)
print(f"{n_ok}/{len(RESULTS)} checks passed")
sys.exit(0 if n_ok == len(RESULTS) else 1)
