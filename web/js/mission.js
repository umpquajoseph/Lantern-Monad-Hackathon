// Lantern Mission Control: plays a campaign run (docs/handoffs.md, "Campaign
// simulator") sortie by sortie on the 3D world (world.js). The hackathon's
// page, with the chain taken out: the 3D scene fills the window, the
// first-person box, the satellite map and the lidar sit in small boxes over
// it, the panels on the left, one play bar along the bottom.
//
// Everything drawn comes from the run record, its poses and, where a record
// has it, the per-step pipeline data (campaign.js loadPipeline). Sensor frames
// are not saved, so the first-person box is drawn here from the 3D ground at
// the Scout's camera pose, with the logged rim points, route and lidar on top.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import { Terrain, fmtClock } from "./data.js";
import { World, COLORS, CAMERA_HEIGHT_M, CAMERA_PITCH_DEG, CAMERA_HFOV_DEG } from "./world.js";
import { MapView } from "./map.js";
import { MissionPlayer, ensureStyles } from "./mission_player.js";
import {
  CampaignRun, FleetRun, loadPipeline, groundEverywhere, loadIndex, assetsBase,
  SCOUT_COLOR, STATUS_COLOR, STATUS_WORD, ACTIVITY, OUTCOME_WORD, DOCKED, fmtNum, fmtDay, standinOf, esc,
} from "./campaign.js";

const $ = (id) => document.getElementById(id);
const hexInt = (h) => parseInt(h.slice(1), 16);
// world.js and map.js colour by key: the Scouts and the campaign's find statuses.
for (const [k, v] of Object.entries(SCOUT_COLOR)) COLORS[k] = hexInt(v);
for (const [k, v] of Object.entries(STATUS_COLOR)) COLORS[k] = hexInt(v);
const scol = (a) => SCOUT_COLOR[a] || "#cccccc";
const NARROW = matchMedia("(max-width: 760px)");

// Rover camera as sim/scene.py: 640 x 480 (FP_SIZE, assumed), 60 deg wide
// (HFOV_DEG), 15 deg down (CAMERA_PITCH_DEG), on the 1.6 m mast.
const FP = { w: 640, h: 480, pitch: THREE.MathUtils.degToRad(CAMERA_PITCH_DEG), camH: CAMERA_HEIGHT_M,
  fx: 320 / Math.tan(THREE.MathUtils.degToRad(CAMERA_HFOV_DEG / 2)) };
FP.vfovDeg = 2 * THREE.MathUtils.radToDeg(Math.atan(Math.tan(THREE.MathUtils.degToRad(CAMERA_HFOV_DEG / 2)) * FP.h / FP.w));
// Lidar, copied from sim/lidar.py: 120 x 33 deg (H_FOV_DEG, V_ANGLES_DEG -35..-2), 30 m
// (MAX_RANGE_M), panel to 25 m (VIEW_RANGE_M), 0.25 m right of the camera (LIDAR_RIGHT_OF_CAMERA_M).
const LIDAR = { hfov: 120, elLo: -35, elHi: -2, maxR: 30, viewR: 25, right: 0.25 };
const LIDAR_FP_COLS = 60, LIDAR_FP_ROWS = 20, LIDAR_FP_STEP_M = 0.5;   // viewer only, assumed: rays in the drawn first-person lidar
const HAZ_RGB = { 1: [95, 98, 104], 2: [240, 160, 40], 3: [230, 50, 40] };   // sim/lidar.py view(): safe, crater floor, no-go
const SEARCH_LABEL_AFTER_S = 1800;   // display only: a search's label stays this long after it ends, with its result
const FIND_VIEW_M = 120;             // display only: finds this near are marked in the first-person box

const S = {
  index: [], batch: null, cr: null, run: null, sortie: null,
  t: 0, playing: false, speed: 600, cam: "third", rover: null, tab: "sorties", lidarKind: "top", busy: false,
  opts: { fill: false, routes: true, particles: true, rims: true, hazards: true, nogo: true, craters: true, truth: false, next: true },
  player: null,
};
let terrain, world, map, fpCam, dockObj = null, craterRings = [], marks = [], odo = {}, susp = {}, dressed = {};
const addedT = new Map();   // crater_id -> campaign time it joined the dock's map

// ------------------------------------------------------------------ boot
async function main() {
  const P = new URLSearchParams(location.search);
  ensureStyles();
  try { S.index = await loadIndex(); } catch { S.index = []; }
  const pick = pickRun(S.index, P.get("batch"), P.get("run"));
  if (!pick) { $("loading").textContent = "No campaign runs yet. Run python app.py, or python web/make_standin_campaign.py for a labelled stand-in."; return; }
  S.batch = pick.batch;
  try {
    S.cr = await CampaignRun.load(pick.batch, pick.run);
  } catch (e) {
    $("loading").textContent = `Run ${pick.run} not found in ${pick.batch}.`;
    throw e;
  }
  const rec = S.cr.rec;
  $("loading").textContent = "Loading terrain";
  try {
    terrain = await Terrain.load(await assetsBase(rec));
  } catch (e) {
    $("loading").textContent = "Terrain assets missing: run python app.py";
    throw e;
  }
  const ground = await groundEverywhere(terrain, rec);
  world = new World($("viewport"), terrain, P.get("quality") || "high");
  world.hideOdom = true;   // no odometry-alone line in the 3D view
  world.buildTerrain();
  world.buildBackdrop();
  world.loadRocks();
  world.initPatches();
  fpCam = new THREE.PerspectiveCamera(FP.vfovDeg, FP.w / FP.h, 0.3, 3000);
  fpCam.up.set(0, 0, 1);
  map = new MissionMap($("map-cv"), terrain);
  map.opts = mapOpts();
  map.groundLayers = [ground.far, ground.near].filter(Boolean).map(hillshade);
  for (const e of S.cr.events) if (e.type === "crater_added") addedT.set(e.crater_id, e.t);
  dock3d();
  craters3d();
  runInfo();
  sortieList();
  bind();
  if (NARROW.matches) $("panel").classList.add("closed");
  syncCollapse();
  const s = S.cr.sortieById[P.get("sortie")] || S.cr.rec.sorties[0];
  await openSortie(s, { t: P.has("t") ? Number(P.get("t")) : null, follow: P.get("follow") });
  window.lantern = { world, S, map };   // for poking at the scene from the browser console
  $("loading").hidden = true;
  requestAnimationFrame(loop);
}

// The run asked for, else one that carries pipeline data (every view has
// something to draw), else the first run with a record.
function pickRun(index, batch, run) {
  if (batch && run) return { batch, run };
  const withMap = index.flatMap((b) => b.runs.filter((r) => r.map).map((r) => ({ batch: b.batch_id, run: r.run_id, r })));
  const piped = withMap.filter((x) => x.r.pipeline);
  const best = piped.find((x) => x.r.n_scouts >= 3) || piped[0] || withMap.find((x) => x.r.n_scouts >= 2) || withMap[0];
  return best ? { batch: best.batch, run: best.run } : null;
}

// One sortie's window: every Scout out during it, plus its pipeline data.
async function openSortie(s, { t = null, follow = null } = {}) {
  if (S.busy) return;
  S.busy = true;
  const wasPlaying = S.playing;
  S.playing = false;
  showToast(`Loading sortie ${s.sortie_id}`);
  const over = S.cr.rec.sorties.filter((x) => x.t_start < s.t_end && x.t_end > s.t_start).map((x) => x.sortie_id);
  const pipes = await loadPipeline(S.cr, over);
  const run = new FleetRun(S.cr, s, { pipes });
  const pts = run.agencies.flatMap((a) => run.tracks[a].truth.filter((_, i) => i % 3 === 0));
  await Promise.all([...terrain.keysNear(pts, 4)].map((k) => terrain.loadTile(k)));
  S.run = run;
  S.sortie = s;
  S.t = Math.min(run.tEnd, Math.max(run.times[0], t ?? s.t_start));
  S.rover = run.agencies.includes(follow) ? follow : run.agencies.includes(S.rover) && follow === "keep" ? S.rover : s.scout;
  world.currentT = S.t;
  world.follow = S.rover;
  world.setRun(run);
  for (const [a, r] of Object.entries(world.rovers)) r.g.visible = run.agencies.includes(a);
  for (const a of run.agencies) if (!dressed[a]) { dressed[a] = true; dressRover(a); }
  odo = {};
  for (const a of run.agencies) {   // distance driven by each step, for the wheels
    const T = run.tracks[a].truth, c = [0];
    for (let i = 1; i < T.length; i++) c.push(c[i - 1] + Math.hypot(T[i][0] - T[i - 1][0], T[i][1] - T[i - 1][1]));
    odo[a] = c;
  }
  vis();
  map.setRun(run);
  searchMarks();
  for (const o of Object.values(plan3d)) { if (o.line) { world.scene.remove(o.line); o.line.geometry.dispose(); o.line = null; } o.k = -1; }
  fpLidarCache = null;
  S.player = new MissionPlayer(checkpointsFor(run), {
    pause: (tt) => { S.t = tt; S.playing = false; setPlayIcon(); },
    note: (html) => { const b = $("checkpoint"); b.hidden = !html; b.innerHTML = html || ""; },
  });
  S.player.rewound(S.t);
  roverSeg();
  timeline();
  markSortie();
  setCam(S.cam);
  if (S.cam === "third") world.setFollow(S.rover);
  urlState();
  S.busy = false;
  S.playing = wasPlaying;
  setPlayIcon();
  render(true);
}

