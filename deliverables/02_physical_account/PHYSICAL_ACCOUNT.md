# Physical Account (Annex C): PoseidonOS, Team 6

Every number the software relies on that is not in Annex A/B, or that narrows an Annex value. All
113 assumptions are in `data/assumptions/*.yaml` with value, unit, range, reason and source, and are read through
`A("<id>")`. CI fails on any other numeric literal in `shipos/physics` or `shipos/engine`. The
**appendix** (generated, pages 2+) lists every use site by file and line.

| Class | What we assumed | Value / range | Why | Where used in code |
|---|---|---|---|---|
| **Electrical power** | Reserve at stopped/slow = cruise (not credited higher) | 200 / 100 kW (2 / 1 gensets) | A.10: judge on the lower of slow and cruise. Scenario B quotes ~100 kW at 3 kn | `physics/power.py` `reserve_kw`, `engine/kernel.py` |
| | Reserve at fast | 150 / 75 kW | bottom of A.1 range; outside default envelope | `physics/power.py` |
| | Line lost on trip | stbd line, bus-tie to port | A.9 leaves it to us | `physics/power.py` `surviving_lines` |
| | Spinning reserve held back when degraded | 0 kW | A.9 ~100 kW is already net of hotel load | `engine/shedding.py` `_capacities` |
| | Overdraw warn / throttle / shed | +10 % / +15 % after 10 s / shed after 60 s | brief: guests exceed by 10–20 % | `engine/shedding.py` overdraw guard |
| **Heat rejection** | kW electric → kW heat | 1.0 (A.2) | stated so it is traceable | `physics/cooling.py` `heat_kw` |
| | Loop vs seawater | linear 160 kW @12 °C → 120 kW @30 °C, clamped; ×0.6 degraded (72 kW @30 °C) | A.2 "falling linearly" | `physics/cooling.py` `loop_capacity_kw` |
| | Inertia (+30 kW / 60 s / 10 min) | modelled, **not** used by D4 | conservative: D4 never counts on it | `physics/cooling.py` `InertiaBuffer` |
| **Positions** | Bay 1 half mass limit | 4 t each | 8 t bay split evenly | `data/ship`, `physics/positions.py` |
| | Hangar module / roof limits | 1.2 t; roof 30 kg, 0.5 m², 0.5 m antenna height | G4 hangar+drone 1.04 t; roof takes G5 antenna only | `physics/positions.py` |
| | Free mast slot is next to the nav radar | true → G1 −30 % | B.G1 | `physics/positions.py` `capability_factor` |
| | One item per mounting point | 1 | A.3 "two 10-ft guests" = two halves | `physics/positions.py` `check` |
| **Mass / stability** | Margin kept beyond A.4 | 0 t (full 10 t and 12 t-eq) | no hidden safety factor | structural: `physics/mass.py` uses the A.4 values directly |
| | G2 split body / winch / probe | 3.0 / 0.4 / 0.1 t | card gives 3.5 t total | `data/guests/G2.yaml` |
| **Data links** | Ship's own SATCOM share | 0.5 Mbit/s | weather, ops, crew | `physics/links.py` |
| | SATCOM admission basis | 2 Mbit/s × 70 % − 0.5 = 0.9 Mbit/s | worst case; elastic, so loss is reported, not a refusal | `physics/links.py` `capacity_mbps` |
| | Acoustic drop | down if manoeuvring, deck machinery or layered water | A.5 | `physics/links.py` `acoustic_up` |
| **State transitions** | Plan on A.6 upper bound; speed/course/noise settle concurrently | e.g. quiet 4 min; deck clear 5 / 12 min (14 / 6 crew) | a deadline met on paper is met at sea | `statemachine.py` planner |
| | Speed band edges | 0.5 / 7.5 / 16 kn | midpoints of A.6 gaps | `physics/power.py` `speed_band` |
| **Compute · Response time** | Per-guest cores (G1–G7) | 4 / 2 / 8+1 acc / 8+1 acc / 4 / 4 / 2 | per-card workload | `physics/compute.py` |
| | D4 horizon / first action / step | 30 min / t+1 s / 2 s steps | ceilings 5 s / 60 s | `engine/shedding.py` |
| | Solver budgets | ≤ 200 000 placements, ≤ 3 restrictions, ≤ 20 000 shed combos, then deterministic fallback | stay under 30 s / 5 s | `engine/placement.py`, `admission.py`, `shedding.py` |
| **Priorities · Crew** | Capability weights high / medium / low | 1.0 / 0.5 / 0.2; mission-critical ≥ 0.8 | B.0: resolved from priority file | `data/priorities/*.yaml`, all engines |
| | Confirmation delay | 15–60 s (14), 60–180 s (6), forbidden at 0 | A.6 | `crew/policy.py` |
| | Six-crew handling window | 07:00–19:00 local | reduced crew, daylight only | `sim/env.py` |
| **Anything else** | G3 recovery 15 min, suspended leg restarts after 75 min; G4 approach/launch 3 min, top-up 30 min | as stated | card gives totals only | `data/guests/G3.yaml`, `G4.yaml` |
| | **G6 (our card)**: towed acoustic array 2.5 t; winch 15 kW × 20 min deploy/recover; listening 4 kW at 4–6 kn, quiet; 120-min transect, 30-min pause; port-only 3 people / 4 h | 17 invented values | civil marine-mammal monitoring | `data/guests/G6.yaml`, `data/assumptions/g6.yaml` |
| | **G7**: survivor-care module 2.4 t, 3 / 18 kW + 10 kW × 20 s peak, requires G5, safety-critical | invented, ranges in registry | exercises B.0 flags | `data/guests/G7.yaml` |
| | Environment: Open-Meteo Strait of Sicily cache, hourly hold, sea-state from Hs (WMO 3700) | cache ≤ 24 h old | offline, replayable | `sim/env.py`, `data/env_cache/` |

**Brief inconsistency recorded.** Annex B says "any four exceed the 10 t margin". By the card masses,
G1+G2+G4+G5 = 7.61 t and G1+G3+G4+G5 = 9.61 t fit. We follow the arithmetic (`tests/physics/test_mass_positions.py`).

**Closing question: one change to the platform.** Add **40 kW of chilled-water capacity at 30 °C
seawater (120 → 160 kW)**, for example a second plate exchanger on the existing loop. In warm water the
loop, not the gensets, is what pushes a degraded ship into shedding. A loop degraded by 40 % at 30 °C gives 72 kW
for guests, while one genset still gives 100 kW, so cooling binds first, and there is no cooling repair at sea
(A.6). With +40 kW the degraded loop holds 96 kW, within 4 kW of the one-genset reserve. In
Scenario A with G4 aboard, the worst-case heat shortfall (G5 45 + G4 35 + G2 20 = 100 kW) falls
from 28 kW to 4 kW, so one throttle step covers it instead of a shed list.
This proposal comes from our model and has not been validated on a real ship.
