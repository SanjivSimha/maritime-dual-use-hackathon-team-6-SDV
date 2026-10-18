"use strict";
// All server text is inserted with textContent; nothing from the server becomes HTML.
const $ = id => document.getElementById(id);
let state, socket;
const node = (tag, text, cls) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
};
const number = x => Number.isFinite(x) ? Number(x.toFixed(2)).toString() : "unavailable";
const json = x => JSON.stringify(x, null, 2);
const short = hash => hash ? `${hash.slice(0, 12)}…` : "none";
const options = (el, items, selected) => {
  el.replaceChildren(...items.map(([value, label]) => {
    const option = node("option", label);
    option.value = value;
    return option;
  }));
  if (selected !== undefined) el.value = selected;
};

// Plain-language labels. Colour classes are always paired with an icon and words.
const VERDICTS = {
  yes: ["Fits", "s-ok", "✓"],
  yes_if: ["Fits, with conditions", "s-cond", "!"],
  not_now: ["Not now: try later", "s-defer", "⏸"],
  no: ["Refused", "s-bad", "✕"],
  decided: ["Decided", "s-info", "i"],
  no_action: ["Nothing needs to change", "s-ok", "✓"],
  shed: ["Power down some equipment", "s-cond", "!"],
};
const KINDS = {
  admission: "Equipment admission (D1)", placement: "Placement (D2)",
  arbitration: "Right of way (D3)", shedding: "Degradation plan (D4)",
};
const RESOURCES = {
  power: "Electrical power", heat: "Cooling (heat rejection)", deck: "Deck space",
  mast: "Mast positions", mass: "Mass and stability", data: "Data links",
  compute: "Compute", time: "Response time",
};
// Runtime controls: enabled only when the server's own OpenAPI schema offers the command AND
// this page is wired to that command's published payload (PR #37). Nothing is guessed.
const RUNTIME = {
  execution: "Simulated execution",
  confirm_deck: "Operator deck confirmation",
  environment: "Cached environment selection",
};
const WIRED = new Set(["execution", "confirm_deck", "environment"]);
let serverOps;  // undefined: checking; null: unreadable; array: Command.op values

const PRIORITY_NAMES = {survey_primary: "Survey", sar_primary: "Search and rescue", patrol: "Patrol"};

// The decision the page explains: the command's own result, else the latest recorded one
// (a quiet tick after an event returns no decision of its own).
function shownDecision() {
  if (state.decision) return state.decision;
  return state.history?.length ? state.history[state.history.length - 1].decision : null;
}

// Ship view state, kept per page. Snapshot comparison and task matching reset with each run.
let view = "top";
const VIEWS = [["viewTop", "top"], ["viewSide", "side"], ["view3d", "3d"]];
let selectedGid = null;
let runKey = null;
let prevSnap = null;
let lastChange = null;
let pendingSeen = new Map();  // pending task id -> entry, as reported earlier in this run
let appliedSeen = new Set();

async function command(cmd) {
  $("error").textContent = "";
  try {
    const response = await fetch(`/api/sessions/${state.id}/commands`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(cmd)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : json(result.detail));
    render(result);
  } catch (error) {
    $("error").textContent = `Not applied: ${error.message}`;
    if (state) syncControls();
  }
}

function syncControls() {
  // Runtime controls always show what the server reports, so a rejected command leaves no
  // half-applied control behind.
  $("execution").checked = Boolean(state.execution?.enabled);
  const env = state.environment?.configuration;
  if (env) {
    $("environment").value = env.source;
    if (env.source !== "scenario") {
      if (env.timezone_offset_min != null) $("envTz").value = env.timezone_offset_min;
      if (env.scenario_start) $("envStart").value = env.scenario_start.slice(0, 16);
      $("envLayered").checked = Boolean(env.layered_water);
      $("envStale").checked = Boolean(env.allow_stale);
    }
  }
  $("envCache").disabled = $("environment").value === "scenario";
  $("mode").value = state.mode;
  $("scenario").value = state.scenario;
  $("crew").value = state.snapshot.crew;
  options($("priority"), state.priorities.map(p => [p, PRIORITY_NAMES[p] ?? p]), state.priority_name);
}

function render(data) {
  const oldCard = $("card").value;
  const oldMass = state?.guests?.[oldCard]?.mass_t;
  state = data;
  trackChanges();
  syncControls();
  document.body.dataset.mode = state.mode;
  $("modeBadge").textContent = state.mode;
  $("provenanceText").textContent = state.mode === "SAMPLE"
    ? "D3/D4 buttons show labelled sample fixtures, not results for this session"
    : state.execution?.enabled
      ? "Engine results for this session · simulated execution is ON (simulation only, no real equipment)"
      : "Results computed by the engine for this session · recommendations only, nothing is executed";
  $("export").href = `/api/sessions/${state.id}/evidence`;
  $("jsonl").href = `/api/sessions/${state.id}/replay.jsonl`;
  const snap = state.snapshot;
  const ship = snap.ship;
  $("stateStrip").textContent = `Now (t = ${number(snap.t_s)} s): ${number(ship.speed_kn)} kn · ${ship.course} course · ${ship.noise} · deck ${ship.deck} · ${snap.platform.gensets_online} generator(s) · seawater ${number(snap.env.seawater_c)} °C · ${snap.crew} crew`;
  const finished = state.next_t_s >= state.horizon_s;
  $("clock").textContent = finished
    ? "This run has ended. Press Start over for a fresh run."
    : `${number(snap.t_s)} s of ${state.horizon_s} s`;
  $("forward").disabled = $("step").disabled = finished;
  $("replayStatus").textContent = state.replay
    ? `Replay ${state.replay.byte_identical ? "✓ byte-identical" : "✕ DIFFERS"} · ${state.replay.records} records · hash ${short(state.replay.final_hash)}`
    : `Not verified yet · log hash ${short(state.final_hash)}`;
  $("replayStatus").title = state.replay?.final_hash ?? state.final_hash ?? "";
  renderAboard(snap);
  const proposed = Object.values(state.guests).filter(g => !snap.guests[g.id]);
  const selected = proposed.some(g => g.id === oldCard) ? oldCard : proposed[0]?.id;
  options($("card"), proposed.map(g => [g.id, `${g.name} (${g.id})`]), selected);
  if (selected !== oldCard || oldMass !== state.guests[selected]?.mass_t || !$("value").value) fillMass();
  if (!$("cardJson").value) fillCardJson();
  $("admit").querySelector("button").disabled = !selected;
  if (selectedGid && !state.guests[selectedGid]) selectedGid = null;
  if (state.decision?.kind === "admission" && state.guests[state.decision.subject]) selectedGid = state.decision.subject;
  if (selectedGid && !state.guests[selectedGid]) selectedGid = null;
  renderShip();
  renderSelection();
  renderStory();
  renderGauges();
  renderDecision(shownDecision());
  renderLog();
  renderMissionStatus();
  renderEnvironment();
  renderPreviousRuns();
}

