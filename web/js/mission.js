// Lantern Mission Control: replays a Surveyor/Prospector registry mission
// (sim/roles.py run log) on Joseph's terrain and 3D world (world.js).
// Layout after NASA's "Experience Curiosity": the 3D scene fills the window,
// the rover camera and an overhead map sit in small boxes over it, and one
// play bar runs along the bottom. Science stops slow the clock and zoom in.
import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { Terrain, Run, loadIndex, fmtClock } from "./data.js";
import { World } from "./world.js";
import { MapView } from "./map.js";
import { ScienceRig, DRILL_DEPTH_M } from "./science_rig.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const short = (h, a = 8, b = 6) => (h ? `${h.slice(0, a)}…${h.slice(-b)}` : "");
const ROLE = { A: "Surveyor", B: "Prospector" };
// The state machines' states, in words a visitor reads at a glance.
const DOING = {
  IDLE: "Waiting", EXPLORING: "Surveying", CANDIDATE_FOUND: "Candidate found", NAVIGATING_TO_SITE: "Lining up",
  INSPECTING: "Drilling", PUBLISHING: "Publishing", RESUMING: "Resuming", ERROR: "Error",
  FETCHING_TARGET: "Reading registry", PLANNING: "Planning", NAVIGATING: "Driving", REPLANNING: "Replanning",
  ANALYZING: "Analysing", REPORTING: "Publishing", COMPLETED: "Done",
};
const PHASE = { park: "Park", nir_scan: "NIR scan", arm_deploy: "Arm out", drill: "Drill 1 m", sample_lift: "Core up",
  analyze: "Analyse", arm_stow: "Stow" };
const CALL = {
  registerCandidate: "Candidate registered",
  submitSurveyorInspection: "Surveyor result",
  submitProspectorAnalysis: "Prospector analysis",
};
// What each kind of write puts on chain, and what stays off chain under its hash.
const SPLIT = {
  registerCandidate: ["Site, position, confidence, hash", "Neutron scan readings"],
  submitSurveyorInspection: ["Result, hash, route ID", "Water by depth, route driven"],
  submitProspectorAnalysis: ["Result, link to the Surveyor's report, hash", "Water by depth, NIR scan, route"],
};
const MODE_SHORT = { local: "Local chain", monad: "Monad testnet", offline: "Offline" };
const STAGE_N = { None: 0, Candidate: 1, SurveyorInspected: 2, ProspectorAnalyzed: 3 };
const SITE_COLOR = { Candidate: 0x9aa7b8, Positive: 0xff4d4d, Negative: 0x8d939b, Inconclusive: 0xd9b44a, Analyzed: 0x58b4ff };
const FOCUS_SPEED = 1.5;   // sim seconds per real second during a science stop
const FOCUS_LEAD_S = 3;    // the close-up starts this many sim seconds before the stop
const FOCUS_TAIL_S = 3;    // and holds this long after it
const NARROW = matchMedia("(max-width: 760px)");

const S = {
  run: null, t: 0, playing: false, speed: 200, cam: "third", rover: "A", tab: "rover", tabBeforeFocus: null,
  opts: { routes: true, hazards: true, truth: false }, focus: true, focusEv: null, prev: null, lastPanel: "",
};
let terrain, world, map, rigs = {}, ov, sci = [], reg = [], hazards = [], planned = [], siteObjs = new Map();
let satObjs = [], tagEls = {}, extras = {};
// Rover camera, as sim/scene.py: 640x480, 60 deg wide, 15 deg down, 2.5 m up.
const FP = { w: 640, h: 480, pitch: (15 * Math.PI) / 180, camH: 2.5, fx: 320 / Math.tan((30 * Math.PI) / 180) };
const evidenceCache = new Map();

// ------------------------------------------------------------------ boot
async function main() {
  const params = new URLSearchParams(location.search);
  try {
    terrain = await Terrain.load("assets/terrain/");
  } catch (e) {
    $("loading").textContent = "Terrain assets missing: run python web/build_assets.py";
    throw e;
  }
  world = new World($("viewport"), terrain, params.get("quality") || "high");
  world.buildTerrain();
  world.buildBackdrop();
  world.loadRocks();
  map = new MissionMap($("map-cv"), terrain);
  const runs = await loadIndex("runs/");
  const want = params.get("run");
  const entry = runs.find((r) => r.run_id === want) || runs.find((r) => r.status === "mission") || runs[0];
  await openRun(entry);
  bind();
  if (NARROW.matches) $("panel").classList.add("closed");
  window.lantern = { world, S, rigs };   // for poking at the scene from the browser console
  $("loading").hidden = true;
  requestAnimationFrame(loop);
}

async function openRun(entry) {
  $("loading").textContent = "Loading the mission";
  const run = await Run.load("runs/", entry);
  const log = run.log;
  // This page draws registry sites itself; world.js would otherwise draw the
  // Lantern-contract finds from the same stops under another name.
  run.events = [];
  const pts = run.agencies.flatMap((a) => run.tracks[a].truth);
  const keys = [...terrain.keysNear(pts.filter((_, i) => i % 2 === 0), 4)];
  let done = 0;
  await Promise.all(keys.map((k) => terrain.loadTile(k).then(() => {
    $("loading").textContent = `Loading terrain ${++done} / ${keys.length}`;
  })));
  S.run = run;
  world.currentT = 0;
  world.setRun(run);
  world.setVisibility({ fill: true, tracks: S.opts.routes, truth: S.opts.truth });
  map.setRun(run);
  map.opts = { tracks: S.opts.routes, truth: S.opts.truth };
  document.title = `Lantern Mission Control · ${log.label || entry.label}`;

  sci = [];
  reg = [];
  run.steps.forEach((s) => {
    for (const e of s.science || []) {
      sci.push({ ...e, t0: e.phases[0].t0, t1: e.phases.at(-1).t0 + e.phases.at(-1).dur });
    }
    for (const e of s.registry || []) reg.push(e);
  });
  sci.sort((a, b) => a.t0 - b.t0);
  reg.sort((a, b) => a.t - b.t);
  hazards = log.hazards || [];
  planned = log.planned_routes || [];

  const R = log.registry || {};
  const badge = $("chain-badge");
  badge.className = `chain-badge ${R.mode || "offline"}`;
  badge.textContent = MODE_SHORT[R.mode] || "No registry";
  badge.title = [R.label, R.contract ? `Contract ${R.contract}` : ""].filter(Boolean).join("\n");

  for (const a of run.agencies) {
    if (!rigs[a]) { rigs[a] = new ScienceRig(world.rovers[a].g, world.scene, { up: "z" }); dressRover(a); }
  }
  buildOverlay();
  buildTimeline();
  setCam("third");
  setRover("A");
  S.t = 0;
  render(true);
}