// ------------------------------------------------------------- rovers
// All Scouts are identical, so all get the same NASA model (a Mars rover,
// standing in for a lunar one, credited on the page). It is measured once
// loaded, turned to this scene's frame (x forward, y left, z up), scaled to
// a rover length and set on the ground. The box rover stays underneath until
// the model arrives, and if it fails.
const MODEL = { file: "models/opportunity.glb", name: "Opportunity (MER-B)", length: 2.2 };
const gltf = new GLTFLoader();
// NASA's files are Draco-compressed; the decoder comes from the same pinned three.js release.
gltf.setDRACOLoader(new DRACOLoader().setDecoderPath("https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/libs/draco/gltf/"));
let modelScene = null, modelWait = null;

function dressRover(a) {
  if (!modelWait) modelWait = new Promise((ok, bad) => gltf.load(MODEL.file, (res) => ok(res.scene), undefined, bad));
  modelWait.then((scene) => {
    modelScene = scene;
    const g = world.rovers[a].g;
    const model = scene.clone(true);
    // The lamp sits inside the model's mast (1.4 m): a stand-in body must not shadow the lamp's own pool.
    model.traverse((o) => { if (o.isMesh) { o.castShadow = false; o.receiveShadow = true; } });
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
    fit.scale.setScalar(MODEL.length / size.x);
    fit.updateMatrixWorld(true);
    box = new THREE.Box3().setFromObject(fit);
    const c = box.getCenter(new THREE.Vector3());
    fit.position.set(-0.2 - c.x, -c.y, -box.min.z);   // centred on the body, wheels on the ground
    fit.name = "nasa-model";
    for (const o of g.children) if (o.isMesh) o.visible = false;   // hide the box body; keep the lamp and the tag
    const sg = new THREE.Group();
    sg.name = "suspension";
    sg.add(fit);
    g.add(sg);
    g.updateMatrixWorld(true);
    setupSuspension(a, g, sg, fit);
    $("model-credit").textContent = `Rover model: NASA 3D Resources, ${MODEL.name}, a Mars rover standing in for the Scouts.`;
  }).catch((err) => console.warn("NASA model not loaded; keeping the simple rover", err));
}

// The body rides on a group that tilts and lifts with the ground under six
// contact points (Opportunity is one mesh: its wheels do not move alone).
function setupSuspension(a, g, sg, fit) {
  g.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(fit).applyMatrix4(g.matrixWorld.clone().invert());
  const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
  const L = size.x * 0.36, W = size.y * 0.4, contacts = [];
  for (const fx of [c.x - L, c.x, c.x + L]) for (const fy of [-W, W]) contacts.push([fx, fy]);
  susp[a] = { g: sg, pitch: 0, roll: 0, z: 0, contacts };
}

// Each frame: fit a plane to the ground under the contacts, tilt and lift the body onto it.
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
  const pitch = Math.atan(b), roll = Math.atan(cc), zc = mz + b * (0 - mx) + cc * (0 - my);
  const jump = Math.abs(pitch - S_.pitch) > 0.25 || Math.abs(roll - S_.roll) > 0.25;
  const k = jump ? 1 : 0.25;
  S_.pitch += (pitch - S_.pitch) * k; S_.roll += (roll - S_.roll) * k; S_.z += (zc - S_.z) * k;
  S_.g.rotation.set(S_.roll, -S_.pitch, 0);
  S_.g.position.z = S_.z;
}

// A very dim rim light on the rover models, in each material's own shader,
// so it lights the silhouettes and nothing else. Viewer only.
const ROVER_RIM_RGB = [0.11, 0.13, 0.16], ROVER_RIM_POW = 2.5;   // assumed, display only
function addRoverRim(a) {
  const g = (world.rovers[a] || {}).g;
  if (!g) return;
  let n = 0;
  g.traverse((o) => { if (o.isMesh) n++; });
  if (g.userData.rimN === n) return;   // the model can arrive late: patch what is new
  g.userData.rimN = n;
  g.traverse((o) => {
    const m = o.isMesh && o.material;
    if (!m || Array.isArray(m) || m.userData.rim || !(m.isMeshStandardMaterial || m.isMeshPhongMaterial || m.isMeshLambertMaterial)) return;
    const r = (o.material = m.clone());
    r.userData.rim = true;
    r.onBeforeCompile = (sh) => {
      sh.fragmentShader = sh.fragmentShader.replace("#include <emissivemap_fragment>", `#include <emissivemap_fragment>
        { float rimF = 1.0 - abs(dot(normalize(normal), normalize(vViewPosition)));
          totalEmissiveRadiance += vec3(${ROVER_RIM_RGB.map((v) => v.toFixed(3)).join(", ")}) * pow(rimF, ${ROVER_RIM_POW.toFixed(1)}); }`);
    };
    r.customProgramCacheKey = () => "lantern-rover-rim";
    r.needsUpdate = true;
  });
}

// ------------------------------------------------------------- overlays
function dock3d() {
  const d = S.cr.rec.dock;
  const g = new THREE.Group();
  const p = world.groundL(d.x, d.y);
  const pad = new THREE.Mesh(new THREE.BoxGeometry(4, 4, 0.4), new THREE.MeshStandardMaterial({ color: 0xdddddd, roughness: 0.7 }));
  pad.position.set(p.x, p.y, p.z + 0.2);
  pad.receiveShadow = true;
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 3, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  mast.rotation.x = Math.PI / 2;
  mast.position.set(p.x, p.y, p.z + 1.9);
  const lbl = world.label("<span>Dock</span>", "#ffffff");
  lbl.position.set(p.x, p.y, p.z + 4);
  g.add(pad, mast, lbl);
  world.scene.add(g);
  dockObj = g;
}

// Craters the Scouts added to the dock's map: a green ring on the ground from
// the moment the dock merged it, so the map can be seen growing in 3D too.
function craters3d() {
  const mat = world.lineMat(0x7ddc7a, 2, { dashed: true, opacity: 0.85 });
  for (const c of S.cr.rec.crater_map.added || []) {
    const ring = world.ring(c.x, c.y, c.d / 2, mat, 48);
    ring.visible = false;
    ring.userData = { t: addedT.has(c.crater_id) ? addedT.get(c.crater_id) : Infinity };
    world.scene.add(ring);
    craterRings.push(ring);
  }
}

// A Scout searching another Scout's find: a ring of the search radius round
// the find, in the searcher's colour, with a label, while the search runs
// and for a while after with its result. This is the cross-check.
function searchMarks() {
  for (const m of marks) for (const o of [m.ring, m.lbl]) { world.scene.remove(o); if (o.geometry) o.geometry.dispose(); if (o.element) o.element.remove(); }
  marks = [];
  const run = S.run, cr = S.cr, T0 = run.times[0], T1 = run.tEnd;
  const findEv = new Map(cr.iceEvents.filter((e) => e.type === "find").map((e) => [e.find_id, e]));
  for (const e of cr.iceEvents) {
    if (e.type !== "search_start" || e.t > T1) continue;
    const end = cr.iceEvents.find((x) => x.type === "search_end" && x.find_id === e.find_id && x.sortie_id === e.sortie_id && x.t >= e.t);
    if ((end ? end.t : e.t) + SEARCH_LABEL_AFTER_S < T0) continue;
    const f = findEv.get(e.find_id);
    if (!f || !run.agencies.includes(e.scout)) continue;
    const ring = world.ring(f.est[0], f.est[1], e.radius_m, world.lineMat(hexInt(scol(e.scout)), 3, { dashed: true }));
    const p = world.groundL(f.est[0], f.est[1]);
    const lbl = world.label("", scol(e.scout));
    lbl.position.set(p.x, p.y, p.z + 3);
    lbl.center.set(1, 0.5);   // low and left of the find's beam, whose own label sits above-right
    world.scene.add(ring, lbl);
    marks.push({ start: e, end, find: f, ring, lbl });
  }
}

function updateMarks(t) {
  for (const s of marks) {
    const t0 = s.start.t, t1 = s.end ? s.end.t : t0;
    const on = t >= t0 && t <= t1 + SEARCH_LABEL_AFTER_S;
    s.ring.visible = on && t <= t1;
    s.lbl.visible = on;
    s.lbl.element.style.display = on ? "" : "none";
    if (!on) continue;
    const res = t > t1 && s.end ? ` · ${s.end.result === "confirmed" ? "confirmed" : "not confirmed"}` : "";
    const html = `<span>${s.start.scout} ${t <= t1 ? "searching" : "searched"} ${s.find.scout}'s find ${s.find.find_id}${res}</span>`;
    if (s.lbl.element.innerHTML !== html) s.lbl.element.innerHTML = html;
  }
  for (const r of craterRings) r.visible = S.opts.craters && t >= r.userData.t;
}

// The route each Scout's pipeline planned at the shown step, draped on the
// ground as amber dashes, the same line the first-person box and the map draw.
const PLAN_HEX = 0xffc83c;
const plan3d = {};
function updatePlan3D(t) {
  const run = S.run, k = run.stepAt(t);
  for (const a of run.agencies) {
    let o = plan3d[a];
    if (!o) {
      const mat = world.lineMat(PLAN_HEX, 3, { dashed: true, opacity: 0.95 });
      mat.dashSize = 0.6; mat.gapSize = 0.4;   // metres, as the first-person dashes
      o = plan3d[a] = { mat, line: null, k: -1 };
    }
    if (o.k !== k) {
      o.k = k;
      if (o.line) { world.scene.remove(o.line); o.line.geometry.dispose(); o.line = null; }
      const plan = run.steps[k].rovers[a].plan;
      if (plan && plan.length > 1) {
        o.line = world.drape(plan, o.mat, 0.5).line;
        o.line.renderOrder = 3;
        world.scene.add(o.line);
      }
    }
    if (o.line) o.line.visible = S.opts.routes;
  }
  for (const [a, o] of Object.entries(plan3d)) if (!run.agencies.includes(a) && o.line) o.line.visible = false;
}

