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
import { frostPatch, ICE_HEX, ICE_CSS } from "./frost.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const short = (h, a = 8, b = 6) => (h ? `${h.slice(0, a)}…${h.slice(-b)}` : "");
const ROLE = { A: "Surveyor", B: "Prospector" };
// The state machines' states, in words a visitor reads at a glance.
const DOING = {
  IDLE: "Waiting", EXPLORING: "Surveying", CANDIDATE_FOUND: "Candidate found", NAVIGATING_TO_SITE: "Driving to site",
  MAPPING_HAZARD: "Mapping a hazard", INSPECTING: "Mapping the site", PUBLISHING: "Publishing", RESUMING: "Resuming", ERROR: "Error",
  FETCHING_TARGET: "Reading registry", PLANNING: "Planning", NAVIGATING: "Driving", REPLANNING: "Replanning",
  ANALYZING: "Analysing", REPORTING: "Publishing", COMPLETED: "Done",
};
const PHASE = { park: "Park", nir_scan: "NIR scan", arm_deploy: "Arm out", drill: "Drill 1 m", sample_lift: "Core up",
  analyze: "Analyse", arm_stow: "Stow", stop: "Stop", mast_pan: "Camera pan", terrain_map: "Map terrain",
  neutron_dwell: "Neutron dwell", photo: "Photo", flag: "Flag" };
// What the Surveyor's name tag says during each step of a stop.
const A_TAG = { mast_pan: "CAMERA PAN", terrain_map: "MAPPING TERRAIN", neutron_dwell: "NEUTRON DWELL", photo: "PHOTO",
  flag: "FLAGGING HAZARD" };
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
const SITE_COLOR = { Candidate: 0x9aa7b8, Positive: ICE_HEX, Negative: 0x8d939b, Inconclusive: 0xd9b44a, Analyzed: 0x58b4ff };
const FOCUS_SPEED = 1.5;   // sim seconds per real second during a science stop
const FOCUS_LEAD_S = 3;    // the close-up starts this many sim seconds before the stop
const FOCUS_TAIL_S = 3;    // and holds this long after it
const NARROW = matchMedia("(max-width: 760px)");