// Each rover gets its own look and a name tag that also says what its
// instrument is doing.
function dressRover(a) {
  const g = world.rovers[a].g;
  g.traverse((o) => {
    if (o.element && o.element.classList.contains("lbl3d")) {
      o.element.innerHTML = `<span>${ROLE[a]} ${a}</span><em></em>`;
      tagEls[a] = o.element.querySelector("em");
    }
  });
  const mat = (c) => new THREE.MeshStandardMaterial({ color: c, roughness: 0.5, metalness: 0.35 });
  const box = (m, cx, cy, cz, dx, dy, dz) => {
    const b = new THREE.Mesh(new THREE.BoxGeometry(dx, dy, dz), m);
    b.position.set(cx, cy, cz); b.castShadow = true; g.add(b); return b;
  };
  if (a === "A") {
    // Surveyor: a wide neutron-spectrometer boom across the nose, and a radiator panel.
    const gold = mat(0xd8b24a);
    box(gold, 0.78, 0, 0.5, 0.1, 1.9, 0.1);
    const s1 = box(mat(0x222222), 0.78, 0.9, 0.5, 0.26, 0.22, 0.22);
    const s2 = box(mat(0x222222), 0.78, -0.9, 0.5, 0.26, 0.22, 0.22);
    const panel = box(mat(0x1d3f7a), -0.75, 0, 1.45, 0.9, 1.5, 0.04);
    panel.rotation.y = -0.5;
    extras.A = { sensors: [s1, s2] };
  } else {
    // Prospector: side battery pods, a taller frame, and the NIR head on the mast.
    const pod = mat(0x8a4a1c);
    box(pod, -0.2, 0.72, 0.95, 1.1, 0.2, 0.36);
    box(pod, -0.2, -0.72, 0.95, 1.1, 0.2, 0.36);
    box(mat(0xc9ccd1), -0.9, 0, 1.3, 0.12, 1.1, 0.5);
    const nir = box(mat(0x2a1c4a), 0.12, 0, 2.05, 0.22, 0.3, 0.2);
    extras.B = { sensors: [nir] };
  }
  loadModel(a);
}

// NASA's own 3D models (github.com/nasa/NASA-3D-Resources): both are Mars
// rovers, used here as stand-ins for the lunar rovers and credited on the
// page. Each is measured once loaded, turned to this scene's frame (x
// forward, y left, z up), scaled to a rover length, and set on the ground.
// The box rover stays underneath until the model arrives, and if it fails.
const MODELS = {
  A: { file: "models/opportunity.glb", name: "Opportunity (MER-B)", length: 2.2, yaw: 0 },
  B: { file: "models/perseverance.glb", name: "Perseverance (Mars 2020)", length: 2.9, yaw: 0 },
};
const gltf = new GLTFLoader();
// NASA's files are Draco-compressed; the decoder comes from the same pinned three.js release.
gltf.setDRACOLoader(new DRACOLoader().setDecoderPath("https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/libs/draco/gltf/"));
const modelNote = {};

function loadModel(a) {
  const spec = MODELS[a];
  const g = world.rovers[a].g;
  gltf.load(spec.file, (res) => {
    const model = res.scene;
    model.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
    const holder = new THREE.Group();     // glTF is y-up: turn it to z-up
    holder.rotation.x = Math.PI / 2;
    holder.add(model);
    const fit = new THREE.Group();
    fit.add(holder);
    fit.updateMatrixWorld(true);
    let box = new THREE.Box3().setFromObject(fit);
    let size = box.getSize(new THREE.Vector3());
    if (size.y > size.x) {                 // put the long side along x (forward)
      fit.rotation.z = Math.PI / 2;
      fit.updateMatrixWorld(true);
      box = new THREE.Box3().setFromObject(fit);
      size = box.getSize(new THREE.Vector3());
    }
    fit.rotation.z += spec.yaw;
    const k = spec.length / size.x;
    fit.scale.setScalar(k);
    fit.updateMatrixWorld(true);
    box = new THREE.Box3().setFromObject(fit);
    const c = box.getCenter(new THREE.Vector3());
    fit.position.set(-0.2 - c.x, -c.y, -box.min.z);   // centred on the body, wheels on the ground
    fit.name = "nasa-model";
    // Hide the box body; keep the lamp, the name tag and the science arm.
    for (const o of g.children) if (o.isMesh) o.visible = false;
    if (extras[a]) extras[a].sensors = [];
    g.add(fit);
    modelNote[a] = spec.name;
    $("model-credit").textContent = `Rover models: NASA 3D Resources, ${Object.values(modelNote).join(" and ")}. Mars rovers, standing in for lunar ones.`;
  }, undefined, (err) => console.warn(`NASA model for ${a} not loaded; keeping the simple rover`, err));
}