// ----------------------------------------------------------------- changes between updates

function trackChanges() {
  const key = `${state.id}|${(state.previous_runs ?? []).length}|${state.scenario}|${state.mode}`;
  const snap = state.snapshot;
  const status = state.execution?.status;
  const fresh = key !== runKey || (prevSnap && snap.t_s < prevSnap.t_s);
  if (fresh) {
    runKey = key;
    prevSnap = null;
    lastChange = null;
    pendingSeen = new Map();
    appliedSeen = new Set((status?.applied ?? []).map(a => a?.task));
    if (prevSnap === null && runKey !== null) selectedGid = null;
  }
  for (const p of status?.pending ?? []) if (p && p.id != null) pendingSeen.set(p.id, p);
  const newlyApplied = (status?.applied ?? []).filter(a => a && !appliedSeen.has(a.task));
  newlyApplied.forEach(a => appliedSeen.add(a.task));
  if (prevSnap && (JSON.stringify(prevSnap) !== JSON.stringify(snap) || newlyApplied.length)) {
    lastChange = {...diffSnapshots(prevSnap, snap), applied: newlyApplied};
  }
  prevSnap = snap;
}

function diffSnapshots(a, b) {
  const lines = [];
  const modes = {};
  const nameOf = gid => state.guests[gid]?.name ?? gid;
  for (const [gid, rt] of Object.entries(b.guests)) {
    const old = a.guests[gid];
    if (!old) { lines.push(`${nameOf(gid)} (${gid}) appeared aboard`); continue; }
    if (old.mode !== rt.mode) {
      modes[gid] = [old.mode, rt.mode];
      lines.push(`${nameOf(gid)} (${gid}) changed from ${old.mode} to ${rt.mode}`);
    }
    if (old.battery_pct !== rt.battery_pct && rt.battery_pct != null) {
      lines.push(`${gid} battery reported ${number(old.battery_pct)}% → ${number(rt.battery_pct)}%`);
    }
  }
  for (const gid of Object.keys(a.guests)) if (!b.guests[gid]) lines.push(`${nameOf(gid)} (${gid}) left`);
  for (const k of ["deck", "course", "noise", "emission", "speed_kn"]) {
    if (a.ship[k] !== b.ship[k]) lines.push(`Ship ${k.replace("_kn", " (kn)")}: ${a.ship[k]} → ${b.ship[k]}`);
  }
  if (a.platform.gensets_online !== b.platform.gensets_online) {
    lines.push(`Generators online: ${a.platform.gensets_online} → ${b.platform.gensets_online}`);
  }
  if (a.crew !== b.crew) lines.push(`Crew aboard: ${a.crew} → ${b.crew}`);
  return {from: a.t_s, to: b.t_s, lines, modes};
}

function appliedText(a) {
  if (a?.task == null) return describe(a);  // not a task audit record: show what was reported
  const p = pendingSeen.get(a.task);
  if (!p) return `task ${a.task} (${a.kind}) at t = ${number(a.t_s)} s: this record carries only its number, kind and time`;
  const what = p.kind === "action" && p.action ? actionText(p.action) : `${p.variable} → ${p.to}`;
  return `${what} at t = ${number(a.t_s)} s (task ${a.task}, matched to the pending entry shown earlier in this run)`;
}

// ----------------------------------------------------------------------------- ship view

function deniedFor(gid) {
  const d = shownDecision();
  const deny = (d?.chosen?.actions ?? []).find(a => a.guest === gid && a.kind === "deny");
  if (deny) return deny.facts?.outcome ?? "risk not reported";
  const blocked = (state.execution?.status?.blocked ?? []).find(b => b?.guest === gid && b?.code === "denied_by_engine");
  return blocked ? (blocked.facts?.outcome ?? "risk not reported") : null;
}

function rolesFor(gid) {
  const d = shownDecision();
  const roles = [];
  const denied = deniedFor(gid);
  if (denied) roles.push({key: "denied", icon: "✕", text: `Recovery denied · reported risk: ${denied}`});
  const change = lastChange?.modes?.[gid];
  if (change) roles.push({key: "changed", icon: "↻", text: `Changed: ${change[0]} → ${change[1]}`});
  if (d) {
    if (d.kind === "admission" && d.subject === gid) roles.push({key: "proposed", icon: "＋", text: "Proposed addition"});
    const move = placementChanges(d).find(([g, , how]) => g === gid && how === "moved");
    if (move) {
      const now = state.snapshot.guests[gid].placement;
      const sameSlots = Object.entries(move[1].slots).every(([k, v]) => now.slots[k] === v);
      roles.push({key: "moved", icon: "↔", text: sameSlots ? `Proposed power-line change: ${now.line} → ${move[1].line}` : "Proposed move"});
    }
    if (d.winner === gid) roles.push({key: "winner", icon: "▲", text: "Goes first"});
    if (d.loser === gid) roles.push({key: "yields", icon: "▼", text: "Yields"});
    const contrib = d.binding?.contributors?.[gid];
    if (contrib != null) roles.push({key: "limit", icon: "◆", text: `Counts toward the limit: ${number(contrib)} ${d.binding.unit}`});
    for (const a of d.chosen?.actions ?? []) {
      if (a.guest === gid && a.kind !== "deny") roles.push({key: "affected", icon: "•", text: `Recommended: ${a.kind}${a.to_mode ? ` → ${a.to_mode}` : ""}`});
    }
  }
  return roles;
}

function shipItems() {
  const snap = state.snapshot;
  const items = [];
  for (const slot of state.ship.slots) {
    if (slot.occupied_by) items.push({gid: null, slotId: slot.id, kind: "ship", name: slot.occupied_by, line2: "ship's own equipment", roles: []});
  }
  for (const [gid, rt] of Object.entries(snap.guests)) {
    const m = state.guests[gid];
    for (const [part, slotId] of Object.entries(rt.placement.slots)) {
      const partName = m?.parts?.find(p => p.id === part)?.name;
      items.push({
        gid, slotId, kind: "installed",
        name: part === "body" ? (m?.name ?? gid) : `${m?.name ?? gid}: ${partName ?? part}`,
        line2: `${gid} · ${rt.mode}${rt.battery_pct == null ? "" : ` · ${number(rt.battery_pct)}%`}`,
        roles: rolesFor(gid),
      });
    }
  }
  const d = shownDecision();
  for (const [gid, p, how] of placementChanges(d)) {
    const m = state.guests[gid];
    for (const [part, slotId] of Object.entries(p.slots)) {
      items.push({
        gid, slotId, kind: "proposed",
        name: part === "body" ? (m?.name ?? gid) : `${m?.name ?? gid}: ${part}`,
        line2: `${gid} · ${how === "new" ? "proposed, not installed" : "proposed new position"}`,
        roles: rolesFor(gid),
      });
    }
  }
  const highlighted = items.some(it => it.roles.length);
  for (const it of items) it.dim = highlighted && !it.roles.length && it.gid !== selectedGid;
  return items;
}