// The planner's no-go ground (assets/terrain/nogo.png: over 15 deg across
// 2 m, the satellite map's layer) shaded red on the 3D ground near the Scout
// being followed. One draped grid, rebuilt when the Scout has moved
// STEEP_STEP_M. Unlit, so it reads in the dark. Drive area only.
const STEEP_HALF_M = 120, STEEP_CELL_M = 0.5, STEEP_STEP_M = 40, STEEP_LIFT_M = 0.06, STEEP_OPACITY = 0.72;   // assumed, display only
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
    // Outside the nogo image the texture clamps to its edge: transparent there (build_assets.py draws clear ground clear).
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

// --------------------------------------------------------- checkpoints
// The moments the video and a viewer want to stop on, from the record's own
// events inside this sortie's window: a find, a second Scout's search result, a drill.
function checkpointsFor(run) {
  const T0 = run.times[0], T1 = run.tEnd, out = [];
  const finder = (id) => (S.cr.iceEvents.find((x) => x.type === "find" && x.find_id === id) || {}).scout || "?";
  for (const e of S.cr.iceEvents) {
    if (e.t <= T0 || e.t > T1 || !run.agencies.includes(e.scout)) continue;
    if (e.type === "find") out.push({ t: e.t, label: `${e.scout} logs find ${e.find_id}`, say: `${fmtNum(e.reading_wt, 1)} wt%, within ${fmtNum(e.bound95, 1)} m (95%)` });
    if (e.type === "search_end") out.push({ t: e.t, label: `${e.scout}: ${finder(e.find_id)}'s find ${e.find_id} ${e.result === "confirmed" ? "confirmed" : "not confirmed"}`, say: SEARCH_WHY[e.reason] || "" });
    if (e.type === "drill") out.push({ t: e.t, label: `${e.scout} drills find ${e.find_id}`, say: `its position error ${fmtNum(e.true_err_m, 1)} m` });
  }
  return out;
}
const SEARCH_WHY = { match: "a matching reading inside the search area", no_readings: "no readings in the search area",
  no_match: "no reading matched", bound_too_large: "the combined position bound was too large to judge" };