// ------------------------------------------------------------- overlays
function buildOverlay() {
  if (ov) { world.scene.remove(ov); ov.traverse((c) => { if (c.element) c.element.remove(); }); }
  ov = new THREE.Group();
  world.scene.add(ov);
  siteObjs = new Map();
  const mats = {};
  for (const [k, c] of Object.entries(SITE_COLOR)) mats[k] = world.lineMat(c, 2.5);
  for (const e of reg) {
    if (e.call !== "registerCandidate" || siteObjs.has(e.site_id)) continue;
    const g = new THREE.Group();
    const ring = world.ring(e.x, e.y, 3.5, mats.Candidate);
    const p = world.groundL(e.x, e.y, 0);
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 5, 8),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.7 }));
    beam.rotation.x = Math.PI / 2;
    beam.position.set(p.x, p.y, p.z + 2.5);
    const el = document.createElement("div");
    el.className = "lbl-site";
    el.onclick = () => openSite(e.site_id);
    const lbl = new CSS2DObject(el);
    lbl.position.set(p.x, p.y, p.z + 5.4);
    lbl.center.set(0, 1);
    // Ice found here: a red patch on the ground, so nobody can miss it.
    const disc = new THREE.Mesh(new THREE.CircleGeometry(4.5, 40),
      new THREE.MeshBasicMaterial({ color: 0xff3b3b, transparent: true, opacity: 0.35, depthWrite: false }));
    disc.position.copy(world.groundL(e.x, e.y, 0.2));
    disc.visible = false;
    g.add(ring, beam, lbl, disc);
    ov.add(g);
    siteObjs.set(e.site_id, { g, ring, beam, el, mats, disc, t: e.t, target: e.target_id, key: "" });
  }
  // What the satellite relayed: where to look. Amber, dashed, until a rover gets there.
  satObjs = [];
  for (const s of S.run.log.satellite_sites || []) {
    const g = new THREE.Group();
    g.add(world.ring(s.x, s.y, 6, world.lineMat(0xe0a83a, 2, { dashed: true, opacity: 0.9 })));
    const el = document.createElement("div");
    el.className = "lbl-sat";
    el.textContent = s.id;
    el.title = "Possible site, relayed by the satellite";
    const lbl = new CSS2DObject(el);
    lbl.position.copy(world.groundL(s.x, s.y, 3.5));
    g.add(lbl);
    ov.add(g);
    satObjs.push({ g, id: s.id, x: s.x, y: s.y });
  }
  for (const h of hazards) {
    const g = new THREE.Group();
    const rockMat = new THREE.MeshStandardMaterial({ color: 0x77736c, roughness: 1 });
    let seed = Math.round(h.x * 13 + h.y * 7);
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < 26; i++) {
      const a = rnd() * Math.PI * 2, r = Math.sqrt(rnd()) * h.radius;
      const s = 0.25 + rnd() * 0.7;
      const m = new THREE.Mesh(new THREE.DodecahedronGeometry(s, 0), rockMat);
      const q = world.groundL(h.x + r * Math.cos(a), h.y + r * Math.sin(a), s * 0.4);
      m.position.copy(q);
      m.rotation.set(rnd() * 3, rnd() * 3, rnd() * 3);
      m.castShadow = true;
      g.add(m);
    }
    g.add(world.ring(h.x, h.y, h.radius + 0.5, world.lineMat(0xff5d5d, 2, { dashed: true })));
    const el = document.createElement("div");
    el.className = "lbl-haz";
    el.textContent = "Obstacle";
    el.title = `Found by ${ROLE[h.seen_by]} ${h.seen_by}`;
    const lbl = new CSS2DObject(el);
    lbl.position.copy(world.groundL(h.x, h.y, 3));
    g.add(lbl);
    g.userData.t = h.seen_t;
    ov.add(g);
    h.obj = g;
  }
  for (const pr of planned) {
    const col = pr.agency === "A" ? 0x5aa7ff : 0xf2994a;
    const d = world.drape(pr.points, world.lineMat(col, 2, { dashed: true, opacity: 0.9 }));
    d.line.visible = false;
    ov.add(d.line);
    pr.line = d.line;
  }
}

// Ticks on the play bar: blue for a science stop, red where ice was found,
// a low red tick for a new obstacle. Hover for what each is.
function buildTimeline() {
  const marks = $("tl-marks");
  marks.innerHTML = "";
  const T = S.run.tEnd || 1;
  const add = (t, cls, title) => {
    const i = document.createElement("i");
    i.className = cls;
    i.style.left = `${(100 * t) / T}%`;
    i.title = title;
    marks.appendChild(i);
  };
  for (const e of sci) add(e.t0, "sci", `${ROLE[e.agency]}: ${e.observation || "science"} at ${e.target_id}`);
  for (const e of reg) if (e.result === "Positive") add(e.t, "ice", `Ice at ${e.target_id} (${ROLE[e.agency]})`);
  for (const h of hazards) add(h.seen_t, "haz", "New obstacle");
}

// ---------------------------------------------------------------- controls
function bind() {
  $("play").onclick = togglePlay;
  $("restart").onclick = () => { S.t = 0; S.playing = false; setPlayIcon(); exitFocus(); S.dismissed = null; render(true); };
  $("speed").onchange = (e) => (S.speed = Number(e.target.value));
  $("focus").onchange = (e) => { S.focus = e.target.checked; if (!S.focus) exitFocus(); };
  $("scrub").oninput = (e) => { S.t = (Number(e.target.value) / 1000) * S.run.tEnd; exitFocus(); S.dismissed = null; render(true); };
  $("zoom-in").onclick = () => zoom(1 / 1.35);
  $("zoom-out").onclick = () => zoom(1.35);
  document.querySelectorAll("#rover-seg button").forEach((b) => (b.onclick = () => { exitFocus(true); setRover(b.dataset.rover); }));
  document.querySelectorAll("[data-expand]").forEach((b) => (b.onclick = () => { exitFocus(true); setCam(b.dataset.expand); }));
  $("back-chip").onclick = () => setCam("third");
  document.querySelectorAll(".rail button").forEach((b) => (b.onclick = () => {
    const p = $("panel");
    if (p.classList.contains("closed")) p.classList.remove("closed");
    else if (S.tab === b.dataset.tab) { p.classList.add("closed"); return; }
    S.tabBeforeFocus = null;
    setTab(b.dataset.tab);
  }));
  $("collapse").onclick = () => $("panel").classList.toggle("closed");
  for (const k of ["routes", "hazards", "truth"]) {
    $("opt-" + k).onchange = (e) => {
      S.opts[k] = e.target.checked;
      world.setVisibility({ fill: true, tracks: S.opts.routes, truth: S.opts.truth });
      map.opts = { tracks: S.opts.routes, truth: S.opts.truth };
      render(true);
    };
  }
  $("layers-btn").onclick = (e) => {
    e.stopPropagation();
    const open = $("layers").hidden;
    $("layers").hidden = !open;
    $("layers-btn").setAttribute("aria-expanded", String(open));
  };
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".pop-wrap")) { $("layers").hidden = true; $("layers-btn").setAttribute("aria-expanded", "false"); }
  });
  $("info-btn").onclick = () => $("about").showModal();
  $("full-btn").onclick = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.());
  $("dr-close").onclick = () => ($("drawer").hidden = true);
  addEventListener("keydown", (e) => {
    if (e.target.closest("input, select")) return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    if (e.key === "Escape") $("drawer").hidden = true;
    if (e.key === "+" || e.key === "=") zoom(1 / 1.35);
    if (e.key === "-") zoom(1.35);
  });
}

function setPlayIcon() {
  $("play").innerHTML = `<svg><use href="#i-${S.playing ? "pause" : "play"}"/></svg>`;
  $("play").setAttribute("aria-label", S.playing ? "Pause" : "Play");
}

