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
  yes: ["Accepted", "s-ok", "✓"],
  yes_if: ["Accepted with conditions", "s-cond", "!"],
  not_now: ["Not now: deferred", "s-defer", "⏸"],
  no: ["Refused", "s-bad", "✕"],
  decided: ["Decided", "s-info", "i"],
  no_action: ["No action needed", "s-ok", "✓"],
  shed: ["Shedding plan", "s-cond", "!"],
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
const PRIORITY_NAMES = {survey_primary: "Survey", sar_primary: "Search and rescue", patrol: "Patrol"};

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
  $("mode").value = state.mode;
  $("scenario").value = state.scenario;
  $("crew").value = state.snapshot.crew;
  options($("priority"), state.priorities.map(p => [p, PRIORITY_NAMES[p] ?? p]), state.priority_name);
}

function render(data) {
  const oldCard = $("card").value;
  const oldMass = state?.guests?.[oldCard]?.mass_t;
  state = data;
  syncControls();
  document.body.dataset.mode = state.mode;
  $("modeBadge").textContent = state.mode;
  $("provenanceText").textContent = state.mode === "SAMPLE"
    ? "D3/D4 buttons show labelled sample fixtures, not results for this session"
    : "Results computed by the engine for this session · recommendations only, nothing is executed";
  $("notice").textContent = state.notice;
  $("export").href = `/api/sessions/${state.id}/evidence`;
  $("jsonl").href = `/api/sessions/${state.id}/replay.jsonl`;
  const snap = state.snapshot;
  const ship = snap.ship;
  $("stateStrip").textContent = `Ship now: ${number(ship.speed_kn)} kn · ${ship.course} course · ${ship.noise} · deck ${ship.deck} · ${snap.platform.gensets_online} generator(s) online · seawater ${number(snap.env.seawater_c)} °C · ${snap.crew} crew · simulated time ${number(snap.t_s)} s`;
  $("clock").textContent = `Simulated time ${number(snap.t_s)} s · next step at ${state.next_t_s} s · the demo timeline stops at ${state.horizon_s} s.`;
  $("replayStatus").textContent = state.replay
    ? `Replay ${state.replay.byte_identical ? "✓ byte-identical" : "✕ DIFFERS"} · ${state.replay.records} records · hash ${short(state.replay.final_hash)}`
    : `Not verified yet · log hash ${short(state.final_hash)}`;
  $("replayStatus").title = state.replay?.final_hash ?? state.final_hash ?? "";
  renderAboard(snap);
  const proposed = Object.values(state.guests).filter(g => !snap.guests[g.id]);
  const selected = proposed.some(g => g.id === oldCard) ? oldCard : proposed[0]?.id;
  options($("card"), proposed.map(g => [g.id, `${g.id} · ${g.name}`]), selected);
  if (selected !== oldCard || oldMass !== state.guests[selected]?.mass_t || !$("value").value) fillMass();
  if (!$("cardJson").value) fillCardJson();
  $("admit").querySelector("button").disabled = !selected;
  renderGauges();
  renderLayout();
  renderDecision(state.decision);
  renderLog();
}

function renderAboard(snap) {
  $("guestStates").replaceChildren(...Object.entries(snap.guests).map(([id, rt]) => {
    const li = node("li");
    const name = node("span");
    name.append(node("b", id), ` ${state.guests[id]?.name ?? ""}`);
    const slots = Object.values(rt.placement.slots).join(" + ");
    const battery = rt.battery_pct == null ? "" : ` · battery ${number(rt.battery_pct)}%`;
    li.append(name, node("span", `${rt.mode} · ${slots}${battery}`, "where"));
    return li;
  }));
}

function renderGauges() {
  const limiting = state.decision?.binding?.cls;
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
    box.append(bar, node("small", `${ratio > 1 ? "Over by" : "Free"} ${number(Math.abs(g.capacity - g.demand))} ${g.unit} · one-generator capacity ${number(g.facts?.degraded_capacity)} ${g.unit}`));
    return box;
  }));
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
    return !now || JSON.stringify(now.slots) !== JSON.stringify(p.slots) || now.line !== p.line;
  }).map(([gid, p]) => [gid, p, state.snapshot.guests[gid] ? "moved" : "new"]);
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
      node("p", unavailable ? state.notice : "Choose equipment and press Evaluate to see whether it fits."));
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
    fact("Checked at", b.corner || "current snapshot");
  } else {
    fact("Limiting constraint", "not reported by this decision");
  }
  fact("Goes first", d.winner);
  fact("Yields", d.loser);
  if (d.holds_one_genset_down != null) fact("Holds with one generator down", d.holds_one_genset_down ? "yes" : "no");
  fact("Next window", d.next_window);
  el.append(facts);

  const details = node("div");
  list(d.kind === "admission" ? "Conditions" : "Recommended actions (not executed)", (d.chosen?.actions ?? []).map(actionText), details);
  list("If a generator trips", (d.contingency ?? []).map(actionText), details);
  const changes = placementChanges(d);
  list("Proposed placement", changes.map(([gid, p, how]) =>
    `${gid} (${how}): ${Object.entries(p.slots).map(([part, slot]) => `${part} → ${slot}`).join(", ")} · ${p.line} power line`), details);
  if (Object.keys(d.placement ?? {}).length && changes.length < Object.keys(d.placement).length) {
    details.append(node("p", "Installed equipment not listed stays where it is.", "note"));
  }
  list("Costs", (d.costs ?? []).map(c => `${c.guest}: ${number(c.amount)} ${c.unit} (${c.what})`), details);
  list("Requested ship state changes", (d.state_requests ?? []).map(r =>
    `${r.variable} → ${r.to} at +${number(r.start_s)} s for ${number(r.duration_s)} s`), details);
  el.append(details);

  el.append(node("h4", "Engine explanation"), node("p", d.rationale || "No rationale provided", "rationale"));
  el.append(node("p", "Recommendation only: ShipOS has not installed, moved, switched or shed any equipment.", "note"));

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
    li.append(node("b", `${KINDS[d.kind] ?? d.kind} · ${d.subject} · ${VERDICTS[d.verdict]?.[0] ?? d.verdict}`),
      ` (${entry.mode})`, node("span", d.rationale, "why"));
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

