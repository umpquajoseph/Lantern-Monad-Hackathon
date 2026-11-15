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

const state = {
  runs: [], run: null, t: 0, playing: false, speed: 200, cam: "A",
  opts: { fill: true, tracks: true, rims: true, truth: false },
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
  map = new MapView($("map"), terrain);

  state.runs = await loadIndex("runs/");
  const list = $("run-list");
  state.runs.forEach((r) => {
    const b = document.createElement("button");
    b.className = "nav";
    b.dataset.run = r.run_id;
    const [head, ...rest] = r.label.split(":");
    b.innerHTML = `${head}<span class="sub">${rest.join(":").trim() || "&nbsp;"}</span>`;
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
    const parts = Object.keys(si).join(", ");
    // Say the chain is fake only when it is (run.fakeTx: a chain stand-in, or no explorer in the log).
    const chainLine = run.fakeTx ? "Nothing was posted to Monad; the transaction hashes are fake. " : "The chain is real: every hash links to Monad testnet. ";
    $("standin-text").innerHTML = `Not real yet: ${parts}. ${chainLine}` +
      `The first-person frames are real renders. <details><summary>Details</summary><ul>${lines.map((l) => `<li>${l}</li>`).join("")}</ul></details>`;
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
    $("opt-" + k).onchange = (e) => {
      state.opts[k] = e.target.checked;
      world.setVisibility(state.opts);
      map.opts = state.opts;
      $("vnote").hidden = !state.opts.fill;
      render(true);
    };
  }
  map.opts = state.opts;
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
  $("clock").textContent = `${fmtClock(t)} · step ${k + 1}/${run.steps.length}`;
  hud(run, k);
  if (force || k !== lastK || lastCamKey !== state.cam) { camera(run, k); lastCamKey = state.cam; }
  if (force || k !== lastK) record(run, k);
  lastK = k;
}

function hud(run, k) {
  const rows = run.agencies.map((a) => {
    const r = run.rover(a, k);
    const [tx, ty] = r.true_pose;
    const err = Math.hypot(r.estimate[0] - tx, r.estimate[1] - ty);
    const odo = r.odom_pose ? Math.hypot(r.odom_pose[0] - tx, r.odom_pose[1] - ty) : NaN;
    const nav = r.nav_on ? "crater fixes on " : `<span class="off">crater fixes off</span>`;
    return `<div class="row"><span class="ag" style="color:${hex(COLORS[a])}">${a}</span><span>${nav}</span>` +
      `<span><span class="k">σ</span> ${r.sigma.toFixed(1)} m</span>` +
      `<span><span class="k">true error</span> ${err.toFixed(1)} m</span>` +
      `<span><span class="k">odometry error</span> ${odo.toFixed(1)} m</span></div>`;
  });
  $("hud").innerHTML = rows.join("");
}

function camera(run, k) {
  const a = state.cam;
  const fr = run.frameAt(a, k);
  const img = $("fp-img");
  $("fp-empty").hidden = !!fr;
  if (fr) {
    if (img.dataset.src !== fr.src) {
      img.dataset.src = fr.src;
      img.src = fr.src;
    }
  } else img.removeAttribute("src");
  const r = run.rover(a, k);
  $("fp-cap").innerHTML = `Rover ${a}. Lamp only, 60° field of view, camera 2.5 m up, lamp 0.2 m below it.` +
    (r.nav_on ? "" : ` <span class="off">Crater fixes off.</span>`) +
    (state.opts.rims && r.rims && r.rims.length ? ` ${r.rims.length} rim points${run.standin ? " (stand-in, from the crater map)" : ""}.` : "");
  drawRims(run, a, fr ? fr.k : k);
}

// Rim points [x_fwd, y_left] in the rover frame, projected into the image
// with the scene's pinhole model and the ground height under each point.
function drawRims(run, a, k) {
  const cv = $("fp-rims");
  const W = cv.clientWidth, H = cv.clientHeight;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  cv.width = Math.round(W * dpr);
  cv.height = Math.round(H * dpr);
  const c = cv.getContext("2d");
  c.setTransform((W * dpr) / FP.w, 0, 0, (H * dpr) / FP.h, 0, 0);
  if (!state.opts.rims) return;
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
  const fl = $("finds");
  if (!finds.length) fl.innerHTML = `<div class="empty">No finds yet.</div>`;
  else fl.innerHTML = finds.map((f) => {
    const checks = f.checks.map((c) => `${c.agency} ${c.agrees ? "agrees" : "disagrees"} (reading ${c.reading.toFixed(2)}${run.unit})`).join(", ");
    return `<div class="find"><span class="id c-${f.agency}">#${f.find_id}</span>` +
      `<span>Ice found by ${f.agency}, ±${f.sigma.toFixed(1)} m</span><span class="status ${f.status}">${f.status}</span>` +
      `<span class="meta">reading ${f.reading.toFixed(2)}${run.unit}${checks ? " · " + checks : " · waiting for another agency"}</span></div>`;
  }).join("");

  const evs = run.eventsUpTo(k).slice().reverse();
  const key = evs.map((e) => e.tx).join();
  const fresh = key !== state.lastEventsKey && state.lastEventsKey !== "" ? evs[0] && evs[0].tx : null;
  state.lastEventsKey = key;
  const ex = run.chain && run.chain.explorer;
  $("feed").innerHTML = evs.length ? evs.map((e) => {
    const what = e.type === "find"
      ? `postFind #${e.find_id} · ±${e.sigma.toFixed(1)} m · reading ${e.reading.toFixed(2)}${run.unit}`
      : `checkFind #${e.find_id} · ${e.agrees ? "agrees" : "disagrees"} · reading ${e.reading.toFixed(2)}${run.unit}`;
    const tx = run.fakeTx && !ex
      ? `<span class="fake">fake tx</span><span class="h">${short(e.tx)}</span>`
      : ex ? `<a class="h" href="${ex.replace(/\/$/, "")}/tx/${e.tx}" target="_blank" rel="noopener">${short(e.tx)}</a>` : `<span class="h">${short(e.tx)}</span>`;
    return `<div class="ev${e.tx === fresh ? " new" : ""}"><span class="t">${fmtClock(e.t).slice(2)}</span>` +
      `<span class="who c-${e.agency}">${e.agency}</span><span>${what}</span><span class="tx">${tx}</span></div>`;
  }).join("") : `<div class="empty">No transactions yet.</div>`;

  $("story").innerHTML = story(run, k, finds);
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