function togglePlay() {
  if (S.t >= S.run.tEnd) S.t = 0;
  S.playing = !S.playing;
  setPlayIcon();
}

// Move the 3D camera toward or away from what it is looking at. Works in
// every 3D view, including a science close-up; the rover camera is a
// recorded frame, so it has nothing to zoom.
function zoom(f) {
  if (S.cam === "first") return;
  const c = world.controls, cam = world.camera;
  const off = cam.position.clone().sub(c.target);
  off.setLength(THREE.MathUtils.clamp(off.length() * f, c.minDistance, c.maxDistance));
  cam.position.copy(c.target).add(off);
  c.update();
}

function setTab(tab) {
  S.tab = tab;
  document.querySelectorAll(".rail button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  document.querySelectorAll(".pane").forEach((p) => (p.hidden = p.dataset.pane !== tab));
}

// The big view is the third-person chase camera; either small box can be
// blown up to fill the window, and "Back to rover view" returns.
function setCam(cam) {
  S.cam = cam;
  $("fp").hidden = cam !== "first";
  $("in-fp").hidden = cam === "first";
  $("in-map").hidden = cam === "overhead";
  $("back-chip").hidden = cam === "third";
  world.labels.domElement.style.display = cam === "first" ? "none" : "";
  if (cam === "third") world.setFollow(S.rover);
  if (cam === "overhead") overhead();
  render(true);
}

function setRover(a) {
  S.rover = a;
  document.querySelectorAll("#rover-seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.rover === a)));
  if (S.cam === "third") world.setFollow(a);
  S.lastPanel = "";
  render(true);
}

function overhead() {
  const run = S.run;
  world.follow = "none";
  const all = run.agencies.flatMap((a) => run.tracks[a].truth);
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) + 80;
  const tgt = world.groundL(cx, cy);
  world.controls.target.copy(tgt);
  world.camera.position.set(tgt.x, tgt.y - span * 0.03, tgt.z + span * 1.05);
  world.controls.update();
}

// A close view of the drill: from the rover's front right, where the arm works.
function closeUp(ev) {
  const [x, y, h] = S.run.poseAt(ev.agency, ev.t0);
  const c = Math.cos(h), s = Math.sin(h);
  const at = (fx, fy) => [x + fx * c - fy * s, y + fx * s + fy * c];
  const [dx, dy] = at(1.55, -0.42);
  const [ex, ey] = at(4.2, -4.6);
  world.follow = "none";
  world.controls.target.copy(world.groundL(dx, dy, 0.7));
  world.camera.position.copy(world.groundL(ex, ey, 2.8));
  world.controls.update();
}

function enterFocus(ev) {
  if (!S.prev) S.prev = { cam: S.cam, rover: S.rover };
  S.focusEv = ev;
  S.rover = ev.agency;
  document.querySelectorAll("#rover-seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.rover === ev.agency)));
  S.cam = "third";
  $("fp").hidden = true;
  $("in-fp").hidden = false;
  $("in-map").hidden = false;
  $("back-chip").hidden = true;
  world.labels.domElement.style.display = "";
  closeUp(ev);
  // Bring the science panel forward while the drill works, then put back whatever was open.
  if (S.tab !== "science") { S.tabBeforeFocus = S.tab; setTab("science"); }
  document.querySelector('.rail [data-tab="science"]').classList.add("live");
}

// byUser: the viewer picked another camera or rover mid-stop, so this stop
// plays on at normal speed instead of pulling the camera back.
function exitFocus(byUser = false) {
  if (!S.focusEv) return;
  if (byUser) S.dismissed = S.focusEv;
  S.focusEv = null;
  document.querySelector('.rail [data-tab="science"]').classList.remove("live");
  if (S.tabBeforeFocus) { setTab(S.tabBeforeFocus); S.tabBeforeFocus = null; }
  const p = S.prev;
  S.prev = null;
  if (p && !byUser) { S.rover = p.rover; setCam(p.cam); setRover(p.rover); }
}

const activeSci = (t) => sci.find((e) => t >= e.t0 - FOCUS_LEAD_S && t <= e.t1 + FOCUS_TAIL_S);

// -------------------------------------------------------------------- loop
let last = performance.now(), lastPanelAt = 0;
function loop(now) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  if (S.playing && S.run) {
    let adv = dt * (S.focusEv ? FOCUS_SPEED : S.speed);
    if (S.focus && !S.focusEv) {
      // Never jump over a science stop at high speed: stop at its start.
      const next = sci.find((e) => e.t0 - FOCUS_LEAD_S > S.t && e.t0 - FOCUS_LEAD_S <= S.t + adv);
      if (next) adv = next.t0 - FOCUS_LEAD_S - S.t;
    }
    S.t = Math.min(S.run.tEnd, S.t + adv);
    if (S.t >= S.run.tEnd) { S.playing = false; setPlayIcon(); }
  }
  if (S.run) {
    const ev = S.focus ? activeSci(S.t) : null;
    if (ev && ev !== S.focusEv && ev !== S.dismissed) enterFocus(ev);
    if (!ev && S.focusEv) exitFocus();
    const panels = now - lastPanelAt > 150;
    render(panels);
    if (panels) lastPanelAt = now;
  }
  requestAnimationFrame(loop);
}

// ------------------------------------------------------------------ render
function render(panels) {
  const run = S.run, t = S.t;
  if (!run) return;
  world.currentT = t;
  world.update(t, { tracks: S.opts.routes });
  for (const a of run.agencies) {
    const ev = sci.filter((e) => e.agency === a && t >= e.t0 - FOCUS_LEAD_S && t <= e.t1 + 1).at(-1) || null;
    rigs[a].setEvent(ev);
    rigs[a].update(t);
    // The arm shows only while an instrument is in use, so its appearance means "science now".
    const busy = ev && !["idle", "done", "park"].includes(rigs[a].phaseAt(t).name);
    rigs[a].mount.visible = !!busy || (ev && rigs[a].phaseAt(t).name === "done" && t < ev.t1 + 1);
    // Name tag: what the instrument is doing right now.
    const txt = instrumentNow(a, t, ev);
    if (tagEls[a] && tagEls[a].textContent !== txt) tagEls[a].textContent = txt;
    const ph = ev ? rigs[a].phaseAt(t).name : "idle";
    const on = txt === "neutron survey" || ph === "nir_scan";
    for (const m of (extras[a] || {}).sensors || []) m.material.emissive.setHex(on ? (a === "A" ? 0x33aa66 : 0x7744ff) : 0);
  }
  updateOverlay(t);
  if (S.cam !== "first") world.render();
  else showFrame($("fp-img"), $("fp-ov"), $("fp-none"), false);
  if (S.cam !== "first") showFrame($("fpi-img"), $("fpi-ov"), $("fpi-none"), true);
  if (S.cam !== "overhead") map.update(t);
  $("clock").textContent = fmtClock(t);
  $("tl-fill").style.width = `${(100 * t) / run.tEnd}%`;
  $("scrub").value = String(Math.round((1000 * t) / run.tEnd));
  if (panels) renderPanels(t);
}

function instrumentNow(a, t, ev) {
  const ph = ev ? rigs[a].phaseAt(t).name : "idle";
  if (ph === "drill") return `DRILLING ${(100 * rigs[a].depthAt(t)).toFixed(0)} cm`;
  if (ph === "nir_scan") return "NIR SCAN";
  if (ph === "analyze") return "ANALYSING CORE";
  if (ph === "arm_deploy" || ph === "arm_stow" || ph === "sample_lift") return "DRILL ARM";
  if (a === "A" && stateAt(a, t).state === "EXPLORING") return "neutron survey";
  return "";
}

function showFrame(img, cv, none, small) {
  const fr = S.run.frameNear(S.rover, S.t);
  none.hidden = !!fr;
  img.hidden = !fr;
  if (fr && img.dataset.src !== fr.src) { img.src = fr.src; img.dataset.src = fr.src; }
  drawFpOverlay(fr, cv, small);
}

// Project a world point into the recorded camera frame (pinhole, sim/scene.py camera_model()).
function project(pose, camZ, wx, wy, wz) {
  const [x, y, h] = pose;
  const dx = wx - x, dy = wy - y, up = wz - camZ;
  const fwd = dx * Math.cos(h) + dy * Math.sin(h), left = -dx * Math.sin(h) + dy * Math.cos(h);
  const depth = fwd * Math.cos(FP.pitch) - up * Math.sin(FP.pitch);
  if (depth < 1.2) return null;
  const yu = fwd * Math.sin(FP.pitch) + up * Math.cos(FP.pitch);
  return [FP.w / 2 - (FP.fx * left) / depth, FP.h / 2 - (FP.fx * yu) / depth, depth];
}

// On the camera frame: the route the rover is following right now, any
// obstacle it has found, and the sites ahead. In the small box the strokes
// and text are drawn larger so they survive being scaled down.
function drawFpOverlay(fr, cv, small) {
  const c = cv.getContext("2d");
  c.clearRect(0, 0, FP.w, FP.h);
  if (!fr) return;
  const k = small ? 2 : 1;
  const font = (px) => `600 ${px * k}px Barlow, Helvetica, Arial, sans-serif`;
  const a = S.rover, run = S.run, tf = run.times[fr.k];
  const pose = run.steps[fr.k].rovers[a].true_pose;
  const camZ = terrain.heightAt(pose[0], pose[1]) + FP.camH;
  const P = (x, y, lift = 0.05) => project(pose, camZ, x, y, terrain.heightAt(x, y) + lift);
  const col = a === "A" ? "#5aa7ff" : "#f2994a";
  const route = planned.filter((p) => p.agency === a && p.t <= tf).at(-1);
  const st = stateAt(a, tf).state;
  if (route && S.opts.routes && ["EXPLORING", "NAVIGATING", "REPLANNING", "PLANNING"].includes(st)) {
    const pts = route.points;
    let best = 0, bd = Infinity;
    pts.forEach((p, i) => { const d = Math.hypot(p[0] - pose[0], p[1] - pose[1]); if (d < bd) { bd = d; best = i; } });
    const path = [[pose[0] + 2 * Math.cos(pose[2]), pose[1] + 2 * Math.sin(pose[2])]];
    let len = 0;
    for (let i = best + 1; i < pts.length && len < 40; i++) {
      const q = path.at(-1), d = Math.hypot(pts[i][0] - q[0], pts[i][1] - q[1]), n = Math.max(1, Math.ceil(d));
      for (let j = 1; j <= n; j++) path.push([q[0] + ((pts[i][0] - q[0]) * j) / n, q[1] + ((pts[i][1] - q[1]) * j) / n]);
      len += d;
    }
    c.lineJoin = c.lineCap = "round";
    for (const [w, style] of [[9 * k, "rgba(0,0,0,.55)"], [4 * k, col]]) {
      c.beginPath();
      let open = false;
      for (const p of path) {
        const q = P(p[0], p[1]);
        if (!q) { open = false; continue; }
        open ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]);
        open = true;
      }
      c.lineWidth = w; c.strokeStyle = style; c.stroke();
    }
    c.fillStyle = col; c.font = font(14);
    const msg = st === "REPLANNING" ? "Replanning" : route.kind === "replanned" ? `To ${route.target_id}, around obstacle` : `To ${route.target_id}`;
    c.textAlign = "right"; c.fillText(msg, FP.w - 12, 12 + 14 * k); c.textAlign = "left";
  }
  for (const h of hazards) {
    if (!S.opts.hazards || tf < h.seen_t) continue;
    c.beginPath();
    let open = false, top = null;
    for (let i = 0; i <= 48; i++) {
      const an = (i / 48) * Math.PI * 2, q = P(h.x + h.radius * Math.cos(an), h.y + h.radius * Math.sin(an));
      if (!q) { open = false; continue; }
      open ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]);
      open = true;
      if (!top || q[1] < top[1]) top = q;
    }
    c.lineWidth = 3 * k; c.strokeStyle = "#ff5d5d"; c.fillStyle = "rgba(255,93,93,.18)"; c.fill(); c.stroke();
    if (top) { c.fillStyle = "#ff5d5d"; c.font = font(13); c.fillText("OBSTACLE", top[0] - 34 * k, top[1] - 8); }
  }
  const sites = registryAt(tf);
  for (const s of run.log.satellite_sites || []) {
    const r = [...sites.values()].find((v) => v.target === s.id);
    const ice = r && (r.surveyor === "Positive" || r.prospector === "Positive");
    const color = ice ? "#ff4d4d" : r ? "#9aa7b8" : "#e0a83a";
    const base = P(s.x, s.y), tip = P(s.x, s.y, 6);
    if (!base || !tip || base[2] > 250) continue;
    c.strokeStyle = color; c.lineWidth = 2 * k;
    c.beginPath(); c.moveTo(base[0], base[1]); c.lineTo(tip[0], tip[1]); c.stroke();
    c.fillStyle = color; c.font = font(12);
    c.fillText(`${s.id}${ice ? " ICE" : ""} · ${base[2].toFixed(0)} m`, tip[0] + 5, tip[1] + 4);
  }
}

