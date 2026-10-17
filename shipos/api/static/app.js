"use strict";
const $ = id => document.getElementById(id);
let state, socket;
const node = (tag, text, cls) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
};
const number = x => Number.isFinite(x) ? Number(x.toFixed(3)).toString() : "unavailable";
const json = x => JSON.stringify(x, null, 2);
const options = (el, items, selected) => {
  el.replaceChildren(...items.map(([value, label]) => {
    const option = node("option", label);
    option.value = value;
    return option;
  }));
  if (selected !== undefined) el.value = selected;
};
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
    $("error").textContent = error.message;
    if (state) syncControls();
  }
}
function syncControls() {
  $("mode").value = state.mode;
  $("scenario").value = state.scenario;
  $("crew").value = state.snapshot.crew;
  options($("priority"), state.priorities.map(p => [p, p]), state.priority_name);
}
function render(data) {
  const oldCard = $("card").value;
  const oldMass = state?.guests?.[oldCard]?.mass_t;
  state = data;
  syncControls();
  $("modeBadge").textContent = state.mode;
  document.body.dataset.mode = state.mode;
  $("notice").textContent = state.notice;
  $("export").href = `/api/sessions/${state.id}/evidence`;
  $("jsonl").href = `/api/sessions/${state.id}/replay.jsonl`;
  $("replayStatus").textContent = state.replay ?
    `Execution replay: ${state.replay.byte_identical ? "byte-identical" : "DIFFERS"} · ${state.replay.records} records · ${state.replay.final_hash}` :
    `Next tick ${state.next_t_s} s · horizon ${state.horizon_s} s · final hash ${state.final_hash}`;
  const snap = state.snapshot;
  $("guestStates").textContent = Object.entries(snap.guests).map(([id, rt]) =>
    `${id}: ${rt.mode}${rt.battery_pct == null ? "" : " · battery " + number(rt.battery_pct) + "%"}`
  ).join(" | ");
  $("stateStrip").textContent = `t = ${number(snap.t_s)} s · ${snap.ship.speed_kn} kn · ${snap.ship.course} course · ${snap.ship.noise} · deck ${snap.ship.deck} · ${snap.platform.gensets_online} gensets · ${snap.env.seawater_c} °C seawater · ${snap.crew} crew`;
  const proposed = Object.values(state.guests).filter(g => !snap.guests[g.id]);
  const selected = proposed.some(g => g.id === oldCard) ? oldCard : proposed[0]?.id;
  options($("card"), proposed.map(g => [g.id, `${g.id} · ${g.name}`]), selected);
  if (selected !== oldCard || oldMass !== state.guests[selected]?.mass_t || !$("value").value) fillMass();
  if (!$("cardJson").value) fillCardJson();
  $("admit").querySelector("button").disabled = !selected;
  $("gauges").replaceChildren(...state.gauges.map(g => {
    const box = node("article", undefined, "gauge");
    box.append(node("h3", g.cls.toUpperCase()));
    if (g.status !== "available") box.append(node("p", `Unavailable${g.unit ? " (" + g.unit + ")" : ""}`), node("small", g.source));
    else {
      box.append(node("strong", `${number(g.demand)} / ${number(g.capacity)} ${g.unit}`));
      const meter = node("meter");
      meter.min = 0; meter.max = Math.max(g.capacity, g.demand, 1); meter.value = g.demand;
      meter.setAttribute("aria-label", `${g.cls} committed`);
      box.append(meter, node("p", `Free ${number(g.capacity - g.demand)} ${g.unit}`));
      box.append(node("small", `Degraded capacity: ${number(g.facts?.degraded_capacity)} ${g.unit}`));
      box.append(node("small", `${g.resource} · ${state.mode}`));
    }
    return box;
  }));
  renderLayout();
  renderDecision(state.decision);
  $("log").replaceChildren(...[...state.history].reverse().map(entry => {
    const d = entry.decision;
    return node("li", `[${entry.mode}] ${d.kind} · ${d.subject} · ${d.verdict} · ${d.rationale}`);
  }));
  for (const unavailable of [...state.unavailable].reverse()) {
    $("log").prepend(node("li", `LIVE t=${unavailable.t_s} s · ${unavailable.events.join(", ")} · ${unavailable.status}. No actions applied.`));
  }
}
function fillMass() {
  $("value").value = state.guests[$("card").value]?.mass_t ?? "";
}
function renderDecision(d) {
  const el = $("decision");
  el.replaceChildren();
  if (!d) { el.textContent = "No current decision. " + state.notice; return; }
  el.append(node("h3", `${state.mode} · ${d.verdict} · ${d.subject}`));
  el.append(node("p", `Latency: ${d.latency_ms == null ? "unavailable" : number(d.latency_ms) + " ms"}`));
  const b = d.binding;
  el.append(node("p", b ? `Binding: ${b.resource} · demand ${number(b.demand)} / capacity ${number(b.capacity)} ${b.unit} · corner: ${b.corner || "not specified"}` : "Binding evidence unavailable"));
  el.append(node("p", d.rationale || "Rationale unavailable"));
  el.append(node("p", `Holds one genset down: ${d.holds_one_genset_down ?? "unavailable"}`));
  for (const [title, value] of [
    ["Conditions / chosen actions", d.chosen?.actions ?? []],
    ["Placement proposal", d.placement ?? {}], ["Alternatives", d.alternatives ?? []],
    ["Contingency", d.contingency ?? []], ["State requests", d.state_requests ?? []],
    ["Costs", d.costs ?? []], ["Envelope checks", d.checks ?? []]
  ]) {
    const details = node("details");
    details.append(node("summary", title), node("pre", json(value)));
    el.append(details);
  }
  if (d.next_window) el.append(node("p", d.next_window));
}
function renderLayout() {
  const svg = $("layout");
  svg.replaceChildren();
  // Derive the SVG namespace from the host element; all text uses textContent.
  const shape = (tag, attrs, text) => {
    const el = document.createElementNS(svg.namespaceURI, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    if (text !== undefined) el.textContent = text;
    svg.append(el);
  };
  shape("path", {d: "M380 8 Q690 70 730 190 L730 548 L30 548 L30 190 Q70 70 380 8Z", class: "hull"});
  state.ship.slots.forEach((slot, i) => {
    const x = 58 + (i % 3) * 220, y = 120 + Math.floor(i / 3) * 86;
    const occupants = [];
    if (slot.occupied_by) occupants.push(slot.occupied_by);
    for (const [gid, rt] of Object.entries(state.snapshot.guests)) {
      for (const [part, sid] of Object.entries(rt.placement.slots)) {
        if (sid === slot.id) occupants.push(`${gid}/${part} · ${rt.placement.line}`);
      }
    }
    const proposals = Object.entries(state.decision?.placement ?? {}).flatMap(([gid, p]) =>
      Object.entries(p.slots).filter(([, sid]) => sid === slot.id).map(([part]) => `${gid}/${part}`));
    shape("rect", {x, y, width: 205, height: 76, rx: 7, class: occupants.length ? "occupied" : "empty"});
    shape("text", {x: x + 8, y: y + 17}, `${slot.id} · ${slot.group}`);
    shape("text", {x: x + 8, y: y + 36}, occupants.join(", ") || "Free");
    shape("text", {x: x + 8, y: y + 54}, `${slot.max_mass_t} t · ${slot.cooling_manifold ? "water" : "no manifold"}`);
    if (proposals.length) {
      shape("rect", {x: x + 2, y: y + 2, width: 201, height: 72, rx: 7, class: "proposal"});
      shape("text", {x: x + 8, y: y + 70}, `Proposed: ${proposals.join(", ")}`);
    }
  });
  shape("text", {x: 75, y: 80}, "PORT");
  shape("text", {x: 355, y: 80}, "BOW");
  shape("text", {x: 630, y: 80}, "STBD");
  for (const [label, active, x] of [
    ["Flight deck", state.snapshot.ship.flight_deck_in_use, 58],
    ["Ramp", state.snapshot.ship.ramp_in_use, 390]
  ]) {
    shape("rect", {x, y: 420, width: 310, height: 60, rx: 7, class: active ? "occupied" : "empty"});
    shape("text", {x: x + 12, y: 447}, `${label} · ${active ? "in use" : "idle"}`);
  }
  $("platform").textContent = `Flight deck: ${state.snapshot.ship.flight_deck_in_use ? "in use" : "idle"}; ramp: ${state.snapshot.ship.ramp_in_use ? "in use" : "idle"}. Mission lines: ${Object.entries(state.ship.power.lines).map(([line, kw]) => `${line} ${kw} kW`).join(" · ")}. Slots sharing a group overlap physically.`;
}
$("admit").onsubmit = event => {
  event.preventDefault();
  command({op: "admit", card: $("card").value, value: Number($("value").value)});
};
function fillCardJson() {
  $("cardJson").value = state.guests[$("card").value] ? json(state.guests[$("card").value]) : "";
}
$("card").onchange = () => { fillMass(); fillCardJson(); };
$("loadCardJson").onclick = fillCardJson;
$("importCard").onsubmit = event => {
  event.preventDefault();
  command({op: "import_card", card_json: $("cardJson").value});
};
for (const id of ["mode", "scenario", "priority", "crew"]) {
  $(id).onchange = () => command({op: id, [id]: id === "crew" ? Number($(id).value) : $(id).value});
}
$("reset").onclick = () => { $("value").value = ""; $("cardJson").value = ""; command({op: "reset"}); };
document.querySelectorAll("[data-op]").forEach(b => b.onclick = () => command({op: b.dataset.op}));
document.querySelectorAll("[data-event]").forEach(b => b.onclick = () => command({
  op: "event", event: b.dataset.event, battery_pct: Number($("battery").value)
}));
$("step").onclick = () => command({op: "step"});
$("forward").onclick = () => command({op: "step", ticks: Number($("ticks").value)});
$("replay").onclick = () => command({op: "replay"});
async function start() {
  try {
    const response = await fetch("/api/sessions", {method: "POST"});
    if (!response.ok) throw new Error("Cannot create local session");
    render(await response.json());
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${scheme}//${location.host}/ws/${state.id}`);
    socket.onopen = () => $("transport").textContent = "Local HTTP + WebSocket connected";
    socket.onmessage = e => {
      const update = JSON.parse(e.data);
      if (update.error) $("error").textContent = update.error;
      else render(update);
    };
    socket.onclose = () => $("transport").textContent = "WebSocket unavailable · HTTP controls remain active";
  } catch (error) { $("error").textContent = error.message; }
}
start();