// --------------------------------------------------------------- controls
function bind() {
  $("play").onclick = togglePlay;
  $("checkpoint").onclick = () => { if (!S.playing) togglePlay(); };
  $("restart").onclick = () => { S.t = S.run.times[0]; S.playing = false; setPlayIcon(); S.player.restart(); render(true); };
  $("speed").onchange = (e) => (S.speed = Number(e.target.value));
  $("scrub").oninput = (e) => {
    const T0 = S.run.times[0], nt = T0 + (Number(e.target.value) / 1000) * (S.run.tEnd - T0);
    if (nt < S.t) S.player.rewound(nt); else S.player.jumped(nt);
    $("checkpoint").hidden = true;
    S.t = nt;
    render(true);
  };
  $("step-back").onclick = () => stepBy(-1);
  $("step-fwd").onclick = () => stepBy(1);
  $("zoom-in").onclick = () => zoom(1 / 1.35);
  $("zoom-out").onclick = () => zoom(1.35);
  $("sortie-pick").onchange = (e) => openSortie(S.cr.sortieById[e.target.value]);
  document.querySelectorAll("#lid-seg button").forEach((b) => (b.onclick = () => setLidar(b.dataset.lk)));
  document.querySelectorAll("[data-expand]").forEach((b) => (b.onclick = () => setCam(S.cam === b.dataset.expand ? "third" : b.dataset.expand)));
  $("back-chip").onclick = () => setCam("third");
  document.querySelectorAll(".rail button").forEach((b) => (b.onclick = () => {
    const p = $("panel");
    if (p.classList.contains("closed")) p.classList.remove("closed");
    else if (S.tab === b.dataset.tab) { p.classList.add("closed"); syncCollapse(); return; }
    setTab(b.dataset.tab);
    syncCollapse();
  }));
  $("collapse").onclick = () => { $("panel").classList.toggle("closed"); syncCollapse(); };
  for (const k of ["fill", "routes", "particles", "rims", "hazards", "nogo", "craters", "truth", "next"]) {
    $("opt-" + k).checked = S.opts[k];
    $("opt-" + k).onchange = (e) => { S.opts[k] = e.target.checked; vis(); render(true); };
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
  $("runs-btn").onclick = openRuns;
  $("dr-close").onclick = () => ($("drawer").hidden = true);
  $("banner").onclick = () => { $("panel").classList.remove("closed"); setTab("run"); syncCollapse(); };
  addEventListener("keydown", (e) => {
    // Typing in a field: leave its keys alone. The slider and the checkboxes keep the shortcuts.
    if (e.target.closest?.("input:not([type=range]):not([type=checkbox]), select, textarea")) return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    if (e.code === "ArrowRight") { e.preventDefault(); stepBy(1); }
    if (e.code === "ArrowLeft") { e.preventDefault(); stepBy(-1); }
    if (e.key === "[") sortieBy(-1);
    if (e.key === "]") sortieBy(1);
    if (e.key === "Escape") $("drawer").hidden = true;
    if (e.key === "+" || e.key === "=") zoom(1 / 1.35);
    if (e.key === "-") zoom(1.35);
    // 1 / 2 blow the first-person box or the satellite map up to the full window (again: back).
    if (e.key === "1") setCam(S.cam === "first" ? "third" : "first");
    if (e.key === "2") setCam(S.cam === "overhead" ? "third" : "overhead");
    if (e.key === "f" || e.key === "F") $("opt-fill").click();
    if (e.key === "t" || e.key === "T") $("opt-truth").click();
    if (e.key === "l" || e.key === "L") setLidar(S.lidarKind === "top" ? "fp" : "top");
  });
}

function vis() {
  world.setVisibility({ fill: S.opts.fill, tracks: S.opts.routes, truth: S.opts.truth });
  $("vnote").hidden = !S.opts.fill;
  map.opts = mapOpts();
  document.body.classList.toggle("truth-on", S.opts.truth);
}
const mapOpts = () => ({ tracks: S.opts.routes, truth: S.opts.truth, nogo: S.opts.nogo, particles: S.opts.particles, terrainCraters: false });

function setLidar(kind) {
  S.lidarKind = kind;
  document.querySelectorAll("#lid-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.lk === kind)));
  lidarKey = null;
  render(true);
}

function syncCollapse() {
  const closed = $("panel").classList.contains("closed"), b = $("collapse");
  b.setAttribute("aria-label", closed ? "Show panel" : "Hide panel");
  b.title = closed ? "Show panel" : "Hide panel";
  b.setAttribute("aria-expanded", String(!closed));
}

function setTab(tab) {
  S.tab = tab;
  document.querySelectorAll(".rail button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  document.querySelectorAll(".pane").forEach((p) => (p.hidden = p.dataset.pane !== tab));
  panelKey = {};
  if (S.run) renderPanels(S.t);
}

// One logged step back or forward, paused.
function stepBy(n) {
  const run = S.run;
  S.playing = false;
  setPlayIcon();
  const k = Math.min(run.steps.length - 1, Math.max(0, run.stepAt(S.t) + n));
  const nt = run.times[k];
  if (nt < S.t) S.player.rewound(nt); else S.player.jumped(nt);
  $("checkpoint").hidden = true;
  S.t = nt;
  render(true);
}

function sortieBy(n) {
  const L = S.cr.rec.sorties, i = L.indexOf(S.sortie) + n;
  if (i >= 0 && i < L.length) openSortie(L[i]);
}

function setPlayIcon() {
  $("play").innerHTML = `<svg><use href="#i-${S.playing ? "pause" : "play"}"/></svg>`;
  $("play").setAttribute("aria-label", S.playing ? "Pause" : "Play");
}

function togglePlay() {
  if (S.busy) return;
  if (S.t >= S.run.tEnd) { S.t = S.run.times[0]; S.player.restart(); }
  S.playing = !S.playing;
  if (S.playing) $("checkpoint").hidden = true;
  setPlayIcon();
}

// Move the 3D camera toward or away from what it is looking at.
function zoom(f) {
  if (S.cam !== "third") return;
  const c = world.controls, cam = world.camera;
  const off = cam.position.clone().sub(c.target);
  off.setLength(THREE.MathUtils.clamp(off.length() * f, c.minDistance, c.maxDistance));
  cam.position.copy(c.target).add(off);
  c.update();
}

// The big view is the third-person chase camera; the first-person box or the
// satellite map can be blown up to fill the window, and the chip returns.
function setCam(cam) {
  S.cam = cam;
  $("fp").hidden = cam !== "first";
  $("in-fp").hidden = cam !== "third";
  $("in-map").classList.toggle("full", cam === "overhead");
  document.body.classList.toggle("map-full", cam === "overhead");
  $("back-chip").hidden = cam === "third";
  world.labels.domElement.style.display = cam === "third" ? "" : "none";
  if (cam === "third") world.setFollow(S.rover);
  if (S.run) render(true);
}

function setRover(a) {
  S.rover = a;
  document.querySelectorAll("#rover-seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.rover === a)));
  if (S.cam === "third") world.setFollow(a);
  steepAt = null;
  lidarKey = null;
  timelineActs();
  panelKey = {};
  render(true);
}

function roverSeg() {
  $("rover-seg").innerHTML = S.run.agencies.map((a) =>
    `<button data-rover="${a}" aria-selected="${a === S.rover}"><i class="dot" style="background:${scol(a)}"></i>Scout ${a}</button>`).join("");
  document.querySelectorAll("#rover-seg button").forEach((b) => (b.onclick = () => setRover(b.dataset.rover)));
}

function urlState() {
  const q = new URLSearchParams({ batch: S.batch, run: S.cr.rec.run_id, sortie: S.sortie.sortie_id });
  history.replaceState(null, "", `?${q}`);
}

// ------------------------------------------------------------------- loop
let last = performance.now(), lastPanelAt = 0;
function loop(now) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  if (S.run && !S.busy) {
    const prevT = S.t;
    if (S.playing) {
      S.t = Math.min(S.run.tEnd, S.t + dt * S.speed);
      if (!S.player.tick(prevT, S.t) && S.t >= S.run.tEnd) {
        // A run plays sortie by sortie: on into the next one, from the same moment when they overlap.
        const L = S.cr.rec.sorties, nx = L[L.indexOf(S.sortie) + 1];
        if (S.opts.next && nx) openSortie(nx, { t: Math.max(nx.t_start, Math.min(S.t, nx.t_end)) });
        else { S.playing = false; setPlayIcon(); }
      }
    }
    if (!S.busy) {
      const panels = now - lastPanelAt > 150;
      render(panels);
      if (panels) lastPanelAt = now;
    }
  }
  requestAnimationFrame(loop);
}

// ----------------------------------------------------------------- render
function render(panels) {
  const run = S.run, t = S.t;
  if (!run || !world) return;
  world.currentT = t;
  for (const a of run.agencies) addRoverRim(a);
  world.update(t, { tracks: S.opts.routes, particles: S.opts.particles });
  const k = run.stepAt(t), nxt = run.steps[Math.min(k + 1, run.steps.length - 1)];
  // A Scout at the dock has no estimate to draw.
  for (const a of run.agencies) {
    const docked = nxt.info[a].act === DOCKED;
    if (world.estDots[a]) world.estDots[a].visible = S.opts.routes && !docked;
    if (world.sigmaRings[a]) world.sigmaRings[a].visible = S.opts.routes && !docked;
    const tag = world.rovers[a] && world.rovers[a].tag;
    const html = `<span>${a}${docked ? " · docked" : ""}</span>`;
    if (tag && tag.innerHTML !== html) tag.innerHTML = html;
  }
  updateSteepShade(t);
  updatePlan3D(t);
  updateMarks(t);
  for (const a of run.agencies) updateSuspension(a, t);
  if (S.cam === "third") world.render();
  else if (S.cam === "first") { world.renderer.setClearColor(0x000000, 1); world.renderer.clear(); }
  if (S.cam !== "overhead") firstPerson(k, t);
  map.update(t);
  drawLidar(k);
  $("clock").innerHTML = `${fmtClock(t - run.times[0])}<small>${fmtDay(t)}</small>`;
  const f = (t - run.times[0]) / Math.max(1, run.tEnd - run.times[0]);
  $("tl-fill").style.width = `${100 * f}%`;
  $("scrub").value = String(Math.round(1000 * f));
  if (panels) renderPanels(t);
}

// ------------------------------------------------------ first-person box
// The 3D ground from the followed Scout's camera pose, lamp only, rendered
// into the canvas under the box (or the full window), then what its pipeline
// logged at that step drawn on top: rim points, planned route, lidar hazards.
function firstPerson(k, t) {
  const a = S.rover, run = S.run;
  const full = S.cam === "first";
  const box = full ? $("fp-box") : $("fp-mini"), ov = full ? $("fp-ov") : $("fpi-ov");
  const docked = run.steps[Math.min(k + 1, run.steps.length - 1)].info[a].act === DOCKED;
  const none = $("fpi-none");
  none.hidden = full || !docked;
  if (!full) none.textContent = "At the dock";
  if (box.offsetParent === null && !full) return;
  const R = box.getBoundingClientRect(), C = world.renderer.domElement.getBoundingClientRect();
  if (!R.width || !R.height) return;
  const r3 = world.renderer;
  const x = R.left - C.left, y = C.bottom - R.bottom;
  if (docked) {
    r3.setScissorTest(true); r3.setScissor(x, y, R.width, R.height); r3.setClearColor(0x050506, 1); r3.clear(); r3.setScissorTest(false);
    ov.getContext("2d").clearRect(0, 0, ov.width, ov.height);
    return;
  }
  const [px, py, ph] = run.poseAt(a, t);
  const camZ = terrain.heightAt(px, py) + FP.camH;
  fpCam.position.copy(world.L(px, py, camZ));
  fpCam.lookAt(world.L(px + Math.cos(ph) * Math.cos(FP.pitch), py + Math.sin(ph) * Math.cos(FP.pitch), camZ - Math.sin(FP.pitch)));
  fpCam.aspect = R.width / R.height;
  fpCam.updateProjectionMatrix();
  // What the camera would see: ground, rocks, other Scouts and their lamps.
  // Not the page's drawings (tracks, rings, labels) nor this Scout's own body.
  const hide = [world.overlay, steepMesh, dockObj && dockObj.children[2], ...craterRings, ...marks.flatMap((m) => [m.ring, m.lbl]),
    ...Object.values(plan3d).map((o) => o.line)].filter((o) => o && o.visible);
  const own = [];
  world.rovers[a].g.traverse((o) => { if (o.isMesh && o.visible) own.push(o); });
  for (const o of [...hide, ...own]) o.visible = false;
  const fill = [world.fill.visible, world.ambient.visible];
  world.fill.visible = world.ambient.visible = false;
  r3.setScissorTest(true);
  r3.setScissor(x, y, R.width, R.height);
  r3.setViewport(x, y, R.width, R.height);
  r3.setClearColor(0x000000, 1);
  r3.render(world.scene, fpCam);
  r3.setViewport(0, 0, C.width, C.height);
  r3.setScissorTest(false);
  [world.fill.visible, world.ambient.visible] = fill;
  for (const o of [...hide, ...own]) o.visible = true;
  drawFpOverlay(ov, k, [px, py, ph], camZ, !full);
}

// Project a world point into the drawn camera (pinhole, sim/scene.py camera_model()).
function project(pose, camZ, wx, wy, wz) {
  const [x, y, h] = pose;
  const dx = wx - x, dy = wy - y, up = wz - camZ;
  const fwd = dx * Math.cos(h) + dy * Math.sin(h), left = -dx * Math.sin(h) + dy * Math.cos(h);
  const depth = fwd * Math.cos(FP.pitch) - up * Math.sin(FP.pitch);
  if (depth < 0.5) return null;
  const yu = fwd * Math.sin(FP.pitch) + up * Math.cos(FP.pitch);
  return [FP.w / 2 - (FP.fx * left) / depth, FP.h / 2 - (FP.fx * yu) / depth, depth];
}

// The pose a step's pipeline data belongs to: the Scout's true pose at the pipeline step.
const pipePose = (a, r) => (r.pipeT != null ? S.run.poseAt(a, r.pipeT) : null);

function drawFpOverlay(cv, k, pose, camZ, small) {
  const c = cv.getContext("2d");
  c.clearRect(0, 0, FP.w, FP.h);
  const a = S.rover, run = S.run, r = run.rover(a, k);
  const K = small ? 2 : 1;
  const font = (px) => `600 ${px * K}px Barlow, Helvetica, Arial, sans-serif`;
  const P = (x, y, lift = 0.05) => project(pose, camZ, x, y, terrain.heightAt(x, y) + lift);
  const col = scol(a);
  const pp = pipePose(a, r);
  const toWorld = (f, l) => [pp[0] + f * Math.cos(pp[2]) - l * Math.sin(pp[2]), pp[1] + f * Math.sin(pp[2]) + l * Math.cos(pp[2])];
  // Lidar hazard cells, translucent on the ground.
  const g = run.lidarGrid;
  if (S.opts.hazards && pp && g && r.lidar) {
    for (let i = 0; i < g.nx; i++) for (let j = 0; j < g.ny; j++) {
      const ch = r.lidar.charCodeAt(i * g.ny + j) - 48;
      if (ch < 2) continue;
      const f0 = g.fwd0_m + i * g.cell_m, l0 = -g.half_width_m + j * g.cell_m;
      const q = [[f0, l0], [f0 + g.cell_m, l0], [f0 + g.cell_m, l0 + g.cell_m], [f0, l0 + g.cell_m]].map(([f, l]) => P(...toWorld(f, l), 0.03));
      if (q.some((v) => !v)) continue;
      const [R_, G_, B_] = HAZ_RGB[ch];
      c.fillStyle = `rgba(${R_},${G_},${B_},0.42)`;
      c.beginPath(); c.moveTo(q[0][0], q[0][1]); for (const v of q.slice(1)) c.lineTo(v[0], v[1]); c.closePath(); c.fill();
    }
  }
  // The planned route as amber dashes on the ground.
  const plan = S.opts.routes ? r.plan : null;
  if (plan && plan.length > 1) {
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
          const pa = P(start[0], start[1]), pb = P(p1[0], p1[1]);
          if (pa && pb) {
            c.lineWidth = K * Math.max(1.2, Math.min(6, 20 / Math.max(pa[2], 1)));
            c.beginPath(); c.moveTo(pa[0], pa[1]); c.lineTo(pb[0], pb[1]); c.stroke();
          }
        }
        left -= take; s0 = s1;
        if (left <= 1e-9) { on = !on; left = on ? DASH : GAP; start = p1; }
      }
    }
  }
  // Finds near enough to see: a beam with its label, and a search ring while one runs.
  for (const f of run.findsAt(k)) {
    const d = Math.hypot(f.x - pose[0], f.y - pose[1]);
    if (d > FIND_VIEW_M) continue;
    const base = P(f.x, f.y), tip = P(f.x, f.y, 4);
    if (!base || !tip) continue;
    const fc = STATUS_COLOR[f.status] || "#fff";
    c.strokeStyle = fc; c.lineWidth = 2 * K;
    c.beginPath(); c.moveTo(base[0], base[1]); c.lineTo(tip[0], tip[1]); c.stroke();
    c.fillStyle = fc; c.font = font(12);
    c.fillText(`find ${f.find_id} · ${f.status} · ${d.toFixed(0)} m`, tip[0] + 5, tip[1] + 4);
  }
  // Rim points the navigation used at this step: rover frame at the step's
  // pose into the world, then onto the drawn view. Filled: matched to a mapped
  // crater. Hollow: no map partner (a sighting). Hidden truth: a red cross on
  // a wrong match.
  if (S.opts.rims && pp && r.rims && r.rims.length) {
    const sz = 3 * K;
    c.lineWidth = 1.5 * K;
    r.rims.forEach(([f, l], i) => {
      const q = P(...toWorld(f, l), 0);
      if (!q) return;
      const [match, wrong] = r.rimInfo[i] || [null, false];
      if (match != null) { c.fillStyle = col; c.fillRect(q[0] - sz, q[1] - sz, 2 * sz, 2 * sz); }
      else { c.strokeStyle = "#ffffff"; c.strokeRect(q[0] - sz, q[1] - sz, 2 * sz, 2 * sz); }
      if (S.opts.truth && wrong) {
        c.strokeStyle = "#ff4d4d";
        c.beginPath(); c.moveTo(q[0] - 2 * sz, q[1] - 2 * sz); c.lineTo(q[0] + 2 * sz, q[1] + 2 * sz);
        c.moveTo(q[0] + 2 * sz, q[1] - 2 * sz); c.lineTo(q[0] - 2 * sz, q[1] + 2 * sz); c.stroke();
      }
    });
  }
  // What this box is, in the box itself (the full-window view has its own note).
  if (small) {
    c.font = font(10); c.fillStyle = "rgba(255,255,255,.7)";
    c.fillText(run.hasPipeline ? (run.pipelineStandin ? "drawn · stand-in pipeline data" : "drawn · logged pipeline data") : "drawn · no pipeline data in this record", 10, FP.h - 12);
  }
}