function registryAt(t) {
  const sites = new Map();
  for (const e of reg) {
    if (e.t > t) break;
    let s = sites.get(e.site_id);
    if (!s) { s = { site_id: e.site_id, target: e.target_id, stage: "Candidate", surveyor: "Unknown", prospector: "Unknown", writes: [] }; sites.set(e.site_id, s); }
    if (e.call === "registerCandidate") Object.assign(s, { x: e.x, y: e.y, confidence: e.confidence });
    if (e.call === "submitSurveyorInspection") Object.assign(s, { stage: "SurveyorInspected", surveyor: e.result });
    if (e.call === "submitProspectorAnalysis") Object.assign(s, { stage: "ProspectorAnalyzed", prospector: e.result });
    s.writes.push(e);
  }
  return sites;
}

function siteStatus(s) {
  const both = s.stage === "ProspectorAnalyzed";
  return both ? (s.prospector === "Positive" ? "Positive" : "Analyzed") : s.stage === "SurveyorInspected" ? s.surveyor : "Candidate";
}

function updateOverlay(t) {
  const sites = registryAt(t);
  for (const [sid, o] of siteObjs) {
    const s = sites.get(sid);
    o.g.visible = !!s;
    if (!s) continue;
    const both = s.stage === "ProspectorAnalyzed";
    const status = siteStatus(s);
    const key = `${status}${both}`;
    if (o.key !== key) {
      o.key = key;
      o.ring.material = o.mats[status];
      o.el.style.color = "#" + SITE_COLOR[status].toString(16).padStart(6, "0");
      o.beam.material.color.set(SITE_COLOR[status]);
      o.disc.visible = status === "Positive";
      const words = { Candidate: "candidate", Positive: both ? "ICE CONFIRMED" : "ICE", Negative: "no ice",
        Inconclusive: "unclear", Analyzed: s.prospector.toLowerCase() }[status];
      o.el.textContent = `${o.target} · ${words}`;
    }
  }
  // During a close-up, clear the markers that would stand in front of the drill.
  const fa = S.focusEv ? S.focusEv.agency : null;
  for (const o of siteObjs.values()) o.beam.visible = !fa;
  for (const ag of S.run.agencies) {
    if (world.estDots[ag]) world.estDots[ag].visible = S.opts.routes && ag !== fa;
    if (world.sigmaRings[ag]) world.sigmaRings[ag].visible = S.opts.routes && ag !== fa;
  }
  const done = new Set([...sites.values()].map((s) => s.target));
  for (const o of satObjs) o.g.visible = !done.has(o.id);
  for (const o of siteObjs.values()) if (o.disc.visible) o.disc.material.opacity = 0.28 + 0.14 * Math.sin(performance.now() / 300);
  for (const h of hazards) if (h.obj) h.obj.visible = S.opts.hazards && t >= h.seen_t;
  for (const a of S.run.agencies) {
    const st = stateAt(a, t).state;
    const latest = planned.filter((p) => p.agency === a && p.t <= t).at(-1);
    for (const p of planned.filter((q) => q.agency === a)) {
      p.line.visible = S.opts.routes && p === latest && ["EXPLORING", "NAVIGATING", "PLANNING", "REPLANNING", "NAVIGATING_TO_SITE"].includes(st);
    }
  }
}

