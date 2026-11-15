// Lantern viewer: replays run logs (docs/handoffs.md, "Run log") on one timeline.
import { Terrain, Run, loadIndex, fmtClock } from "./data.js";
import { World, COLORS } from "./world.js";
import { MapView } from "./map.js";

const $ = (id) => document.getElementById(id);
const hex = (c) => "#" + c.toString(16).padStart(6, "0");
const short = (h) => (h ? h.slice(0, 10) + "…" + h.slice(-6) : "");

// First-person camera, as in sim/scene.py camera_model(): 640x480, 60 deg
// horizontal field of view, pitched 15 deg down, 2.5 m up, level.
const FP = { w: 640, h: 480, hfov: 60, pitch: 15, camH: 2.5 };
FP.fx = FP.w / 2 / Math.tan((FP.hfov / 2) * Math.PI / 180);

// Plain names for the log's stand-in keys; others print with "_" as a space.
const PART_NAMES = { nav: "navigation", rims: "rim detection", planner: "route planner", chain: "chain", ice_sensor: "ice sensor" };

const state = {
  runs: [], run: null, t: 0, playing: false, speed: 200, cam: "A",
  opts: { fill: true, tracks: true, particles: true, rims: true, nogo: false, truth: false },
  lastEventsKey: "",
};

let terrain, world, map;

async function main() {
  const params = new URLSearchParams(location.search);
  try {
    terrain = await Terrain.load("assets/terrain/");
  } catch (e) {
    $("loading").textContent = "Terrain assets missing. Run python web/build_assets.py.";
    throw e;
  }
  // ?quality=low: half-resolution drive-area mesh and smaller shadow maps.
  world = new World($("viewport"), terrain, params.get("quality") || "high");
  world.buildTerrain();
  world.buildBackdrop();
  world.loadRocks();
  world.initPatches();
  map = new MapView($("map"), terrain);

  state.runs = await loadIndex("runs/");
  const list = $("run-list");
  state.runs.forEach((r) => {
    const b = document.createElement("button");
    b.className = "nav";
    b.dataset.run = r.run_id;
    const [head, ...rest] = r.label.split(":");
    const st = r.status ? ` <span class="st ${r.status}">${r.status}</span>` : "";
    b.innerHTML = `${head}<span class="sub">${rest.join(":").trim() || "&nbsp;"}${st}</span>`;
    b.onclick = () => openRun(r.run_id, 0);
    list.appendChild(b);
  });

  bindControls();
  const want = params.get("run") || state.runs[0].run_id;
  await openRun(want, Number(params.get("t")) || 0);
  $("loading").hidden = true;
  // Someone opening the link cold sees it move; a deep link (?t=) holds still.
  if (!params.has("t") && !params.has("paused")) togglePlay();
  requestAnimationFrame(loop);
}