const S = {
  run: null, t: 0, playing: false, speed: 200, cam: "third", rover: "A", tab: "rover", tabBeforeFocus: null,
  opts: { routes: true, hazards: true, truth: false }, focus: true, focusEv: null, prev: null, lastPanel: "",
};
let terrain, world, map, rigs = {}, ov, sci = [], reg = [], hazards = [], planned = [], siteObjs = new Map();
let satObjs = [], tagEls = {}, extras = {}, dressed = {}, odo = {}, susp = {}, fans = {};
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

  // Only the Prospector has an arm and a drill; the Surveyor works with its mast camera.
  for (const a of run.agencies) {
    if (!dressed[a]) { dressed[a] = true; dressRover(a); }
    if (a === "B" && !rigs[a]) rigs[a] = new ScienceRig(world.rovers[a].g, world.scene, { up: "z" });
  }
  odo = {};
  for (const a of run.agencies) {   // distance driven by each step, for the wheels
    const T = run.tracks[a].truth, c = [0];
    for (let i = 1; i < T.length; i++) c.push(c[i - 1] + Math.hypot(T[i][0] - T[i - 1][0], T[i][1] - T[i - 1][1]));
    odo[a] = c;
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
    // The mast camera's field of view on the ground while it maps a site: a
    // pale wedge ahead of the rover, 60 deg wide like the camera.
    const fan = new THREE.Mesh(new THREE.CircleGeometry(14, 32, -Math.PI / 6, Math.PI / 3),
      new THREE.MeshBasicMaterial({ color: 0x7fd8ff, transparent: true, opacity: 0.16, depthWrite: false,
        side: THREE.DoubleSide, blending: THREE.AdditiveBlending }));
    fan.position.set(0.1, 0, 0.25);
    fan.visible = false;
    g.add(fan);
    fans.A = fan;
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

// ----------------------------------------------------------- suspension
// NASA's Perseverance file has all six wheels in one mesh ("Wheels_objs") and
// the rocker-bogie in another ("suspension"). We cut the wheels apart by
// clustering their vertices (3 along the body, 2 across), give each its own
// pivot at its centre, and do the same for the two rocker halves. Opportunity
// is a single mesh, so its body tilts and lifts but its wheels do not move alone.
function clusterMesh(mesh, seedsFor) {
  const geo = mesh.geometry, pos = geo.attributes.position, n = pos.count;
  if (!geo.index) return null;
  const get = [(i) => pos.getX(i), (i) => pos.getY(i), (i) => pos.getZ(i)];
  geo.computeBoundingBox();
  const bb = geo.boundingBox, ctr = bb.getCenter(new THREE.Vector3()), ext = bb.getSize(new THREE.Vector3());
  let best = null;
  for (const [fa, la] of [[0, 2], [2, 0]]) {       // glTF is y up: forward is x or z
    const seeds = seedsFor(ctr.getComponent(fa), ext.getComponent(fa), ctr.getComponent(la), ext.getComponent(la));
    const lab = new Uint8Array(n);
    let sse = 0;
    for (let it = 0; it < 8; it++) {
      const acc = seeds.map(() => [0, 0, 0]);
      sse = 0;
      for (let v = 0; v < n; v++) {
        const f = get[fa](v), l = get[la](v);
        let bi = 0, bd = Infinity;
        for (let k = 0; k < seeds.length; k++) {
          const d = (f - seeds[k][0]) ** 2 + (l - seeds[k][1]) ** 2;
          if (d < bd) { bd = d; bi = k; }
        }
        lab[v] = bi; acc[bi][0] += f; acc[bi][1] += l; acc[bi][2]++; sse += bd;
      }
      acc.forEach((q, k) => { if (q[2]) seeds[k] = [q[0] / q[2], q[1] / q[2]]; });
    }
    if (!best || sse < best.sse) best = { lab, sse, seeds, fa, la };
  }
  // One pivot per cluster, at the cluster's centre, holding that cluster's triangles.
  const idx = geo.index.array, root = new THREE.Group();
  root.position.copy(mesh.position); root.quaternion.copy(mesh.quaternion); root.scale.copy(mesh.scale);
  mesh.parent.add(root);
  mesh.visible = false;
  const parts = best.seeds.map(() => ({ tri: [], min: new THREE.Vector3(Infinity, Infinity, Infinity), max: new THREE.Vector3(-Infinity, -Infinity, -Infinity) }));
  for (let i = 0; i < idx.length; i += 3) parts[best.lab[idx[i]]].tri.push(idx[i], idx[i + 1], idx[i + 2]);
  for (let v = 0; v < n; v++) {
    const q = parts[best.lab[v]];
    q.min.min(new THREE.Vector3(pos.getX(v), pos.getY(v), pos.getZ(v)));
    q.max.max(new THREE.Vector3(pos.getX(v), pos.getY(v), pos.getZ(v)));
  }
  const out = [];
  for (const q of parts) {
    if (!q.tri.length) continue;
    const g2 = new THREE.BufferGeometry();
    for (const [k, v] of Object.entries(geo.attributes)) g2.setAttribute(k, v);
    g2.setIndex(q.tri);
    const m = new THREE.Mesh(g2, mesh.material);
    m.castShadow = m.receiveShadow = true;
    const c = q.min.clone().add(q.max).multiplyScalar(0.5);
    const pivot = new THREE.Group();
    pivot.position.copy(c);
    m.position.copy(c).negate();
    pivot.add(m);
    root.add(pivot);
    out.push({ pivot, base: c.clone(), radius: (q.max.y - q.min.y) / 2, la: best.la, fa: best.fa });
  }
  return out;
}

function setupSuspension(a, g, sg, fit) {
  const box = new THREE.Box3().setFromObject(fit), size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
  const S_ = { g: sg, pitch: 0, roll: 0, z: 0, wheels: [], rockers: [], contacts: [] };
  const wheelMesh = fit.getObjectByName("Wheels_objs"), rockMesh = fit.getObjectByName("suspension");
  if (wheelMesh && wheelMesh.isMesh) {
    const w = clusterMesh(wheelMesh, (fc, fe, lc, le) => [-1, 0, 1].flatMap((i) => [-1, 1].map((j) => [fc + (i * fe) / 2.6, lc + (j * le) / 3])));
    if (w && w.length === 6) {
      g.updateMatrixWorld(true);
      const sc = new THREE.Vector3();
      w[0].pivot.getWorldScale(sc);
      for (const wh of w) {
        const p = wh.pivot.getWorldPosition(new THREE.Vector3());
        g.worldToLocal(p);
        S_.wheels.push({ ...wh, fx: p.x, fy: p.y, k: sc.x, rM: wh.radius * sc.x });
      }
      S_.contacts = S_.wheels.map((wh) => [wh.fx, wh.fy]);
    }
  }
  if (rockMesh && rockMesh.isMesh && S_.wheels.length) {
    const r = clusterMesh(rockMesh, (fc, fe, lc, le) => [[fc, lc - le / 4], [fc, lc + le / 4]]);
    if (r) {
      g.updateMatrixWorld(true);
      for (const half of r) {
        const p = half.pivot.getWorldPosition(new THREE.Vector3());
        g.worldToLocal(p);
        S_.rockers.push({ ...half, fy: p.y });
      }
    }
  }
  if (!S_.contacts.length) {   // no wheels to cut out: six contact points under the body
    const L = size.x * 0.36, W = size.y * 0.4;
    for (const fx of [c.x - L, c.x, c.x + L]) for (const fy of [-W, W]) S_.contacts.push([fx, fy]);
  }
  susp[a] = S_;
}

// Each frame: fit a plane to the ground under the wheels, tilt and lift the
// body onto it, then let each wheel follow the ground under it (what is left
// over from the plane, a few cm) and spin by the distance driven.
function updateSuspension(a, t) {
  const S_ = susp[a];
  if (!S_) return;
  const [x, y, h] = S.run.poseAt(a, t);
  const c = Math.cos(h), s = Math.sin(h), z0 = terrain.heightAt(x, y);
  const pts = S_.contacts.map(([fx, fy]) => [fx, fy, terrain.heightAt(x + fx * c - fy * s, y + fx * s + fy * c) - z0]);
  const n = pts.length;
  const mx = pts.reduce((q, p) => q + p[0], 0) / n, my = pts.reduce((q, p) => q + p[1], 0) / n, mz = pts.reduce((q, p) => q + p[2], 0) / n;
  let sxx = 0, syy = 0, sxy = 0, sxz = 0, syz = 0;
  for (const [fx, fy, z] of pts) {
    const dx = fx - mx, dy = fy - my, dz = z - mz;
    sxx += dx * dx; syy += dy * dy; sxy += dx * dy; sxz += dx * dz; syz += dy * dz;
  }
  const det = sxx * syy - sxy * sxy || 1;
  const b = (sxz * syy - syz * sxy) / det, cc = (syz * sxx - sxz * sxy) / det;
  const plane = (fx, fy) => mz + b * (fx - mx) + cc * (fy - my);
  const pitch = Math.atan(b), roll = Math.atan(cc), zc = plane(0, 0);
  const jump = Math.abs(pitch - S_.pitch) > 0.25 || Math.abs(roll - S_.roll) > 0.25;
  const k = jump ? 1 : 0.25;
  S_.pitch += (pitch - S_.pitch) * k; S_.roll += (roll - S_.roll) * k; S_.z += (zc - S_.z) * k;
  S_.g.rotation.set(S_.roll, -S_.pitch, 0);
  S_.g.position.z = S_.z;
  if (!S_.wheels.length) return;
  const T = odo[a], kk = S.run.stepAt(t), f = kk < T.length - 1 ? (t - S.run.times[kk]) / (S.run.times[kk + 1] - S.run.times[kk]) : 0;
  const dist = T[kk] + (kk < T.length - 1 ? (T[kk + 1] - T[kk]) * f : 0);
  const res = pts.map(([fx, fy, z]) => THREE.MathUtils.clamp(z - plane(fx, fy), -0.18, 0.18));
  S_.wheels.forEach((wh, i) => {
    wh.pivot.position.y = wh.base.y + res[i] / wh.k;
    wh.pivot.rotation[wh.la === 0 ? "x" : "z"] = -dist / wh.rM;
  });
  // Rockers: each side tips by the difference between its front and rear wheels.
  for (const rk of S_.rockers) {
    const side = S_.wheels.map((wh, i) => [wh, res[i]]).filter(([wh]) => Math.sign(wh.fy) === Math.sign(rk.fy));
    if (side.length < 2) continue;
    side.sort((p, q) => q[0].fx - p[0].fx);
    const span = side[0][0].fx - side.at(-1)[0].fx || 1;
    rk.pivot.rotation[rk.la === 0 ? "x" : "z"] = Math.atan2(side[0][1] - side.at(-1)[1], span) * 0.6;
  }
}

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
    // The body rides on a suspension group that tilts and lifts with the
    // ground under its wheels (updateSuspension); the wheels, where the model
    // lets us cut them apart, spin and ride up and down on their own.
    const sg = new THREE.Group();
    sg.name = "suspension";
    sg.add(fit);
    g.add(sg);
    g.updateMatrixWorld(true);
    setupSuspension(a, g, sg, fit);
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
    // Ice found here: a frost patch on the ground, so nobody can miss it.
    const disc = frostPatch(world, e.x, e.y, 4.5, e.site_id);
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
    const rim = h.crater_d ? h.crater_d / 2 : null;
    for (let i = 0; i < 34; i++) {
      // Boulders on the crater's rim when it is a crater, scattered otherwise.
      const a = rnd() * Math.PI * 2, r = rim ? rim - 0.4 + rnd() * 1.8 : Math.sqrt(rnd()) * h.radius;
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
    el.textContent = h.hazard_id ? `Crater ${h.hazard_id} · flagged` : "Obstacle";
    el.title = `Not on the orbital map. Found, mapped and flagged by ${ROLE[h.seen_by]} ${h.seen_by}`;
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

// Ticks on the play bar: blue for a science stop, ice white where ice was found,
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
  $("back-chip").onclick = () => { exitFocus(true); setCam("third"); };
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
  $("in-map").hidden = false;
  $("back-chip").hidden = true;
  if (ev.agency === "A") {
    // The Surveyor's stops play in its own camera: what it sees, and what it maps.
    S.cam = "first";
    $("fp").hidden = false;
    $("in-fp").hidden = true;
    $("back-chip").hidden = false;
    world.labels.domElement.style.display = "none";
  } else {
    S.cam = "third";
    $("fp").hidden = true;
    $("in-fp").hidden = false;
    world.labels.domElement.style.display = "";
    closeUp(ev);
  }
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
    updateSuspension(a, t);
    const ev = sci.filter((e) => e.agency === a && t >= e.t0 - FOCUS_LEAD_S && t <= e.t1 + 1).at(-1) || null;
    const rig = rigs[a];
    if (rig) {
      rig.setEvent(ev);
      rig.update(t);
      // The arm shows only while an instrument is in use, so its appearance means "science now".
      const busy = ev && !["idle", "done", "park"].includes(rig.phaseAt(t).name);
      rig.mount.visible = !!busy || (ev && rig.phaseAt(t).name === "done" && t < ev.t1 + 1);
    }
    // Name tag: what the instrument is doing right now.
    const txt = instrumentNow(a, t, ev);
    if (tagEls[a] && tagEls[a].textContent !== txt) tagEls[a].textContent = txt;
    const ph = ev ? (phaseOf(ev, t) || {}).name : "idle";
    if (fans[a]) {
      fans[a].visible = ["mast_pan", "terrain_map", "photo", "neutron_dwell"].includes(ph);
      fans[a].rotation.z = ph === "mast_pan" ? 0.45 * Math.sin(performance.now() / 500) : 0;
      fans[a].material.opacity = ph === "photo" ? 0.4 : 0.12 + 0.06 * Math.sin(performance.now() / 180);
    }
    const on = txt === "neutron survey" || txt === "NEUTRON DWELL" || ph === "nir_scan";
    for (const m of (extras[a] || {}).sensors || []) m.material.emissive.setHex(on ? (a === "A" ? 0x33aa66 : 0x7744ff) : 0);
  }
  updateOverlay(t);
  if (S.cam !== "first") world.render();
  else showFrame($("fp-img"), $("fp-ov"), $("fp-none"), false);
  if (S.cam !== "first") showFrame($("fpi-img"), $("fpi-ov"), $("fpi-none"), true);
  showLidar(t);
  if (S.cam !== "overhead") map.update(t);
  $("clock").textContent = fmtClock(t);
  $("tl-fill").style.width = `${(100 * t) / run.tEnd}%`;
  $("scrub").value = String(Math.round((1000 * t) / run.tEnd));
  if (panels) renderPanels(t);
}