function stateAt(a, t) {
  const list = (S.run.log.states || {})[a] || [];
  let cur = list[0] || { state: "IDLE", objective: "" };
  for (const s of list) { if (s.t <= t) cur = s; else break; }
  return cur;
}

// The overhead box: Joseph's 2D map (hillshade, tracks, rovers), with this
// mission's satellite sites, registry sites and obstacles drawn on top.
class MissionMap extends MapView {
  draw() {
    super.draw();
    const run = this.run;
    if (!run || !this.view || !S.run) return;
    const c = this.ctx, W = this.cv.clientWidth, H = this.cv.clientHeight;
    const w = this.window(), s = this.view.s, cx = w.cx + this.pan[0], cy = w.cy + this.pan[1];
    const X = (x) => W / 2 + (x - cx) * s, Y = (y) => H / 2 - (y - cy) * s;
    const t = this.t || 0, sites = registryAt(t);
    const done = new Set([...sites.values()].map((v) => v.target));
    c.lineWidth = 1.5;
    for (const o of S.run.log.satellite_sites || []) {
      if (done.has(o.id)) continue;
      c.setLineDash([3, 3]); c.strokeStyle = "#e0a83a";
      c.beginPath(); c.arc(X(o.x), Y(o.y), 5, 0, Math.PI * 2); c.stroke();
    }
    c.setLineDash([]);
    for (const v of sites.values()) {
      const st = siteStatus(v), col = "#" + SITE_COLOR[st].toString(16).padStart(6, "0");
      if (st === "Positive") {
        c.fillStyle = "rgba(255,77,77,.28)";
        c.beginPath(); c.arc(X(v.x), Y(v.y), 9, 0, Math.PI * 2); c.fill();
      }
      c.fillStyle = col;
      c.beginPath(); c.arc(X(v.x), Y(v.y), 4, 0, Math.PI * 2); c.fill();
    }
    if (S.opts.hazards) {
      c.strokeStyle = "#ff5d5d"; c.lineWidth = 2;
      for (const h of hazards) {
        if (t < h.seen_t) continue;
        const r = Math.max(4, h.radius * s);
        c.beginPath(); c.arc(X(h.x), Y(h.y), r, 0, Math.PI * 2); c.stroke();
      }
    }
  }
}

// ------------------------------------------------------------------ panels
function renderPanels(t) {
  renderRover(t);
  renderToast(t);
  renderScience(t);
  renderChain(t);
  renderRegistry(t);
}