async function openRun(id, t) {
  const entry = state.runs.find((r) => r.run_id === id) || state.runs[0];
  state.playing = false;
  $("loading").hidden = false;
  $("loading").textContent = "Loading run";
  const run = await Run.load("runs/", entry);
  // Fine tiles under every track first, so lines drape on the real ground.
  const pts = run.agencies.flatMap((a) => [...run.tracks[a].truth, ...run.tracks[a].est, ...run.tracks[a].odom]);
  const keys = [...terrain.keysNear(pts.filter((_, i) => i % 3 === 0), 4)];
  let done = 0;
  await Promise.all(keys.map((k) => terrain.loadTile(k).then(() => {
    $("loading").textContent = `Loading ground under the tracks, ${++done} of ${keys.length}`;
  })));
  state.run = run;
  state.t = Math.min(t, run.tEnd);
  state.lastEventsKey = "";
  document.querySelectorAll("#run-list .nav").forEach((b) => b.setAttribute("aria-current", String(b.dataset.run === run.log.run_id)));
  $("run-title").textContent = run.log.label;
  document.title = `Lantern · ${run.log.label}`;

  const si = run.standin;
  $("standin").hidden = !si;
  if (si) {
    // Say what is not real yet, in the log's own words.
    const lines = Object.entries(si).map(([k, v]) => (String(v).startsWith(k) ? v : `${k}: ${v}`));
    const parts = Object.keys(si).map((k) => PART_NAMES[k] || k.replace(/_/g, " ")).join(", ");
    // Say the chain is fake only when it is (run.fakeTx: a chain stand-in, or no explorer in the log).
    const chainLine = run.fakeTx ? " Nothing is posted to Monad yet; the transaction hashes are fake." : " The chain is real: every hash links to Monad testnet.";
    $("standin-text").innerHTML = `Stand-ins: ${parts}.${chainLine} Camera frames are real renders. ` +
      `<details><summary>Details</summary><ul>${lines.map((l) => `<li>${l}</li>`).join("")}</ul></details>`;
  }

  const ch = run.chain || {};
  $("chain-hint").textContent = ch.contract && /^0x/.test(ch.contract) ? `contract ${short(ch.contract)}` : ch.contract || (si ? "not posted" : "");

  const ticks = $("ticks");
  ticks.innerHTML = "";
  for (const e of run.events) {
    const d = document.createElement("div");
    d.className = "tick";
    d.style.left = `${(100 * e.t) / run.tEnd}%`;
    d.style.background = hex(COLORS[e.agency]);
    d.title = `${e.type} by ${e.agency}`;
    ticks.appendChild(d);
  }
  $("time").max = run.tEnd;
  world.currentT = state.t;
  world.setRun(run);
  speedNote();
  world.setVisibility(state.opts);
  map.setRun(run);
  $("loading").hidden = true;
  reserveHeights(run);
  render(true);
}

function bindControls() {
  $("play").onclick = togglePlay;
  $("back").onclick = () => stepBy(-1);
  $("fwd").onclick = () => stepBy(1);
  $("speed").onchange = (e) => { state.speed = Number(e.target.value); speedNote(); };
  $("time").oninput = (e) => { state.t = Number(e.target.value); render(); };
  document.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => setView(b.dataset.view)));
  setView("A");
  document.querySelectorAll("#cam-seg button").forEach((b) => (b.onclick = () => {
    state.cam = b.dataset.ag;
    document.querySelectorAll("#cam-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render(true);
  }));
  document.querySelectorAll("#map-seg button").forEach((b) => (b.onclick = () => {
    map.setMode(b.dataset.map);
    document.querySelectorAll("#map-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render(true);
  }));
  for (const k of Object.keys(state.opts)) {
    if (!$("opt-" + k)) continue;   // a toggle the page does not offer keeps its default
    state.opts[k] = $("opt-" + k).checked;   // the markup's checked state is the default
    $("opt-" + k).onchange = (e) => {
      state.opts[k] = e.target.checked;
      world.setVisibility(state.opts);
      map.opts = state.opts;
      $("vnote").hidden = !state.opts.fill;
      if (state.run) reserveHeights(state.run);
      render(true);
    };
  }
  map.opts = state.opts;
  // Live boxes are sized for the page as laid out: measure again when the
  // width changes or the web fonts arrive.
  let resizeT = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(() => state.run && reserveHeights(state.run), 150);
  });
  if (document.fonts) document.fonts.ready.then(() => state.run && reserveHeights(state.run));
  // Click the camera frame to enlarge it (the page styles .fp.big); a second
  // click or Esc closes it. The rim canvas is redrawn at the new size.
  const fp = document.querySelector(".fp");
  const setBig = (on) => { fp.classList.toggle("big", on); render(true); };
  if (fp) fp.addEventListener("click", () => setBig(!fp.classList.contains("big")));
  window.addEventListener("keydown", (e) => {
    if (e.code === "Escape" && fp && fp.classList.contains("big")) { e.preventDefault(); setBig(false); }
  });
  window.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" && e.target.type !== "range" && e.target.type !== "checkbox") return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    else if (e.code === "ArrowRight") { e.preventDefault(); stepBy(1); }
    else if (e.code === "ArrowLeft") { e.preventDefault(); stepBy(-1); }
  });
}

// The rover's real speed beside the playback speed, so 200x reads as what it is.
function speedNote() {
  const run = state.run;
  const v = run && Object.values(run.speed).filter((x) => x);
  const real = v && v.length ? `rover ${(Math.max(...v) * 100).toFixed(1)} cm/s · ` : "";
  $("speed-note").textContent = `${real}playback ${state.speed}×`;
}