function renderLayout() {
  const svg = $("layout");
  svg.replaceChildren();
  const shape = (tag, attrs, text) => {
    const el = document.createElementNS(svg.namespaceURI, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    if (text !== undefined) el.textContent = text;
    svg.append(el);
  };
  shape("path", {d: "M380 8 Q690 60 730 170 L730 508 L30 508 L30 170 Q70 60 380 8Z", class: "hull"});
  state.ship.slots.forEach((slot, i) => {
    const x = 58 + (i % 3) * 220, y = 100 + Math.floor(i / 3) * 86;
    const occupants = [];
    if (slot.occupied_by) occupants.push(slot.occupied_by);
    for (const [gid, rt] of Object.entries(state.snapshot.guests)) {
      for (const [part, sid] of Object.entries(rt.placement.slots)) {
        if (sid === slot.id) occupants.push(part === "body" ? gid : `${gid} ${part}`);
      }
    }
    const proposals = placementChanges(state.decision).flatMap(([gid, p]) =>
      Object.entries(p.slots).filter(([, sid]) => sid === slot.id).map(([part]) => part === "body" ? gid : `${gid} ${part}`));
    shape("rect", {x, y, width: 205, height: 76, rx: 7, class: occupants.length ? "occupied" : "empty"});
    shape("text", {x: x + 10, y: y + 20, "font-weight": 600}, slot.id);
    shape("text", {x: x + 10, y: y + 40}, occupants.join(", ") || "free");
    shape("text", {x: x + 10, y: y + 58}, `max ${slot.max_mass_t} t · ${slot.cooling_manifold ? "water cooling" : "no cooling"}`);
    if (proposals.length) {
      shape("rect", {x: x + 3, y: y + 3, width: 199, height: 70, rx: 6, class: "proposal"});
      shape("text", {x: x + 10, y: y + 72}, `proposed: ${proposals.join(", ")}`);
    }
  });
  shape("text", {x: 70, y: 70}, "PORT");
  shape("text", {x: 365, y: 60}, "BOW");
  shape("text", {x: 650, y: 70}, "STBD");
  for (const [label, active, x] of [
    ["Flight deck", state.snapshot.ship.flight_deck_in_use, 58],
    ["Stern ramp", state.snapshot.ship.ramp_in_use, 390]
  ]) {
    shape("rect", {x, y: 400, width: 310, height: 56, rx: 7, class: active ? "occupied" : "empty"});
    shape("text", {x: x + 12, y: 433}, `${label} · ${active ? "in use" : "idle"}`);
  }
  $("platform").textContent = `Mission power lines: ${Object.entries(state.ship.power.lines).map(([line, kw]) => `${line} ${kw} kW`).join(" · ")}. Slots in the same bay group share physical space.`;
}

function fillCardJson() {
  $("cardJson").value = state.guests[$("card").value] ? json(state.guests[$("card").value]) : "";
}

function selectTab(name) {
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

$("admit").onsubmit = event => {
  event.preventDefault();
  command({op: "admit", card: $("card").value, value: Number($("value").value)});
};
$("card").onchange = () => { fillMass(); fillCardJson(); };
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
$("reset").onclick = () => { $("value").value = ""; $("cardJson").value = ""; selectTab("evaluate"); command({op: "reset"}); };
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
    socket.onopen = () => $("transport").textContent = "Local HTTP and WebSocket connected · offline";
    socket.onmessage = e => {
      const update = JSON.parse(e.data);
      if (update.error) $("error").textContent = update.error;
      else render(update);
    };
    socket.onclose = () => $("transport").textContent = "WebSocket unavailable · HTTP controls remain active";
  } catch (error) { $("error").textContent = error.message; }
}
start();