function limitSlots() {
  const set = new Set();
  const resource = shownDecision()?.binding?.resource ?? "";
  const part = resource.split(".")[1];
  if (part && state.ship.slots.some(s => s.id === part || s.group === part)) set.add(part);
  return set;
}

function renderShip() {
  const items = shipItems();
  const is3d = view === "3d";
  $("ship").toggleAttribute("hidden", is3d); // an SVG element has no .hidden property
  $("ship3d").hidden = !is3d;
  $("ship3dNote").hidden = !is3d;
  if (is3d && !window.ship3d) {
    $("ship3d").textContent = "The 3D view could not load in this browser (needs WebGL and JavaScript modules). Use the top or side view.";
  } else if (is3d) {
    window.ship3d.draw($("ship3d"), {ship: state.ship, items, highlightSlots: limitSlots(), selected: selectedGid,
      gauges: state.gauges, platform: state.snapshot.platform, binding: shownDecision()?.binding,
      onSelect: gid => { selectedGid = selectedGid === gid ? null : gid; renderShip(); renderSelection(); }});
  } else {
    window.ship3d?.stop();
  }
  if (!is3d) drawShip($("ship"), view, state.ship, state.snapshot.ship, items, limitSlots(), selectedGid, gid => {
    selectedGid = selectedGid === gid ? null : gid;
    if (state.guests[gid] && !state.snapshot.guests[gid] && [...$("card").options].some(o => o.value === gid)) {
      $("card").value = gid;
      fillMass();
      fillCardJson();
    }
    renderShip();
    renderSelection();
    const again = document.querySelector(`#ship [data-gid="${CSS.escape(gid)}"]`);
    if (again) again.focus();
  });
  for (const [id, v] of VIEWS) $(id).setAttribute("aria-pressed", String(v === view));
  const used = new Set(items.flatMap(it => it.roles.map(r => r.key)));
  const LEGEND = [["proposed", "＋", "Proposed addition"], ["moved", "↔", "Proposed move"], ["winner", "▲", "Goes first"],
    ["yields", "▼", "Yields"], ["limit", "◆", "Counts toward the limiting resource"], ["affected", "•", "Recommended action"],
    ["changed", "↻", "Changed since the last update"], ["denied", "✕", "Denied by the engine"]];
  $("legend").replaceChildren(...LEGEND.filter(([k]) => used.has(k)).map(([k, icon, text]) => node("span", `${icon} ${text}`, `chip r-${k}`)));
}

function conditionsText(c) {
  if (!c) return "no special ship conditions";
  const out = [];
  if (c.speed_kn) out.push(`speed ${c.speed_kn.lo ?? 0}–${c.speed_kn.hi ?? "any"} kn`);
  if (c.course) out.push(`${c.course} course`);
  if (c.noise) out.push(`${c.noise} ship`);
  if (c.deck) out.push(`deck ${c.deck}`);
  if (c.emission) out.push(`emission ${c.emission}`);
  if (c.deck_machinery_allowed === false) out.push("no deck machinery running");
  if (c.wind_max_kn != null) out.push(`wind ≤ ${c.wind_max_kn} kn`);
  if (c.sea_state_max != null) out.push(`sea state ≤ ${c.sea_state_max}`);
  if (c.uses_flight_deck) out.push("uses the flight deck");
  if (c.uses_ramp) out.push("uses the stern ramp");
  return out.join(", ") || "no special ship conditions";
}

function renderSelection() {
  const el = $("selection");
  const m = selectedGid ? state.guests[selectedGid] : null;
  el.hidden = !m;
  el.replaceChildren();
  if (!m) return;
  const rt = state.snapshot.guests[selectedGid];
  const head = node("div", undefined, "selhead");
  head.append(node("h3", m.name), node("span", `${m.id} · ${rt ? "installed" : "proposed, not installed"}`, rt ? "badge" : "badge proposed"));
  const close = node("button", "×", "close");
  close.type = "button";
  close.setAttribute("aria-label", "Close equipment details");
  close.onclick = () => { selectedGid = null; renderShip(); renderSelection(); };
  head.append(close);
  el.append(head);
  const dl = node("dl", undefined, "facts compact");
  const fact = (t, v) => dl.append(node("dt", t), node("dd", v));
  fact("What it is", m.capability?.name ?? "not described on its card");
  if (rt) {
    const mode = m.modes.find(x => x.name === rt.mode);
    fact("What it is doing", `${rt.mode}${rt.activity ? ` (${rt.activity})` : ""} · ${mode ? number(mode.power_kw) + " kW declared" : "power not declared"}${rt.battery_pct == null ? "" : ` · battery ${number(rt.battery_pct)}%`}`);
    fact("Needs while doing it", conditionsText(mode?.conditions));
  } else {
    fact("What it is doing", "Not aboard: this is a proposal only");
    fact("Biggest demand", `${number(Math.max(...m.modes.map(x => x.power_kw)))} kW in its most demanding mode`);
  }
  const total = m.mass_t + (m.parts ?? []).reduce((s, p) => s + p.mass_t, 0);
  fact("Restrictions", `${number(total)} t · ${m.cooling} cooled · fits ${m.allowed.join(", ")}${m.excludes?.length ? ` · ${m.excludes.length} exclusion rule(s)` : ""}`);
  const roles = rolesFor(m.id);
  if (roles.length) fact("In this result", roles.map(r => `${r.icon} ${r.text}`).join(" · "));
  el.append(dl);
}

// ------------------------------------------------------------------ short explanation

const missionUnderway = () => state.scenario === "B" && state.next_t_s > 0;
// Recovery is ready and only the operator's confirmation is missing: that is the headline.
const awaitingConfirmation = () => Boolean(state.execution?.enabled)
  && (state.crew_policy?.blockers ?? []).includes("confirmation_pending") && state.snapshot.ship.deck === "clear";

