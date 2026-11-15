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
import { MissionPlayer, ensureStyles } from "./mission_player.js";
// The judges' page plays Kellen's mission log (run_mission.py, docs/handoffs.md
// "Mission log"): checkpoints and live chain posts come from mission_player.js,
// the chain feed from the log's find and check events.
const AUTO_B_HOLD_MS = 3000;   // assumed: wall time on the Surveyor after its ice find, then follow the Prospector

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
  neutron_dwell: "Neutron dwell", photo: "Photo", flag: "Flag", neutron_survey: "Neutron survey" };
// What the Surveyor's name tag says during each step of a stop.
const A_TAG = { park: "PARKED", nir_scan: "NIR SCAN", neutron_survey: "NEUTRON SURVEY", analyze: "ANALYSING", mast_pan: "CAMERA PAN", terrain_map: "MAPPING TERRAIN", neutron_dwell: "NEUTRON DWELL", photo: "PHOTO",
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
const FOCUS_SPEED = 1.5;   // sim seconds per real second during a science stop (Ritvik's short stops)
const FOCUS_WALL_S = 12;   // assumed: a mission stop (160-270 s of sim time) plays in about this many real seconds
const FOCUS_LEAD_S = 3;    // the close-up starts this many sim seconds before the stop
const FOCUS_TAIL_S = 3;    // and holds this long after it
const NARROW = matchMedia("(max-width: 760px)");

const S = {
  lidarKind: "top",   // lidar inset: "top" (lidar_frame) or "fp" (lidar_fp_frame)
  run: null, t: 0, playing: false, speed: 200, cam: "third", rover: "A", tab: "rover", tabBeforeFocus: null,
  opts: { fill: true, routes: true, hazards: true, nogo: true, truth: false, particles: true, rims: true }, sensor: "camera", focus: true, focusEv: null, prev: null, lastPanel: "",
  player: null,        // MissionPlayer: checkpoints and live chain
  autoB: { armed: true, at: 0 },   // switch to the Prospector once after the Surveyor's find
};
let terrain, world, map, rigs = {}, ov, sci = [], reg = [], hazards = [], planned = [], siteObjs = new Map();
let satObjs = [], tagEls = {}, extras = {}, dressed = {}, odo = {}, susp = {};
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
  const entry = runs.find((r) => r.run_id === want) || runs.find((r) => r.run_id === "mission-demo") || runs[0];
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
  const pts = run.agencies.flatMap((a) => run.tracks[a].truth);
  const keys = [...terrain.keysNear(pts.filter((_, i) => i % 2 === 0), 4)];
  let done = 0;
  await Promise.all(keys.map((k) => terrain.loadTile(k).then(() => {
    $("loading").textContent = `Loading terrain ${++done} / ${keys.length}`;
  })));
  S.run = run;
  world.currentT = 0;
  world.setRun(run);
  run.hasLidar = run.agencies.some((ag) => run.steps.some((st) => st.rovers[ag] && st.rovers[ag].lidar_frame));
  for (const b of document.querySelectorAll("[data-sensor]")) {
    b.onclick = () => setSensor(b.dataset.sensor);
    if (b.dataset.sensor === "lidar") {
      b.disabled = !run.hasLidar;
      b.title = run.hasLidar ? "Lidar (L)" : "No lidar in this run";
    }
  }
  let saved = "camera";
  try { saved = localStorage.getItem("lantern.sensor") || "camera"; } catch (e) { /* private window */ }
  S.sensor = run.hasLidar && saved === "lidar" ? "lidar" : "camera";
  for (const b of document.querySelectorAll("[data-sensor]")) b.setAttribute("aria-selected", String(b.dataset.sensor === S.sensor));
  world.setVisibility({ fill: S.opts.fill, tracks: S.opts.routes, truth: S.opts.truth });
  $("vnote").hidden = !S.opts.fill;
  map.setRun(run);
  map.opts = { tracks: S.opts.routes, truth: S.opts.truth, nogo: S.opts.nogo };
  document.title = `Lantern Mission Control · ${log.label || entry.label}`;

  sci = [];
  reg = [];
  run.steps.forEach((s) => {
    for (const e of s.science || []) {
      sci.push({ ...e, t0: e.phases[0].t0, t1: e.phases.at(-1).t0 + e.phases.at(-1).dur });
    }
    for (const e of s.registry || []) reg.push(e);
  });
  if (!sci.length) sci = missionScience(run);
  sci.sort((a, b) => a.t0 - b.t0);
  reg.sort((a, b) => a.t - b.t);
  hazards = log.hazards || [];
  planned = log.planned_routes || [];

  ensureStyles();
  S.player = new MissionPlayer(run, {
    pause: (tt) => { S.playing = false; S.t = tt; setPlayIcon(); },
    resume: () => { S.playing = true; setPlayIcon(); },
    note: (html) => { const b = $("checkpoint"); b.hidden = !html; b.innerHTML = html || ""; },
    rerender: () => { $("chain-now").dataset.key = ""; render(true); },
  });
  setChainBadge();

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

// Kellen's mission log has no science events: each rover step carries the
// instrument phase it ended in ({name, step, of}, docs/handoffs.md "Mission
// log"). Rebuild Ritvik's science events from them: one event per stop, one
// phase per run of steps with the same instrument name, spanning those steps.
function missionScience(run) {
  const out = [], sites = (run.log.mission || {}).sites || [];
  for (const a of run.agencies) {
    let cur = null;
    run.steps.forEach((st, k) => {
      const r = st.rovers[a] || {}, ins = r.instrument;
      const stop = /^(survey|prospect)\b/.test(r.phase || "") ? r.phase : null;
      if (!ins || !stop || k === 0) { if (cur) { out.push(cur); cur = null; } return; }
      if (!cur || cur.stop !== stop) {
        if (cur) out.push(cur);
        const m = /survey (\S+)/.exec(stop);
        const fid = /find (\d+)/.exec(stop);
        const fe = fid ? run.events.find((e) => e.type === "find" && e.find_id === Number(fid[1])) : null;
        const target = m ? m[1] : fe ? fe.site_id : stop;
        const site = sites.find((x) => x.id === target);
        const chk = run.events.find((e) => e.type === "check" && fe && e.find_id === fe.find_id);
        cur = { agency: a, stop, target_id: target, kind: "site", record_id: `${a}-${stop}`, phases: [],
          verdict: a === "A" ? (site && site.verdict) || "dry" : chk ? (chk.agrees ? "ice" : "dry") : "ice",
          observation: a === "A" ? "survey" : "core", reading: null };
      }
      const t0 = run.times[k - 1], dur = run.times[k] - run.times[k - 1];
      const last = cur.phases.at(-1);
      if (last && last.name === ins.name) last.dur = t0 + dur - last.t0;
      else cur.phases.push({ name: ins.name, t0, dur });
      if (r.ice_reading != null) cur.reading = r.ice_reading;
    });
    if (cur) out.push(cur);
  }
  for (const e of out) { e.t0 = e.phases[0].t0; e.t1 = e.phases.at(-1).t0 + e.phases.at(-1).dur; }
  return out;
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

// ----------------------------------------------------------- suspension
// NASA's Perseverance file has all six wheels in one mesh ("Wheels_objs") and
// the rocker-bogie in another ("suspension"). We cut the wheels apart by
// clustering their vertices (3 along the body, 2 across), give each its own
// pivot at its centre, and do the same for the two rocker halves. Opportunity
// is a single mesh, so its body tilts and lifts but its wheels do not move alone.
function clusterMesh(obj, seedsFor) {
  // NASA's glTF splits a part into one mesh per material under a group, so
  // gather every child mesh's vertices into the group's frame and cluster them
  // together; each mesh is then cut by the shared labels.
  obj.updateMatrix();
  const meshes = obj.isMesh ? [{ mesh: obj, mat: new THREE.Matrix4() }] : obj.children.filter((o) => o.isMesh).map((o) => { o.updateMatrix(); return { mesh: o, mat: o.matrix.clone() }; });
  if (!meshes.length || meshes.some(({ mesh }) => !mesh.geometry.index)) return null;
  const P = [];                                     // [x, y, z] in obj's frame
  const v3 = new THREE.Vector3();
  for (const m of meshes) {
    const pos = m.mesh.geometry.attributes.position;
    m.off = P.length;
    for (let i = 0; i < pos.count; i++) { v3.fromBufferAttribute(pos, i).applyMatrix4(m.mat); P.push([v3.x, v3.y, v3.z]); }
  }
  const n = P.length, bb = new THREE.Box3();
  for (const q of P) bb.expandByPoint(v3.set(q[0], q[1], q[2]));
  const ctr = bb.getCenter(new THREE.Vector3()), ext = bb.getSize(new THREE.Vector3());
  let best = null;
  for (const [fa, la] of [[0, 2], [2, 0]]) {       // glTF is y up: forward is x or z
    const seeds = seedsFor(ctr.getComponent(fa), ext.getComponent(fa), ctr.getComponent(la), ext.getComponent(la));
    const lab = new Uint8Array(n);
    let sse = 0;
    for (let it = 0; it < 8; it++) {
      const acc = seeds.map(() => [0, 0, 0]);
      sse = 0;
      for (let v = 0; v < n; v++) {
        const f = P[v][fa], l = P[v][la];
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
  // One pivot per cluster, at the cluster's centre, holding that cluster's
  // triangles from every mesh.
  const root = new THREE.Group();
  root.position.copy(obj.position); root.quaternion.copy(obj.quaternion); root.scale.copy(obj.scale);
  obj.parent.add(root);
  obj.visible = false;
  const parts = best.seeds.map(() => ({ min: new THREE.Vector3(Infinity, Infinity, Infinity), max: new THREE.Vector3(-Infinity, -Infinity, -Infinity), count: 0 }));
  for (let v = 0; v < n; v++) {
    const q = parts[best.lab[v]];
    v3.set(P[v][0], P[v][1], P[v][2]);
    q.min.min(v3); q.max.max(v3); q.count++;
  }
  const out = [];
  parts.forEach((q, k) => {
    if (!q.count) return;
    const c = q.min.clone().add(q.max).multiplyScalar(0.5);
    const pivot = new THREE.Group();
    pivot.position.copy(c);
    const inner = new THREE.Group();
    inner.position.copy(c).negate();
    pivot.add(inner);
    for (const m of meshes) {
      const geo = m.mesh.geometry, idx = geo.index.array, tri = [];
      for (let i = 0; i < idx.length; i += 3) if (best.lab[m.off + idx[i]] === k) tri.push(idx[i], idx[i + 1], idx[i + 2]);
      if (!tri.length) continue;
      const g2 = new THREE.BufferGeometry();
      for (const [key, v] of Object.entries(geo.attributes)) g2.setAttribute(key, v);
      g2.setIndex(tri);
      const piece = new THREE.Mesh(g2, m.mesh.material);
      piece.castShadow = piece.receiveShadow = true;
      piece.matrixAutoUpdate = false;
      piece.matrix.copy(m.mat);
      inner.add(piece);
    }
    root.add(pivot);
    out.push({ pivot, base: c.clone(), radius: (q.max.y - q.min.y) / 2, la: best.la, fa: best.fa });
  });
  return out;
}

function setupSuspension(a, g, sg, fit) {
  // Measure the model in the rover's own frame: setFromObject gives scene
  // coordinates, and the rover is hundreds of metres from the scene origin.
  g.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(fit).applyMatrix4(g.matrixWorld.clone().invert());
  const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
  const S_ = { g: sg, pitch: 0, roll: 0, z: 0, wheels: [], rockers: [], contacts: [] };
  const wheelMesh = fit.getObjectByName("Wheels_objs"), rockMesh = fit.getObjectByName("suspension");
  if (wheelMesh) {
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
  if (rockMesh && S_.wheels.length) {
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
  for (const c of (S.run.log.mission || {}).checkpoints || []) if (c.pause) add(c.t, "cp", `Checkpoint: ${c.label}`);
  for (const e of S.run.events) add(S.run.times[e.k], "ice", e.type === "find" ? `Ice find posted (${ROLE[e.agency]})` : `Check posted (${ROLE[e.agency]})`);
}

// ---------------------------------------------------------------- controls
function bind() {
  $("play").onclick = togglePlay;
  $("restart").onclick = () => { S.t = 0; S.playing = false; setPlayIcon(); exitFocus(); S.dismissed = null; S.player.restart(); rearmAutoB(0); render(true); };
  $("speed").onchange = (e) => (S.speed = Number(e.target.value));
  $("focus").onchange = (e) => { S.focus = e.target.checked; if (!S.focus) exitFocus(); };
  $("scrub").oninput = (e) => {
    const nt = (Number(e.target.value) / 1000) * S.run.tEnd;
    if (nt < S.t) { S.player.rewound(nt); rearmAutoB(nt); }
    else if (!S.player.busy) $("checkpoint").hidden = true;   // a stale checkpoint card from before the jump
    S.t = nt; exitFocus(); S.dismissed = null; render(true);
  };
  $("zoom-in").onclick = () => zoom(1 / 1.35);
  $("zoom-out").onclick = () => zoom(1.35);
  document.querySelectorAll("#rover-seg button").forEach((b) => (b.onclick = () => { exitFocus(true); setRover(b.dataset.rover); }));
  document.querySelectorAll("#lid-seg button").forEach((b) => (b.onclick = () => {
    S.lidarKind = b.dataset.lk;
    document.querySelectorAll("#lid-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render(true);
  }));
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
  for (const k of ["fill", "routes", "hazards", "nogo", "truth", "particles", "rims"]) {
    $("opt-" + k).onchange = (e) => {
      S.opts[k] = e.target.checked;
      world.setVisibility({ fill: S.opts.fill, tracks: S.opts.routes, truth: S.opts.truth });
  $("vnote").hidden = !S.opts.fill;
      map.opts = { tracks: S.opts.routes, truth: S.opts.truth, nogo: S.opts.nogo };
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
    // Typing in a field: leave its keys alone. The timeline and the checkboxes
    // keep focus after a click, so they still let the shortcuts through.
    if (e.target.closest?.("input:not([type=range]):not([type=checkbox]), select, textarea")) return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    if (e.key === "Escape") $("drawer").hidden = true;
    if (e.key === "+" || e.key === "=") zoom(1 / 1.35);
    if (e.key === "-") zoom(1.35);
    // 1 / 2 blow the rover camera or the satellite map up to the full window (again: back); L flips the lidar view.
    if (e.key === "1") { exitFocus(true); setCam(S.cam === "first" ? "third" : "first"); }
    if (e.key === "2") { exitFocus(true); setCam(S.cam === "overhead" ? "third" : "overhead"); }
    if (e.key === "f" || e.key === "F") $("opt-fill").click();   // fill light on / off
    if (e.key === "l" || e.key === "L") $(`lid-seg`).querySelector(`[data-lk="${S.lidarKind === "top" ? "fp" : "top"}"]`)?.click();
  });
}

// The right column: rover camera, satellite map and lidar, stacked and all showing.
function applyInsets() {
  $("in-fp").hidden = S.cam === "first";
  $("in-map").hidden = S.cam === "overhead";
  $("in-lidar").hidden = !S.run.hasLidar;
}

// After the Surveyor posts its ice find, hold on it a moment, then follow the
// Prospector, once per play-through. A rover picked by hand in the hold wins.
function rearmAutoB(t) {
  const f = S.run.events.find((e) => e.type === "find");
  if (f && t >= S.run.times[f.k]) return;
  // The auto switch fired in this play-through: going back before the find returns to the Surveyor.
  if (!S.autoB.armed && S.autoB.switched && S.rover === "B") setRover("A");
  S.autoB = { armed: true, at: 0 };
}

function autoB(prevT, t, now) {
  const f = S.run.events.find((e) => e.type === "find");
  if (!f) return;
  const ft = S.run.times[f.k];
  if (S.autoB.armed && !S.autoB.at && prevT < ft && t >= ft) S.autoB = { armed: true, at: now + AUTO_B_HOLD_MS, rover: S.rover };
  if (S.autoB.at && now >= S.autoB.at) {
    const untouched = S.rover === S.autoB.rover;
    S.autoB = { armed: false, at: 0, switched: untouched && S.rover !== "B" };
    if (S.autoB.switched) { exitFocus(true); setRover("B"); }
  }
}

function setChainBadge() {
  const run = S.run, badge = $("chain-badge");
  const live = S.player && S.player.liveOn;
  const real = run.chain && run.chain.explorer && !run.fakeTx;
  badge.className = `chain-badge ${live || real ? "monad" : "offline"}`;
  badge.textContent = live ? "Monad testnet · live" : real ? "Monad testnet" : "Recorded";
  badge.title = live ? "Each find and check is signed and sent to Monad testnet as the replay crosses it."
    : real ? `Contract ${run.chain.contract}` : "Recorded transactions from the mission log. Add ?key= to post them live.";
}

function setPlayIcon() {
  $("play").innerHTML = `<svg><use href="#i-${S.playing ? "pause" : "play"}"/></svg>`;
  $("play").setAttribute("aria-label", S.playing ? "Pause" : "Play");
}

function togglePlay() {
  if (S.player && S.player.busy) return;   // a live chain post is in flight
  if (S.t >= S.run.tEnd) { S.t = 0; S.player.restart(); rearmAutoB(0); }
  S.playing = !S.playing;
  if (S.playing && S.player && !(S.player.active && S.player.active.step === 0 && S.t === 0)) $("checkpoint").hidden = true;
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
  applyInsets();
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
  $("back-chip").hidden = true;
  if (ev.agency === "A") {
    // The Surveyor's stops keep the view the user picked (no snap to first person).
  } else {
    S.cam = "third";
    $("fp").hidden = true;
    applyInsets();
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

const focusSpeed = (ev) => Math.min(S.speed, Math.max(FOCUS_SPEED, (ev.t1 - ev.t0) / FOCUS_WALL_S));
const activeSci = (t) => sci.find((e) => t >= e.t0 - FOCUS_LEAD_S && t <= e.t1 + FOCUS_TAIL_S);

// -------------------------------------------------------------------- loop
let last = performance.now(), lastPanelAt = 0;
function loop(now) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  const prevT = S.t;
  if (S.playing && S.run) {
    let adv = dt * (S.focusEv ? focusSpeed(S.focusEv) : S.speed);
    if (S.focus && !S.focusEv) {
      // Never jump over a science stop at high speed: stop at its start.
      const next = sci.find((e) => e.t0 - FOCUS_LEAD_S > S.t && e.t0 - FOCUS_LEAD_S <= S.t + adv);
      if (next) adv = next.t0 - FOCUS_LEAD_S - S.t;
    }
    S.t = Math.min(S.run.tEnd, S.t + adv);
    if (S.t >= S.run.tEnd) { S.playing = false; setPlayIcon(); }
    S.player.tick(prevT, S.t);   // a checkpoint or a live post pauses and sets S.t
  }
  if (S.run) {
    autoB(prevT, S.t, now);
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
  world.update(t, { tracks: S.opts.routes, particles: S.opts.particles });
  updateSteepShade(t);
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
    const on = txt === "neutron survey" || txt === "NEUTRON DWELL" || ph === "nir_scan";
    for (const m of (extras[a] || {}).sensors || []) m.material.emissive.setHex(on ? (a === "A" ? 0x33aa66 : 0x7744ff) : 0);
  }
  updateOverlay(t);
  if (S.cam !== "first") world.render();
  else showFrame($("fp-img"), $("fp-ov"), $("fp-none"), false);
  if (S.cam !== "first") showFrame($("fpi-img"), $("fpi-ov"), $("fpi-none"), true);
  showLidar(t);
  const cap = sensorCaption(t);
  if ($("fpi-cap").textContent !== cap) $("fpi-cap").textContent = cap;
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
  const run = S.run;
  if (run.hasLidar === undefined) run.hasLidar = run.steps.some((s) => Object.values(s.rovers).some((r) => r.lidar_frame));
  if (!run.hasLidar) return;
  // Top-down / First person (lidar_fp_frame, step-6-nav 6846caf), only when the run logs both.
  if (run.hasLidarFp === undefined) run.hasLidarFp = run.steps.some((s) => Object.values(s.rovers).some((r) => r.lidar_fp_frame));
  $("lid-seg").hidden = !run.hasLidarFp;
  const fp = run.hasLidarFp && S.lidarKind === "fp";
  const a = S.rover, k = run.stepAt(t);
  let src = null;
  for (let i = k; i >= 0 && i > k - 50 && !src; i--) {
    const r = run.steps[i].rovers[a];
    src = r && (fp && r.lidar_fp_frame ? r.lidar_fp_frame : r.lidar_frame);
  }
  const img = $("lid-img");
  $("lid-none").hidden = !!src;
  img.hidden = !src;
  if (src && img.dataset.src !== run.dir + src) { img.src = run.dir + src; img.dataset.src = run.dir + src; }
  // Top-down scans (step-6-nav 40304d4) are 800 px wide; older range images 400.
  const top = !(img.naturalWidth > 0 && img.naturalWidth < 800);   // not loaded yet: the current kind
  const isFp = fp && src && src.endsWith("_fp.jpg");
  const cap = run.hasLidarFp ? `${ROLE[a] || "Rover " + a} lidar` : `${ROLE[a] || "Rover " + a} lidar, ${top ? "from above" : "range image"}`;
  const tip = isFp ? "First person: the same scan, 120° across, near bright, far dark. Red no-go, amber crater floor."
    : top ? "Top-down, forward up, rover at the bottom centre. Grey safe, amber crater floor (avoided), red steeper than 15° (no-go). Rings every 5 m."
    : "Range image beside the camera, 120° x 33°: near warm, far cool.";
  if ($("lid-cap").textContent !== cap) $("lid-cap").textContent = cap;
  if ($("in-lidar").title !== tip) $("in-lidar").title = tip;
}

function showFrame(img, cv, none, small) {
  let fr = S.run.frameNear(S.rover, S.t);
  // Lidar view: Calvin's Lidar.view of the scan taken with that camera frame
  // (sim/mission_render.py): from above, forward up, rover at the bottom
  // centre. It is not a camera view, so the route overlay is left off.
  const lidar = S.sensor === "lidar";
  img.parentElement.classList.toggle("lidar-on", lidar);
  if (fr && lidar) {
    const lf = S.run.steps[fr.k].rovers[S.rover].lidar_frame;
    fr = lf ? { ...fr, src: S.run.dir + lf, sub: false } : null;
  }
  none.hidden = !!fr;
  img.hidden = !fr;
  if (fr && img.dataset.src !== fr.src) { img.src = fr.src; img.dataset.src = fr.src; }
  if (lidar) { cv.getContext("2d").clearRect(0, 0, cv.width, cv.height); return; }
  drawFpOverlay(fr, cv, small);
}

// Camera | Lidar: one setting for the camera box and the full-screen view.
function setSensor(v) {
  if (v === "lidar" && !S.run.hasLidar) v = "camera";
  S.sensor = v;
  try { localStorage.setItem("lantern.sensor", v); } catch (e) { /* private window */ }
  for (const b of document.querySelectorAll("[data-sensor]")) b.setAttribute("aria-selected", String(b.dataset.sensor === v));
  $("fpi-img").alt = $("fp-img").alt = v === "lidar" ? "Lidar view" : "Rover camera";
  render(true);
}

function sensorCaption(t) {
  if (S.sensor !== "lidar") return "Rover camera";
  const fr = S.run.frameNear(S.rover, t);
  const hz = fr ? S.run.steps[fr.k].rovers[S.rover].lidar_hazards || {} : {};
  const n = (hz.crater || 0) + (hz.slope || 0);
  return n ? `Lidar · ${n} hazard cells` : "Lidar · clear";
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
  // A Surveyor stop under way: just its camera, no marks.
  const sev = a === "A" ? sci.find((e) => e.agency === "A" && S.t >= e.t0 && S.t <= e.t1 + FOCUS_TAIL_S) : null;
  if (sev) return;   // a plain camera view while it surveys
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
  // The mission log's planned route ("plan", world xy, out to ~20 m, re-planned
  // every step) as amber dashes, from the pose the shown image was taken at
  // (ported from the old page's drawPlan, main.js).
  const plan = !route && S.opts.routes ? (run.steps[fr.k].rovers[a] || {}).plan : null;
  if (plan && plan.length > 1) {
    const k1 = Math.min(fr.k + 1, run.steps.length - 1);
    const tp = run.times[fr.k] + (fr.sub ? fr.f : 0) * (run.times[k1] - run.times[fr.k]);
    const [x0, y0, h0] = run.poseAt(a, tp);
    const camZ0 = terrain.heightAt(x0, y0) + FP.camH;
    const cp = Math.cos(FP.pitch), sp = Math.sin(FP.pitch), ch = Math.cos(h0), sh = Math.sin(h0);
    const proj = (wx, wy) => {
      const dx = wx - x0, dy = wy - y0, f = dx * ch + dy * sh, l = -dx * sh + dy * ch;
      const z = terrain.heightAt(wx, wy) + 0.05 - camZ0;
      const Xc = -l, Yc = f * sp + z * cp, Zc = f * cp - z * sp;
      return Zc <= 0.5 ? null : [FP.w / 2 + (FP.fx * Xc) / Zc, FP.h / 2 - (FP.fx * Yc) / Zc, Math.hypot(f, l)];
    };
    c.strokeStyle = "rgba(255, 200, 60, 0.95)";
    c.lineCap = "round";
    const DASH = 0.6, GAP = 0.4;
    let on = true, left = DASH, start = plan[0];
    for (let i = 0; i + 1 < plan.length; i++) {
      const [ax, ay] = plan[i], [bx, by] = plan[i + 1], seg = Math.hypot(bx - ax, by - ay);
      let s0 = 0;
      while (s0 < seg) {
        const take = Math.min(left, seg - s0), s1 = s0 + take;
        const p1 = [ax + ((bx - ax) * s1) / seg, ay + ((by - ay) * s1) / seg];
        if (on) {
          const pa = proj(start[0], start[1]), pb = proj(p1[0], p1[1]);
          if (pa && pb) {
            c.lineWidth = k * Math.max(1.2, Math.min(6, 20 / Math.max(pa[2], 1)));
            c.beginPath(); c.moveTo(pa[0], pa[1]); c.lineTo(pb[0], pb[1]); c.stroke();
          }
        }
        left -= take; s0 = s1;
        if (left <= 1e-9) { on = !on; left = on ? DASH : GAP; start = p1; }
      }
    }
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
  // Rim points the navigation used at this step (sensor frames only; display frames sit between steps).
  const rr = run.steps[fr.k].rovers[a];
  if (S.opts.rims && !fr.sub && rr && rr.rims) {
    c.fillStyle = col;
    for (const [f, l] of rr.rims) {
      const wx = pose[0] + f * Math.cos(pose[2]) - l * Math.sin(pose[2]), wy = pose[1] + f * Math.sin(pose[2]) + l * Math.cos(pose[2]);
      const q = P(wx, wy, 0);
      if (q) c.fillRect(q[0] - 3 * k, q[1] - 3 * k, 6 * k, 6 * k);
    }
  }
  const sites = registryAt(tf);
  for (const s of run.log.satellite_sites || []) {
    const r = [...sites.values()].find((v) => v.target === s.id);
    const ice = (r && (r.surveyor === "Positive" || r.prospector === "Positive")) || siteVerdict(s.id, tf) === "ice";
    const color = ice ? ICE_CSS : r ? "#9aa7b8" : "#e0a83a";
    const base = P(s.x, s.y), tip = P(s.x, s.y, 6);
    if (!base || !tip || base[2] > 250) continue;
    c.strokeStyle = color; c.lineWidth = 2 * k;
    c.beginPath(); c.moveTo(base[0], base[1]); c.lineTo(tip[0], tip[1]); c.stroke();
    c.fillStyle = color; c.font = font(12);
    c.fillText(`${s.id}${ice ? " ICE" : ""} · ${base[2].toFixed(0)} m`, tip[0] + 5, tip[1] + 4);
  }
}

// ------------------------------------------------------- steep ground
// The planner's no-go ground (assets/terrain/nogo.png: sim/rover.py SlopeMap,
// over 15 deg across 2 m, the overhead map's layer) shaded red on the 3D
// ground near the rover being followed. One draped grid, rebuilt when the
// rover has moved STEEP_STEP_M. Unlit, so it reads in the dark.
const STEEP_HALF_M = 120, STEEP_CELL_M = 0.5, STEEP_STEP_M = 40, STEEP_LIFT_M = 0.06;   // assumed, display only
const STEEP_OPACITY = 0.55;
let steepMesh = null, steepAt = null, steepTex = null;
function updateSteepShade(t) {
  const m = terrain.meta;
  if (!m.nogo) return;
  if (steepMesh) steepMesh.visible = S.opts.nogo;
  if (!S.opts.nogo) return;
  const [x, y] = S.run.poseAt(S.rover, t);
  if (steepAt && Math.hypot(x - steepAt[0], y - steepAt[1]) < STEEP_STEP_M) return;
  steepAt = [x, y];
  if (!steepTex) {
    steepTex = new THREE.TextureLoader().load(terrain.base + m.nogo.file);
    steepTex.colorSpace = THREE.SRGBColorSpace;
    steepTex.magFilter = THREE.NearestFilter;
    steepTex.generateMipmaps = false;
    steepTex.minFilter = THREE.LinearFilter;
  }
  const ax0 = m.x0 - m.cell / 2, ay0 = m.y0 - m.cell / 2, aw = m.cols * m.cell, ah = m.rows * m.cell;
  const n = Math.round((2 * STEEP_HALF_M) / STEEP_CELL_M) + 1;
  const cx = Math.round(x / STEEP_CELL_M) * STEEP_CELL_M - STEEP_HALF_M, cy = Math.round(y / STEEP_CELL_M) * STEEP_CELL_M - STEEP_HALF_M;
  const pos = new Float32Array(n * n * 3), uv = new Float32Array(n * n * 2);
  for (let i = 0, p = 0, q = 0; i < n; i++) for (let j = 0; j < n; j++) {
    const wx = cx + j * STEEP_CELL_M, wy = cy + i * STEEP_CELL_M;
    const v = world.groundL(wx, wy, STEEP_LIFT_M);
    pos[p++] = v.x; pos[p++] = v.y; pos[p++] = v.z;
    uv[q++] = (wx - ax0) / aw; uv[q++] = (wy - ay0) / ah;
  }
  const idx = new Uint32Array((n - 1) * (n - 1) * 6);
  for (let a = 0, k = 0; a < n - 1; a++) for (let b = 0; b < n - 1; b++) {
    const v = a * n + b;
    idx[k++] = v; idx[k++] = v + 1; idx[k++] = v + n; idx[k++] = v + 1; idx[k++] = v + n + 1; idx[k++] = v + n;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  if (!steepMesh) {
    steepMesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ map: steepTex, transparent: true, opacity: STEEP_OPACITY,
      depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -4 }));
    steepMesh.frustumCulled = false;
    steepMesh.renderOrder = 2;
    world.scene.add(steepMesh);
  } else {
    steepMesh.geometry.dispose();
    steepMesh.geometry = g;
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
  $("navt").innerHTML = navTable(run, k);
  if (!$("standin").dataset.done) {
    $("standin").dataset.done = "1";
    const si = run.standin || run.log.standins;
    $("standin").hidden = !si;
    if (si) {
      const lines = Object.entries(si).map(([kk, v]) => (String(v).startsWith(kk) ? v : `${kk.replace(/_/g, " ")}: ${v}`));
      const chainLine = run.fakeTx ? " Transaction hashes in this log are stand-ins; ?key= posts them live." : "";
      $("standin").innerHTML = `<b>Stand-ins:</b> ${esc(Object.keys(si).map((kk) => kk.replace(/_/g, " ")).join(", "))}.${chainLine} Camera frames are real renders.` +
        `<details><summary>Details</summary><ul>${lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>`;
    }
  }
}

// Where each rover thinks it is against where it is (the old page's HUD table).
function navTable(run, k) {
  const rows = run.agencies.map((a) => {
    const r = run.rover(a, k);
    const [tx, ty] = r.true_pose;
    const err = Math.hypot(r.estimate[0] - tx, r.estimate[1] - ty);
    const odo = r.odom_pose ? Math.hypot(r.odom_pose[0] - tx, r.odom_pose[1] - ty) : NaN;
    const odoCell = !isFinite(odo) ? "n/a" : odo.toFixed(1) === err.toFixed(1) ? "same" : `${odo.toFixed(1)} m`;
    return `<tr><td><i class="dot ${a}"></i>${a}</td><td>${r.nav_on ? "crater rims" : "odometry only"}</td><td>±${r.sigma.toFixed(1)} m</td><td>${err.toFixed(1)} m</td><td>${odoCell}</td></tr>`;
  }).join("");
  const nav = run.nav ? run.nav.replace(/\s*\([^)]*\)/g, "").split(",").slice(0, 2).join(",").trim() : "";
  return `<table class="navt"><thead><tr><th></th><th>Steering by</th><th title="The rover's own 1-sigma estimate of its position error">Thinks within</th>` +
    `<th title="Distance from the estimate to the hidden true position">Actually off</th><th title="Where odometry alone would put it, distance from the true position">Odometry alone</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>` + (nav ? `<div class="hint small" title="${esc(run.nav)}">${esc(nav)}${S.opts.particles ? "; dots in 3D are its particles" : ""}</div>` : "");
}

// A mission site's verdict once the Surveyor has published it (log.states).
function siteVerdict(id, t) {
  const site = ((S.run.log.mission || {}).sites || []).find((x) => x.id === id);
  if (!site) return null;
  const pub = ((S.run.log.states || {}).A || []).find((q) => q.state === "PUBLISHING" && q.objective.includes(id));
  return pub && t >= pub.t ? site.verdict : null;
}

// One line over the play bar: who is doing what, and how fast time runs.
function renderToast(t) {
  const run = S.run, el = $("toast");
  let html;
  if (S.focusEv) {
    const ev = S.focusEv, a = ev.agency;
    const what = instrumentNow(a, t, ev).toLowerCase() || (a === "A" ? "facing it" : "science");
    const where = ev.kind === "hazard" ? `crater ${ev.target_id} (not on the orbital map)` : ev.target_id;
    html = `<i class="dot ${a}"></i> <b>${ROLE[a]} ${a}</b> · ${esc(what)} at ${esc(where)}<span class="sp">${Math.round(focusSpeed(ev))}× for science</span>`;
  } else if (t >= run.tEnd) {
    const f = (run.log.final_finds || [])[0];
    html = f ? `<b>Mission complete</b> · find #${f.id} ${esc(f.status)} on chain` : `<b>Mission complete</b>`;
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
    }).join("") : (pastDrill && ev.reading != null ? `<div class="hint">Core reading ${ev.reading.toFixed(2)}${esc(S.run.unit || "")}</div>` : "");
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
    if (!html && !j && cur >= 0) html = `<div class="hint">${esc(PHASE[names[cur]] || names[cur])}${ev.reading != null && finished ? ` · reading ${ev.reading.toFixed(2)}${esc(S.run.unit || "")}` : ""}</div>`;
    if (!html) html = `<div class="hint">${names[cur] === "mast_pan" ? "Panning the camera across it" : "Stopping and turning to face it"}</div>`;
    $("sci-bins").innerHTML = html;
    const after = (name) => names.includes(name) && done(name);
    if (!finished && !(after("photo") || (hz && ph.name === "flag") || (!j && after("analyze")))) { $("sci-result").innerHTML = ""; return; }
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

// The chain feed: Kellen's postFind and checkFind calls from the mission log,
// newest first. Recorded hashes unless ?key= posted them live in this play-through.
function txLink(e) {
  const run = S.run, ex = run.chain && run.chain.explorer;
  if (ex && (e.live || !run.fakeTx)) return `<a class="mono" href="${ex.replace(/\/$/, "")}/tx/${e.tx}" target="_blank" rel="noopener">${short(e.tx, 6, 4)}</a>`;
  return `<span class="mono">${short(e.tx, 6, 4)}</span> <span class="tag offline">${run.fakeTx ? "stand-in" : "recorded"}</span>`;
}

function chainHtml(e) {
  const unit = S.run.unit || "";
  const head = e.type === "find"
    ? `postFind #${e.find_id} · ${esc(e.site_id || "")}`
    : `checkFind #${e.find_id} · ${esc(e.site_id || "")} <span class="res ${e.agrees ? "Positive" : "Negative"}">${e.agrees ? "agrees" : "disagrees"}</span>`;
  return `<div class="chain-item${e.live ? " live" : ""}">
    <div class="what"><i class="dot ${e.agency}"></i> ${ROLE[e.agency]} ${e.agency} · ${head}${e.live ? `<span class="tag monad" style="margin-left:auto">Live</span>` : ""}</div>
    <div class="split"><div><span class="k">Position</span>${e.x.toFixed(1)}, ${e.y.toFixed(1)} ± ${e.sigma.toFixed(2)} m</div><div><span class="k">Reading</span>${e.reading.toFixed(2)}${esc(unit)}</div></div>
    <div class="verify-row"><span class="hint small">tx ${txLink(e)}${e.live_status ? ` · now <b>${esc(e.live_status)}</b>` : ""}</span></div>
  </div>`;
}

function renderChain(t) {
  const k = S.run.stepAt(t);
  const done = S.run.events.filter((e) => e.k <= k);
  const key = `chain|${done.length}|${done.map((e) => e.tx).join()}`;
  if ($("chain-now").dataset.key === key) return;
  $("chain-now").dataset.key = key;
  $("chain-now").innerHTML = done.length ? [...done].reverse().map(chainHtml).join("")
    : `<p class="hint">Nothing yet. The Surveyor posts a find when it measures ice; the Prospector posts its check.</p>`;
  $("txs").innerHTML = "";
  if (done.length > (S.lastChainN || 0) && S.playing) document.querySelector('.rail [data-tab="chain"]').classList.add("live");
  S.lastChainN = done.length;
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
  const run = S.run, k = run.stepAt(t);
  const sites = (run.log.mission || {}).sites || [];
  const evs = run.events.filter((e) => e.k <= k);
  const key = `sites|${sites.map((x) => siteVerdict(x.id, t)).join()}|${evs.length}`;
  if ($("reg-body").dataset.key === key) return;
  $("reg-body").dataset.key = key;
  $("reg-count").textContent = sites.length ? String(sites.length) : "";
  $("reg-body").innerHTML = sites.map((x) => {
    const v = siteVerdict(x.id, t);
    const find = evs.find((e) => e.type === "find" && e.site_id === x.id);
    const chk = evs.find((e) => e.type === "check" && e.site_id === x.id);
    const sv = v === "ice" ? `<span class="res Positive">ice</span>` : v === "dry" ? `<span class="res Negative">dry</span>` : `<span class="hint">pending</span>`;
    const pv = chk ? `<span class="res ${chk.agrees ? "Positive" : "Negative"}">${chk.agrees ? "confirmed" : "disputed"}</span>` : find ? `<span class="hint">queued</span>` : "–";
    return `<tr><td><b>${esc(x.id)}</b></td><td>${find ? `find #${find.find_id}` : "–"}</td><td>${sv}</td><td>${pv}</td></tr>`;
  }).join("") || `<tr><td colspan="4" class="hint">No sites.</td></tr>`;
  const finds = run.findsAt ? run.findsAt(k) : [];
  $("finds").innerHTML = finds.length ? finds.map((f) => `<div class="find"><b>Find #${f.find_id}</b> at ${esc(f.site_id || "")} · ${ROLE[f.agency] || f.agency} ${f.agency} · ±${f.sigma.toFixed(2)} m · ${f.reading.toFixed(2)}${esc(run.unit || "")}` +
    ` · <span class="res ${f.status === "confirmed" ? "Positive" : f.status === "disputed" ? "Negative" : "Inconclusive"}">${esc(f.status)}</span></div>`).join("")
    : `<p class="hint">No finds yet.</p>`;
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