// The phase of a stop at time t, with how far through it is (0..1).
function phaseOf(ev, t) {
  let cur = null;
  ev.phases.forEach((p, i) => { if (t >= p.t0) cur = { ...p, i, p: Math.min(1, (t - p.t0) / p.dur) }; });
  return cur;
}

function instrumentNow(a, t, ev) {
  if (a === "A") {
    const ph = ev ? phaseOf(ev, t) : null;
    if (ph && ph.p < 1 || ph && ph.i < ev.phases.length - 1) return A_TAG[ph.name] || "";
    return stateAt(a, t).state === "EXPLORING" ? "neutron survey" : "";
  }
  const ph = ev && rigs[a] ? rigs[a].phaseAt(t).name : "idle";
  if (ph === "drill") return `DRILLING ${(100 * rigs[a].depthAt(t)).toFixed(0)} cm`;
  if (ph === "nir_scan") return "NIR SCAN";
  if (ph === "analyze") return "ANALYSING CORE";
  if (ph === "arm_deploy" || ph === "arm_stow" || ph === "sample_lift") return "DRILL ARM";
  if (a === "A" && stateAt(a, t).state === "EXPLORING") return "neutron survey";
  return "";
}

// The lidar scan (sim/lidar.py Lidar.view, logged per step as lidar_frame):
// the latest one at or before the shown moment, for the selected rover.
// Runs without lidar keep the box hidden.
function showLidar(t) {
  const run = S.run, box = $("in-lidar");
  if (run.hasLidar === undefined) run.hasLidar = run.steps.some((s) => Object.values(s.rovers).some((r) => r.lidar_frame));
  box.hidden = !run.hasLidar;
  if (!run.hasLidar) return;
  const a = S.rover, k = run.stepAt(t);
  let src = null;
  for (let i = k; i >= 0 && i > k - 50 && !src; i--) src = run.steps[i].rovers[a] && run.steps[i].rovers[a].lidar_frame;
  const img = $("lid-img");
  $("lid-none").hidden = !!src;
  img.hidden = !src;
  if (src && img.dataset.src !== run.dir + src) { img.src = run.dir + src; img.dataset.src = run.dir + src; }
  // Top-down scans (step-6-nav 40304d4) are 800 px wide; older range images 400.
  const top = img.naturalWidth >= 800;
  const cap = `${ROLE[a] || "Rover " + a} lidar, ${top ? "from above" : "range image"}`;
  if ($("lid-cap").textContent !== cap) {
    $("lid-cap").textContent = cap;
    $("lid-cap").title = top
      ? "Top-down, forward up, rover at the bottom centre. Grey safe, amber crater floor (avoided), red steeper than 15° (no-go). Rings every 5 m."
      : "Range image beside the camera, 120° x 33°: near warm, far cool.";
  }
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
  // A Surveyor stop under way: its own effects instead of the usual marks.
  const sev = a === "A" ? sci.find((e) => e.agency === "A" && S.t >= e.t0 && S.t <= e.t1 + FOCUS_TAIL_S) : null;
  if (sev) { drawSurveyFx(c, sev, S.t, P, k, font); return; }
  if (route && S.opts.routes && ["EXPLORING", "NAVIGATING", "REPLANNING", "PLANNING", "NAVIGATING_TO_SITE"].includes(st)) {
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
    if (top) { c.fillStyle = "#ff5d5d"; c.font = font(13); c.fillText(h.hazard_id ? `FLAGGED ${h.hazard_id} · KEEP CLEAR` : "OBSTACLE", top[0] - 34 * k, top[1] - 8); }
  }
  const sites = registryAt(tf);
  for (const s of run.log.satellite_sites || []) {
    const r = [...sites.values()].find((v) => v.target === s.id);
    const ice = r && (r.surveyor === "Positive" || r.prospector === "Positive");
    const color = ice ? ICE_CSS : r ? "#9aa7b8" : "#e0a83a";
    const base = P(s.x, s.y), tip = P(s.x, s.y, 6);
    if (!base || !tip || base[2] > 250) continue;
    c.strokeStyle = color; c.lineWidth = 2 * k;
    c.beginPath(); c.moveTo(base[0], base[1]); c.lineTo(tip[0], tip[1]); c.stroke();
    c.fillStyle = color; c.font = font(12);
    c.fillText(`${s.id}${ice ? " ICE" : ""} · ${base[2].toFixed(0)} m`, tip[0] + 5, tip[1] + 4);
  }
}