// Mode changes the simulation carried out, when the latest update brought no decision of its own.
function carriedOut() {
  if (state.decision || !state.execution?.enabled || !lastChange) return [];
  if (shownDecision()?.t_s > lastChange.from) return [];  // a decision inside the window explains it
  return Object.entries(lastChange.modes);
}

function storyVerdict(d) {
  if (awaitingConfirmation()) return ["Confirm the deck is clear", "s-cond", "!"];
  if (!d && missionUnderway()) return ["No conflicts so far", "s-ok", "✓"];
  if (!d) return ["Nothing evaluated yet", "s-info", "i"];
  const denied = (d.chosen?.actions ?? []).find(a => a.kind === "deny");
  if (denied) return [`Recovery of ${denied.guest} denied`, "s-bad", "✕"];
  if (carriedOut().length) return ["Ship updated", "s-ok", "↻"];
  const [label, cls, icon] = VERDICTS[d.verdict] ?? [d.verdict, "s-info", "i"];
  if (d.kind === "admission" && d.verdict === "no") return ["Proposal refused (ship unaffected)", cls, icon];
  if (d.kind === "arbitration" && d.winner) return [`${state.guests[d.winner]?.name ?? d.winner} goes first`, "s-ok", "▲"];
  return [label, cls, icon];
}

function happenedText(d) {
  if (!d) return state.scenario === "B" ? "The mission timeline is ready. Nothing has been decided yet." : "Nothing has been evaluated yet.";
  const name = gid => `${state.guests[gid]?.name ?? gid} (${gid})`;
  if (d.kind === "admission") return `You proposed adding ${name(d.subject)}.`;
  if (d.kind === "arbitration") {
    const [challenger] = d.subject.split(" vs ");
    const battery = state.snapshot.guests[challenger]?.battery_pct;
    return `${name(challenger)} needs to come back aboard${battery == null ? "" : ` (battery ${number(battery)}%)`}, and that conflicts with other work: ${d.subject}.`;
  }
  if (d.kind === "shedding") return `Power and cooling were re-checked (${state.snapshot.platform.gensets_online} generator(s) online).`;
  return `${KINDS[d.kind] ?? d.kind}: ${d.subject}`;
}

function recommendText(d) {
  if (!d) return "—";
  const denied = (d.chosen?.actions ?? []).find(a => a.kind === "deny");
  if (denied) {
    const risk = (d.costs ?? []).filter(c => c.guest === denied.guest).map(c => c.what).join("; ");
    return `Do not recover ${denied.guest}. Reported risk: ${denied.facts?.outcome ?? "not reported"}${risk ? ` (${risk})` : ""}. This is not a safe continued flight.`;
  }
  const b = d.binding;
  const limit = b ? `${RESOURCES[b.cls] ?? b.cls}: ${number(b.demand)} of ${number(b.capacity)} ${b.unit} (${b.corner ? `checked for the proposal in its worst case: ${b.corner}` : "current snapshot"})` : null;
  if (d.kind === "admission") {
    if (d.verdict === "no") return `Refuse it. Limited by ${limit ?? "an unreported constraint"}.`;
    if (d.verdict === "yes_if") return `Accept only if ${(d.chosen?.actions ?? []).map(actionText).join("; ")}. Tightest: ${limit}.`;
    if (d.verdict === "not_now") return `Not now${d.next_window ? `: ${d.next_window}` : ""}.`;
    return `Accept it${limit ? `. Tightest: ${limit}` : ""}.`;
  }
  if (d.kind === "arbitration" && d.winner) {
    const acts = (d.chosen?.actions ?? []).map(a => `${a.guest} ${a.kind}${a.to_mode ? ` → ${a.to_mode}` : ""}`).join(", ");
    return `${d.winner} goes first; ${d.loser} yields (${acts}).`;
  }
  if (d.kind === "shedding") {
    const acts = (d.chosen?.actions ?? []).filter(a => a.kind !== "keep").map(actionText);
    return acts.length ? `Reduce load: ${acts.join("; ")}.` : "No equipment needs to be reduced.";
  }
  return VERDICTS[d.verdict]?.[0] ?? d.verdict;
}

// A few words for the collapsed view; the full sentences live in the drop-down.
function headlineText(d) {
  if (awaitingConfirmation()) return "Recovery is ready. An operator must confirm the deck is clear before it starts.";
  if (!d && missionUnderway()) return "Everything fits. Keep advancing, or report the drone battery.";
  if (!d) return state.scenario === "B" ? "Advance time below to start the mission." : "Pick equipment below, then press “Check if it fits”.";
  const denied = (d.chosen?.actions ?? []).find(a => a.kind === "deny");
  if (denied) return `Keep ${denied.guest} off the deck. Reported risk: ${denied.facts?.outcome ?? "not reported"}.`;
  const done = carriedOut();
  if (done.length) return done.map(([gid, [from, to]]) => `${state.guests[gid]?.name ?? gid} (${gid}) switched from ${from} to ${to}`).join(". ") + ".";
  const b = d.binding;
  const tightest = b ? RESOURCES[b.cls] ?? b.cls : null;
  const actions = (d.chosen?.actions ?? []).filter(a => a.kind !== "keep");
  if (d.kind === "admission") {
    if (d.verdict === "no") return `Does not fit${tightest ? `: ${tightest} limit` : ""}.`;
    if (d.verdict === "yes_if") return `Only if ${actions.map(plainAction).join(", and ")}.`;
    if (d.verdict === "not_now") return d.next_window ? `Try again: ${d.next_window}.` : "Try again later.";
    return "Within all eight limits.";
  }
  if (d.kind === "arbitration" && d.winner) return `${state.guests[d.loser]?.name ?? d.loser} (${d.loser}) waits.`;
  if (d.kind === "shedding") return actions.length ? `${actions.map(plainAction).join(", and ")}.` : "All equipment stays on.";
  return VERDICTS[d.verdict]?.[0] ?? d.verdict;
}

function nextText(d) {
  const exec = state.execution;
  const status = exec?.status;
  if (!d) return state.scenario === "B" ? "Advance time, or report the drone battery, in Run the mission." : "Pick equipment, then press “Check if it fits”.";
  if (d.kind === "admission") {
    return d.verdict === "no" ? "Nothing to do. The equipment already aboard is not affected." : "Nothing is installed: this demo has no installation step. The result is advice.";
  }
  if ((d.chosen?.actions ?? []).some(a => a.kind === "deny")) return "Nothing will be simulated for this recovery: the engine denied it and accepted the reported risk.";
  if (!exec?.enabled) return "Recommendation only. To simulate it, turn on “Let the simulation carry out the engine’s actions” in Run the mission.";
  if (!status) return "Execution status was not reported by the server.";
  const pending = status.pending ?? [];
  const blockers = state.crew_policy?.blockers ?? [];
  if (blockers.includes("confirmation_pending") && state.snapshot.ship.deck === "clear") {
    return "The deck is clear. An operator must confirm it before recovery can start (Confirm deck is clear).";
  }
  if (pending.length) {
    const due = pending.map(p => p?.due_s).filter(Number.isFinite);
    return `${pending.length} preparation step(s) are pending${due.length ? `; the next is due at t = ${Math.min(...due)} s` : ""}. Advance time.`;
  }
  return "Nothing is pending for this plan.";
}

