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
// Runtime controls proposed for main's API. A control is enabled only when the server's own
// OpenAPI schema offers its command AND this UI is wired to that command's published payload.
// Nothing here guesses a payload or a response.
const RUNTIME = {
  execution: "Simulated execution",
  confirm_deck: "Operator deck confirmation",
  environment: "Cached environment selection",
};
// Wired to the payloads published in PR #37 (runtime API v2).
const WIRED = new Set(["execution", "confirm_deck", "environment"]);
let serverOps;  // undefined: checking; null: unreadable; array: Command.op values

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
  renderMissionStatus();
  renderEnvironment();
  renderPreviousRuns();
}

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
  row("Weather source", c.source === "scenario" ? "Scenario default (no cached forecast)" : c.source ?? "not reported");
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
    a.setAttribute("download", `shipos-evidence-run-${r.run}.json`);
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
  const rec = ["arbitration", "shedding"].includes(state.decision?.kind) ? state.decision
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
  if (status) applied = listOr(status.applied, "none applied yet");
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
    `${r.variable} → ${r.to} ${requestTime(r.start_s, state.state_request_time_base)} for ${number(r.duration_s)} s`), details);
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