// ------------------------------------------------- Surveyor camera effects
// The ground around a site or hazard as the mast camera maps it: a 1 m grid,
// each point with its slope across 2 m (as the planner's slope map) and,
// at a hazard, whether it lies in the boulder field.
const gridCache = new Map();
function surveyGrid(ev) {
  if (gridCache.has(ev.record_id)) return gridCache.get(ev.record_id);
  const hz = ev.kind === "hazard" ? ev.hazard : null;
  const site = hz || (S.run.log.satellite_sites || []).find((q) => q.id === ev.target_id) || ev;
  const R = hz ? hz.radius + 6 : 9, n = Math.round(R);
  const rows = [];
  let maxSlope = 0, count = 0;
  for (let i = -n; i <= n; i++) {
    const row = [];
    for (let j = -n; j <= n; j++) {
      const x = site.x + j, y = site.y + i;
      const gx = (terrain.heightAt(x + 1, y) - terrain.heightAt(x - 1, y)) / 2;
      const gy = (terrain.heightAt(x, y + 1) - terrain.heightAt(x, y - 1)) / 2;
      const slope = (Math.atan(Math.hypot(gx, gy)) * 180) / Math.PI;
      const inside = Math.hypot(i, j) <= R;
      const rock = !!hz && Math.hypot(x - hz.x, y - hz.y) <= hz.radius;
      if (inside) { maxSlope = Math.max(maxSlope, slope); count++; }
      row.push({ x, y, slope, inside, rock });
    }
    rows.push(row);
  }
  const g = { site, hz, R, rows, maxSlope, count };
  gridCache.set(ev.record_id, g);
  return g;
}
const slopeColor = (q) => (q.rock || q.slope >= 15 ? "#ff5d5d" : q.slope >= 8 ? "#f2c94c" : "#4fd18b");