function renderStory() {
  const d = shownDecision();
  const [label, cls, icon] = storyVerdict(d);
  const banner = node("div", undefined, `verdict small ${cls}`);
  const words = node("div");
  words.append(node("strong", label), node("span", headlineText(d), "headline"));
  banner.append(node("span", icon, "icon"), words);
  $("storyVerdict").replaceChildren(banner);
  const denial = (d?.chosen?.actions ?? []).some(a => a.kind === "deny");
  renderLimitMeter(awaitingConfirmation() || carriedOut().length || denial ? null : d);
  $("storyTitle").textContent = state.mode === "SAMPLE" ? "Sample fixture, not this session" : "";
  $("storyMore").hidden = !d;
  const dl = $("storyRows");
  dl.replaceChildren();
  const row = (t, ...content) => {
    const dd = node("dd");
    dd.append(...content);
    dl.append(node("dt", t), dd);
  };
  row("What happened", happenedText(d), ...(d && !state.decision ? [node("span", "Latest recorded decision; the last update produced none of its own.", "why")] : []));
  const rec = node("span", recommendText(d));
  const why = d?.rationale ? node("span", d.rationale.length > 170 ? `${d.rationale.slice(0, 169)}…` : d.rationale, "why") : null;
  row("Engine recommends", rec, ...(why ? [why] : []));
  row("What happens next", nextText(d));
  if (lastChange && (lastChange.lines.length || lastChange.applied.length)) {
    const ul = node("ul");
    ul.append(...lastChange.lines.slice(0, 4).map(t => node("li", t)), ...lastChange.applied.slice(-3).map(a => node("li", `Simulated: ${appliedText(a)}`)));
    row("What actually changed", ul, node("span", `Snapshots t = ${number(lastChange.from)} s → ${number(lastChange.to)} s; seconds in between are not shown.`, "why"));
  } else {
    row("What actually changed", state.execution?.enabled ? "Nothing on the simulated ship since the last update." : "Nothing on the ship: results are advice only.");
  }
  $("confirmDeck").classList.toggle("primary", awaitingConfirmation());
}

// The one resource that decided the result, as a single bar. Over 100% means it does not fit.
function renderLimitMeter(d) {
  const el = $("limitMeter");
  const b = d?.binding;
  el.hidden = !b || !(b.capacity > 0);
  el.replaceChildren();
  if (el.hidden) return;
  const ratio = b.demand / b.capacity;
  const head = node("div", undefined, "lmhead");
  head.append(node("span", `Tightest: ${RESOURCES[b.cls] ?? b.cls}`), node("b", `${number(b.demand)} / ${number(b.capacity)} ${b.unit}`));
  const bar = node("div", undefined, `lmbar${ratio > 1 ? " over" : ratio > 0.8 ? " warn" : ""}`);
  const fill = node("span");
  fill.style.width = `${Math.min(100, ratio * 100)}%`;
  bar.append(fill);
  bar.setAttribute("role", "img");
  bar.setAttribute("aria-label", `${Math.round(ratio * 100)}% of capacity`);
  const where = b.corner ? "in the worst case" : "right now";
  const foot = node("small", b.cls === "time"
    ? `Needs ${number(b.demand)} ${b.unit}; ${number(b.capacity)} ${b.unit} available.`
    : `${ratio > 1 ? "Over by" : "Spare"} ${number(Math.abs(b.capacity - b.demand))} ${b.unit} ${where}.`);
  foot.title = b.corner ? `Worst case: ${b.corner}` : "";
  el.append(head, bar, foot);
}

// ----------------------------------------------------------- details below the fold

