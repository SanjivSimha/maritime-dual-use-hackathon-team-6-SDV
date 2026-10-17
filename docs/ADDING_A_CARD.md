# Adding or editing a guest card

A guest card is one YAML file in `data/guests/` that loads into `GuestManifest`
(`shipos/contracts.py`, Annex B.0 ontology). Every command below works unchanged in PowerShell
and in a POSIX shell from the repository root.

## 1. Start from the closest existing card

| Card has... | Copy from |
|---|---|
| one always-on mode | `G1.yaml` |
| deck machinery, parts, conditional exclusions | `G2.yaml` |
| an activity that can be paused or suspended | `G3.yaml` |
| launch / recovery, battery, at-sea swap | `G4.yaml` |
| a part on another slot, an absolute exclusion | `G5.yaml` |

Unknown keys are rejected (`extra="forbid"`), so every key must be a field of a contract type.

## 2. Numbers: Annex value or assumption

- A value stated in Annex B is written as a plain number.
- Any other value is written `"$<id>"` and added to `data/assumptions/<class>.yaml` with
  `value`, `unit`, `range`, `why` and `source`. Use ids scoped to your card
  (`guests.g7.winch_kw`). `shipos.data.resolve` swaps the string for the value before validation.

## 3. Names that must line up

| Field | Must name |
|---|---|
| `initial_mode`, `modes[].next_mode` | a mode on this card |
| `activities[].mode`, `activities[].preempt[].hold_mode` | a mode on this card |
| `activities[].followed_by` | an activity on this card |
| `excludes[].mode` | a mode on this card (omit for any mode) |
| `excludes[].target` | a guest id, or `emission:silent` |
| `excludes[].target_mode` | a mode of the target guest (omit for any mode) |
| `requires[].target` | a guest id, or `slot:<kind>` with a `SlotKind` value |
| `preferred`, `allowed`, `parts[].allowed` | `bay1_whole`, `bay1_half`, `bay2`, `mast`, `hangar`, `hangar_roof` |

`kind: absolute` refuses admission outright; `kind: conditional` only stops the two modes from
running at the same time. The stern ramp and flight deck are not mounting slots: a mode that uses
them says so in `conditions` (`uses_ramp: true`, `uses_flight_deck: true`).

## 4. Validate

```
uv run shipos validate                         # every file under data/
uv run shipos validate data/guests/G2.yaml     # one card, checked against data/guests/
```

Exit status 0 prints `OK: ...`; anything wrong prints one line per problem and exits 1, e.g.

```
data/guests/G2.yaml: modes.0.power_kw: Input should be a valid number, unable to parse string as a number (got 'lots')
data/guests/G2.yaml: unknown assumption $guests.g2.no_such_value; add it to data/assumptions/
data/guests/G2.yaml: initial_mode 'idle' is not a mode of G2
```

A card path outside `data/guests/` works too. If its `id` matches an existing guest it stands in for
that guest, which is how to try a one-number edit on a copy before changing the real card.

## 5. Schema

`schema/guest_manifest.schema.json` is generated from `GuestManifest` for editors and external
tools. It describes a *resolved* card, so a raw card with `"$<id>"` strings only matches it after
resolution. Never edit it by hand:

```
uv run python -m tools.guest_schema            # regenerate after a contract change
uv run python -m tools.guest_schema --check    # exit 1 if stale; writes nothing
```

`tests/test_b1_schema.py` runs the same check in CI.

## 6. Before the PR

```
uv run ruff check .
uv run ruff format --check .
uv run python -m tools.check_no_literals
uv run pytest
```

Tests that pin the exact set of guests (for example "all five fit at cruise") have to change in
the same PR as a new card; if they are another track's files, agree that change with its owner first.
