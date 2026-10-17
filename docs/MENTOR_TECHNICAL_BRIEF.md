# ShipOS Technical Brief: Core Architecture & Clarification Questions

- **Author:** ShipOS Task Coordination & System Verification (agy)
- **Date:** October 2026
- **Audience:** Technical Mentors, System Judges, and Integration Leads
- **Repository:** `SanjivSimha/maritine-dual-use-hackathon-team-6-SDV`

---

## Executive Summary

ShipOS is an autonomous dual-use shipboard operating system designed for deterministic resource arbitration, payload admission, and mission envelope enforcement. The platform enforces physical constraints across eight resource classes (mass, deck geometry, electrical power, chilled water cooling, mast cooling air, data links, crew hours, and acoustic noise signatures) without live LLMs in safety-critical loops.

This brief provides mentors and judges with key technical clarifications regarding physical modeling, operational chronology, and autonomous safety boundaries.

---

## 5 Key Technical Clarification Questions for Mentors & Judges

### 1. Scenario B Chronology & Battery Exhaustion Semantics
* **Context:** In Scenario B, the initial world snapshot begins at simulation time $t = 480\,\text{s}$ (minute 8) under patrol priorities with Guests G1 and G2 installed. An incoming telemetry report at minute 8 issues a 12-minute landing deadline for the airborne drone (G4), requiring recovery on deck by $t = 1200\,\text{s}$ (minute 20).
* **Technical Ambiguity:** In previous discussions, minute 20 was colloquially referenced as "battery exhaustion." However, aeronautical recovery envelopes distinguish between *exhaustion of usable mission energy* and *depletion of statutory emergency reserves*. Furthermore, the deck clearance transition sequence (clearing the flight deck, halting conflicting deck machinery, and securing crane/cradle motion) requires physical lead time (governed by assumption `A.6`).
* **Clarification Sought:** Should the system treat $t = 1200\,\text{s}$ as a hard fail-closed landing deadline where recovery must be fully complete, or as the latest time by which recovery approach may commence? If deck clearance takes 180 seconds, does the recovery gate trigger a late-clearance fault if not commanded by $t = 1020\,\text{s}$?

---

### 2. Safety Interlocks vs. Priority Arbitration
* **Context:** When a priority swap occurs (e.g. from `patrol` to `sar_primary`), payload weights change dynamically. G4 recovery may require shedding or placing Guests G1 or G2 into standby to satisfy electrical power or deck clearance interlocks.
* **Technical Ambiguity:** Under standard priority arbitration, the engine ranks actions by capability-weighted cost ($\Delta \text{capability} \times \text{priority}$). However, flight deck safety is physical and absolute: a drone attempting landing on an occupied deck or near active deck machinery risks catastrophic vehicle loss.
* **Clarification Sought:** Does flight deck safety act as a strict physical gate prior to any priority arbitration (i.e. lexicographically prior to mission utility), or should the engine weigh the risk of drone loss against mission-critical sensor uptime during an active SAR incident? In the current implementation, `recovery_gate` strictly fails closed if the deck is occupied or unconfirmed, regardless of priority.

---

### 3. Thermal Derating at 30 °C vs. Power Capacity Sizing
* **Context:** Operating in the Mediterranean (Strait of Sicily) presents extreme thermal environments. In the A.10 mission envelope, the degraded contingency corner models single genset outage combined with peak seawater temperatures of 30 °C.
* **Technical Ambiguity:** Shipboard diesel gensets derate slightly with intake temperature, but the primary constraint is the centralized chilled-water heat exchanger manifold. High-power electronic payloads (e.g., G1 active phased-array radar) reject substantial heat into the cooling loop. In our robustness probes, chilled-water manifold limits (`heat.manifold.cw`) bound admission before electrical power limits (`power.total`).
* **Clarification Sought:** In contingency corners where chilled-water loop margin is negative but electrical generation margin is positive, does operational doctrine allow transient thermal storage (thermal inertia of the ship's piping and structure) for time-limited modes, or must steady-state heat rejection balance at every evaluation tick? Currently, ShipOS strictly enforces steady-state thermal balance across all envelope corners.

---

### 4. WMO 3700 Environmental Proxy Fidelity & Layered Water Dynamics
* **Context:** The environment runner ingests historical open-ocean metocean data (WMO station 3700, Strait of Sicily) to drive realistic sea state, wind speed, seawater temperature, and ocean current fields.
* **Technical Ambiguity:** Subsurface payloads (such as G2 towed acoustic sonar or G3 survey vehicle) are heavily dependent on thermocline depth and acoustic velocity profiles (layered water). When `layered_water: false`, sound speed is treated as isothermal. When `layered_water: true`, acoustic propagation ducting alters sonar performance.
* **Clarification Sought:** For hackathon evaluation, is the surface environmental proxy (sea state 1–5, surface water temp 16–30 °C, wind 0–35 kn) sufficient for judging payload operational limits, or do judges expect multi-layer depth stratification to influence G2/G3 acoustic capability factors?

---

### 5. Crew Confirmation Protocol During Uncrewed Transition
* **Context:** ShipOS PR #27 introduces the `CrewEvidence` lifecycle helper, formalizing flight deck clearance protocols. When crew is aboard ($\text{crew} = 14$), deck clearance requires explicit human confirmation (`by="deck officer"`), which is automatically dropped if the deck becomes occupied. When operating uncrewed ($\text{crew} = 0$), human confirmation is physically impossible.
* **Technical Ambiguity:** In uncrewed mode, autonomous recovery requires verified simulated camera evidence (`source="simulated_camera"`, dated precisely to the current snapshot). Stale evidence or unverified camera feeds immediately trigger fail-closed interlocks.
* **Clarification Sought:** During transition periods where crew departs or vessel switches to remote control, what is the approved hand-off protocol for pre-authorized flight deck operations? Can a human confirmation be pre-authorized with a fixed expiry window, or must uncrewed recovery rely exclusively on onboard real-time sensor verification?

---

## Summary of Empirical Verification

| Subsystem | Metric | Verified Result | Design Requirement |
| :--- | :--- | :--- | :--- |
| **D1 Admission** | Decision Latency | **79.6 ms** (median) | $\le 30.0\,\text{s}$ |
| **D2 Placement** | Slot Search Latency | **1.89 ms** (median) | Sub-second |
| **Replay Hash** | Replay Determinism | **Byte-for-byte identical** | 100% reproducible |
| **Tamper Guard** | Altered Trace Detection | **Caught by execution replay** | Zero false-negative |
| **Physical Registry**| Literal Guard | **0 unreferenced constants** | 100% assumption tracked |