function renderRover(t) {
  const run = S.run, k = run.stepAt(t);
  $("now").innerHTML = run.agencies.map((a) => {
    const r = run.rover(a, k), st = stateAt(a, t);
    const [x, y] = run.poseAt(a, t), [x2, y2] = run.poseAt(a, Math.max(0, t - 20));
    const v = Math.hypot(x - x2, y - y2) / Math.min(20, Math.max(t, 1e-6));
    const soc = r.soc ?? null;
    return `<div class="rv ${a === S.rover ? "sel" : ""}" data-rover="${a}">
      <div class="rv-head"><i class="dot ${a}"></i>${ROLE[a]} ${a}<span class="doing">${esc(DOING[st.state] || st.state)}</span></div>
      <div class="rv-obj">${esc(st.objective)}</div>
      <div class="stats">
        <div class="stat"><b>${v < 0.005 ? "0" : (100 * v).toFixed(0)}</b><span>cm/s</span></div>
        <div class="stat"><b>${soc == null ? "–" : (100 * soc).toFixed(0) + "%"}</b><span>battery</span>${soc == null ? "" : `<div class="batt"><i style="width:${100 * soc}%;background:${soc < 0.3 ? "var(--neg)" : "var(--pos)"}"></i></div>`}</div>
        <div class="stat"><b>±${r.sigma.toFixed(1)}</b><span>m position</span></div>
      </div></div>`;
  }).join("");
  $("now").querySelectorAll(".rv").forEach((el) => (el.onclick = () => { exitFocus(true); setRover(el.dataset.rover); }));
}

// One line over the play bar: who is doing what, and how fast time runs.
function renderToast(t) {
  const run = S.run, el = $("toast");
  let html;
  if (S.focusEv) {
    const ev = S.focusEv, a = ev.agency;
    const what = instrumentNow(a, t, ev).toLowerCase() || "science";
    html = `<i class="dot ${a}"></i> <b>${ROLE[a]} ${a}</b> · ${esc(what)} at ${esc(ev.target_id)}<span class="sp">${FOCUS_SPEED}× for science</span>`;
  } else if (t >= run.tEnd) {
    const ice = [...registryAt(t).values()].filter((s) => siteStatus(s) === "Positive").length;
    html = `<b>Mission complete</b> · ice at ${ice} site${ice === 1 ? "" : "s"}`;
  } else if (!S.playing && t === 0) {
    html = `Press <b>play</b> to start · drag to look around`;
  } else {
    const a = S.rover, st = stateAt(a, t);
    html = `<i class="dot ${a}"></i> <b>${ROLE[a]} ${a}</b> · ${esc(DOING[st.state] || st.state)}<span class="sp">${S.playing ? S.speed + "×" : "Paused"}</span>`;
  }
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
  el.hidden = false;
}

async function evidence(cid) {
  if (!cid) return null;
  if (!evidenceCache.has(cid)) {
    evidenceCache.set(cid, fetch(`${S.run.dir}evidence/${cid}.json`).then(async (res) => {
      if (!res.ok) return null;
      const buf = await res.arrayBuffer();
      return { buf, json: JSON.parse(new TextDecoder().decode(buf)) };
    }).catch(() => null));
  }
  return evidenceCache.get(cid);
}

function renderScience(t) {
  const ev = S.focusEv || sci.filter((e) => e.t0 <= t).at(-1);
  $("sci-empty").hidden = !!ev;
  $("sci-body").hidden = !ev;
  if (!ev) { $("sci-title").textContent = "Science"; return; }
  const rig = rigs[ev.agency];
  const ph = ev.phases;
  let cur = -1;
  ph.forEach((p, i) => { if (t >= p.t0) cur = i; });
  const finished = t >= ev.t1;
  $("sci-title").innerHTML = `<i class="dot ${ev.agency}"></i>${ROLE[ev.agency]} · ${esc(ev.target_id)}`;
  $("sci-phases").innerHTML = ph.map((p, i) =>
    `<li class="${finished || i < cur ? "done" : i === cur ? "now" : ""}">${PHASE[p.name] || p.name}</li>`).join("");
  const key = `${ev.record_id}|${cur}|${finished}|${Math.floor(rig.depthAt(t) * 10)}`;
  if (S.lastPanel === key) return;
  S.lastPanel = key;
  const drillIdx = ph.findIndex((p) => p.name === "drill");
  const pastDrill = cur > drillIdx || finished;
  const depth = cur === drillIdx ? rig.depthAt(t) : pastDrill ? DRILL_DEPTH_M : 0;
  evidence(ev.report_cid).then((e) => {
    if (S.lastPanel !== key) return;
    const bins = e ? e.json.measurements.water_wt_pct_by_depth : [];
    const max = Math.max(1, ...bins.map((b) => b[1]));
    $("sci-bins").innerHTML = bins.length ? bins.map(([d, w]) => {
      const got = d <= depth + 1e-6;
      const nowBin = got && d > depth - 0.1;
      return `<div class="b ${got ? (nowBin && !pastDrill ? "now" : "") : "pending"}"><span>${(d * 100).toFixed(0)} cm</span><span class="bar2"><i style="width:${got ? (100 * w) / max : 0}%"></i></span><span>${got ? w.toFixed(2) + "%" : "…"}</span></div>`;
    }).join("") : `<div class="hint">Waiting for the core.</div>`;
    const analysed = ph.findIndex((p) => p.name === "analyze");
    const done = finished || cur > analysed;
    if (!done) {
      $("sci-result").innerHTML = `<div class="hint">${cur < drillIdx ? "Getting ready" : cur === drillIdx ? `Drilling · ${(depth * 100).toFixed(0)} cm` : "Reading the core"}</div>`;
      return;
    }
    const res = e ? e.json.result : { verdict: ev.verdict, reading_wt_pct: ev.reading };
    const word = { ice: "Water ice", dry: "No ice", trace: "Trace only" }[res.verdict] || res.verdict;
    const conf = res.confidence ? `${res.confidence.level} confidence` : "";
    $("sci-result").innerHTML = `<div class="verdict ${res.verdict}">${word}<small>${esc(conf)}</small><span class="sim-tag" title="Simulated measurement, not NASA data">SIMULATED</span></div>
      ${ev.report_cid ? `<div class="small hint" style="margin-top:4px">Report <a class="mono" href="${S.run.dir}evidence/${ev.report_cid}.json" target="_blank" rel="noopener">${short(ev.report_cid, 10, 6)}</a></div>` : ""}`;
  });
}