function setView(v) {
  document.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-current", String(b.dataset.view === v)));
  if (world) world.setFollow(v);
  if (v === "A" || v === "B") {
    state.cam = v;
    document.querySelectorAll("#cam-seg button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.ag === v)));
    render(true);
  }
}

function togglePlay() {
  if (!state.run) return;
  if (!state.playing && state.t >= state.run.tEnd) state.t = 0;
  state.playing = !state.playing;
  $("play").textContent = state.playing ? "Pause" : "Play";
}

function stepBy(n) {
  const run = state.run;
  state.playing = false;
  $("play").textContent = "Play";
  const k = Math.min(run.steps.length - 1, Math.max(0, run.stepAt(state.t) + n));
  state.t = run.times[k];
  render();
}

let lastFrame = performance.now();
function loop(now) {
  const dt = Math.min(0.1, (now - lastFrame) / 1000);
  lastFrame = now;
  if (state.playing && state.run) {
    state.t += dt * state.speed;
    if (state.t >= state.run.tEnd) { state.t = state.run.tEnd; state.playing = false; $("play").textContent = "Play"; }
    render();
  }
  world.render();
  requestAnimationFrame(loop);
}

// ------------------------------------------------------------------ render
let lastK = -1, lastCamKey = "";
function render(force = false) {
  const run = state.run;
  if (!run) return;
  const t = state.t;
  const k = run.stepAt(t);
  world.currentT = t;
  world.update(t, state.opts);
  map.update(t);
  $("time").value = t;
  // Fixed length: the step number is padded with figure spaces (U+2007, the
  // width of a digit, never collapsed), so the clock never changes width.
  const n = String(run.steps.length);
  $("clock").textContent = `${fmtClock(t)} · step ${String(k + 1).padStart(n.length, "\u2007")}/${n}`;
  hud(run, k);
  const fr = run.frameNear(state.cam, t);
  const camKey = `${state.cam}:${fr ? fr.src : "-"}`;
  if (force || camKey !== lastCamKey) { camera(run, fr, k); lastCamKey = camKey; }
  if (force || k !== lastK) record(run, k);
  lastK = k;
}

function hud(run, k) {
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  const rows = run.agencies.map((a) => {
    const r = run.rover(a, k);
    const [tx, ty] = r.true_pose;
    const err = Math.hypot(r.estimate[0] - tx, r.estimate[1] - ty);
    const odo = r.odom_pose ? Math.hypot(r.odom_pose[0] - tx, r.odom_pose[1] - ty) : NaN;
    const by = r.nav_on ? "crater rims" : `<span class="off">odometry only</span>`;
    // "same" only when the two printed numbers are the same, not by assumption.
    const odoCell = !isFinite(odo) ? `<td class="k">n/a</td>`
      : odo.toFixed(1) === err.toFixed(1) ? `<td class="k">same</td>` : `<td>${odo.toFixed(1)} m</td>`;
    return `<tr><td class="ag" style="color:${hex(COLORS[a])}">${a}</td><td>${by}</td>` +
      `<td>±${r.sigma.toFixed(1)} m</td><td>${err.toFixed(1)} m</td>${odoCell}</tr>`;
  });
  const out = [`<table class="hudt"><thead><tr><th></th><th>Steering by</th>` +
    `<th title="The rover's own 1-sigma estimate of its position error">Thinks it is within</th>` +
    `<th title="Distance from the estimate to the hidden true position">Actually off by</th>` +
    `<th title="Where odometry alone would put it, distance from the true position">Odometry alone</th></tr></thead>` +
    `<tbody>${rows.join("")}</tbody></table>`];
  if (run.nav) {
    // "ShadowNav particle filter (Cauligi et al. 2023, Algs. 1-3), 100 particles, ..."
    // -> "ShadowNav particle filter, 100 particles"; the full line on hover.
    const short = run.nav.replace(/\s*\([^)]*\)/g, "").split(",").slice(0, 2).join(",").trim();
    const dots = run.hasParticles && state.opts.tracks && state.opts.particles ? "; dots are its particles, larger for more weight" : "";
    out.push(`<div class="hud-src" title="${esc(run.nav)}">${short}${dots}</div>`);
  }
  if (run.planner) {
    // "A* on a 2 m grid; a cell is blocked if ..." -> "Route: A* on a 2 m grid"; the full line on hover.
    const short = run.planner.split(/[;(]/)[0].replace(/[.,\s]+$/, "");
    out.push(`<div class="hud-src" title="${esc(run.planner)}">Route: ${short}</div>`);
  }
  $("hud").innerHTML = out.join("");
}

function camera(run, fr, k) {
  const a = state.cam;
  const img = $("fp-img");
  $("fp-empty").hidden = !!fr;
  if (fr) {
    if (img.dataset.src !== fr.src) {
      img.dataset.src = fr.src;
      img.src = fr.src;
    }
  } else img.removeAttribute("src");
  $("fp-cap").innerHTML = captionHtml(run, a, fr ? fr.k : k, !!(fr && fr.sub));
  // Lidar panel: the scan taken with this step's live frame (run_demo.py --vision).
  const r = run.rover(a, fr ? fr.k : k);
  const li = $("lidar-img");
  // Runs without --vision have no lidar: hide the panel rather than show an empty box.
  if (run.hasLidar === undefined) run.hasLidar = run.agencies.some((ag) => run.steps.some((_, j) => run.rover(ag, j).lidar_frame));
  li.parentElement.hidden = $("lidar-cap").hidden = !run.hasLidar;
  if (r.lidar_frame) {
    const src = run.dir + r.lidar_frame;
    if (li.dataset.src !== src) { li.dataset.src = src; li.src = src; }
  } else {
    li.removeAttribute("src");
    delete li.dataset.src;
  }
  $("lidar-cap").textContent = lidarCapText(run, a, fr ? fr.k : k);
  // Rim points are logged per step, in that step's pose: draw them only on a step's own frame.
  drawRims(run, a, fr ? fr.k : k, !!(fr && fr.sub));
  drawPlan(run, a, fr, k);
}

// The rover's planned route (logged per step as "plan", world xy, out to
// ~60 m) as amber dots on the camera image, projected from the pose of the
// frame shown, so the line moves with every frame (every 0.5 m with
// sub-frames) and bends as soon as the rover re-plans around a crater.
function drawPlan(run, a, fr, k) {
  const kk = fr ? fr.k : k;
  const r = run.rover(a, kk);
  if (!r.plan || r.plan.length < 2) return;
  const c = $("fp-rims").getContext("2d");
  // The pose the shown image was taken from: its step, plus the sub-frame's
  // fraction toward the next step.
  const k1 = Math.min(kk + 1, run.steps.length - 1);
  const tf = run.times[kk] + (fr ? fr.f || 0 : 0) * (run.times[k1] - run.times[kk]);
  const [x, y, h] = run.poseAt(a, tf);
  const camZ = terrain.heightAt(x, y) + FP.camH;
  const p = (FP.pitch * Math.PI) / 180, cp = Math.cos(p), sp = Math.sin(p);
  const ch = Math.cos(h), sh = Math.sin(h);
  c.fillStyle = "rgba(255, 200, 60, 0.95)";
  const step = 0.75;   // metres between dots
  let carry = 0;
  for (let i = 0; i + 1 < r.plan.length; i++) {
    const [ax, ay] = r.plan[i], [bx, by] = r.plan[i + 1];
    const seg = Math.hypot(bx - ax, by - ay);
    for (let s = carry; s < seg; s += step) {
      const wx = ax + (bx - ax) * s / seg, wy = ay + (by - ay) * s / seg;
      const dx = wx - x, dy = wy - y;
      const f = dx * ch + dy * sh, l = -dx * sh + dy * ch;   // rover frame
      const z = terrain.heightAt(wx, wy) + 0.05 - camZ;
      const Xc = -l, Yc = f * sp + z * cp, Zc = f * cp - z * sp;
      if (Zc <= 0.5) continue;
      const u = FP.w / 2 + (FP.fx * Xc) / Zc, v = FP.h / 2 - (FP.fx * Yc) / Zc;
      if (u < 0 || v < 0 || u > FP.w || v > FP.h) continue;
      const rad = Math.max(1.2, Math.min(5, 18 / Math.hypot(f, l)));
      c.beginPath(); c.arc(u, v, rad, 0, 2 * Math.PI); c.fill();
    }
    carry = ((carry - seg) % step + step) % step;
  }
}

function captionHtml(run, a, k, sub) {
  const r = run.rover(a, k);
  // Say how far apart the recorded frames are, from the log.
  const gapM = run.frameSpacing(a);
  const every = gapM ? `, one frame per ${gapM < 10 ? gapM.toFixed(1).replace(/\.0$/, "") : Math.round(gapM)} m` : "";
  return `Recorded lamp-only view from rover ${a}${every}. 60° view, camera 2.5 m up. Click to enlarge.` +
    (r.nav_on ? "" : ` <span class="off">Crater fixes off.</span>`) +
    (state.opts.rims && r.rims && r.rims.length && !sub ? ` ${r.rims.length} rim points${run.standin && run.standin.rims ? " (stand-in, from the crater map)" : " from the camera"}.` : "") +
    (r.plan && !sub ? " Amber dots: the route it plans to drive, re-planned every 2 m." : "") +
    (r.lidar_frame && !sub ? " Red marks: lidar hazards." : "");
}

// Lidar panel caption: the scan taken with this step's live frame (run_demo.py --vision).
function lidarCapText(run, a, k) {
  const r = run.rover(a, k);
  if (!r.lidar_frame) return "No lidar in this run.";
  const hz = r.lidar_hazards || {};
  return `Rover ${a} lidar, beside the camera: 120° x 33°, near warm, far cool. ` +
    `This scan: ${hz.crater || 0} crater cells, ${hz.slope || 0} steep cells.`;
}

// Text that changes as a run plays must not move the page. Each live box is
// given, as its min-height, the height of the tallest text it will show in
// this run, measured on the page as laid out now. Called when the run, the
// window size or a toggle changes; playing then never moves anything.
function reserveHeights(run) {
  const last = run.steps.length - 1;
  const caps = new Set(), stories = new Set(), lidarCaps = new Set();
  for (let k = 0; k <= last; k++) {
    for (const a of run.agencies) for (const sub of [false, true]) caps.add(captionHtml(run, a, k, sub));
    for (const a of run.agencies) lidarCaps.add(lidarCapText(run, a, k));
    stories.add(story(run, k, run.findsAt(k)));
  }
  const evsAll = run.eventsUpTo(last).slice().reverse();
  const boxes = [
    [$("fp-cap"), caps],
    [$("lidar-cap"), lidarCaps],
    [$("story"), stories],
    // Finds and transactions only accumulate: the run's end is the most.
    [$("finds"), [findsHtml(run, []), findsHtml(run, run.findsAt(last))]],
    [$("feed"), [feedHtml(run, [], null), feedHtml(run, evsAll, null)]],
  ];
  for (const [el, htmls] of boxes) {
    if (!el) continue;
    const keep = el.innerHTML;
    el.style.minHeight = "";
    let h = 0;
    for (const html of htmls) { el.innerHTML = html; h = Math.max(h, el.getBoundingClientRect().height); }
    el.innerHTML = keep;
    const cs = getComputedStyle(el);
    if (cs.boxSizing !== "border-box") {
      h -= parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) + parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
    }
    el.style.minHeight = `${Math.ceil(h)}px`;
  }
}