// ------------------------------------------------------------------ lidar
// The logged scan of the followed Scout at the shown step. Top-down: the
// hazard grid as sim/lidar.py view() draws it, forward up, rings every 5 m.
// First person: the same wedge drawn from the 3D ground at the lidar's pose
// (ray-marched here), shaded by range, the logged hazard cells coloured in.
let lidarKey = null, fpLidarCache = null;
function drawLidar(k) {
  const cv = $("lid-cv"), c = cv.getContext("2d"), W = cv.width, H = cv.height;
  const a = S.rover, run = S.run, r = run.rover(a, k), g = run.lidarGrid;
  const docked = run.steps[Math.min(k + 1, run.steps.length - 1)].info[a].act === DOCKED;
  const cap = `Scout ${a} lidar${run.pipelineStandin ? " (stand-in)" : ""}`;
  if ($("lid-cap").textContent !== cap) $("lid-cap").textContent = cap;
  const has = !!(g && r.lidar && !docked);
  $("lid-none").hidden = has;
  if (!has) {
    $("lid-none").textContent = docked ? "At the dock" : run.hasPipeline ? "No scan yet" : "No lidar logged in this record";
    if (lidarKey !== "none") { c.fillStyle = "#000"; c.fillRect(0, 0, W, H); lidarKey = "none"; }
    return;
  }
  const key = `${a}|${r.pipeT}|${S.lidarKind}`;
  if (key === lidarKey) return;
  lidarKey = key;
  c.fillStyle = "#000";
  c.fillRect(0, 0, W, H);
  if (S.lidarKind === "fp") return lidarFp(c, W, H, r, g, a);
  const pxm = (H - 14) / LIDAR.viewR, ox = W / 2, oy = H - 8;
  for (let i = 0; i < g.nx; i++) for (let j = 0; j < g.ny; j++) {
    const ch = r.lidar.charCodeAt(i * g.ny + j) - 48;
    if (ch < 1) continue;
    const [R_, G_, B_] = HAZ_RGB[ch];
    c.fillStyle = `rgb(${R_},${G_},${B_})`;
    const f0 = g.fwd0_m + i * g.cell_m, l0 = -g.half_width_m + j * g.cell_m;
    c.fillRect(ox - (l0 + g.cell_m) * pxm, oy - (f0 + g.cell_m) * pxm, g.cell_m * pxm + 0.5, g.cell_m * pxm + 0.5);
  }
  c.strokeStyle = "rgb(150,150,150)"; c.fillStyle = "rgb(150,150,150)"; c.lineWidth = 1;
  c.font = "14px Barlow, sans-serif";
  const hf = THREE.MathUtils.degToRad(LIDAR.hfov / 2);
  for (let rr = 5; rr <= LIDAR.viewR; rr += 5) {
    c.beginPath(); c.arc(ox, oy, rr * pxm, -Math.PI / 2 - hf, -Math.PI / 2 + hf); c.stroke();
    c.fillText(`${rr} m`, ox + rr * pxm * Math.sin(hf) + 3, oy - rr * pxm * Math.cos(hf));
  }
  for (const sg of [-1, 1]) { c.beginPath(); c.moveTo(ox, oy); c.lineTo(ox + sg * LIDAR.viewR * pxm * Math.sin(hf), oy - LIDAR.viewR * pxm * Math.cos(hf)); c.stroke(); }
  c.fillStyle = "#fff";
  c.beginPath(); c.moveTo(ox, oy - 7); c.lineTo(ox - 5, oy + 5); c.lineTo(ox + 5, oy + 5); c.closePath(); c.fill();
  let y = 18;
  c.font = "15px Barlow, sans-serif";
  for (const [label, ch] of [["no-go: steeper than 15°", 3], ["crater floor: avoided", 2], ["judged safe", 1]]) {
    const [R_, G_, B_] = HAZ_RGB[ch];
    c.fillStyle = `rgb(${R_},${G_},${B_})`; c.fillRect(6, y - 11, 11, 11);
    c.fillStyle = "#e6e6e6"; c.fillText(label, 22, y); y += 20;
  }
}

function lidarFp(c, W, H, r, g, a) {
  const pp = pipePose(a, r);
  if (!pp) return;
  const [x, y, h] = pp, cs = Math.cos(h), sn = Math.sin(h);
  const ox = x + LIDAR.right * sn, oy = y - LIDAR.right * cs;   // right of the camera
  const oz = terrain.heightAt(x, y) + FP.camH;
  const img = c.createImageData(LIDAR_FP_COLS, LIDAR_FP_ROWS);
  for (let row = 0; row < LIDAR_FP_ROWS; row++) {
    const el = THREE.MathUtils.degToRad(LIDAR.elHi - (row + 0.5) * (LIDAR.elHi - LIDAR.elLo) / LIDAR_FP_ROWS);
    for (let col = 0; col < LIDAR_FP_COLS; col++) {
      const az = THREE.MathUtils.degToRad(LIDAR.hfov / 2 - (col + 0.5) * LIDAR.hfov / LIDAR_FP_COLS);   // left of the rover on the left
      const ce = Math.cos(el), dx = ce * Math.cos(az), dy = ce * Math.sin(az), dz = Math.sin(el);
      let hit = null;
      for (let s = 1; s <= LIDAR.maxR; s += LIDAR_FP_STEP_M) {
        const f = s * dx, l = s * dy;
        const wx = ox + f * cs - l * sn, wy = oy + f * sn + l * cs;
        if (oz + s * dz <= terrain.heightAt(wx, wy)) { hit = [s, f, l]; break; }
      }
      const o = 4 * (row * LIDAR_FP_COLS + col);
      img.data[o + 3] = 255;
      if (!hit) continue;
      const [rng, f, l] = hit;
      const shade = 235 - 190 * Math.min(1, rng / LIDAR.maxR);
      let rgb = [shade * 0.8, shade * 0.88, shade];
      const i = Math.floor((f - g.fwd0_m) / g.cell_m), j = Math.floor((l + g.half_width_m) / g.cell_m);
      if (i >= 0 && i < g.nx && j >= 0 && j < g.ny) {
        const ch = r.lidar.charCodeAt(i * g.ny + j) - 48;
        if (ch >= 2) rgb = HAZ_RGB[ch];
      }
      img.data[o] = rgb[0]; img.data[o + 1] = rgb[1]; img.data[o + 2] = rgb[2];
    }
  }
  const tmp = document.createElement("canvas");
  tmp.width = LIDAR_FP_COLS; tmp.height = LIDAR_FP_ROWS;
  tmp.getContext("2d").putImageData(img, 0, 0);
  const L = 40, Rm = 40, T = 8, B = 34;
  c.imageSmoothingEnabled = false;
  c.drawImage(tmp, L, T, W - L - Rm, H - T - B);
  c.strokeStyle = "rgb(150,150,150)"; c.lineWidth = 2; c.strokeRect(L - 1, T - 1, W - L - Rm + 2, H - T - B + 2);
  c.fillStyle = "#e6e6e6"; c.font = "18px Barlow, sans-serif"; c.textAlign = "center";
  for (const [b, lab] of [[60, "60L"], [0, "ahead"], [-60, "60R"]]) c.fillText(lab, L + ((LIDAR.hfov / 2 - b) / LIDAR.hfov) * (W - L - Rm), H - 10);
  c.textAlign = "left";
  c.fillText(`${LIDAR.elHi}°`, 4, T + 16); c.fillText(`${LIDAR.elLo}°`, 4, H - B);
  c.font = "13px Barlow, sans-serif"; c.fillStyle = "rgba(255,255,255,.65)";
  c.fillText("drawn from the 3D ground; hazards from the logged scan", L + 6, T + 18);
}