function tzLabel(min) {
  const a = Math.abs(min);
  return `UTC${min < 0 ? "−" : "+"}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
}

function renderEnvironment() {
  const el = $("environmentView");
  el.replaceChildren();
  const env = state.environment;
  if (!env || typeof env !== "object") return;
  const row = (label, value) => el.append(node("dt", label), node("dd", value));
  const c = env.configuration ?? {};
  const label = [...$("environment").options].find(o => o.value === c.source)?.textContent;
  row("Weather source", label ?? c.source ?? "not reported");
  if (c.source && c.source !== "scenario") {
    row("Scenario start", `${c.scenario_start ?? "not reported"}${c.timezone_offset_min == null ? "" : ` (${tzLabel(c.timezone_offset_min)})`}`);
    row("Layered water / stale allowed", `${c.layered_water ? "yes" : "no"} / ${c.allow_stale ? "yes" : "no"}`);
  }
  const p = env.provenance;
  row("Provenance", p
    ? `${p.kind ?? "unknown"} cache${p.stale ? " · STALE" : ""} · retrieved ${p.retrieved_at ?? "time not reported"} · sources: ${Object.keys(p.sources ?? {}).join(", ") || "none listed"}`
    : "none (no cached forecast in use)");
  const dd = node("dd");
  const ops = Object.entries(env.windows ?? {});
  if (!ops.length) dd.append(node("span", "none computed", "na"));
  for (const [op, list] of ops) {
    const text = Array.isArray(list) && list.length
      ? list.map(w => `t = ${number(w.start_s)}–${number(w.end_s)} s (latest start ${number(w.latest_start_s)} s)`).join("; ")
      : "no eligible window in the forecast";
    dd.append(node("div", `${op.replace("_", " ")}: ${text}`));
  }
  el.append(node("dt", "Installation windows (conditional planning eligibility only; not permission)"), dd);
}

function renderPreviousRuns() {
  const runs = Array.isArray(state.previous_runs) ? state.previous_runs : [];
  $("previousRuns").hidden = runs.length === 0;
  $("previousCount").textContent = runs.length;
  $("previousList").replaceChildren(...runs.map(r => {
    const li = node("li");
    const a = node("a", `Run ${r.run} evidence`);
    a.href = `/api/sessions/${state.id}/evidence?run=${encodeURIComponent(r.run)}`;
    a.setAttribute("download", `poseidonos-evidence-run-${r.run}.json`);
    li.append(a, ` · hash ${short(r.final_hash)}`);
    li.title = r.final_hash ?? "";
    return li;
  }));
}

function capabilityReason(op) {
  if (serverOps === undefined) return "checking what this server offers";
  if (serverOps === null) return "the server's capabilities could not be read";
  if (!serverOps.includes(op)) return "this server does not offer it yet";
  if (!WIRED.has(op)) return "the server offers it, but this page is not wired to its published payload yet";
  return null;
}

function applyCapabilities() {
  document.querySelectorAll("[data-capability]").forEach(el => {
    const reason = capabilityReason(el.dataset.capability);
    el.disabled = reason !== null;
    el.title = reason ? `Unavailable: ${reason}` : "";
  });
  $("capabilityNotes").replaceChildren(...Object.entries(RUNTIME)
    .filter(([op]) => capabilityReason(op))
    .map(([op, label]) => node("li", `${label} unavailable: ${capabilityReason(op)}.`)));
  const env = capabilityReason("environment");
  $("environmentStatus").textContent = env
    ? `Unavailable: ${env}. Installation-window eligibility: not reported.`
    : "";
  if (state) renderMissionStatus();
}

async function loadCapabilities() {
  try {
    const response = await fetch("/openapi.json");
    if (!response.ok) throw new Error();
    const schema = await response.json();
    serverOps = schema?.components?.schemas?.Command?.properties?.op?.enum ?? [];
  } catch { serverOps = null; }
  applyCapabilities();
}

// Five things a mission operator must not confuse, plus the current simulated state.
// Each row shows only what the server reported; a verdict never implies execution or permission.
function describe(item) {
  if (item == null) return "unspecified";
  if (typeof item !== "object") return String(item);
  if (item.kind && item.guest) {
    const reason = item.reason ?? item.blocker ?? item.why;
    return `${actionText(item)}${reason ? ` · ${reason}` : ""}`;
  }
  return Object.entries(item)
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ");
}

function renderMissionStatus() {
  const el = $("missionStatus");
  const snap = state.snapshot;
  const rec = ["arbitration", "shedding"].includes(shownDecision()?.kind) ? shownDecision()
    : [...state.history].reverse().map(h => h.decision).find(d => ["arbitration", "shedding"].includes(d.kind));
  const exec = state.execution;
  const status = exec?.status && typeof exec.status === "object" ? exec.status : null;
  const crew = state.crew_policy;
  const na = text => node("span", text, "na");
  const items = values => {
    const ul = node("ul");
    ul.append(...values.map(v => node("li", v)));
    return ul;
  };
  const row = (label, cls, ...content) => {
    el.append(node("dt", label, cls));
    const dd = node("dd");
    dd.append(...content);
    el.append(dd);
  };
  const listOr = (values, empty) =>
    Array.isArray(values) && values.length ? items(values.map(describe)) : na(empty);
  el.replaceChildren();
  row("Current simulated state", "k-state", `t = ${number(snap.t_s)} s · ${snap.platform.gensets_online} generator(s) · deck ${snap.ship.deck} · ` +
    Object.entries(snap.guests).map(([id, rt]) => `${id} ${rt.mode}${rt.battery_pct == null ? "" : ` (${number(rt.battery_pct)}%)`}`).join(", "));

  if (rec) {
    const [label] = VERDICTS[rec.verdict] ?? [rec.verdict];
    const lines = [`${KINDS[rec.kind] ?? rec.kind}: ${label}`];
    if (rec.winner) lines.push(`Goes first: ${rec.winner}; yields: ${rec.loser ?? "none"}`);
    for (const c of rec.costs ?? []) lines.push(`${c.guest === rec.loser ? "Losing mission cost" : "Cost"}: ${c.guest} ${number(c.amount)} ${c.unit} (${c.what})`);
    for (const a of rec.chosen?.actions ?? []) lines.push(`Recommended: ${actionText(a)}`);
    row("Engine recommendation", "k-rec", items(lines));
  } else {
    row("Engine recommendation", "k-rec", na("No D3/D4 recommendation yet: report the battery, trip a generator or ask the engine"));
  }

  const prep = node("div");
  const requested = (rec?.state_requests ?? []).map(r => `${r.variable} → ${r.to} ${requestTime(r.start_s, state.state_request_time_base)} for ${number(r.duration_s)} s`);
  const part = (title, content) => {
    const div = node("div");
    div.append(node("strong", title), content);
    prep.append(div);
  };
  part("Requested by the engine: ", requested.length ? items(requested) : na("none"));
  part("Pending in the simulator: ",
    status ? listOr(status.pending, "none") : na(exec ? "not reported" : "not reported by this server"));
  row("Requested / pending preparation", "k-prep", prep);

  if (crew && typeof crew === "object") {
    const lines = [
      `Confirmation required from: ${crew.confirm ?? "not reported"}`,
      `Confirmation status: ${crew.confirmation ?? "not reported"}`,
      `Recovery permitted by crew policy: ${crew.permitted === true ? "yes" : crew.permitted === false ? "no" : "not reported"}`,
    ];
    if (crew.confirm === "automatic") lines.push("Based on simulated deck-camera evidence (no real camera)");
    if (Array.isArray(crew.blockers) && crew.blockers.length) lines.push(`Blockers: ${crew.blockers.join(", ")}`);
    for (const r of crew.requests ?? []) {
      lines.push(`Crew request: ${r?.variable ? `${r.variable} → ${r.to} ${requestTime(r.start_s, crew.request_time_base)} for ${number(r.duration_s)} s` : describe(r)}`);
    }
    row("Operator confirmation", "k-confirm", items(lines));
  } else {
    row("Operator confirmation", "k-confirm", na("Not reported by this server"));
  }

  let applied;
  if (status) applied = Array.isArray(status.applied) && status.applied.length ? items(status.applied.map(appliedText)) : na("none applied yet");
  else if (exec && exec.enabled === false) applied = na("None: simulated execution is off; decisions are recorded only");
  else if (exec) applied = na("Not reported: execution status unavailable");
  else applied = na(serverOps && !serverOps.includes("execution")
    ? "None: this server records decisions without applying them" : "Not reported");
  row("Applied simulated action", "k-applied", applied);
  row("Blocked action and reason", "k-blocked", status ? listOr(status.blocked, "none") : na("Not reported"));
}

function renderAboard(snap) {
  $("guestStates").replaceChildren(...Object.entries(snap.guests).map(([id, rt]) => {
    const li = node("li");
    const name = node("span");
    name.append(node("b", state.guests[id]?.name ?? id), ` ${id}`);
    const slots = Object.values(rt.placement.slots).join(" + ");
    const battery = rt.battery_pct == null ? "" : ` · battery ${number(rt.battery_pct)}%`;
    li.append(name, node("span", `${rt.mode} · ${slots}${battery}`, "where"));
    return li;
  }));
}

function renderGauges() {
  const limiting = shownDecision()?.binding?.cls;
  $("gauges").replaceChildren(...state.gauges.map(g => {
    const box = node("article", undefined, "gauge");
    const title = node("h3", RESOURCES[g.cls] ?? g.cls);
    box.append(title);
    if (g.status !== "available") {
      box.classList.add("unavailable");
      box.append(node("p", "No data", "value"), node("small", g.source));
      return box;
    }
    const ratio = g.capacity > 0 ? g.demand / g.capacity : (g.demand > 0 ? Infinity : 0);
    if (ratio > 1) box.classList.add("over"); else if (ratio > 0.8) box.classList.add("warn");
    if (g.cls === limiting) {
      box.classList.add("limiting");
      box.title = "Binding constraint of the current result, checked in its own envelope corner; the bar shows this snapshot.";
      title.append(node("span", "◆ limits the result", "flag"));
    }
    box.append(node("p", `${number(g.demand)} / ${number(g.capacity)} ${g.unit}`, "value"));
    const bar = node("div", undefined, "bar");
    const fill = node("span");
    fill.style.width = `${Math.min(100, ratio * 100)}%`;
    bar.append(fill);
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", `${Math.round(ratio * 100)}% of capacity in use`);
    box.append(bar);
    box.title ||= `${ratio > 1 ? "Over by" : "Free"} ${number(Math.abs(g.capacity - g.demand))} ${g.unit} · one-generator capacity ${number(g.facts?.degraded_capacity)} ${g.unit}`;
    return box;
  }));
}

// Headline wording for one action, in everyday words; the rows below keep the exact terms.
const PLAIN = {
  keep: "stays as it is", throttle: "runs at reduced power", standby: "goes to standby",
  shed: "is switched off", remove: "is taken off the ship", move: "moves to another spot",
  defer: "waits", pause: "pauses", suspend: "is suspended", proceed: "goes ahead",
  deny: "is refused", reserve: "has capacity held for it", retask: "is given another task",
};
function plainAction(a) {
  const who = `${state.guests[a.guest]?.name ?? a.guest} (${a.guest})`;
  const when = a.when === "degraded" ? " if a generator fails" : "";
  if (a.kind === "interlock") {
    const [og, om] = (a.other ?? "").split(".");
    const other = `${state.guests[og]?.name ?? og}${om ? ` is ${om}` : ""}`;
    return `${who} does not ${a.to_mode ?? "run"} while ${other}${when}`;
  }
  return `${who} ${PLAIN[a.kind] ?? a.kind}${when}`;
}

function actionText(a) {
  const when = a.when === "degraded" ? " (only with one generator down)" : "";
  const delay = a.delay_min ? ` after ${number(a.delay_min)} min` : "";
  if (a.kind === "interlock") return `${a.guest} ${a.to_mode ?? ""} must not run at the same time as ${a.other}${when}`;
  return `${a.guest}: ${a.kind}${a.to_mode ? ` → ${a.to_mode}` : ""}${a.other ? ` (${a.other})` : ""}${delay}${when}`;
}

// Placement entries that differ from what is installed now: the new card, or a moved guest.
function placementChanges(d) {
  return Object.entries(d?.placement ?? {}).filter(([gid, p]) => {
    const now = state.snapshot.guests[gid]?.placement;
    const same = (a, b) => Object.keys(a).length === Object.keys(b).length && Object.entries(a).every(([k, v]) => b[k] === v);
    return !now || !same(now.slots, p.slots) || now.line !== p.line;
  }).map(([gid, p]) => [gid, p, state.snapshot.guests[gid] ? "moved" : "new"]);
}

// Request times are rendered on the basis the server declares; never assumed.
function requestTime(start, base) {
  if (base === "absolute_simulation_seconds") return `at t = ${number(start)} s`;
  if (base === "snapshot_relative") return `at +${number(start)} s from this snapshot`;
  return `starting ${number(start)} s (time basis not reported)`;
}

function list(title, items, parent) {
  if (!items.length) return;
  parent.append(node("h4", title));
  const ul = node("ul");
  ul.append(...items.map(text => node("li", text)));
  parent.append(ul);
}

function renderDecision(d) {
  const el = $("decision");
  el.replaceChildren();
  if (!d) {
    const unavailable = /unavailable/i.test(state.notice ?? "");
    const banner = node("div", undefined, `verdict ${unavailable ? "s-na" : "s-info"}`);
    banner.append(node("span", unavailable ? "—" : "i", "icon"));
    const text = node("div");
    text.append(node("h3", unavailable ? "Result unavailable" : "No current decision"),
      node("p", unavailable ? state.notice : "Pick equipment, then press “Check if it fits”."));
    banner.append(text);
    el.append(banner);
    return;
  }
  const [label, cls, icon] = VERDICTS[d.verdict] ?? [d.verdict, "s-info", "i"];
  const banner = node("div", undefined, `verdict ${cls}`);
  banner.append(node("span", icon, "icon"));
  const head = node("div");
  head.append(node("h3", label), node("p", `${KINDS[d.kind] ?? d.kind} · ${d.subject} · engine verdict “${d.verdict}” · ${state.mode === "SAMPLE" ? "SAMPLE fixture" : "computed (LIVE)"}`));
  banner.append(head);
  el.append(banner);

  const facts = node("dl", undefined, "facts");
  const fact = (term, value) => { if (value != null && value !== "") facts.append(node("dt", term), node("dd", value)); };
  const b = d.binding;
  if (b) {
    const over = b.demand > b.capacity;
    fact("Limiting constraint", `${RESOURCES[b.cls] ?? b.cls} (${b.resource}): ${number(b.demand)} of ${number(b.capacity)} ${b.unit} (${over ? "over by" : "spare"} ${number(Math.abs(b.capacity - b.demand))} ${b.unit})`);
    fact("Checked at", b.corner ? `${b.corner} (the proposal's worst case, not the current snapshot)` : "current snapshot");
  } else {
    fact("Limiting constraint", "not reported by this decision");
  }
  fact("Goes first", d.winner);
  fact("Yields", d.loser);
  if (d.holds_one_genset_down != null) fact("Holds with one generator down", d.holds_one_genset_down ? "yes" : "no");
  fact("Next window", d.next_window);
  el.append(facts);

  const details = node("div");
  list(d.kind === "admission" ? "Conditions" : "Recommended actions (not executed by this result)", (d.chosen?.actions ?? []).map(actionText), details);
  list("If a generator trips", (d.contingency ?? []).map(actionText), details);
  const changes = placementChanges(d);
  list("Proposed placement", changes.map(([gid, p, how]) =>
    `${gid} (${how}): ${Object.entries(p.slots).map(([part, slot]) => `${part} → ${slot}`).join(", ")} · ${p.line} power line`), details);
  if (Object.keys(d.placement ?? {}).length && changes.length < Object.keys(d.placement).length) {
    details.append(node("p", "Installed equipment not listed stays where it is.", "note"));
  }
  list("Costs", (d.costs ?? []).map(c => `${c.guest}: ${number(c.amount)} ${c.unit} (${c.what})`), details);
  list("Requested ship state changes", (d.state_requests ?? []).map(r =>
    `${r.variable} → ${r.to} ${requestTime(r.start_s, state.state_request_time_base)} for ${number(r.duration_s)} s`), details);
  el.append(details);

  el.append(node("h4", "Engine explanation"), node("p", d.rationale || "No rationale provided", "rationale"));
  el.append(node("p", "A recommendation is not an action: see Mission status for what the simulation actually applied.", "note"));

  const tech = node("details", undefined, "tech");
  tech.append(node("summary", "Technical details"));
  tech.append(node("p", `Response latency: ${d.latency_ms == null ? "not measured" : number(d.latency_ms) + " ms"}`, "note"));
  for (const [title, value] of [
    ["Binding constraint", d.binding], ["Alternatives considered", d.alternatives ?? []],
    ["All checks", d.checks ?? []],
  ]) {
    tech.append(node("h4", title), node("pre", json(value)));
  }
  el.append(tech);
}

