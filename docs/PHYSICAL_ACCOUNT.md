# Physical Account: ShipOS

**Basis:** merged `fcd8c58` (PR #46). Annex A/B inputs and the
[assumption registry](PHYSICAL_ACCOUNT_REGISTRY.md) drive the public physics and
D1-D4 engines. This is a simulated engineering prototype, not certification or a ship trial.

## Eight resource budgets

| Budget | Implemented model and material boundary |
|---|---|
| Power | Guest reserve at cruise: 200 kW, or 100 kW with one genset; two 150 kW mission lines. Peak reserve is +250/+100 kW for up to 60 s. Other speed reserves and lost-line selection are assumptions. |
| Heat | Water-cooled electrical load becomes loop heat 1:1. Capacity interpolates 160 kW at 12 °C to 120 kW at 30 °C, clamped outside that range; degraded factor × 0.6 gives 72 kW at 30 °C. Air-cooled G1 adds no loop heat. Inertia-buffer library is not a demonstrated transient decision model. |
| Deck | Declared discrete bay/hangar/roof slot groups and occupancy, not structural analysis. |
| Mast | Three positions, two occupied by ship equipment; declared guest position and mounting constraints. |
| Mass / stability | 10 t raw margin; 12 t-equivalent proxy with mounting multipliers 1.0 bays, 1.2 hangar/roof, 3.0 mast. No GM, VCG, free-surface or hydrostatics. |
| Data links | Admission SATCOM worst case: 2 x 0.7 - 0.5 = 0.9 Mbit/s. Silence blocks SATCOM/radio; manoeuvring, deck machinery or layering blocks acoustic. Elastic losses are reported. |
| Compute | Guest allocation: 102 cores, 409.6 GB RAM, 3 whole accelerators (80% rack allocation). |
| Response time | A.8 ceilings and A.6 upper-bound transition planning; concurrent preparations with explicit dependencies. Acoustic hold precedes manoeuvring; human confirmation follows deck clearance. No measured latency compliance claimed. |

## Decisions, simulation and evidence

D1 tests the declared envelope corners; D2 ranks placements. D3/D4 are implemented.
The opt-in controller executes supported chosen plans with crew gates and recorded
operator input; observation mode applies no actions. API acceptance tests cover
14-crew recovery at t=841 after preparation, both priorities, reduced-crew denials
and execution replay. Browser tests cover controls/rendering; synthetic edge tests
are separate evidence. These do not establish physical safety or full challenge completion.

## Accounting cautions and next constraint

Baseline B's t=720 trip is **not an overload**: observation nominal demand is
1.5 + 1 + 2 + 45 = 49.5 kW versus 100 kW. The 106.5 kW charging case requires
an explicit overlap. G3 includes its cradle; surveying/holding/recovering draw 1 kW,
charging 25 kW. G4 follows recovery -> launch -> flying, not charging.
A loop rated 160 kW at 30 °C (**+40 kW**) is a proposed next investment, not an
optimised or measured result. Cached forecasts and sea-state mapping are proxies.

Exact D4 metadata producer #43 remains separate; missing facts and ambiguous D3
deferrals fail closed. Winner-protection lifetime is provisional. G6 is absent.
Deck-camera evidence is simulated. Replay needs compatible code and assumptions.

**Registry appendix:** 85 assumptions, 76 with literal uses, 9 without literal uses,
plus one dynamic lookup, as generated at this baseline. The appendix records values,
units, reasons, sources and use sites; unused structural choices are disclosed.
The [PDF](PHYSICAL_ACCOUNT.pdf) contains this one-page account followed by that appendix.