// -------------------------------------------------------- satellite map
// MapView (hillshade, tracks, estimates, particles, finds, plan) with the
// campaign's own layers under the tracks: ground beyond the drive area, the
// dock's crater map as it stood at time t (added craters marked), the hidden
// truth behind its toggle, the dock and the search areas.
class MissionMap extends MapView {
  drawUnder(c, X, Y, s, W, H) {
    if (!S.cr) return;
    const rec = S.cr.rec, t = this.t || 0;
    const inView = (x, y, r) => X(x) + r * s > -20 && X(x) - r * s < W + 20 && Y(y) + r * s > -20 && Y(y) - r * s < H + 20;
    if (S.opts.craters) {
      c.lineWidth = 1;
      c.strokeStyle = "rgba(199, 210, 223, .45)";
      c.beginPath();
      for (const [x, y, d] of rec.crater_map.start) {
        const r = d / 2;
        if (r * s < 0.8 || !inView(x, y, r)) continue;
        c.moveTo(X(x) + r * s, Y(y));
        c.arc(X(x), Y(y), r * s, 0, 2 * Math.PI);
      }
      c.stroke();
      // Added craters known by t; thicker for the dock's most recent merge.
      const known = (rec.crater_map.added || []).filter((a) => (addedT.get(a.crater_id) ?? Infinity) <= t);
      const lastT = known.reduce((m, a) => Math.max(m, addedT.get(a.crater_id)), -Infinity);
      c.strokeStyle = "#7ddc7a";
      for (const a of known) {
        if (!inView(a.x, a.y, a.d / 2)) continue;
        c.lineWidth = addedT.get(a.crater_id) === lastT ? 2.4 : 1.3;
        c.beginPath(); c.arc(X(a.x), Y(a.y), Math.max(2.5, (a.d / 2) * s), 0, 2 * Math.PI); c.stroke();
      }
      if (S.opts.truth) {
        // Craters missing from the map that no correctly placed added crater stands for.
        const found = new Set(known.filter((a) => !a.misplaced && a.true_crater != null).map((a) => a.true_crater));
        c.setLineDash([3, 3]);
        c.strokeStyle = "rgba(255, 122, 107, .6)";
        c.lineWidth = 1;
        c.beginPath();
        rec.truth.craters_missing.forEach(([x, y, d], i) => {
          const r = d / 2;
          if (found.has(i) || r * s < 0.8 || !inView(x, y, r)) return;
          c.moveTo(X(x) + r * s, Y(y));
          c.arc(X(x), Y(y), r * s, 0, 2 * Math.PI);
        });
        c.stroke();
        c.setLineDash([]);
        // Added in the wrong place: a cross over it and a line to the real crater.
        c.strokeStyle = "#ff9b5e";
        c.lineWidth = 1.5;
        for (const a of known) {
          if (!a.misplaced || !inView(a.x, a.y, a.d)) continue;
          const px = X(a.x), py = Y(a.y), q = Math.max(3, (a.d / 2) * s * 0.7);
          c.beginPath(); c.moveTo(px - q, py - q); c.lineTo(px + q, py + q); c.moveTo(px + q, py - q); c.lineTo(px - q, py + q);
          const tc = a.true_crater != null && rec.truth.craters_missing[a.true_crater];
          if (tc) { c.moveTo(px, py); c.lineTo(X(tc[0]), Y(tc[1])); }
          c.stroke();
        }
      }
    }
    // Search areas while a search runs.
    c.setLineDash([5, 4]);
    for (const m of marks) {
      if (t < m.start.t || t > (m.end ? m.end.t : m.start.t)) continue;
      c.strokeStyle = scol(m.start.scout); c.lineWidth = 2;
      c.beginPath(); c.arc(X(m.find.est[0]), Y(m.find.est[1]), Math.max(4, m.start.radius_m * s), 0, 2 * Math.PI); c.stroke();
    }
    c.setLineDash([]);
    const d = rec.dock, DX = X(d.x), DY = Y(d.y);
    c.fillStyle = "#fff"; c.strokeStyle = "#000"; c.lineWidth = 2;
    c.beginPath(); c.rect(DX - 5, DY - 5, 10, 10); c.fill(); c.stroke();
    c.font = "600 11px Barlow, sans-serif"; c.fillText("Dock", DX + 8, DY + 4);
  }
}

function hillshade(g) {
  const { rows, cols, cell, z } = g;
  const cv = document.createElement("canvas");
  cv.width = cols; cv.height = rows;
  const im = cv.getContext("2d").createImageData(cols, rows);
  const az = (315 * Math.PI) / 180, el = (35 * Math.PI) / 180;
  const lx = Math.cos(el) * Math.sin(az), ly = Math.cos(el) * Math.cos(az), lz = Math.sin(el);
  for (let i = 0; i < rows; i++) for (let j = 0; j < cols; j++) {
    const jl = Math.max(0, j - 1), jr = Math.min(cols - 1, j + 1), id = Math.max(0, i - 1), iu = Math.min(rows - 1, i + 1);
    const dzdx = (z[i * cols + jr] - z[i * cols + jl]) / ((jr - jl) * cell);
    const dzdy = (z[iu * cols + j] - z[id * cols + j]) / ((iu - id) * cell);
    const L = Math.max(0, (-dzdx * lx - dzdy * ly + lz) / Math.hypot(dzdx, dzdy, 1));
    const v = 30 + 150 * L;
    const p = 4 * ((rows - 1 - i) * cols + j);   // rows run north; image row 0 is the north edge
    im.data[p] = v * 0.96; im.data[p + 1] = v * 0.98; im.data[p + 2] = v; im.data[p + 3] = 255;
  }
  cv.getContext("2d").putImageData(im, 0, 0);
  return { img: cv, x0: g.x0 - cell / 2, y0: g.y0 - cell / 2, x1: g.x0 + (cols - 0.5) * cell, y1: g.y0 + (rows - 0.5) * cell };
}

