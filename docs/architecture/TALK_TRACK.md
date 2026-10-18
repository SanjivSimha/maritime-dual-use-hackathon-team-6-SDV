# ShipOS architecture: talk track

About 30 seconds per slide. Slide numbers match `shipos_architecture.pdf` and `png/NN.png`.

**1 · Title.** "ShipOS decides what mission equipment the ship can carry and run, and explains every
decision in numbers. It's offline and deterministic, and there's no ML in the decision path."

**2 · Big picture.** "The ship is fixed: two generators, one cooling loop, limited deck and mast. Mission
equipment is swapped per mission. ShipOS sits between them, a bit like air-traffic control for power,
heat, space and time. It answers four questions, D1 to D4."

**3 · Eight budgets.** "Every decision checks all eight. Hard budgets give 'no' or 'yes, if'. Elastic
ones give 'yes, with a stated loss'. Power isn't the only limit: in warm water, cooling can bind first."

**4 · One engine.** "D1 to D4 all ask the same question: does this loadout fit? One feasibility kernel
answers it. The four decisions only differ in what options they try and what they optimise. So if you
change one number on a card, every answer updates consistently."

**5 · Pipeline.** "Six steps: read the inputs, check every budget, find the binding limit, try every fix,
rank by mission, then explain and record. The problem is small (about 1,000 combinations), so we try
them all. That's fast, repeatable, and gives you the ranked alternatives."

**6 · Envelope corners.** "We admit only what works in the worst case the mission allows: slow and cruise
speed, 30 °C water, with and without a generator. For the G4 drone, both one-generator corners come up
1.5 kW short, so the answer is yes-if."

**7 · Worked example.** "This is real engine output. 101.5 kW needed, 100 usable. Fix: the winch waits
while the drone charges, and only when a generator is down. The sentence is built from those numbers,
not written by hand. Change G4's mass to 20 t and it flips to NO, binding on hangar mass."

**8 · Priority swap.** "Mission priorities are a data file. Same ship, same physics: under survey, G3 is
yes-if; under search and rescue, it's no, because the fix costs more than G3 returns. The same goes for
degradation: survey throttles the comms node, SAR defers the vehicle's charge."
*Be precise:* the D4 row uses a labelled controlled input (charging overlap), not baseline B.

**9 · Scenario B.** "At t=480 the drone's battery report triggers D3, and recovery wins. At t=720 a
generator trips. D4 runs and says no action, because 49.5 kW fits under 100. A trip isn't an overload.
At t=840 the ship is ready, but the drone keeps flying until a human confirms the deck is clear."

**10 · Tech stack.** "Five layers on one laptop: YAML data checked against frozen contracts, a pure
Python engine, a seeded simulator, a FastAPI and WebSocket API, and a plain browser UI. There's no
cloud, no build step and no external solver."

**11 · One tick.** "Every second the simulator builds an immutable snapshot of the world. The engine
takes a snapshot and returns a Decision, with no clock and no I/O. Each decision goes into a
hash-chained log. Same inputs and seed always give the same log."

**12 · Trust.** "Six guarantees, each enforced in CI: no magic numbers, no canned explanations,
deterministic, tested against the Annex, tamper-evident, and fails closed. Every decision traces to a
number, and every number to a source."

## Likely judge questions

- **Why not a solver or ML?** Enumeration is exhaustive, deterministic and fast at this size, and it
  gives the full ranked list. There's a guard that falls back to greedy search if the space grows.
  OR-Tools CP-SAT is the scale-up path. ML would break explainability and replay.
- **What if we change a number?** Edit any card in the UI. The verdict, binding constraint and rationale
  recompute. Not every edit changes the verdict, but the rationale always cites the current numbers.
- **How do you know the numbers are right?** Annex values are in `data/ship/` and `data/guests/`. Every
  value we invented is in `data/assumptions/` with a unit, range, reason and source. The Physical
  Account lists them all.
- **What's not done?** No real vessel or hardware is connected, and deck evidence is simulated. We
  haven't measured decision latency, and G6 isn't in. Stability, weather mapping and transitions are
  engineering proxies. (See README "Export, replay and limits".)
