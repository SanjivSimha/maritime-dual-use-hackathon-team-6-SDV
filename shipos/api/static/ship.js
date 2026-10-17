"use strict";
// Illustrated ship: a schematic (not to scale) top or side view drawn from the real ship slots,
// the snapshot's placements and the current decision. Geometry only arranges the real slots;
// it carries no physical meaning. All text is set with textContent.

const SVGNS = "http://www.w3.org/2000/svg";

// Schematic areas, bow to the right. Each slot kind maps to an area; halves split bay 1.
const AREAS = {
  top: {
    viewBox: "25 28 970 400",
    hull: "M40 50 L700 50 Q930 60 985 220 Q930 380 700 390 L40 390 Z",
    ramp: [40, 165, 70, 110, "Stern ramp"],
    flight: [120, 75, 200, 290, "Flight deck"],
    hangar: [330, 160, 140, 185, "Hangar"],
    hangar_roof: [330, 90, 140, 64, "Hangar roof (above)"],
    bay1_whole: [480, 80, 190, 280, "Bay 1"],
    bay1_half: [[480, 80, 190, 138, "Bay 1 port half"], [480, 222, 190, 138, "Bay 1 starboard half"]],
    bay2: [680, 120, 130, 200, "Bay 2"],
    bridge: [820, 160, 80, 120, "Bridge"],
    mast: [[832, 172, 56, 30], [832, 205, 56, 30], [832, 238, 56, 30]],
    labels: [["PORT", 500, 40], ["STARBOARD", 470, 420], ["BOW →", 900, 40]],
  },
  side: {
    viewBox: "25 30 970 330",
    hull: "M40 250 L960 250 Q975 300 930 345 L70 345 Q48 320 40 300 Z",
    water: 320,
    ramp: [40, 250, 80, 40, "Stern ramp"],
    flight: [120, 222, 200, 28, "Flight deck"],
    hangar: [330, 140, 140, 110, "Hangar"],
    hangar_roof: [330, 66, 140, 70, "Hangar roof"],
    bay1_whole: [480, 170, 190, 80, "Bay 1 (both halves)"],
    bay1_half: [[480, 170, 190, 80, "Bay 1 (port half)"], [480, 170, 190, 80, "Bay 1 (starboard half)"]],
    bay2: [680, 190, 130, 60, "Bay 2"],
    bridge: [820, 150, 80, 100, "Bridge"],
    mast: [[840, 45, 40, 30], [840, 80, 40, 30], [840, 115, 40, 30]],
    labels: [["WATERLINE", 60, 336], ["BOW →", 900, 300]],
  },
};

function svgEl(tag, attrs = {}, text) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}

// Where each real slot is drawn, by kind and by order within its kind.
function slotRects(view, slots) {
  const A = AREAS[view];
  const seen = {};
  const rects = {};
  for (const slot of slots) {
    const i = (seen[slot.kind] = (seen[slot.kind] ?? -1) + 1);
    const area = A[slot.kind];
    if (!area) continue;
    const r = Array.isArray(area[0]) ? area[Math.min(i, area.length - 1)] : area;
    rects[slot.id] = {x: r[0], y: r[1], w: r[2], h: r[3], label: r[4] ?? slot.id, slot};
  }
  return rects;
}

const shortName = (name, n = 22) => (name.length > n ? `${name.slice(0, n - 1)}…` : name);