// Rim points [x_fwd, y_left] in the rover frame, projected into the image
// with the scene's pinhole model and the ground height under each point.
function drawRims(run, a, k, none = false) {
  const cv = $("fp-rims");
  const W = cv.clientWidth, H = cv.clientHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  cv.width = Math.round(W * dpr);
  cv.height = Math.round(H * dpr);
  const c = cv.getContext("2d");
  c.setTransform((W * dpr) / FP.w, 0, 0, (H * dpr) / FP.h, 0, 0);
  if (!state.opts.rims || none) return;
  const r = run.rover(a, k);
  if (!r.rims) return;
  const [x, y, h] = r.true_pose;
  const camZ = terrain.heightAt(x, y) + FP.camH;
  const p = (FP.pitch * Math.PI) / 180, cp = Math.cos(p), sp = Math.sin(p);
  c.fillStyle = hex(COLORS[a]);
  for (const [f, l] of r.rims) {
    const wx = x + f * Math.cos(h) - l * Math.sin(h), wy = y + f * Math.sin(h) + l * Math.cos(h);
    const z = terrain.heightAt(wx, wy) - camZ;
    const Xc = -l, Yc = f * sp + z * cp, Zc = f * cp - z * sp;
    if (Zc <= 0.1) continue;
    const u = FP.w / 2 + (FP.fx * Xc) / Zc, v = FP.h / 2 - (FP.fx * Yc) / Zc;
    if (u < 0 || v < 0 || u > FP.w || v > FP.h) continue;
    c.fillRect(u - 2, v - 2, 4, 4);
  }
}