function writeHtml(e) {
  const R = S.run.log.registry || {};
  const mode = e.mode || R.mode;
  const [on, off] = SPLIT[e.call] || ["Hash", "Report"];
  const exp = S.run.chain && S.run.chain.explorer;
  const tx = exp ? `<a class="mono" href="${exp}/tx/${e.tx}" target="_blank" rel="noopener">${short(e.tx, 6, 4)}</a>` : `<span class="mono">${short(e.tx, 6, 4)}</span>`;
  return `<div class="chain-item">
    <div class="what">${esc(CALL[e.call])} · ${esc(e.target_id)}${e.result ? ` <span class="res ${e.result}">${e.result}</span>` : ""}<span class="tag ${mode}" style="margin-left:auto">${esc(MODE_SHORT[mode] || mode)}</span></div>
    <div class="split"><div><span class="k">On chain</span>${esc(on)}</div><div><span class="k">Off chain</span>${esc(off)}</div></div>
    <div class="verify-row"><button class="btn verify-btn" data-cid="${e.report_cid}" data-hash="${e.evidence_hash}" title="Re-hash the off-chain report and compare with the chain">Verify</button><span class="verify"></span><span class="hint small" style="margin-left:auto">tx ${tx} · block ${e.block}</span></div>
  </div>`;
}

function renderChain(t) {
  const done = reg.filter((e) => e.t <= t);
  const key = `chain|${done.length}`;
  if ($("chain-now").dataset.key === key) return;
  $("chain-now").dataset.key = key;
  $("chain-now").innerHTML = done.length ? writeHtml(done.at(-1))
    : `<p class="hint">Nothing yet. A record is written when a site is found, drilled, or analysed.</p>`;
  bindVerify($("chain-now"));
  $("txs").innerHTML = done.slice(0, -1).reverse().map((e) =>
    `<li><span class="t">${fmtClock(e.t).replace("T+", "")}</span><span><i class="dot ${e.agency}"></i> ${esc(CALL[e.call])} · <b>${esc(e.target_id)}</b>${e.result ? ` <span class="res ${e.result}">${e.result}</span>` : ""}</span></li>`).join("");
}

function bindVerify(root) {
  root.querySelectorAll(".verify-btn").forEach((b) => (b.onclick = async () => {
    const out = b.nextElementSibling;
    out.textContent = "Hashing…";
    const e = await evidence(b.dataset.cid);
    if (!e) { out.innerHTML = `<span class="bad">Report not found</span>`; return; }
    const got = "0x" + keccak256(e.buf);
    const ok = got.toLowerCase() === (b.dataset.hash || "").toLowerCase();
    out.innerHTML = ok ? `<span class="ok">✓ Matches the chain</span>` : `<span class="bad">✕ Does not match</span>`;
    out.title = ok ? "keccak256 of the report file equals the hash stored on chain" : `${got} vs ${b.dataset.hash}`;
  }));
}

function renderRegistry(t) {
  const sites = [...registryAt(t).values()];
  const key = `reg|` + sites.map((s) => `${s.target}${s.stage}${s.surveyor}${s.prospector}`).join();
  if ($("reg-body").dataset.key === key) return;
  $("reg-body").dataset.key = key;
  $("reg-count").textContent = sites.length ? String(sites.length) : "";
  $("reg-body").innerHTML = sites.map((s) => {
    const n = STAGE_N[s.stage];
    const pips = [1, 2, 3].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("");
    return `<tr data-site="${s.site_id}"><td><b>${esc(s.target)}</b></td><td><span class="pips" title="${s.stage}">${pips}</span></td>
      <td><span class="res ${s.surveyor}">${s.surveyor === "Unknown" ? "pending" : s.surveyor}</span></td>
      <td><span class="res ${s.prospector}">${s.stage === "ProspectorAnalyzed" ? s.prospector : s.surveyor === "Positive" ? "queued" : "–"}</span></td></tr>`;
  }).join("") || `<tr><td colspan="4" class="hint">No sites yet.</td></tr>`;
  $("reg-body").querySelectorAll("tr[data-site]").forEach((tr) => (tr.onclick = () => openSite(tr.dataset.site)));
}

async function openSite(sid) {
  const site = (S.run.log.registry_sites || []).find((s) => s.site_id === sid);
  const writes = reg.filter((e) => e.site_id === sid && e.t <= S.t);
  $("drawer").hidden = false;
  $("dr-title").textContent = `Site ${writes[0]?.target_id || ""}`;
  const known = new Set(writes.map((w) => w.report_id));
  const reps = (site ? site.reports : []).filter((r) => known.has(r.report_id));
  const body = reps.map((r) => {
    const w = writes.find((x) => x.report_id === r.report_id) || {};
    return `<div class="rep ${r.result}">
      <div><b>Report #${r.report_id}</b> · ${esc(r.stage)} · <span class="res ${r.result}">${r.result === "Unknown" ? "candidate" : r.result}</span>${r.parent_report_id ? ` · follows #${r.parent_report_id}` : ""}</div>
      <dl class="kv"><dt>Hash</dt><dd class="mono">${esc(r.evidence_hash)}</dd>
      <dt>Report</dt><dd class="mono"><a href="${S.run.dir}evidence/${r.report_cid}.json" target="_blank" rel="noopener">${esc(r.report_cid)}</a></dd>
      ${r.route_cid ? `<dt>Route</dt><dd class="mono"><a href="${S.run.dir}evidence/${r.route_cid}.json" target="_blank" rel="noopener">${esc(r.route_cid)}</a></dd>` : ""}
      <dt>Tx</dt><dd class="mono">${esc(w.tx || "")} (block ${w.block ?? "–"})</dd>
      <dt>By</dt><dd class="mono">${esc(r.submitter)}</dd></dl>
      <div class="verify-row"><button class="btn verify-btn" data-cid="${r.report_cid}" data-hash="${r.evidence_hash}">Verify</button><span class="verify"></span></div>
      <details style="margin-top:8px"><summary class="hint">Show the report</summary><pre class="json" data-cid="${r.report_cid}">Loading…</pre></details>
    </div>`;
  }).join("");
  $("dr-body").innerHTML = (body || `<p class="hint">Nothing published for this site yet.</p>`) +
    `<p class="hint small">Each report is a new record that names the one before it; nothing on chain is edited.</p>`;
  bindVerify($("dr-body"));
  $("dr-body").querySelectorAll("details").forEach((d) => (d.ontoggle = async () => {
    const pre = d.querySelector("pre");
    const e = await evidence(pre.dataset.cid);
    pre.textContent = e ? JSON.stringify(e.json, null, 1) : "Not found.";
  }));
}

main();