// items: [{key, gid, part, slotId, name, line2, kind: installed|proposed|ship, roles: [...], dim}]
function drawShip(svg, view, ship, deck, items, highlightSlots, selected, onSelect) {
  const A = AREAS[view];
  svg.replaceChildren();
  svg.dataset.view = view;
  svg.setAttribute("viewBox", A.viewBox);
  svg.append(svgEl("path", {d: A.hull, class: "s-hull"}));
  if (A.water) svg.append(svgEl("line", {x1: 10, x2: 990, y1: A.water, y2: A.water, class: "s-water"}));
  for (const [text, x, y] of A.labels) svg.append(svgEl("text", {x, y, class: "s-axis"}, text));
  if (view === "side") svg.append(svgEl("line", {x1: 860, x2: 860, y1: 40, y2: 150, class: "s-mastpole"}));

  // Fixed ship areas that are not mountable slots.
  const fixed = (key, label, extra = "") => {
    const [x, y, w, h] = A[key];
    const g = svgEl("g", {class: `s-area s-fixed ${extra}`});
    g.append(svgEl("rect", {x, y, width: w, height: h, rx: 6}));
    g.append(svgEl("text", {x: x + 6, y: y + 16, class: "s-arealabel"}, label));
    svg.append(g);
    return g;
  };
  fixed("flight", `Flight deck · ${deck.flight_deck_in_use ? "in use" : "idle"} · deck ${deck.deck}`,
    deck.flight_deck_in_use ? "s-active" : "");
  fixed("ramp", `Ramp · ${deck.ramp_in_use ? "in use" : "idle"}`, deck.ramp_in_use ? "s-active" : "");
  const bridge = fixed("bridge", "");
  const [bx, by, bw, bh] = A.bridge;
  bridge.append(svgEl("text", {x: bx + 6, y: by + bh - 6, class: "s-arealabel"}, "Bridge / mast"));

  const rects = slotRects(view, ship.slots);
  // Draw the big areas first so the hangar roof and halves sit on top.
  const order = Object.values(rects).sort((a, b) => b.w * b.h - a.w * a.h);
  for (const r of order) {
    const slot = r.slot;
    const cls = ["s-area", "s-slot"];
    if (highlightSlots.has(slot.id) || highlightSlots.has(slot.group)) cls.push("s-limit");
    const g = svgEl("g", {class: cls.join(" ")});
    g.append(svgEl("rect", {x: r.x, y: r.y, width: r.w, height: r.h, rx: 6}));
    if (slot.kind !== "mast") g.append(svgEl("text", {x: r.x + 6, y: r.y + 15, class: "s-arealabel"}, r.label));
    if (highlightSlots.has(slot.id)) g.append(svgEl("text", {x: r.x + 6, y: r.y + r.h - 6, class: "s-limittext"}, "◆ limit"));
    svg.append(g);
  }

  // Equipment tokens, stacked inside their slot area.
  const perSlot = {};
  for (const it of items) (perSlot[it.slotId] ??= []).push(it);
  for (const [slotId, list] of Object.entries(perSlot)) {
    const r = rects[slotId];
    if (!r) continue;
    const mast = r.slot.kind === "mast";
    const rows = mast ? 1 : Math.max(1, Math.min(list.length, Math.floor((r.h - 20) / 40)));
    list.forEach((it, i) => {
      const th = mast ? r.h : Math.min(52, (r.h - 22) / rows - 4);
      const tw = mast ? r.w : r.w - 12;
      const x = mast ? r.x : r.x + 6;
      const y = mast ? r.y : r.y + 20 + (i % rows) * (th + 4);
      const cls = ["s-token", `s-${it.kind}`, ...it.roles.map(role => `r-${role.key}`)];
      if (it.dim) cls.push("s-dim");
      if (selected && selected === it.gid) cls.push("s-selected");
      const g = svgEl("g", {class: cls.join(" ")});
      const label = `${it.name}${it.gid ? ` (${it.gid})` : ""}: ${it.line2}${it.roles.length ? ". " + it.roles.map(r => r.text).join(". ") : ""}`;
      g.append(svgEl("title", {}, label));
      g.append(svgEl("rect", {x, y, width: tw, height: th, rx: 5}));
      if (mast) {
        g.append(svgEl("text", {x: x + tw / 2, y: y + th / 2 + 4, "text-anchor": "middle", class: "s-tokenid"}, it.gid ?? it.name.split(" ").pop()));
      } else {
        g.append(svgEl("text", {x: x + 6, y: y + 15, class: "s-tokenname"}, shortName(it.name, Math.floor(tw / 7.2))));
        if (th > 30) g.append(svgEl("text", {x: x + 6, y: y + 30, class: "s-tokenline"}, shortName(it.line2, Math.floor(tw / 6.4))));
        const tag = it.roles[0];
        if (tag && th > 44) g.append(svgEl("text", {x: x + 6, y: y + 44, class: "s-tokentag"}, shortName(`${tag.icon} ${tag.text}`, Math.floor(tw / 6.6))));
      }
      if (it.gid) {
        g.setAttribute("tabindex", "0");
        g.setAttribute("role", "button");
        g.setAttribute("aria-label", label);
        g.setAttribute("aria-pressed", String(selected === it.gid));
        g.dataset.gid = it.gid;
        g.addEventListener("click", () => onSelect(it.gid));
        g.addEventListener("keydown", e => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(it.gid); }
        });
      }
      svg.append(g);
    });
  }
}