function renderLog() {
  const items = [...state.history].reverse().map(entry => {
    const d = entry.decision;
    const li = node("li");
    const why = node("span", d.rationale, "why");
    why.title = d.rationale;  // clamped to one line; full text on hover
    li.append(node("b", `${KINDS[d.kind] ?? d.kind} · ${d.subject} · ${VERDICTS[d.verdict]?.[0] ?? d.verdict}`),
      entry.mode === "SAMPLE" ? " (SAMPLE)" : "", why);
    return li;
  });
  const unavailable = [...state.unavailable].reverse().map(u =>
    node("li", `t = ${u.t_s} s · ${u.events.join(", ")} · ${u.status}. No actions applied.`));
  $("log").replaceChildren(...unavailable, ...items);
  if (!items.length && !unavailable.length) $("log").append(node("li", "No decisions yet."));
}

function fillMass() {
  $("value").value = state.guests[$("card").value]?.mass_t ?? "";
}

function fillCardJson() {
  $("cardJson").value = state.guests[$("card").value] ? json(state.guests[$("card").value]) : "";
}

// ------------------------------------------------------------------------- controls

const TITLES = {
  evaluate: "Can this equipment come aboard?",
  mission: "Who goes first when resources run short?",
};
function selectTab(name) {
  $("pageTitle").textContent = TITLES[name];
  for (const tab of ["evaluate", "mission"]) {
    const on = tab === name;
    $(`tab-${tab}`).setAttribute("aria-selected", String(on));
    $(`tab-${tab}`).tabIndex = on ? 0 : -1;
    $(tab).hidden = !on;
  }
}
for (const tab of ["evaluate", "mission"]) {
  $(`tab-${tab}`).onclick = () => selectTab(tab);
  $(`tab-${tab}`).onkeydown = event => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const next = tab === "evaluate" ? "mission" : "evaluate";
    selectTab(next);
    $(`tab-${next}`).focus();
  };
}
for (const [id, v] of VIEWS) {
  $(id).onclick = () => { view = v; if (state) renderShip(); };
}

