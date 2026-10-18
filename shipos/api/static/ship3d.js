// 3D ship: the same schematic as ship.js (not to scale), lifted into 3D. Each box takes its
// length and beam from the top view and its height from the side view, so the three views always
// agree. Below deck it adds the systems the result can hit: generators, the two mission power
// lines and the cooling loop. Geometry carries no physical meaning. Text is drawn on canvases.
import * as THREE from "./vendor/three.module.min.js";
import {OrbitControls} from "./vendor/OrbitControls.js";

// ship.js coordinates: x runs stern → bow, top-view y runs port → starboard, side-view y runs down.
const CX = 512, CZ = 220, DECK = 250, KEEL = 345, WATER = 320;
const HULL = [[40, 50], [700, 50], ["q", 930, 60, 985, 220], ["q", 930, 380, 700, 390], [40, 390]];
const FIXED = [["ramp", "Stern ramp"], ["flight", "Flight deck"], ["bridge", "Bridge"]];

// Same palette as style.css (role colours pair with the legend chips and their icons).
const C = {
  hull: 0x9fb3c2, water: 0x8fb7d3, deck: 0xd9e0e6, slot: 0x55626f, ship: 0xc5ccd3,
  installed: 0x155e8b, proposed: 0xd99a1e, ok: 0x1d6b45, warn: 0xd99a1e, bad: 0xa3271f,
  defer: 0x4a4fa0, off: 0x8a939b, selected: 0x111111,
};
const ROLE_EDGE = {limit: C.bad, denied: C.bad, winner: C.ok, yields: C.defer, proposed: C.proposed,
  moved: C.proposed, affected: C.proposed, changed: C.installed};

let S = null; // the scene, built once per container

function label(text, color = "#17212b", size = 22) {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  ctx.font = `600 ${size}px "Segoe UI", system-ui, sans-serif`;
  canvas.width = Math.ceil(ctx.measureText(text).width) + 16;
  canvas.height = size + 12;
  ctx.font = `600 ${size}px "Segoe UI", system-ui, sans-serif`;
  ctx.fillStyle = "rgba(255,255,255,.85)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = color;
  ctx.fillText(text, 8, size + 2);
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map, depthTest: false}));
  sprite.scale.set(canvas.width * 0.5, canvas.height * 0.5, 1);
  sprite.renderOrder = 10;
  return sprite;
}

// A box spanning a top-view rect and a side-view height band.
function boxFrom(top, side, color, opacity = 1) {
  const [x, y, w, h] = top;
  const y0 = DECK - (side[1] + side[3]), y1 = DECK - side[1];
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, Math.max(y1 - y0, 2), h),
    new THREE.MeshStandardMaterial({color, transparent: opacity < 1, opacity}));
  mesh.position.set(x + w / 2 - CX, (y0 + y1) / 2, y + h / 2 - CZ);
  return mesh;
}

function edges(mesh, color) {
  const line = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), new THREE.LineBasicMaterial({color}));
  mesh.add(line);
  return line;
}

function tube(points, radius, color, closed = false) {
  const curve = new THREE.CatmullRomCurve3(points.map(([x, y, z]) => new THREE.Vector3(x, y, z)), closed, "catmullrom", 0);
  return new THREE.Mesh(new THREE.TubeGeometry(curve, 64, radius, 8, closed), new THREE.MeshStandardMaterial({color}));
}

function disposeTree(obj) {
  obj.traverse(o => {
    o.geometry?.dispose();
    for (const m of [o.material].flat()) { m?.map?.dispose(); m?.dispose(); }
  });
}

