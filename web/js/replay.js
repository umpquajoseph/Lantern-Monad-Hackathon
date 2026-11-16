// 3D replay of one sortie: a time-lapse of its saved poses in the existing 3D
// world (world.js). Nothing is re-simulated. The sortie is converted to the
// "Run log" shape (docs/handoffs.md, "Part 4 reads") so world.js draws it as is.
import * as THREE from "three";
import { Terrain, Run } from "./data.js";
import { World, COLORS } from "./world.js";
import { CampaignRun, SCOUT_COLOR, STATUS_COLOR, ACTIVITY, OUTCOME_WORD, fmtNum, fmtDay, standinBanner } from "./campaign.js";

const $ = (id) => document.getElementById(id);
const P = new URLSearchParams(location.search);
const hexInt = (h) => parseInt(h.slice(1), 16);
// world.js colours by key: add the Scouts and the campaign's find statuses.
for (const [k, v] of Object.entries(SCOUT_COLOR)) COLORS[k] = hexInt(v);
for (const [k, v] of Object.entries(STATUS_COLOR)) COLORS[k] = hexInt(v);

const ACT_COLOR = ["#4a6a8f", "#6f8fb3", "#4a6a8f", "#e0b84a", "#4fbf72", "#58c8ff", "#b58cff", "#3b4048", "#e5574f"];
const state = { t: 0, playing: false, speed: 600, fill: true, truth: P.get("truth") === "1", follow: true };
let world, terrain, cr, sortie, run;

// A sortie as a Run: one rover, keyed by its Scout id; the 95% bound drawn as the ring.
class SortieRun extends Run {
  constructor(cr, s) {
    const rows = cr.rows(s.sortie_id), c = cr.col;
    const steps = rows.map((r) => ({
      t: r[c.t],
      rovers: { [s.scout]: { true_pose: [r[c.true_x], r[c.true_y], r[c.true_heading]], estimate: [r[c.est_x], r[c.est_y]],
        odom_pose: [r[c.est_x], r[c.est_y], r[c.true_heading]], sigma: r[c.bound95] } },
      act: r[c.activity], energy: r[c.energy_kwh],
    }));
    super({ run_id: `${cr.rec.run_id}:${s.sortie_id}`, steps, truth: { ice: cr.rec.truth.ice } }, "");
    this.agencies = [s.scout];
    this.tracks = { [s.scout]: { truth: steps.map((x) => x.rovers[s.scout].true_pose.slice(0, 2)),
      est: steps.map((x) => x.rovers[s.scout].estimate), odom: steps.map((x) => x.rovers[s.scout].estimate) } };
    this.cr = cr;
    this.sortie = s;
    // What the dock knew at launch: every event of a sortie already home.
    const home = new Set(cr.rec.sorties.filter((x) => x.t_end <= s.t_start).map((x) => x.sortie_id));
    this.before = cr.events.filter((e) => e.sortie_id && home.has(e.sortie_id));
    this.mine = cr.events.filter((e) => e.sortie_id === s.sortie_id);
    this._finds = new Map();
  }

  // The ice map as this Scout knows it at step k: world.js draws each find
  // with its 95% bound, coloured by status.
  findsAt(k) {
    if (this._finds.has(k)) return this._finds.get(k);
    const T = this.times[k];
    const list = this.cr.findsFrom([...this.before, ...this.mine.filter((e) => e.t <= T)]).map((f) => ({
      find_id: f.find_id, agency: f.scout, x: f.est[0], y: f.est[1], sigma: f.bound95, status: f.status, checks: [],
    }));
    this._finds.set(k, list);
    return list;
  }
}