$("admit").onsubmit = event => {
  event.preventDefault();
  command({op: "admit", card: $("card").value, value: Number($("value").value)});
};
$("card").onchange = () => {
  fillMass();
  fillCardJson();
  selectedGid = $("card").value || null;
  renderShip();
  renderSelection();
};
$("loadCardJson").onclick = fillCardJson;
$("importCard").onsubmit = event => {
  event.preventDefault();
  command({op: "import_card", card_json: $("cardJson").value});
};
for (const id of ["mode", "scenario", "priority", "crew"]) {
  $(id).onchange = () => {
    if (id === "scenario") selectTab($(id).value === "B" ? "mission" : "evaluate");
    command({op: id, [id]: id === "crew" ? Number($(id).value) : $(id).value});
  };
}
$("reset").onclick = () => { $("value").value = ""; $("cardJson").value = ""; selectedGid = null; selectTab("evaluate"); command({op: "reset"}); };
document.querySelectorAll("[data-op]").forEach(b => b.onclick = () => command({op: b.dataset.op}));
document.querySelectorAll("[data-event]").forEach(b => b.onclick = () => command({
  op: "event", event: b.dataset.event, battery_pct: Number($("battery").value)
}));
$("step").onclick = () => command({op: "step"});
$("forward").onclick = () => command({op: "step", ticks: Number($("ticks").value)});
$("replay").onclick = () => command({op: "replay"});
$("execution").onchange = () => command({op: "execution", enabled: $("execution").checked, fresh_run: true});
$("confirmDeck").onclick = () => command({op: "confirm_deck", by: $("confirmBy").value.trim() || "deck officer"});
$("environment").onchange = () => { $("envCache").disabled = $("environment").value === "scenario"; };
function offsetString(min) {
  const a = Math.abs(min);
  return `${min < 0 ? "-" : "+"}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
}
$("envForm").onsubmit = event => {
  event.preventDefault();
  if ($("environment").value === "scenario") {
    command({op: "environment", fresh_run: true, environment: {source: "scenario"}});
    return;
  }
  const tz = Number($("envTz").value);
  command({op: "environment", fresh_run: true, environment: {
    source: $("environment").value,
    scenario_start: `${$("envStart").value}:00${offsetString(tz)}`,
    layered_water: $("envLayered").checked,
    timezone_offset_min: tz,
    allow_stale: $("envStale").checked,
  }});
};

async function start() {
  try {
    const response = await fetch("/api/sessions", {method: "POST"});
    if (!response.ok) throw new Error("Cannot create local session");
    render(await response.json());
    loadCapabilities();
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${scheme}//${location.host}/ws/${state.id}`);
    socket.onmessage = e => {
      const update = JSON.parse(e.data);
      if (update.error) $("error").textContent = update.error;
      else render(update);
    };
    socket.onclose = () => $("transport").textContent = "WebSocket unavailable · HTTP controls remain active";
  } catch (error) { $("error").textContent = error.message; }
}
start();