function record(run, k) {
  const finds = run.findsAt(k);
  $("finds").innerHTML = findsHtml(run, finds);
  const evs = run.eventsUpTo(k).slice().reverse();
  const key = evs.map((e) => e.tx).join();
  const fresh = key !== state.lastEventsKey && state.lastEventsKey !== "" ? evs[0] && evs[0].tx : null;
  state.lastEventsKey = key;
  $("feed").innerHTML = feedHtml(run, evs, fresh);
  $("story").innerHTML = story(run, k, finds);
}

function findsHtml(run, finds) {
  if (!finds.length) return `<div class="empty">No finds yet.</div>`;
  return finds.map((f) => {
    const checks = f.checks.map((c) => `${c.agency} ${c.agrees ? "agrees" : "disagrees"} (reading ${c.reading.toFixed(2)}${run.unit})`).join(", ");
    return `<div class="find"><span class="id c-${f.agency}">#${f.find_id}</span>` +
      `<span>Ice found by ${f.agency}, ±${f.sigma.toFixed(1)} m</span><span class="status ${f.status}">${f.status}</span>` +
      `<span class="meta">reading ${f.reading.toFixed(2)}${run.unit}${checks ? " · " + checks : " · waiting for another agency"}</span></div>`;
  }).join("");
}

function feedHtml(run, evs, fresh) {
  const ex = run.chain && run.chain.explorer;
  return evs.length ? evs.map((e) => {
    const what = e.type === "find"
      ? `postFind #${e.find_id} · ±${e.sigma.toFixed(1)} m · reading ${e.reading.toFixed(2)}${run.unit}`
      : `checkFind #${e.find_id} · ${e.agrees ? "agrees" : "disagrees"} · reading ${e.reading.toFixed(2)}${run.unit}`;
    const tx = run.fakeTx && !ex
      ? `<span class="fake">fake tx</span><span class="h">${short(e.tx)}</span>`
      : ex ? `<a class="h" href="${ex.replace(/\/$/, "")}/tx/${e.tx}" target="_blank" rel="noopener">${short(e.tx)}</a>` : `<span class="h">${short(e.tx)}</span>`;
    return `<div class="ev${e.tx === fresh ? " new" : ""}"><span class="t">${fmtClock(e.t).slice(2)}</span>` +
      `<span class="who c-${e.agency}">${e.agency}</span><span>${what}</span><span class="tx">${tx}</span></div>`;
  }).join("") : `<div class="empty">No transactions yet.</div>`;
}

// One line that says what is happening, for someone who just walked up.
function story(run, k, finds) {
  const f = finds[0];
  const a = run.agencies[0];
  const r = run.rover(a, k);
  if (!f) return r.nav_on
    ? `Rover A drives in the dark and fixes its position on lamp-lit crater rims. Its estimate stays on the true track; plain odometry drifts.`
    : `Rover A drives on odometry alone. Its position error grows with every metre, and so does its σ.`;
  const other = run.agencies.find((x) => x !== f.agency);
  if (f.status === "reported") return `${f.agency} posted a find at <b>±${f.sigma.toFixed(1)} m</b>. ${other}, from another agency, reads it from the chain and drives there to check.`;
  if (f.status === "confirmed") return `${other} measured ice at the reported spot. The find is <b>confirmed</b> on chain.`;
  return `${other} found no ice at the reported spot: ${f.agency}'s position was off by more than the ice patch. The find stays on the record, <b>disputed</b>.`;
}

window.lantern = { state, get world() { return world; }, get map() { return map; } };
main();