// ------------------------------------------------------------- timeline
// Over the play bar: the followed Scout's activity, in order, and ticks for
// the moments that matter (finds, searches, drilling, position checks).
function timeline() {
  timelineActs();
  const run = S.run, T0 = run.times[0], span = run.tEnd - T0 || 1;
  const finder = (id) => (S.cr.iceEvents.find((x) => x.type === "find" && x.find_id === id) || {}).scout || "?";
  const out = [];
  for (const e of S.cr.events) {
    if (e.t < T0 || e.t > run.tEnd || !run.agencies.includes(e.scout)) continue;
    const at = `left:${(100 * (e.t - T0)) / span}%`;
    if (e.type === "find") out.push(`<i class="ice" style="${at};background:${STATUS_COLOR.find}" title="${esc(`${fmtDay(e.t)}: ${e.scout} logs find ${e.find_id}`)}"></i>`);
    else if (e.type === "search_end") out.push(`<i class="ice" style="${at};background:${e.result === "confirmed" ? STATUS_COLOR.confirmed : STATUS_COLOR.unconfirmed}" title="${esc(`${fmtDay(e.t)}: ${e.scout}, ${finder(e.find_id)}'s find ${e.find_id} ${e.result}`)}"></i>`);
    else if (e.type === "drill") out.push(`<i class="ice" style="${at};background:${STATUS_COLOR.drilled}" title="${esc(`${fmtDay(e.t)}: ${e.scout} drills find ${e.find_id}`)}"></i>`);
    else if (e.type === "check" && e.scout === S.sortie.scout) out.push(`<i class="chk${e.wrong_match ? " wrong" : ""}" style="${at}" title="${esc(`${fmtDay(e.t)}: ${e.scout} position check${e.fixed === false ? ", no fix" : ""}`)}"></i>`);
    else if (e.type === "fault" || e.type === "turn_back") out.push(`<i class="haz" style="${at}" title="${esc(`${fmtDay(e.t)}: ${e.scout} ${e.type === "fault" ? e.module + " fault" : "turns back (" + e.reason + ")"}`)}"></i>`);
  }
  $("tl-marks").innerHTML = out.join("");
}

const ACT_COLOR = ["#4a6a8f", "#6f8fb3", "#4a6a8f", "#e0b84a", "#4fbf72", "#58c8ff", "#b58cff", "#3b4048", "#e5574f"];
function timelineActs() {
  const run = S.run, a = S.rover, T0 = run.times[0], span = run.tEnd - T0 || 1;
  let html = "";
  for (let k = 0; k < run.steps.length - 1; k++) {
    const w = (100 * (run.times[k + 1] - run.times[k])) / span, act = run.steps[k + 1].info[a].act;
    if (w > 0) html += `<i style="width:${w}%;background:${ACT_COLOR[act] || "#333"}" title="${a}: ${ACTIVITY[act]}"></i>`;
  }
  $("tl-acts").innerHTML = html;
}

// --------------------------------------------------------- sortie list
// Every sortie of the run: a lane per Scout across the campaign, and a list.
function sortieList() {
  const rec = S.cr.rec, L = rec.sorties, Tend = Math.max(...L.map((s) => s.t_end)) || 1;
  $("so-count").textContent = `${L.length}, ${rec.scouts.length} Scout${rec.scouts.length > 1 ? "s" : ""}`;
  const OUT_COL = { below_reserve: "#d9b44a", fault_return: "#8a9099", lost: "#e5574f" };
  const finds = (sid) => S.cr.iceEvents.filter((e) => e.type === "find" && e.sortie_id === sid).length;
  $("gantt").innerHTML = rec.scouts.map((a) => `<div class="lane"><span class="ln">${a}</span><div class="bars">${L.filter((s) => s.scout === a).map((s) =>
    `<b data-s="${s.sortie_id}" style="left:${(100 * s.t_start) / Tend}%;width:${Math.max(0.6, (100 * (s.t_end - s.t_start)) / Tend)}%;background:${OUT_COL[s.outcome] || scol(a)}" title="${esc(`${s.sortie_id}: ${OUTCOME_WORD[s.outcome] || s.outcome}`)}"></b>`).join("")}</div></div>`).join("") +
    `<div class="lane axis"><span class="ln"></span><div class="bars"><i id="gantt-now"></i><span>day 1</span><span style="right:0">day ${Math.ceil(Tend / 86400)}</span></div></div>`;
  $("so-list").innerHTML = L.map((s) => {
    const n = finds(s.sortie_id);
    const outc = s.outcome === "ok" ? "" : `<span class="out ${s.outcome}">${esc(OUTCOME_WORD[s.outcome] || s.outcome)}</span>`;
    return `<li data-s="${s.sortie_id}"><i class="dot" style="background:${scol(s.scout)}"></i><b>${esc(s.sortie_id)}</b>
      <span class="hint">Scout ${esc(s.scout)}, sortie ${s.n} · ${fmtDay(s.t_start)} · ${fmtNum(s.distance_m / 1000, 1)} km · ${s.checks} checks${n ? ` · ${n} find${n > 1 ? "s" : ""}` : ""}</span>${outc}</li>`;
  }).join("");
  $("sortie-pick").innerHTML = L.map((s) => `<option value="${s.sortie_id}">${s.sortie_id}</option>`).join("");
  for (const el of document.querySelectorAll("#gantt b[data-s], #so-list li[data-s]")) el.onclick = () => openSortie(S.cr.sortieById[el.dataset.s]);
}

function markSortie() {
  const id = S.sortie.sortie_id;
  $("sortie-pick").value = id;
  for (const el of document.querySelectorAll("#gantt b[data-s], #so-list li[data-s]")) el.classList.toggle("cur", el.dataset.s === id);
  const li = document.querySelector(`#so-list li[data-s="${id}"]`);
  if (li && !S.tab.startsWith("x")) li.scrollIntoView({ block: "nearest" });
}

// ------------------------------------------------------------- panels
let panelKey = {};
function once(id, key, html) {
  if (panelKey[id] === key) return;
  panelKey[id] = key;
  $(id).innerHTML = typeof html === "function" ? html() : html;
}

function renderPanels(t) {
  const run = S.run, k = run.stepAt(t);
  renderStory(t, k);
  renderToast(t, k);
  const g = $("gantt-now");
  if (g) g.style.left = `${(100 * t) / (Math.max(...S.cr.rec.sorties.map((s) => s.t_end)) || 1)}%`;
  if (S.tab === "scouts") renderScouts(t, k);
  if (S.tab === "finds") renderFinds(t);
  if (S.tab === "map") renderCraterMap(t);
}

function activityOf(a, k) {
  const i = S.run.steps[Math.min(k + 1, S.run.steps.length - 1)].info[a];
  return i.act === DOCKED ? (i.sortie ? "docked" : "at the dock") : ACTIVITY[i.act];
}

function renderScouts(t, k) {
  const run = S.run, cap = S.cr.rec.settings.battery_kwh;
  once("now", `${k}|${S.rover}|${S.opts.truth}`, () => run.agencies.map((a) => {
    const r = run.rover(a, k), info = run.steps[Math.min(k + 1, run.steps.length - 1)].info[a];
    const docked = info.act === DOCKED;
    const tp = run.poses.truth[a][k];
    const err = Math.hypot(r.estimate[0] - tp[0], r.estimate[1] - tp[1]);
    const so = info.sortie;
    const e = info.energy, soc = e != null && cap ? e / cap : null;
    const d = S.cr.rec.dock, ex = r.estimate[0] - d.x, ey = r.estimate[1] - d.y;
    return `<div class="rv ${a === S.rover ? "sel" : ""}" data-rover="${a}">
      <div class="rv-head"><i class="dot" style="background:${scol(a)}"></i>Scout ${a}<span class="doing">${esc(activityOf(a, k))}</span></div>
      <div class="rv-obj">${so ? `Sortie ${esc(so.sortie_id)}, launched ${fmtDay(so.t_start)}` : "Charging or waiting at the dock"}</div>
      ${docked ? "" : `<div class="stats">
        <div class="stat"><b>${fmtNum(e, 2)}</b><span>kWh left</span>${soc == null ? "" : `<div class="batt"><i style="width:${Math.max(0, Math.min(100, 100 * soc))}%;background:${soc < S.cr.rec.settings.reserve_frac + 0.05 ? "var(--neg)" : "var(--pos)"}"></i></div>`}</div>
        <div class="stat"><b>±${fmtNum(r.sigma, 1)}</b><span>m bound, 95%</span></div>
        <div class="stat truth"><b>${fmtNum(err, 1)}</b><span>m true error</span></div>
      </div>
      <div class="rv-pos">Thinks it is ${fmtNum(Math.abs(ex), 0)} m ${ex >= 0 ? "east" : "west"}, ${fmtNum(Math.abs(ey), 0)} m ${ey >= 0 ? "north" : "south"} of the dock; really ${fmtNum(err, 1)} m from there.</div>`}
    </div>`;
  }).join(""));
  $("now").querySelectorAll(".rv").forEach((el) => (el.onclick = () => setRover(el.dataset.rover)));
  once("navt", `${k}`, () => {
    const rows = run.agencies.map((a) => {
      const r = run.rover(a, k), tp = run.poses.truth[a][k];
      const docked = activityOf(a, k).includes("dock");
      const err = Math.hypot(r.estimate[0] - tp[0], r.estimate[1] - tp[1]);
      const by = docked ? "docked" : !run.hasPipeline ? "not logged" : r.rims.length ? `${r.rims.length} rim points` : "odometry";
      return `<tr><td><i class="dot" style="background:${scol(a)}"></i>${a}</td><td>${by}</td><td>${docked ? "–" : `±${fmtNum(r.sigma, 1)} m`}</td><td class="truth">${docked ? "–" : `${fmtNum(err, 1)} m`}</td></tr>`;
    }).join("");
    return `<table class="navt"><thead><tr><th></th><th title="What the pipeline logged at this step">Steering by</th><th title="The Scout's own 95% position bound">Thinks within</th>` +
      `<th class="truth" title="Distance from the estimate to the true position">Actually off</th></tr></thead><tbody>${rows}</tbody></table>` +
      `<div class="hud-src">${esc(navLine())}</div>`;
  });
}

function navLine() {
  const nav = String(S.cr.rec.nav || "");
  if (nav.startsWith("sampled")) return "Navigation in this record: sampled from ShadowNav's published errors, not the camera pipeline. Its rim points, particles and lidar come from the stand-in pipeline file.";
  if (nav.startsWith("camera")) return "Navigation in this record: the camera pipeline (rims from vision, particle filter).";
  return nav ? `Navigation: ${nav}` : "";
}

function renderFinds(t) {
  const cr = S.cr;
  const evs = cr.iceEvents.filter((e) => e.t <= t);
  const finds = cr.findsFrom(evs);
  $("finds-count").textContent = finds.length ? String(finds.length) : "";
  once("finds", `${evs.length}|${S.opts.truth}`, () => finds.length ? finds.slice().reverse().map((f) => {
    const starts = evs.filter((e) => e.type === "search_start" && e.find_id === f.find_id);
    const rows = starts.map((s) => {
      const end = f.searches.find((e) => e.sortie_id === s.sortie_id);
      return `<li><i class="dot" style="background:${scol(s.scout)}"></i>${esc(s.scout)} searched within ${fmtNum(s.radius_m, 1)} m on ${esc(s.sortie_id)}: ${end ? `<span class="res ${end.result}">${end.result === "confirmed" ? "confirmed" : "not confirmed"}</span> <span class="hint">${esc(SEARCH_WHY[end.reason] || "")}</span>` : `<span class="hint">searching now</span>`}` +
        (S.opts.truth && end ? ` <span class="truth">${end.truth_has_deposit ? "a deposit is there" : "no deposit there"}${end.false_confirmed ? ", a false confirmation" : ""}</span>` : "") + `</li>`;
    });
    if (!starts.length) rows.push(`<li class="hint">No second Scout has searched it yet.</li>`);
    if (f.drill) rows.push(`<li><i class="dot" style="background:${scol(f.drill.scout)}"></i>${esc(f.drill.scout)} drilled it on ${esc(f.drill.sortie_id)}` +
      (S.opts.truth ? ` <span class="truth">${fmtNum(f.drill.miss_m, 1)} m from the reading point, ${f.drill.hit ? "on a deposit" : "no deposit under the drill"}</span>` : "") + `</li>`);
    const off = S.opts.truth && f.x != null ? ` <span class="truth">really ${fmtNum(Math.hypot(f.est[0] - f.x, f.est[1] - f.y), 1)} m off</span>` : "";
    return `<div class="find" data-s="${esc(f.sortie_id)}" data-t="${f.t}"><div class="fh"><b>Find ${f.find_id}</b> <span class="hint">by ${esc(f.scout)} on ${esc(f.sortie_id)}, ${fmtDay(f.t)}</span>
      <span class="st" style="color:${STATUS_COLOR[f.status]}">${esc(STATUS_WORD[f.status] || f.status)}</span></div>
      <div class="small">${fmtNum(f.reading_wt, 1)} wt% · within ${fmtNum(f.bound95, 1)} m (95%)${off}</div><ul class="fl">${rows.join("")}</ul></div>`;
  }).join("") : `<p class="hint">No finds yet. A reading above the ice threshold is logged as a find; another Scout later searches round it.</p>`);
  $("finds").querySelectorAll(".find").forEach((el) => (el.onclick = () => {
    const s = S.cr.sortieById[el.dataset.s], tt = Number(el.dataset.t) - 60;
    if (s === S.sortie) { S.player.rewound(tt); S.t = Math.max(S.run.times[0], tt); render(true); } else openSortie(s, { t: tt });
  }));
}

function renderCraterMap(t) {
  const rec = S.cr.rec, added = rec.crater_map.added || [];
  const known = added.filter((a) => (addedT.get(a.crater_id) ?? Infinity) <= t);
  once("cmap", `${known.length}|${S.opts.truth}`, () => {
    const by = new Map();
    for (const a of known) by.set(a.after_sortie, (by.get(a.after_sortie) || 0) + 1);
    const merges = [...by.entries()].slice(-8).reverse().map(([sid, n]) => `<li>after ${esc(sid)}: <b>+${n}</b></li>`).join("");
    const mis = known.filter((a) => a.misplaced).length;
    return `<div class="stats wide"><div class="stat"><b>${rec.crater_map.start.length}</b><span>from orbit</span></div>
      <div class="stat"><b style="color:#7ddc7a">+${known.length}</b><span>added so far</span></div>
      <div class="stat"><b>${added.length}</b><span>added by the end</span></div></div>
      <p class="small hint">The dock merges what every Scout saw after each sortie: a crater seen ${rec.settings.sightings_to_add || 2} times or more, not on the map, joins it. Green on the map and on the ground; thicker for the latest merge.${rec.crater_map.scope ? ` Listed: ${esc(rec.crater_map.scope)}.` : ""}</p>
      ${merges ? `<ol class="merges">${merges}</ol>` : ""}
      ${S.opts.truth ? `<p class="small truth">Hidden truth: ${mis} of the added craters are misplaced (still effectively missing); red dashed circles on the map are true craters the map lacks.</p>` : ""}`;
  });
}

function runInfo() {
  const rec = S.cr.rec, sm = rec.summary || {}, st = rec.settings || {};
  $("run-title").textContent = rec.run_id;
  document.title = `Lantern Mission Control · ${rec.run_id}`;
  const kv = (rows) => `<dl class="kv">${rows.filter(([, v]) => v != null && v !== "–").map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
  const notes = [standinOf(rec), rec.showcase].filter(Boolean);
  $("run-info").innerHTML = `<p class="small hint">Batch ${esc(S.batch)} · seed ${rec.seed} · made ${esc(String(rec.made_at || "").slice(0, 16).replace("T", " "))}</p>` +
    `<div class="mini-h">Setup</div>` + kv([["Site", esc(rec.terrain && rec.terrain.site)], ["Scouts", st.n_scouts], ["Sorties per Scout", st.sorties_per_scout],
      ["Check policy", esc(st.check_policy)], ["Map refinement", st.map_refinement == null ? null : st.map_refinement ? "on" : "off"],
      ["Top speed", st.top_speed_mps ? `${fmtNum(st.top_speed_mps * 100, 0)} cm/s` : null], ["Battery", st.battery_kwh ? `${st.battery_kwh} kWh` : null],
      ["Navigation", esc(String(rec.nav || "").split(":")[0])]]) +
    `<div class="mini-h">Results</div>` + kv([["Sorties ok", sm.sorties != null ? `${sm.sorties_ok}/${sm.sorties}` : null], ["Scouts lost", sm.scouts_lost],
      ["Finds", sm.finds], ["Confirmed", sm.confirmed], ["False confirmations", sm.false_confirmed], ["Deposits missed", sm.deposits_missed],
      ["Wrong-match rate", sm.wrong_match_rate != null ? `${fmtNum(sm.wrong_match_rate * 100, 1)}%` : null],
      ["Drill error p95", sm.drill_err_p95_m != null ? `${fmtNum(sm.drill_err_p95_m, 1)} m` : null], ["Area", sm.area_km2 != null ? `${fmtNum(sm.area_km2, 2)} km²` : null]]) +
    (notes.length ? `<div class="standin"><b>Not real yet:</b> ${notes.map(esc).join(" ")}</div>` : "") +
    `<p class="small"><a href="run.html?batch=${encodeURIComponent(S.batch)}&run=${encodeURIComponent(rec.run_id)}">Run map, sortie by sortie</a> · <a href="runs.html?batch=${encodeURIComponent(S.batch)}">All runs, as a table</a></p>`;
}

// One sentence on what is happening now, for someone who just walked up.
function renderStory(t, k) {
  const run = S.run, so = S.sortie, a = so.scout;
  const act = activityOf(a, k);
  const live = marks.filter((m) => t >= m.start.t && t <= (m.end ? m.end.t : m.start.t));
  let html = `Sortie <b>${esc(so.sortie_id)}</b>: Scout ${esc(a)} is ${esc(act === "docked" ? "back at the dock" : act)}.`;
  for (const m of live) html += ` ${esc(m.start.scout)} searches ${esc(m.find.scout)}'s find ${m.find.find_id}.`;
  const known = (S.cr.rec.crater_map.added || []).filter((c) => (addedT.get(c.crater_id) ?? Infinity) <= t).length;
  html += ` Dock map: ${S.cr.rec.crater_map.start.length} + <b>${known}</b> craters.`;
  if ($("story").dataset.html !== html) { $("story").innerHTML = html; $("story").dataset.html = html; }
  // Stand-in banner: what is made up in what is on screen.
  const bits = [];
  if (S.cr.rec.standin) bits.push("stand-in record");
  if (run.pipelineStandin) bits.push("stand-in pipeline data");
  if (!run.hasPipeline) bits.push("no pipeline data: no rims, particles or lidar");
  const b = bits.length ? `<b>Labelled:</b> ${bits.join(" · ")}` : "";
  if ($("banner").dataset.html !== b) { $("banner").innerHTML = b; $("banner").dataset.html = b; $("banner").hidden = !b; $("banner").title = [standinOf(S.cr.rec), run.pipelineNote].filter(Boolean).join("\n\n"); }
}

let toastUntil = 0;
function showToast(html) { $("toast").innerHTML = html; $("toast").hidden = false; toastUntil = performance.now() + 1200; }
function renderToast(t, k) {
  if (performance.now() < toastUntil) return;
  const a = S.rover, el = $("toast");
  const html = t >= S.run.tEnd && !S.playing ? `<b>Sortie ${esc(S.sortie.sortie_id)} over</b> · ${esc(OUTCOME_WORD[S.sortie.outcome] || S.sortie.outcome)}`
    : !S.playing && t === S.run.times[0] ? `Press <b>play</b> to start · drag to look around`
    : `<i class="dot" style="background:${scol(a)}"></i> <b>Scout ${esc(a)}</b> · ${esc(activityOf(a, k))}<span class="sp">${S.playing ? S.speed + "×" : "Paused"}</span>`;
  if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
  el.hidden = false;
}

// ------------------------------------------------------------- runs list
// Every run with its setup and results; clicking one loads it here.
function openRuns() {
  $("drawer").hidden = false;
  $("dr-title").textContent = "Runs";
  const batches = S.index.filter((b) => b.runs.some((r) => r.map));
  const cur = batches.find((b) => b.batch_id === S.batch) || batches[0];
  $("dr-body").innerHTML = `<label class="small">Batch <select id="dr-batch">${batches.map((b) => `<option value="${esc(b.batch_id)}"${b === cur ? " selected" : ""}>${esc(b.batch_id)} (${b.runs.filter((r) => r.map).length})</option>`).join("")}</select></label>
    <div id="dr-about" class="small hint"></div><div class="runs-wrap"><table class="runs"><thead><tr><th>Run</th><th>Site</th><th title="Scouts">Sc.</th><th title="Sorties ok of all">Sorties</th><th>Finds</th><th title="Confirmed by a second Scout">Conf.</th><th title="Wrong-match rate">Wrong</th><th title="Per-step pipeline data for the first-person, lidar and particle views">Views</th></tr></thead><tbody id="dr-runs"></tbody></table></div>
    <p class="small"><a href="runs.html">Every column, as a table</a></p>`;
  const fill = (b) => {
    $("dr-about").textContent = [b.standin, b.showcase].filter(Boolean).join(" ").slice(0, 400);
    $("dr-runs").innerHTML = b.runs.filter((r) => r.map).slice(0, 400).map((r) => `<tr data-b="${esc(b.batch_id)}" data-r="${esc(r.run_id)}" class="${b.batch_id === S.batch && r.run_id === S.cr.rec.run_id ? "cur" : ""}">
      <td class="mono">${esc(r.run_id)}</td><td>${esc(r.site || "")}</td><td>${r.n_scouts ?? "–"}</td><td>${r.sorties != null ? `${r.sorties_ok}/${r.sorties}` : "–"}</td>
      <td>${r.finds ?? "–"}</td><td>${r.confirmed ?? "–"}</td><td>${r.wrong_match_rate != null ? fmtNum(r.wrong_match_rate * 100, 0) + "%" : "–"}</td>
      <td>${r.pipeline ? `<span class="tag">${esc(r.pipeline)}</span>` : `<span class="hint">3D, map</span>`}</td></tr>`).join("");
    $("dr-runs").querySelectorAll("tr").forEach((tr) => (tr.onclick = () => { location.search = `?batch=${encodeURIComponent(tr.dataset.b)}&run=${encodeURIComponent(tr.dataset.r)}`; }));
  };
  fill(cur);
  $("dr-batch").onchange = (e) => fill(batches.find((b) => b.batch_id === e.target.value));
}

main();