function strokeWorld(c, pts, P, style, w, lift = 0.05) {
  c.beginPath();
  let open = false;
  for (const p of pts) {
    const q = P(p[0], p[1], lift);
    if (!q) { open = false; continue; }
    open ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]);
    open = true;
  }
  c.lineWidth = w; c.strokeStyle = style; c.stroke();
}
const circlePts = (x, y, r, n = 48) => Array.from({ length: n + 1 }, (_, i) => [x + r * Math.cos((i / n) * 2 * Math.PI), y + r * Math.sin((i / n) * 2 * Math.PI)]);
const dense = (pts, step = 1) => {
  const out = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i], n = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / step));
    for (let j = 1; j <= n; j++) out.push([a[0] + ((b[0] - a[0]) * j) / n, a[1] + ((b[1] - a[1]) * j) / n]);
  }
  return out;
};

function drawSurveyFx(c, ev, t, P, k, font) {
  const ph = phaseOf(ev, t);
  if (!ph) return;
  const G = surveyGrid(ev), names = ev.phases.map((p) => p.name);
  const past = (name) => names.indexOf(name) >= 0 && (ph.i > names.indexOf(name) || (ph.name === name && ph.p >= 1));
  const W = FP.w, H = FP.h, now = performance.now(), cyan = "#7fd8ff";
  const hz = G.hz;
  c.save();
  c.fillStyle = "rgba(8, 28, 44, 0.22)";
  c.fillRect(0, 0, W, H);
  // Viewfinder: corner brackets, a header, a blinking REC.
  c.strokeStyle = "rgba(127,216,255,.9)"; c.lineWidth = 2 * k;
  const m = 12, L = 30 * k;
  for (const [x0, y0, sx, sy] of [[m, m, 1, 1], [W - m, m, -1, 1], [m, H - m, 1, -1], [W - m, H - m, -1, -1]]) {
    c.beginPath(); c.moveTo(x0, y0 + sy * L); c.lineTo(x0, y0); c.lineTo(x0 + sx * L, y0); c.stroke();
  }
  c.font = font(11); c.fillStyle = cyan; c.textAlign = "left";
  c.fillText(hz ? `SURVEYOR A · HAZARD ${ev.target_id}` : `SURVEYOR A · SITE ${ev.target_id}`, m + 10, m + 14 * k + 4);
  c.textAlign = "right"; c.fillStyle = "#ff8a8a"; c.fillText("REC", W - m - 10, m + 14 * k + 4);
  if (Math.floor(now / 450) % 2) { c.fillStyle = "#ff4d4d"; c.beginPath(); c.arc(W - m - 10 - 30 * k, m + 10 * k + 4, 4 * k, 0, 7); c.fill(); }
  c.textAlign = "left";

  // Terrain map: a 1 m grid revealed from near to far while mapping, then kept.
  if (ph.name === "terrain_map" || past("terrain_map")) {
    const proj = G.rows.map((row) => row.map((q) => (q.inside ? P(q.x, q.y, 0.06) : null)));
    let dmin = Infinity, dmax = 0;
    for (const row of proj) for (const q of row) if (q) { dmin = Math.min(dmin, q[2]); dmax = Math.max(dmax, q[2]); }
    const front = ph.name === "terrain_map" ? dmin + (dmax - dmin) * Math.min(1, ph.p * 1.1) : Infinity;
    const seg = (q1, q2, a1, a2) => {
      if (!q1 || !q2 || q1[2] > front || q2[2] > front) return;
      const near = Math.abs(q1[2] - front) < 1.2;
      c.strokeStyle = slopeColor(a1.slope > a2.slope ? a1 : a2);
      c.globalAlpha = near ? 1 : ph.name === "terrain_map" ? 0.75 : 0.5;
      c.lineWidth = (near ? 2.2 : 1.1) * k;
      c.beginPath(); c.moveTo(q1[0], q1[1]); c.lineTo(q2[0], q2[1]); c.stroke();
    };
    for (let i = 0; i < proj.length; i++) for (let j = 0; j < proj[i].length; j++) {
      if (j + 1 < proj[i].length) seg(proj[i][j], proj[i][j + 1], G.rows[i][j], G.rows[i][j + 1]);
      if (i + 1 < proj.length) seg(proj[i][j], proj[i + 1][j], G.rows[i][j], G.rows[i + 1][j]);
    }
    c.globalAlpha = 1;
    if (hz) {   // the boulders, as the stereo map picks them out
      c.fillStyle = "#ff5d5d";
      for (let i = 0; i < proj.length; i++) for (let j = 0; j < proj[i].length; j++) {
        const q = proj[i][j];
        if (q && G.rows[i][j].rock && q[2] <= front && (i + j) % 2 === 0) c.fillRect(q[0] - 1.5 * k, q[1] - 1.5 * k, 3 * k, 3 * k);
      }
    }
  }

  // The reticle on the site or hazard: it closes in while the camera pans, then holds.
  const ctr = P(G.site.x, G.site.y, 0.4);
  if (ctr) {
    const lock = ph.name === "stop" || ph.name === "park" ? 0 : ph.name === "mast_pan" ? ph.p : 1;
    const half = (70 - 40 * lock) * k * (ctr[2] < 8 ? 1.3 : 1);
    const col = hz ? "#ff6b6b" : cyan;
    c.strokeStyle = col; c.lineWidth = 2 * k;
    const b = half * 0.35;
    for (const [sx, sy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
      const x0 = ctr[0] + sx * half, y0 = ctr[1] + sy * half * 0.7;
      c.beginPath(); c.moveTo(x0 - sx * b, y0); c.lineTo(x0, y0); c.lineTo(x0, y0 - sy * b * 0.7); c.stroke();
    }
    c.fillStyle = col; c.font = font(12);
    c.fillText(`${ev.target_id} · ${ctr[2].toFixed(0)} m`, ctr[0] + half + 6, ctr[1] - half * 0.7 + 12 * k);
  }

  // Camera pan: a bright line sweeping across the frame.
  if (ph.name === "mast_pan") {
    const x = W * (0.5 + 0.44 * Math.sin(ph.p * Math.PI * 2));
    const gr = c.createLinearGradient(x - 40, 0, x + 40, 0);
    gr.addColorStop(0, "rgba(127,216,255,0)"); gr.addColorStop(0.5, "rgba(127,216,255,.45)"); gr.addColorStop(1, "rgba(127,216,255,0)");
    c.fillStyle = gr; c.fillRect(x - 40, 0, 80, H);
    c.fillStyle = cyan; c.fillRect(x - 1, 0, 2, H);
  }

  // Neutron dwell: rings pulsing out from the site, the seven dwell points filling in.
  if (ph.name === "neutron_dwell") {
    for (let i = 0; i < 3; i++) {
      const r = ((now / 900 + i / 3) % 1) * 5;
      c.globalAlpha = 1 - r / 5;
      strokeWorld(c, circlePts(G.site.x, G.site.y, r, 40), P, "#b8f0ff", 2 * k, 0.1);
    }
    c.globalAlpha = 1;
    const pts = [[0, 0], ...Array.from({ length: 6 }, (_, i) => [3 * Math.cos((i * Math.PI) / 3), 3 * Math.sin((i * Math.PI) / 3)])];
    pts.forEach(([dx, dy], i) => {
      const q = P(G.site.x + dx, G.site.y + dy, 0.1);
      if (!q) return;
      const on = i < ph.p * 7;
      c.fillStyle = on ? "#b8f0ff" : "rgba(184,240,255,.25)";
      c.beginPath(); c.arc(q[0], q[1], (on ? 4 : 3) * k, 0, 7); c.fill();
    });
  }

  // Photo: a flash, then the frame it kept.
  if (ph.name === "photo") {
    const f = Math.max(0, 1 - ph.p * 3);
    if (f > 0) { c.fillStyle = `rgba(255,255,255,${0.85 * f})`; c.fillRect(0, 0, W, H); }
    if (ctr) {
      c.strokeStyle = "#fff"; c.lineWidth = 2 * k;
      c.strokeRect(ctr[0] - 110 * k, ctr[1] - 75 * k, 220 * k, 150 * k);
    }
  }

  // Hazard: flag it, cross out the way the rover was going, show the way round.
  if (hz && (ph.name === "flag" || past("flag"))) {
    const pulse = 0.6 + 0.4 * Math.sin(now / 160);
    c.globalAlpha = pulse;
    strokeWorld(c, circlePts(hz.x, hz.y, hz.radius), P, "#ff4d4d", 4 * k, 0.15);
    c.globalAlpha = 1;
    const t0 = ev.phases[0].t0;
    const old = planned.filter((q) => q.agency === "A" && q.t <= t0).at(-1);
    const nxt = planned.find((q) => q.agency === "A" && q.kind === "replanned" && q.t >= t0);
    const pose = S.run.poseAt("A", t);
    const ahead = (r) => {
      const d = dense(r.points);
      let bi = 0, bd = Infinity;
      d.forEach((p, i) => { const dd = Math.hypot(p[0] - pose[0], p[1] - pose[1]); if (dd < bd) { bd = dd; bi = i; } });
      return d.slice(bi, bi + 45);
    };
    if (old) {
      const pts = ahead(old);
      strokeWorld(c, pts, P, "rgba(255,77,77,.85)", 4 * k);
      const mid = pts[Math.min(pts.length - 1, 12)], q = mid && P(mid[0], mid[1], 0.1);
      if (q) {
        c.strokeStyle = "#ff4d4d"; c.lineWidth = 4 * k; const r = 14 * k;
        c.beginPath(); c.moveTo(q[0] - r, q[1] - r); c.lineTo(q[0] + r, q[1] + r); c.moveTo(q[0] + r, q[1] - r); c.lineTo(q[0] - r, q[1] + r); c.stroke();
      }
    }
    if (nxt) {
      strokeWorld(c, ahead(nxt), P, "rgba(0,0,0,.5)", 9 * k);
      strokeWorld(c, ahead(nxt), P, "#5aa7ff", 4 * k);
    }
    banner(c, `UNSAFE · ${ev.target_id} FLAGGED FOR THE PROSPECTOR`, "#ff4d4d", k, font);
  }

  // The verdict at the end of a site survey.
  if (!hz && (past("photo") || (ph.name === "photo" && ph.p > 0.4))) {
    const v = { ice: ["HYDROGEN HIGH · WORTH A CORE", ICE_CSS], trace: ["SOME HYDROGEN · BELOW THRESHOLD", "#f2c94c"],
      dry: ["DRY · NOTHING TO DRILL", "#c9ccd1"] }[ev.verdict] || [String(ev.verdict).toUpperCase(), "#fff"];
    banner(c, v[0], v[1], k, font);
  }

  // What it is doing, bottom left, in words.
  const pct = Math.round(100 * ph.p);
  const label = {
    stop: "STOPPING · FACING IT", park: "PARKED · FACING THE SITE", mast_pan: "CAMERA PAN",
    terrain_map: past("terrain_map") ? `MAP DONE · STEEPEST ${G.maxSlope.toFixed(0)}°` : `MAPPING TERRAIN ${pct}% · ${Math.round(G.count * ph.p)} POINTS`,
    neutron_dwell: `NEUTRON DWELL ${Math.round(42 * ph.p)}/42 READINGS`, photo: "PHOTO SAVED TO THE REPORT",
    flag: "WRITING IT INTO THE ROUTE REPORT",
  }[ph.name] || "";
  c.font = font(15); c.fillStyle = "#fff";
  c.fillText(label, m + 10, H - m - 12);
  if (ph.name === "terrain_map" || past("terrain_map")) {
    c.font = font(10);
    let x = m + 10;
    for (const [col, txt] of [["#4fd18b", "flat"], ["#f2c94c", "8–15°"], ["#ff5d5d", hz ? "steep / rocks" : "over 15°"]]) {
      c.fillStyle = col; c.fillRect(x, H - m - 40 * k, 10 * k, 4 * k);
      c.fillText(txt, x + 14 * k, H - m - 36 * k);
      x += c.measureText(txt).width + 30 * k;
    }
  }
  c.restore();
}

function banner(c, text, col, k, font) {
  c.font = font(14);
  const w = c.measureText(text).width + 28 * k, h = 30 * k, x = (FP.w - w) / 2, y = 40 * k;
  c.fillStyle = "rgba(10,12,16,.82)"; c.fillRect(x, y, w, h);
  c.fillStyle = col; c.fillRect(x, y, 4 * k, h);
  c.textAlign = "center"; c.fillText(text, FP.w / 2 + 2 * k, y + h / 2 + 5 * k); c.textAlign = "left";
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
        c.setLineDash([3, 2]);
        c.beginPath(); c.arc(X(h.x), Y(h.y), r, 0, Math.PI * 2); c.stroke();
        c.setLineDash([]);
        if (h.hazard_id) { c.fillStyle = "#ff5d5d"; c.font = "600 11px Barlow, sans-serif"; c.fillText(h.hazard_id, X(h.x) + r + 3, Y(h.y) + 4); }
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
    const what = instrumentNow(a, t, ev).toLowerCase() || (a === "A" ? "facing it" : "science");
    const where = ev.kind === "hazard" ? `crater ${ev.target_id} (not on the orbital map)` : ev.target_id;
    html = `<i class="dot ${a}"></i> <b>${ROLE[a]} ${a}</b> · ${esc(what)} at ${esc(where)}<span class="sp">${FOCUS_SPEED}× for science</span>`;
  } else if (t >= run.tEnd) {
    const ice = [...registryAt(t).values()].filter((s) => siteStatus(s) === "Positive").length;
    html = `<b>Mission complete</b> · ice at ${ice} site${ice === 1 ? "" : "s"}`;
  } else if (!S.playing && t === 0) {
    html = `Press <b>play</b> to start · drag to look around`;
  } else {
    const a = S.rover, st = stateAt(a, t);
    // The Prospector driving a route planned around a flagged crater says so.
    const plan = a === "B" ? ((S.run.log.states || {}).B || []).filter((q) => q.t <= t && q.state === "PLANNING").at(-1) : null;
    const m = plan && st.state === "NAVIGATING" && /flags crater (H\d+)/.exec(plan.objective);
    const extra = m ? ` · keeping clear of crater ${m[1]}, flagged by the Surveyor` : "";
    html = `<i class="dot ${a}"></i> <b>${ROLE[a]} ${a}</b> · ${esc(DOING[st.state] || st.state)}${extra}<span class="sp">${S.playing ? S.speed + "×" : "Paused"}</span>`;
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
  const ph = ev.phases;
  let cur = -1;
  ph.forEach((p, i) => { if (t >= p.t0) cur = i; });
  const finished = t >= ev.t1;
  const what = ev.kind === "hazard" ? `crater ${esc(ev.target_id)}` : esc(ev.target_id);
  $("sci-title").innerHTML = `<i class="dot ${ev.agency}"></i>${ROLE[ev.agency]} · ${what}`;
  $("sci-phases").innerHTML = ph.map((p, i) =>
    `<li class="${finished || i < cur ? "done" : i === cur ? "now" : ""}">${PHASE[p.name] || p.name}</li>`).join("");
  if (ev.agency === "A") return renderSurveyPane(ev, t, cur, finished);
  const rig = rigs[ev.agency];
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

// The Surveyor's stop: what its camera mapped, what its neutron dwell read,
// and what it concluded. Numbers come from the report stored off chain.
function renderSurveyPane(ev, t, cur, finished) {
  const names = ev.phases.map((p) => p.name);
  const done = (name) => finished || cur > names.indexOf(name);
  const ph = phaseOf(ev, t) || { name: "", p: 0 };
  const key = `${ev.record_id}|${cur}|${finished}|${Math.floor(ph.p * 7)}`;
  if (S.lastPanel === key) return;
  S.lastPanel = key;
  evidence(ev.report_cid).then((e) => {
    if (S.lastPanel !== key) return;
    const j = e ? e.json : null;
    const hz = ev.kind === "hazard";
    const terr = j ? (hz ? j.hazard : j.measurements.terrain) : null;
    let html = "";
    if (terr && (done("terrain_map") || ph.name === "terrain_map")) {
      const f = done("terrain_map") ? 1 : ph.p;
      html += `<div class="mini-h">Terrain map</div><div class="stats wide">
        <div class="stat"><b>${(terr.max_slope_deg * f).toFixed(0)}°</b><span>steepest</span></div>
        <div class="stat"><b>${(terr.mean_slope_deg * f).toFixed(0)}°</b><span>average</span></div>
        <div class="stat"><b>${Math.round(100 * terr.no_go_fraction * f)}%</b><span>no-go</span></div></div>`;
    }
    if (!hz && j && (done("neutron_dwell") || ph.name === "neutron_dwell")) {
      const d = j.measurements.dwell, n = done("neutron_dwell") ? d.length : Math.floor(ph.p * d.length);
      const max = Math.max(1.5, ...d.map((q) => q[2]));
      html += `<div class="mini-h">Neutron dwell <span class="hint">water-equivalent, top 1 m</span></div>` + d.map(([dx, dy, w], i) =>
        `<div class="b ${i < n ? "" : "pending"}"><span>${i ? `${Math.round((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360}°` : "centre"}</span><span class="bar2"><i style="width:${i < n ? (100 * w) / max : 0}%"></i></span><span>${i < n ? w.toFixed(2) + "%" : "…"}</span></div>`).join("");
    }
    if (!html) html = `<div class="hint">${names[cur] === "mast_pan" ? "Panning the camera across it" : "Stopping and turning to face it"}</div>`;
    $("sci-bins").innerHTML = html;
    if (!finished && !(done("photo") || (hz && ph.name === "flag"))) { $("sci-result").innerHTML = ""; return; }
    if (hz) {
      const dtxt = terr && terr.crater_diameter_m ? `Crater ${terr.crater_diameter_m.toFixed(0)} m across, boulders on the rim` : "Boulder field";
      $("sci-result").innerHTML = `<div class="verdict ice">Unsafe<small>flagged</small><span class="sim-tag">SIMULATED</span></div>
        <div class="small">${dtxt}. It goes into the Surveyor's route report, so the Prospector plans around it.</div>
        ${ev.report_cid ? `<div class="small hint" style="margin-top:4px">Hazard report <a class="mono" href="${S.run.dir}evidence/${ev.report_cid}.json" target="_blank" rel="noopener">${short(ev.report_cid, 10, 6)}</a></div>` : ""}`;
      return;
    }
    const word = { ice: ["Hydrogen high", "worth a core"], trace: ["Some hydrogen", "below the threshold"], dry: ["Dry", "nothing to drill"] }[ev.verdict] || [ev.verdict, ""];
    $("sci-result").innerHTML = `<div class="verdict ${ev.verdict}">${word[0]}<small>${word[1]}</small><span class="sim-tag" title="Simulated measurement, not NASA data">SIMULATED</span></div>
      ${ev.verdict === "ice" ? `<div class="small hint">The Prospector drills it to confirm.</div>` : ""}
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
