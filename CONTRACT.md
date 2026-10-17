# ShipOS: Shared Interface Guide (Read-Only Summary)

**Authoritative contract:** `shipos/contracts.py` and the callable signatures in `shipos/engine/`. This document is a quick orientation for parallel teams, **not** a separate schema or permission to change existing interfaces. `CLAUDE.md` and `docs/PLAN.md` remain authoritative for rules and scope.

## Data flow

`data/ship/` + `data/guests/` + `data/priorities/` + `data/envelopes/` → `shipos/data.py` loaders → simulator-produced `WorldSnapshot` → Sanjiv's decision engine → structured `Decision` → Person B's API/dashboard/event log.

The simulator and UI must use the real objects and JSON representation supplied by the contract, not guessed fields. Sample JSON objects live in `tests/fixtures/`; regenerate using `uv run python -m tools.make_fixtures` only when an approved change makes this necessary.

## Core existing types

- **`ShipSpec`**: physical capacities, positions, limits, transitions and installation conditions.
- **`GuestManifest`**: equipment modes, demands, installation needs, exclusions, relations and capability.
- **`PriorityFile` and `Envelope`**: swappable mission choices and conditions an admission verdict must survive.
- **`WorldSnapshot`**: `t_s`, `ship`, `platform`, `env`, `crew`, `guests`, and `mission_days_left`. Use `GuestRuntime` for installed equipment and current mode/placement.
- **`Event`**: `t_s`, `kind`, `payload`; simulation uses it for generator failures, battery reports, card edits, priority changes, etc.
- **`Decision`**: `kind`, `subject`, `verdict`, `binding`, `chosen`, `alternatives`, `placement`, `state_requests`, `winner`, `loser`, `costs`, `holds_one_genset_down`, `contingency`, `next_window`, `checks`, `rationale`, `latency_ms`, `t_s`. Optional fields may be absent or null. Never assume every decision has a winner or a chosen option.
- **`ConstraintResult`**: reports the actual binding limit and its capacity/demand, unit, contributors and corner.

## Engine entry points (implemented by Person A)

The current source declares these interfaces. Person B can read and call them but must not alter their definitions:

```python
# D1: evaluate a proposed new equipment card
admission.decide(ship, guests, snapshot, card, priorities, envelope) -> Decision

# D3: resolve activity contention
arbitration.decide(ship, guests, snapshot, priorities,
                   challenger, challenger_activity) -> Decision

# D4: plan degraded operation
shedding.plan(ship, guests, snapshot, priorities,
              protected=frozenset()) -> Decision
```

`placement.decide` has a separate existing signature: inspect `shipos/engine/placement.py` before invoking it. Do not infer signatures from `docs/PLAN.md` examples.

## Boundaries and integration

- **A owns:** feasibility physics and D1-D4 decisions, along with state-machine logic.
- **B owns:** data cards and scenario feeds, simulator orchestration, crew policy, API and UI, documentation.
- The UI initially may consume **fixture-backed decisions**, clearly labeled as sample data. Once connected, it must display actual decisions returned by the engine rather than hardcoded verdicts.
- Judging controls must edit a real input value (for example, one guest's declared power or cooling requirement), rerun evaluation, and display the actual changed constraint and reason.
- Swapping priority files must actually rerun decisions. A generator failure must change `PlatformStatus` and trigger evaluation based on real active equipment modes; don't fabricate overload conditions or impossible concurrent charging states.
- Avoid creating new API endpoints, field names, or implied engine behavior in this document. Person B owns the API design, but it must translate to existing contract types and have tests.

## If an interface must change

Stop. Open an issue or notify both humans with the required change, why existing fields cannot support it, and the proposed migration. Only proceed once approved. Update contract source, fixtures, dependent tests and this summary together via an appropriate reviewed PR.