function build(container) {
  const renderer = new THREE.WebGLRenderer({antialias: true});
  renderer.setPixelRatio(window.devicePixelRatio);
  container.append(renderer.domElement);
  const tip = document.createElement("div");
  tip.className = "tip3d";
  tip.hidden = true;
  container.append(tip);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xdbe7ef);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 2.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(-300, 600, 400);
  scene.add(sun);

  const camera = new THREE.PerspectiveCamera(40, 2, 1, 5000);
  camera.position.set(-560, 380, 820);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(40, 20, 0);
  controls.enableDamping = true;
  controls.update();

  // Hull: the top-view outline extruded from the keel to the deck, see-through so systems show.
  const shape = new THREE.Shape();
  for (const [i, p] of HULL.entries()) {
    if (p[0] === "q") shape.quadraticCurveTo(p[1] - CX, p[2] - CZ, p[3] - CX, p[4] - CZ);
    else if (i === 0) shape.moveTo(p[0] - CX, p[1] - CZ);
    else shape.lineTo(p[0] - CX, p[1] - CZ);
  }
  const hull = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, {depth: KEEL - DECK, bevelEnabled: false}),
    new THREE.MeshStandardMaterial({color: C.hull, transparent: true, opacity: 0.22, depthWrite: false}));
  hull.rotation.x = Math.PI / 2; // shape y → world z, extrusion → down from the deck
  edges(hull, 0x5f7686);
  scene.add(hull);
  const water = new THREE.Mesh(new THREE.PlaneGeometry(2400, 1400),
    new THREE.MeshStandardMaterial({color: C.water, transparent: true, opacity: 0.35, depthWrite: false}));
  water.rotation.x = -Math.PI / 2;
  water.position.y = DECK - WATER;
  scene.add(water);

  const top = AREAS.top, side = AREAS.side;
  for (const [key, text] of FIXED) {
    const box = boxFrom(top[key], side[key], C.deck, 0.6); // see-through: generators sit below
    box.material.depthWrite = false;
    edges(box, 0x9aa8b4);
    const tag = label(text, "#55626f", 18);
    tag.position.set(0, box.geometry.parameters.height / 2 + 12, 0);
    box.add(tag);
    scene.add(box);
  }
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(3, 3, 110), new THREE.MeshStandardMaterial({color: 0x55626f}));
  pole.position.set(860 - CX, DECK - 95, 0);
  scene.add(pole);

  // Below-deck systems. Port is top-view up (negative z), as in the 2D views.
  const systems = {
    "power.line.port": tube([[110 - CX, -22, -130], [880 - CX, -22, -130]], 4, C.ok),
    "power.line.stbd": tube([[110 - CX, -22, 130], [880 - CX, -22, 130]], 4, C.ok),
    "heat.loop": tube([[340 - CX, -55, -60], [800 - CX, -55, -60], [800 - CX, -55, 60], [340 - CX, -55, 60]], 4, C.ok, true),
  };
  for (const [key, text, [x, y, z]] of [["power.line.port", "Port mission power line", [120, -10, -150]],
    ["power.line.stbd", "Starboard mission power line", [120, -10, 150]], ["heat.loop", "Cooling loop", [560, -40, 0]]]) {
    systems[key].userData.label = text;
    const tag = label(text, "#3d5568", 16);
    tag.position.set(x - CX, y, z);
    scene.add(systems[key], tag);
  }

  S = {container, renderer, scene, camera, controls, tip, systems, gensets: new THREE.Group(), dynamic: new THREE.Group(),
    pickable: [], pulsing: [], onSelect: null, running: false};
  scene.add(S.gensets, S.dynamic);

  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
  const pick = event => {
    const r = renderer.domElement.getBoundingClientRect();
    mouse.set(((event.clientX - r.left) / r.width) * 2 - 1, -((event.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(mouse, camera);
    return ray.intersectObjects(S.pickable, false)[0]?.object;
  };
  renderer.domElement.addEventListener("pointermove", event => {
    const hit = pick(event);
    tip.hidden = !hit;
    if (!hit) return;
    tip.textContent = hit.userData.label;
    const r = container.getBoundingClientRect();
    tip.style.left = `${event.clientX - r.left + 12}px`;
    tip.style.top = `${event.clientY - r.top + 12}px`;
    renderer.domElement.style.cursor = hit.userData.gid ? "pointer" : "default";
  });
  renderer.domElement.addEventListener("click", event => {
    const gid = pick(event)?.userData.gid;
    if (gid && S.onSelect) S.onSelect(gid);
  });
  new ResizeObserver(() => {
    const w = container.clientWidth, h = container.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }).observe(container);
}

function loop(t) {
  if (!S.running) return;
  const glow = 0.35 + 0.35 * Math.sin(t / 220);
  for (const m of S.pulsing) m.material.emissiveIntensity = glow;
  S.controls.update();
  S.renderer.render(S.scene, S.camera);
  requestAnimationFrame(loop);
}

function tint(ratio) {
  return ratio > 1 ? C.bad : ratio > 0.8 ? C.warn : C.ok;
}

function pulse(mesh, color = C.bad) {
  mesh.material.emissive = new THREE.Color(color);
  S.pulsing.push(mesh);
}

// Same inputs as drawShip, plus the gauges, platform status and the shown decision's binding.
function draw(container, {ship, items, highlightSlots, selected, onSelect, gauges, platform, binding}) {
  if (!S || S.container !== container) build(container);
  S.onSelect = onSelect;
  S.pulsing = [];
  S.pickable = [];
  disposeTree(S.dynamic);
  S.dynamic.clear();
  disposeTree(S.gensets);
  S.gensets.clear();

  // Systems: tint by the snapshot gauge for their class; the binding resource pulses red.
  const ratio = cls => {
    const g = gauges.find(x => x.cls === cls && x.status === "available");
    return g ? (g.capacity > 0 ? g.demand / g.capacity : (g.demand > 0 ? Infinity : 0)) : 0;
  };
  for (const [key, mesh] of Object.entries(S.systems)) {
    const cls = key.split(".")[0];
    mesh.material.color.set(tint(ratio(cls)));
    mesh.material.emissive = new THREE.Color(0x000000);
    // A line-specific limit names its line; a ship-wide power or heat limit lights every part of its class.
    const hit = binding && (binding.resource === key || (binding.cls === cls && !binding.resource.startsWith("power.line.")));
    if (hit) { mesh.material.color.set(C.bad); pulse(mesh); }
    S.pickable.push(mesh);
  }
  const total = Math.max(...Object.keys(ship.power.reserve_kw.stopped ?? {1: 0}).map(Number));
  for (let i = 0; i < total; i++) {
    const on = i < platform.gensets_online;
    const g = new THREE.Mesh(new THREE.BoxGeometry(70, 26, 50), new THREE.MeshStandardMaterial({color: on ? C.ok : C.off}));
    g.position.set(230 - CX, -78, (i - (total - 1) / 2) * 70);
    g.userData.label = `Generator ${i + 1}: ${on ? "online" : "offline"}`;
    edges(g, on ? 0x0f3d27 : 0x555555);
    if (!on) pulse(g);
    const tag = label(`Gen ${i + 1} · ${on ? "on" : "OFF"}`, on ? "#1d6b45" : "#a3271f", 16);
    tag.position.set(0, 24, 0);
    g.add(tag);
    S.gensets.add(g);
    S.pickable.push(g);
  }

  // Slots: faint outlines; the slot or group named by the binding resource turns red.
  const tops = slotRects("top", ship.slots), sides = slotRects("side", ship.slots);
  for (const slot of ship.slots) {
    if (!tops[slot.id] || !sides[slot.id]) continue;
    const t = tops[slot.id], s = sides[slot.id];
    const limit = highlightSlots.has(slot.id) || highlightSlots.has(slot.group);
    const box = boxFrom([t.x, t.y, t.w, t.h], [s.x, s.y, s.w, s.h], limit ? C.bad : C.slot, limit ? 0.18 : 0.04);
    box.material.depthWrite = false;
    edges(box, limit ? C.bad : 0x8a96a3);
    if (limit) {
      const tag = label(`◆ limit: ${t.label}`, "#a3271f", 18);
      tag.position.set(0, box.geometry.parameters.height / 2 + 14, 0);
      box.add(tag);
    }
    S.dynamic.add(box);
  }

  // Equipment: one block per item, side by side along the slot's length.
  const perSlot = {};
  for (const it of items) (perSlot[it.slotId] ??= []).push(it);
  for (const [slotId, list] of Object.entries(perSlot)) {
    const t = tops[slotId], s = sides[slotId];
    if (!t || !s) continue;
    const mast = t.slot.kind === "mast";
    list.forEach((it, i) => {
      const w = (t.w - 8) / list.length;
      const h = mast ? s.h : Math.max(12, s.h * 0.6);
      const color = it.kind === "proposed" ? C.proposed : it.kind === "ship" ? C.ship : C.installed;
      const opacity = it.kind === "proposed" ? 0.55 : it.dim ? 0.3 : 1;
      const box = boxFrom([t.x + 4 + i * w + 2, t.y + 6, w - 4, t.h - 12], [0, s.y + s.h - h, 0, h], color, opacity);
      const role = it.roles[0]?.key;
      edges(box, selected && selected === it.gid ? C.selected : ROLE_EDGE[role] ?? 0x0b3350);
      if (it.roles.some(r => r.key === "limit" || r.key === "denied")) pulse(box);
      else if (role && ROLE_EDGE[role]) pulse(box, ROLE_EDGE[role]);
      box.userData = {gid: it.gid, label: `${it.name}${it.gid ? ` (${it.gid})` : ""}: ${it.line2}${it.roles.length ? ". " + it.roles.map(r => `${r.icon} ${r.text}`).join(". ") : ""}`};
      if (!it.dim || role) {
        const tag = label(`${it.roles[0]?.icon ?? ""} ${it.gid ?? it.name}`.trim(), "#17212b", 18);
        tag.position.set(0, h / 2 + 12, 0);
        box.add(tag);
      }
      S.dynamic.add(box);
      S.pickable.push(box);
    });
  }

  if (!S.running) { S.running = true; requestAnimationFrame(loop); }
}

function stop() {
  if (S) { S.running = false; S.tip.hidden = true; }
}

// What is on screen, for tests and the evidence trail: every hoverable part, its state and where
// its centre is drawn (pixels from the top-left of the 3D view).
function inspect() {
  const w = S?.container.clientWidth, h = S?.container.clientHeight;
  return (S?.pickable ?? []).map(m => {
    const p = m.getWorldPosition(new THREE.Vector3()).project(S.camera);
    return {label: m.userData.label, gid: m.userData.gid ?? null, color: m.material.color.getHexString(),
      pulsing: S.pulsing.includes(m), x: Math.round((p.x + 1) / 2 * w), y: Math.round((1 - p.y) / 2 * h)};
  });
}

window.ship3d = {draw, stop, inspect};