async function main() {
  const batch = P.get("batch"), id = P.get("run");
  $("runs-link").href = `index.html?batch=${encodeURIComponent(batch || "")}`;
  $("run-link").href = `run.html?batch=${encodeURIComponent(batch || "")}&run=${encodeURIComponent(id || "")}`;
  $("run-link").textContent = id;
  try {
    [cr, terrain] = await Promise.all([CampaignRun.load(batch, id), Terrain.load("assets/terrain/")]);
  } catch (e) {
    $("loading").textContent = String(e.message).includes("assets") ? "Terrain assets missing. Run python app.py." : "Run not found.";
    throw e;
  }
  if (cr.rec.standin) { $("standin-slot").innerHTML = standinBanner(cr.rec.standin); $("page").classList.add("has-banner"); }
  const want = P.get("sortie");
  sortie = cr.sorties.find((s) => s.sortie_id === want) || cr.sorties[0];
  $("sortie-pick").innerHTML = cr.sorties.map((s) => `<option value="${s.sortie_id}">${s.sortie_id}</option>`).join("");
  $("sortie-pick").value = sortie.sortie_id;
  $("sortie-pick").onchange = (e) => {
    const q = new URLSearchParams(location.search);
    q.set("sortie", e.target.value);
    location.search = q.toString();
  };
  $("sortie-title").textContent = sortie.sortie_id;
  document.title = `Lantern · ${sortie.sortie_id} replay`;

  await groundEverywhere();
  world = new World($("viewport"), terrain, P.get("quality") || "high");
  world.hideOdom = true;
  world.buildTerrain();
  world.buildBackdrop();
  world.loadRocks();
  world.initPatches();

  run = new SortieRun(cr, sortie);
  // Fine tiles under the track first, so the lines drape on the real ground.
  const pts = run.tracks[sortie.scout].truth.filter((_, i) => i % 3 === 0);
  const keys = [...terrain.keysNear(pts, 4)];
  let done = 0;
  await Promise.all(keys.map((k) => terrain.loadTile(k).then(() => { $("loading").textContent = `Loading ground under the track, ${++done} of ${keys.length}`; })));
  state.t = run.times[0];
  world.currentT = state.t;
  world.follow = sortie.scout;   // world.js starts on rover "A"
  world.setRun(run);
  world.rovers[sortie.scout].tag.innerHTML = `<span>Scout ${sortie.scout}</span>`;
  dock();
  vis();
  acts();
  bind();
  $("scrub").min = run.times[0];
  $("scrub").max = run.tEnd;
  render();
  $("loading").hidden = true;
  if (!P.has("paused")) toggle();
  let last = performance.now();
  const loop = (now) => {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (state.playing) {
      state.t += dt * state.speed;
      if (state.t >= run.tEnd) { state.t = run.tEnd; toggle(false); }
      render();
    }
    world.render();
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

// A sortie runs kilometres past the 1 km drive area. Outside it, heights come
// from the real LOLA backdrops, lowered exactly as world.js draws them
// (buildBackdrop: 0.5 m plus r^2 / 2R), so the Scout sits on the drawn ground.
async function groundEverywhere() {
  const m = terrain.meta;
  const near = await terrain.loadBackdrop("backdrop_near"), far = await terrain.loadBackdrop("backdrop");
  const ox = m.x0 + 0.5 * (m.cols - 1) * m.cell, oy = m.y0 + 0.5 * (m.rows - 1) * m.cell;
  const R = (m.display && m.display.moon_radius_m) || 1737400;
  const x1 = m.x0 + (m.cols - 1) * m.cell, y1 = m.y0 + (m.rows - 1) * m.cell;
  const inner = terrain.heightAt.bind(terrain);
  terrain.heightAt = (x, y) => {
    if (x >= m.x0 && x <= x1 && y >= m.y0 && y <= y1) return inner(x, y);
    const drop = 0.5 + ((x - ox) ** 2 + (y - oy) ** 2) / (2 * R);
    if (near && near.contains(x, y)) return near.at(x, y) - drop;
    if (far && far.contains(x, y)) return far.at(x, y) - drop;
    return inner(x, y);
  };
}

function dock() {
  const d = cr.rec.dock;
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
}

function vis() {
  world.setVisibility({ fill: state.fill, tracks: true, truth: state.truth });
  // world.js draws the true path with the tracks; here it is hidden truth, behind the reveal.
  world.tracks[sortie.scout].truth.line.visible = state.truth;
  $("vnote").hidden = !state.fill;
  $("fill-btn").textContent = state.fill ? "Fill light on" : "Fill light off";
  $("fill-btn").classList.toggle("on", state.fill);
  $("truth-btn").textContent = state.truth ? "Hide hidden truth" : "Reveal hidden truth";
  $("truth-btn").classList.toggle("on", state.truth);
  $("truth-btn").setAttribute("aria-pressed", String(state.truth));
  $("cam-btn").textContent = state.follow ? "Whole sortie" : `Follow ${sortie.scout}`;
}

// The activity strip over the time bar: where the sortie's time went, in order.
function acts() {
  const T0 = run.times[0], span = run.tEnd - T0 || 1;
  let html = "";
  for (let k = 0; k < run.steps.length - 1; k++) {
    const w = (100 * (run.times[k + 1] - run.times[k])) / span;
    if (w > 0) html += `<i style="width:${w}%;background:${ACT_COLOR[run.steps[k + 1].act] || "#333"}" title="${ACTIVITY[run.steps[k + 1].act]}"></i>`;
  }
  $("acts").innerHTML = html;
}

function bind() {
  $("play").onclick = () => toggle();
  $("speed").onchange = (e) => { state.speed = Number(e.target.value); };
  $("scrub").oninput = (e) => { state.t = Number(e.target.value); render(); };
  $("fill-btn").onclick = () => { state.fill = !state.fill; vis(); };
  $("truth-btn").onclick = () => { state.truth = !state.truth; vis(); render(); };
  $("cam-btn").onclick = () => { state.follow = !state.follow; world.setFollow(state.follow ? sortie.scout : "site"); vis(); };
  window.addEventListener("keydown", (e) => {
    if (e.target.tagName === "SELECT") return;
    if (e.code === "Space") { toggle(); e.preventDefault(); }
    if (e.key === "f" || e.key === "F") { state.fill = !state.fill; vis(); }
  });
}

function toggle(on = !state.playing) {
  if (on && state.t >= run.tEnd) state.t = run.times[0];
  state.playing = on;
  $("play").textContent = on ? "Pause" : "Play";
}

function render() {
  world.currentT = state.t;
  world.update(state.t, { tracks: true, particles: false });
  $("scrub").value = state.t;
  const T0 = run.times[0], dt = state.t - T0;
  const h = Math.floor(dt / 3600), m = Math.floor((dt % 3600) / 60);
  $("clock").textContent = `T+${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")} · ${fmtDay(state.t)}`;
  hud();
}

function hud() {
  const k = run.stepAt(state.t), st = run.steps[Math.min(k + 1, run.steps.length - 1)], r = run.steps[k].rovers[sortie.scout];
  const act = ACTIVITY[st.act] || "–";
  const [x, y] = run.poseAt(sortie.scout, state.t);
  const err = Math.hypot(r.estimate[0] - r.true_pose[0], r.estimate[1] - r.true_pose[1]);
  const finds = run.findsAt(k);
  const counts = ["find", "confirmed", "unconfirmed", "drilled"].map((s) => [s, finds.filter((f) => f.status === s).length]).filter(([, n]) => n);
  const dist = Math.hypot(x - cr.rec.dock.x, y - cr.rec.dock.y);
  $("hud").innerHTML = `<h3><i class="sdot" style="background:${SCOUT_COLOR[sortie.scout]}"></i>Scout ${sortie.scout}, sortie ${sortie.n}
      <span class="outcome ${sortie.outcome}">${OUTCOME_WORD[sortie.outcome] || sortie.outcome}</span></h3>
    <div class="act">${act}</div>
    <dl class="kv">
      <dt>Battery</dt><dd>${fmtNum(st.energy, 2)} kWh</dd>
      <dt>Position bound (95%)</dt><dd>±${fmtNum(r.sigma, 1)} m</dd>
      ${state.truth ? `<dt style="color:var(--truth)">True error (hidden)</dt><dd style="color:var(--truth)">${fmtNum(err, 1)} m</dd>` : ""}
      <dt>From the dock</dt><dd>${fmtNum(dist / 1000, 2)} km</dd>
      <dt>Ice map</dt><dd>${counts.map(([s, n]) => `<span style="color:${STATUS_COLOR[s]}">${n} ${s}</span>`).join(", ") || "empty"}</dd>
    </dl>
    <div class="note">Coloured line: the Scout's estimated path. Ring: its 95% position bound.${state.truth ? ' <span style="color:var(--truth)">White line and dashed rings: where it really drove and the real ice (hidden truth).</span>' : ""} A time-lapse of the saved poses; nothing is re-simulated.</div>`;
}

main();
