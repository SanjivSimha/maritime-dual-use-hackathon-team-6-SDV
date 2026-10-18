# Demo runbook: 90 s of Scenario B, then Scenario A with the jury's card

## Before the jury arrives (laptop offline)

```bash
make install        # done once while online
make demo           # http://127.0.0.1:8000/  (Windows: .\tools\demo.ps1)
```

Open the page full-screen and turn Wi-Fi off. Everything is local: no CDN, no runtime fetch.

## Scenario B: 90 seconds

1. Click **Run the mission**, then **set Situation to "At sea, running a mission"** first.
   (If Situation stays "In port", the battery report fails silently because G4 is not aboard.)
2. Set **Priority** to the file the jury picked (Survey or Search and rescue) and **Crew** to 14.
3. The baseline timeline is scripted: the 18 % battery report fires at **t = 480** and the genset
   trip at **t = 720**. Just press **Advance time** (120 s per press). Four presses reach minute 8,
   and two more reach minute 12. Use **Report battery** / **Trip a generator** only to inject a
   jury variation *before* those times. A manual trip at exactly t = 720 is rejected as a duplicate.
4. **At minute 8 (D3).** Point to the green card *"VTOL rescue drone + hangar goes first"* and
   the binding *Response time: needs 41 min, 12 min available* (49 min if reported at t = 0).
   Read the one sentence and the cost to the loser:
   - Survey: *G3 holds 15 min; G3 loses 23 survey-min*.
   - SAR: *G3 suspends; 35 kW reserved for G4; G3 loses 83 survey-min*.
5. **At minute 12 (D4).** The engine answers *no shedding needed: the 100 kW reserve keeps about 50 kW
   free*, with the **armed ladder** (G5 → low-power, G1 off, G3 standby). Say: "the drone is
   still airborne, so nothing overloads. Here is what happens when its 35 kW charge overlaps
   G3's non-interruptible recharge." Then show the overlap result (106.5 kW vs 100 kW):
   survey primary throttles G5 to 8 kW at t+1 s; SAR primary defers G3's charge 5 min and keeps the node.
   Both are in `../05_validation/validation_output.txt` (B12-*).
6. Switch **Priority** (this starts a fresh run at t = 0) and repeat steps 3–5. *"Same code, two different ships."*

Close: **Session record → Verify by replaying** re-executes the engine and compares the log hash.

## Scenario A: the jury's card

1. **Start over** → **Add equipment** → Situation "In port, adding equipment" (G1, G2, G5 installed).
2. Choose the jury's card under **Equipment** (G3, G4, or our G6) and **Check if it fits**.
   Point to the verdict, the **binding resource**, the placement on the schematic, "holds with one
   genset down: yes/no", and for *yes-if* the ranked list under *Conditions*.
3. **Jury changes one number:**
   - Mass: edit **Mass (t)** and check again (G4 at 20 t → refused on hangar mass).
   - Any other number: **More controls → Import a complete equipment card (JSON)**, load the card,
     change the number (for example G4 charging `power_kw` 35 → 41 or 70), change `id` (for example
     `JUDGE_DRONE`), then validate and evaluate.
4. **A′ (same card, under way):** set Situation to "At sea" and re-check. A port-only card gives *Not
   now: next port call within 14 days*. At Crew 0 the verdict also proposes re-tasking G5's edge
   compute and the free rack cores.

## Expected answers (computed from the Annex, verified by `validate.py`)

| Card (installed G1, G2, G5) | Verdict | Binding |
|---|---|---|
| G4 | yes-if: G2 winch waits while G4 charges, one genset down | power 101.5 / 100 kW (slow, 30 °C, 1 genset) |
| G3 | yes-if: displace G5 | deck.bay1 3 / 2 units |
| G6 | yes, placed on Bay 1b | Bay 1 now full |
| G6 without G5 aboard | no | G6 requires G5 (telemedicine over SATCOM) |

| Installed G2, G3, G4 | Verdict | Binding |
|---|---|---|
| G1 | no | mass 10.08 / 10 t |
| G5 | no | deck.bay2 2 / 1 units |

## If something goes wrong

- Port busy: `uv run --locked --offline python -m uvicorn shipos.api.server:app --port 8765 --ws websockets-sansio`.
- Sessions are in memory: **Export evidence** before restarting the server.
- A backup recording is welcome but does not replace the live run.
