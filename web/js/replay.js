// 3D replay of one sortie's time window: a time-lapse of the saved poses of
// every Scout out during it, in the existing 3D world (world.js, unchanged).
// Nothing is re-simulated. The window is converted to the "Run log" shape
// (docs/handoffs.md, "Part 4 reads"), one rover per Scout.
import * as THREE from "three";
import { Terrain, Run } from "./data.js";
import { World, COLORS } from "./world.js";
import { CampaignRun, SCOUT_COLOR, STATUS_COLOR, ACTIVITY, OUTCOME_WORD, fmtNum, fmtDay, standinBanner, standinOf, bannersOf, windowFor } from "./campaign.js";

const $ = (id) => document.getElementById(id);
const P = new URLSearchParams(location.search);
const hexInt = (h) => parseInt(h.slice(1), 16);
// world.js colours by key: add the Scouts and the campaign's find statuses.
for (const [k, v] of Object.entries(SCOUT_COLOR)) COLORS[k] = hexInt(v);
for (const [k, v] of Object.entries(STATUS_COLOR)) COLORS[k] = hexInt(v);

const ACT_COLOR = ["#4a6a8f", "#6f8fb3", "#4a6a8f", "#e0b84a", "#4fbf72", "#58c8ff", "#b58cff", "#3b4048", "#e5574f"];
const DOCKED = 7;
const PARK_M = 4;              // display only: Scouts at the dock are drawn this far apart, beside the pad
const SEARCH_LABEL_AFTER_S = 1800;  // display only: a search's label stays this long after it ends, with its result
const state = { t: 0, playing: false, speed: 600, fill: true, truth: P.get("truth") === "1", follow: null };
let world, terrain, cr, sortie, run, searches = [];

function lerpPose(a, b, f) {
  let dh = b[2] - a[2];
  dh = Math.atan2(Math.sin(dh), Math.cos(dh));
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + dh * f];
}

// Every Scout out during the picked sortie, sampled on one shared timeline.
// Between its own sorties a Scout is at the dock. world.js draws the rover
// body at true_pose; until the truth is revealed it gets the pose the Scout
// believed, and the true pose is kept aside.
class FleetRun extends Run {
  constructor(cr, s) {
    const T0 = s.t_start, T1 = s.t_end, c = cr.col, d = cr.rec.dock;
    const over = cr.rec.sorties.filter((x) => x.t_start < T1 && x.t_end > T0);
    const scouts = [s.scout, ...[...new Set(over.map((x) => x.scout))].filter((a) => a !== s.scout).sort()];
    const rowsOf = Object.fromEntries(over.map((x) => [x.sortie_id, cr.rows(x.sortie_id)]));
    const tset = new Set([T0, T1]);
    for (const rows of Object.values(rowsOf)) for (const r of rows) if (r[c.t] >= T0 && r[c.t] <= T1) tset.add(r[c.t]);
    const times = [...tset].sort((a, b) => a - b);
    const park = (i) => [d.x - PARK_M * (i + 1), d.y - PARK_M, 0];

    const sample = (a, i, t) => {
      const so = over.find((x) => x.scout === a && x.t_start <= t && t <= x.t_end);
      const rows = so && rowsOf[so.sortie_id];
      if (!rows || !rows.length) {
        const p = park(i);
        return { tru: p, est: p, b: 0.3, act: DOCKED, energy: null, sortie: null };
      }
      let lo = 0, hi = rows.length - 1;
      if (t <= rows[0][c.t]) hi = 0;
      else while (lo < hi) { const m = (lo + hi + 1) >> 1; if (rows[m][c.t] <= t) lo = m; else hi = m - 1; }
      const r0 = rows[lo], r1 = rows[Math.min(lo + 1, rows.length - 1)];
      const span = r1[c.t] - r0[c.t], f = span > 0 ? Math.min(1, Math.max(0, (t - r0[c.t]) / span)) : 0;
      const tru = lerpPose([r0[c.true_x], r0[c.true_y], r0[c.true_heading]], [r1[c.true_x], r1[c.true_y], r1[c.true_heading]], f);
      const est = lerpPose([r0[c.est_x], r0[c.est_y], r0[c.true_heading]], [r1[c.est_x], r1[c.est_y], r1[c.true_heading]], f);
      return { tru, est, b: r0[c.bound95] + (r1[c.bound95] - r0[c.bound95]) * f, act: r1[c.activity], energy: r0[c.energy_kwh], sortie: so };
    };

    const poses = { truth: {}, believed: {} };
    for (const a of scouts) { poses.truth[a] = []; poses.believed[a] = []; }
    const steps = times.map((t) => {
      const rovers = {}, info = {};
      scouts.forEach((a, i) => {
        const q = sample(a, i, t);
        rovers[a] = { true_pose: q.tru, estimate: q.est.slice(0, 2), odom_pose: q.est, sigma: q.b };
        info[a] = { act: q.act, energy: q.energy, sortie: q.sortie };
        poses.truth[a].push(q.tru);
        poses.believed[a].push(q.est);
      });
      return { t, rovers, info };
    });
    super({ run_id: `${cr.rec.run_id}:${s.sortie_id}`, steps, truth: { ice: cr.rec.truth.ice } }, "");
    this.agencies = scouts;
    this.tracks = {};
    for (const a of scouts) this.tracks[a] = { truth: poses.truth[a].map((p) => p.slice(0, 2)),
      est: poses.believed[a].map((p) => p.slice(0, 2)), odom: poses.believed[a].map((p) => p.slice(0, 2)) };
    this.poses = poses;
    this.cr = cr;
    this.sortie = s;
    this.over = over;
    this.showTruth(false);
    this._finds = new Map();
  }

  showTruth(on) {
    const P = on ? this.poses.truth : this.poses.believed;
    for (const a of this.agencies) this.steps.forEach((st, k) => { st.rovers[a].true_pose = P[a][k]; });
  }

  // The ice map as the Scouts had logged it by step k, every Scout's finds
  // with their status. world.js draws each with its 95% bound.
  findsAt(k) {
    if (this._finds.has(k)) return this._finds.get(k);
    const T = this.times[k];
    const list = this.cr.findsFrom(this.cr.iceEvents.filter((e) => e.t <= T)).map((f) => ({
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
  const banners = bannersOf(cr.rec);
  if (banners) { $("standin-slot").innerHTML = banners; $("page").classList.add("has-banner"); }
  const want = P.get("sortie");
  sortie = cr.sorties.find((s) => s.sortie_id === want) || cr.sorties[0];
  $("sortie-pick").innerHTML = cr.sorties.map((s) => `<option value="${s.sortie_id}">${s.sortie_id}</option>`).join("");
  $("sortie-pick").value = sortie.sortie_id;
  $("sortie-pick").onchange = (e) => {
    const q = new URLSearchParams(location.search);
    q.set("sortie", e.target.value);
    q.delete("t"); q.delete("follow");
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

  run = new FleetRun(cr, sortie);
  // Fine tiles under every track first, so the lines drape on the real ground.
  const pts = run.agencies.flatMap((a) => run.tracks[a].truth.filter((_, i) => i % 3 === 0));
  const keys = [...terrain.keysNear(pts, 4)];
  let done = 0;
  await Promise.all(keys.map((k) => terrain.loadTile(k).then(() => { $("loading").textContent = `Loading ground under the tracks, ${++done} of ${keys.length}`; })));
  state.follow = run.agencies.includes(P.get("follow")) ? P.get("follow") : sortie.scout;
  state.t = Math.min(run.tEnd, Math.max(run.times[0], Number(P.get("t")) || run.times[0]));
  world.currentT = state.t;
  world.follow = state.follow;   // world.js starts on rover "A"
  world.setRun(run);
  dock();
  searchMarks();
  fpInit();
  vis();
  acts();
  bind();
  $("scrub").min = run.times[0];
  $("scrub").max = run.tEnd;
  render();
  $("loading").hidden = true;
  if (!P.has("paused") && !P.has("t")) toggle();
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
// (buildBackdrop: 0.5 m plus r^2 / 2R), so the Scouts sit on the drawn ground.
async function groundEverywhere() {
  const m = terrain.meta;
  // A run on a 5 m window outside the near backdrop: the window takes the near
  // backdrop's place (world.js buildBackdrop draws it, with the 20 m one round it).
  const win = await windowFor(cr.rec);
  if (win) {
    const n = m.backdrop_near;
    const inside = n && win.x0 >= n.x0 && win.y0 >= n.y0 && win.x0 + win.cols * win.cell <= n.x0 + n.cols * n.cell &&
      win.y0 + win.rows * win.cell <= n.y0 + n.rows * n.cell;
    if (!inside) m.backdrop_near = win;
  }
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

// A Scout searching another Scout's find: a ring of the search radius round
// the find, in the searcher's colour, with a label, while the search runs
// and for a while after with its result. This is the cross-check.
function searchMarks() {
  const T0 = run.times[0], T1 = run.tEnd;
  const findEv = new Map(cr.iceEvents.filter((e) => e.type === "find").map((e) => [e.find_id, e]));
  for (const e of cr.iceEvents) {
    if (e.type !== "search_start" || e.t > T1) continue;
    const end = cr.iceEvents.find((x) => x.type === "search_end" && x.find_id === e.find_id && x.sortie_id === e.sortie_id && x.t >= e.t);
    if ((end ? end.t : e.t) + SEARCH_LABEL_AFTER_S < T0) continue;
    const f = findEv.get(e.find_id);
    if (!f || !run.agencies.includes(e.scout)) continue;
    const col = SCOUT_COLOR[e.scout];
    const ring = world.ring(f.est[0], f.est[1], e.radius_m, world.lineMat(hexInt(col), 3, { dashed: true }));
    const p = world.groundL(f.est[0], f.est[1]);
    const lbl = world.label("", col);
    // Low and to the left of the find's beam, whose own label sits above-right.
    lbl.position.set(p.x, p.y, p.z + 3);
    lbl.center.set(1, 0.5);
    world.scene.add(ring, lbl);
    searches.push({ start: e, end, find: f, ring, lbl });
  }
}

function vis() {
  run.showTruth(state.truth);
  world.setVisibility({ fill: state.fill, tracks: true, truth: state.truth });
  // world.js draws the true paths with the tracks; here they are hidden truth, behind the reveal.
  for (const a of run.agencies) world.tracks[a].truth.line.visible = state.truth;
  $("vnote").hidden = !state.fill;
  $("fill-btn").textContent = state.fill ? "Fill light on" : "Fill light off";
  $("fill-btn").classList.toggle("on", state.fill);
  $("truth-btn").textContent = state.truth ? "Hide hidden truth" : "Reveal hidden truth";
  $("truth-btn").classList.toggle("on", state.truth);
  $("truth-btn").setAttribute("aria-pressed", String(state.truth));
  $("cam-btn").textContent = state.follow === "site" ? `Follow ${sortie.scout}` : "Whole sortie";
}

// Over the time bar: the followed Scout's activity, in order, and the
// moments that matter for the whole fleet (finds, searches, drilling).
function acts() {
  const a = state.follow === "site" ? sortie.scout : state.follow;
  const T0 = run.times[0], span = run.tEnd - T0 || 1;
  let html = "";
  for (let k = 0; k < run.steps.length - 1; k++) {
    const w = (100 * (run.times[k + 1] - run.times[k])) / span, act = run.steps[k + 1].info[a].act;
    if (w > 0) html += `<i style="width:${w}%;background:${ACT_COLOR[act] || "#333"}" title="${a}: ${ACTIVITY[act]}"></i>`;
  }
  $("acts").innerHTML = html;
  const marks = cr.iceEvents.filter((e) => e.t >= T0 && e.t <= run.tEnd && ["find", "search_start", "drill"].includes(e.type) && run.agencies.includes(e.scout));
  $("evmarks").innerHTML = marks.map((e) => {
    const col = e.type === "find" ? STATUS_COLOR.find : e.type === "drill" ? STATUS_COLOR.drilled : SCOUT_COLOR[e.scout];
    return `<b class="${e.type}" style="left:${(100 * (e.t - T0)) / span}%;background:${col}" title="${fmtDay(e.t)}: ${esc(sentence(e))}"></b>`;
  }).join("");
}

function bind() {
  $("play").onclick = () => toggle();
  $("speed").onchange = (e) => { state.speed = Number(e.target.value); };
  $("scrub").oninput = (e) => { state.t = Number(e.target.value); render(); };
  $("fill-btn").onclick = () => { state.fill = !state.fill; vis(); };
  $("truth-btn").onclick = () => { state.truth = !state.truth; vis(); render(); world.setFollow(state.follow); };
  $("cam-btn").onclick = () => follow(state.follow === "site" ? sortie.scout : "site");
  $("next-search").onclick = nextSearch;
  $("hud").addEventListener("click", (e) => { const li = e.target.closest(".fleet li[data-s]"); if (li) follow(li.dataset.s); });
  window.addEventListener("keydown", (e) => {
    if (e.target.tagName === "SELECT") return;
    if (e.code === "Space") { toggle(); e.preventDefault(); }
    if (e.key === "f" || e.key === "F") { state.fill = !state.fill; vis(); }
  });
}

function follow(a) {
  state.follow = a;
  world.setFollow(a);
  vis();
  acts();
  hudAt = 0;
  hud();
}

// Jump to just before the next cross-check and follow the searching Scout.
function nextSearch() {
  const s = searches.map((x) => x.start).filter((e) => e.t > state.t + 60).sort((p, q) => p.t - q.t)[0]
    || searches.map((x) => x.start).sort((p, q) => p.t - q.t)[0];
  if (!s) return;
  state.t = Math.max(run.times[0], s.t - 120);
  follow(s.scout);
  if (state.speed > 200) { state.speed = 200; $("speed").value = "200"; }
  render();
}

function toggle(on = !state.playing) {
  if (on && state.t >= run.tEnd) state.t = run.times[0];
  state.playing = on;
  $("play").textContent = on ? "Pause" : "Play";
}

// Showcase run: the first-person frames the followed Scout's navigation read, newest at or before t.
let fpFrames = null, fpShown = null;
function fpInit() {
  const by = cr.rec.frames && cr.rec.frames.by_sortie;
  if (!by) return;
  fpFrames = {};
  for (const s of cr.sorties) for (const [t, f] of by[s.sortie_id] || []) (fpFrames[s.scout] ||= []).push([t, f]);
  for (const k in fpFrames) fpFrames[k].sort((p, q) => p[0] - q[0]);
  $("fpcam").hidden = false;
}
function fpUpdate() {
  if (!fpFrames) return;
  const a = state.follow === "site" ? sortie.scout : state.follow;
  const list = fpFrames[a] || [];
  let lo = 0, hi = list.length - 1, k = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (list[m][0] <= state.t) { k = m; lo = m + 1; } else hi = m - 1; }
  const key = k < 0 ? `${a}:none` : `${a}:${list[k][1]}`;
  if (key === fpShown) return;
  fpShown = key;
  if (k < 0) {
    $("fp-img").removeAttribute("src");
    $("fp-cap").textContent = `${a}: no camera frame yet (at the dock)`;
  } else {
    $("fp-img").src = cr.dir + list[k][1];
    $("fp-cap").textContent = `${a}'s lamp camera: the frame its navigation read, ${fmtDay(list[k][0])}`;
  }
}

function render() {
  world.currentT = state.t;
  world.update(state.t, { tracks: true, particles: false });
  $("scrub").value = state.t;
  const T0 = run.times[0], dt = state.t - T0;
  const h = Math.floor(dt / 3600), m = Math.floor((dt % 3600) / 60);
  $("clock").textContent = `T+${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")} · ${fmtDay(state.t)}`;
  for (const s of searches) {
    const t0 = s.start.t, t1 = s.end ? s.end.t : t0;
    const on = state.t >= t0 && state.t <= t1 + SEARCH_LABEL_AFTER_S;
    s.ring.visible = on && state.t <= t1;
    s.lbl.visible = on;
    s.lbl.element.style.display = on ? "" : "none";
    const res = state.t > t1 && s.end ? ` · ${s.end.result === "confirmed" ? "confirmed" : "not confirmed"}` : "";
    s.lbl.element.innerHTML = `<span>${s.start.scout} ${state.t <= t1 ? "searching" : "searched"} ${s.find.scout}'s find ${s.find.find_id}${res}</span>`;
  }
  fpUpdate();
  hud();
}

function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }

function sentence(e) {
  const finder = (id) => { const f = cr.iceEvents.find((x) => x.type === "find" && x.find_id === id); return f ? f.scout : "?"; };
  switch (e.type) {
    case "launch": return `${e.scout} launched (${e.sortie_id})`;
    case "dock_arrive": return `${e.scout} home at the dock`;
    case "find": return `${e.scout} found ice: find ${e.find_id}, ${fmtNum(e.reading_wt, 1)} wt%`;
    case "search_start": return `${e.scout} started searching ${finder(e.find_id)}'s find ${e.find_id}`;
    case "search_end": return `${e.scout}: find ${e.find_id} ${e.result === "confirmed" ? "confirmed" : "not confirmed"}`;
    case "drill": return `${e.scout} drilled find ${e.find_id}`;
    case "fault": return `${e.scout}: ${e.module} fault`;
    case "turn_back": return `${e.scout} turned back (${e.reason})`;
    case "crater_added": return `dock: crater ${e.crater_id} added to the map`;
    default: return `${e.scout || "dock"}: ${e.type}`;
  }
}

// The cross-check running now, or just finished, in one line at the top of the HUD.
function checkNow() {
  const s = searches.filter((x) => state.t >= x.start.t && state.t <= (x.end ? x.end.t : x.start.t) + SEARCH_LABEL_AFTER_S)
    .sort((p, q) => q.start.t - p.start.t)[0];   // the latest one: a running search over one just finished
  if (!s) return "";
  const done = s.end && state.t > s.end.t;
  const res = done ? (s.end.result === "confirmed" ? " It is confirmed." : " It is not confirmed.") : "";
  return `<div class="xcheck" style="border-color:${SCOUT_COLOR[s.start.scout]}">Cross-check: ${s.start.scout} ${done ? "searched" : "is searching"} around ${s.find.scout}'s find ${s.find.find_id}, within ${fmtNum(s.start.radius_m, 1)} m.${res}</div>`;
}

const FEED_TYPES = new Set(["launch", "dock_arrive", "find", "search_start", "search_end", "drill", "fault", "turn_back"]);

let hudAt = 0, feedEvents = null;
function hud() {
  // Rebuilt at most 5 times a second while playing, so a click on a Scout row lands.
  const now = performance.now();
  if (state.playing && now - hudAt < 200) return;
  hudAt = now;
  const k = run.stepAt(state.t), nxt = run.steps[Math.min(k + 1, run.steps.length - 1)];
  const a = state.follow === "site" ? sortie.scout : state.follow;
  const r = run.steps[k].rovers[a], inf = nxt.info[a];
  const tp = run.poses.truth[a][k];
  const err = Math.hypot(r.estimate[0] - tp[0], r.estimate[1] - tp[1]);
  const finds = run.findsAt(k);
  const counts = ["find", "confirmed", "unconfirmed", "drilled"].map((s) => [s, finds.filter((f) => f.status === s).length]).filter(([, n]) => n);
  const so = inf.sortie;
  const out = inf.act !== DOCKED;
  for (const b of run.agencies) {
    const tag = world.rovers[b] && world.rovers[b].tag;
    if (tag) tag.innerHTML = `<span>${b}${nxt.info[b].act === DOCKED ? " · docked" : ""}</span>`;
  }
  const fleet = run.agencies.map((b) => {
    const i = nxt.info[b], rb = run.steps[k].rovers[b];
    return `<li data-s="${b}" class="${b === a && state.follow !== "site" ? "cur" : ""}" title="Follow ${b}">
      <i class="sdot" style="background:${SCOUT_COLOR[b]}"></i><b>${b}</b>
      <span>${i.act === DOCKED ? "at the dock" : ACTIVITY[i.act]}</span>
      <span class="num">${i.act === DOCKED ? "" : `±${fmtNum(rb.sigma, 1)} m`}</span></li>`;
  }).join("");
  feedEvents ||= cr.events.filter((e) => FEED_TYPES.has(e.type) && run.agencies.includes(e.scout));
  const feed = feedEvents.filter((e) => e.t <= state.t && e.t >= run.times[0] - 1)
    .slice(-5).reverse().map((e) => `<li><span class="num">${fmtDay(e.t).replace("day ", "d")}</span> ${esc(sentence(e))}</li>`).join("");
  $("hud").innerHTML = `<h3><i class="sdot" style="background:${SCOUT_COLOR[a]}"></i>${state.follow === "site" ? "Whole sortie" : `Following ${a}`}
      ${so ? `<span class="dim" style="font-weight:400">${so.sortie_id}</span>` : ""}
      ${so && so.sortie_id === sortie.sortie_id ? `<span class="outcome ${sortie.outcome}">${OUTCOME_WORD[sortie.outcome] || sortie.outcome}</span>` : ""}</h3>
    <div class="act">${out ? ACTIVITY[inf.act] : "at the dock"}</div>
    ${checkNow()}
    ${out ? `<dl class="kv">
      <dt>Battery</dt><dd>${fmtNum(inf.energy, 2)} kWh</dd>
      <dt>Position bound (95%)</dt><dd>±${fmtNum(r.sigma, 1)} m</dd>
      ${state.truth ? `<dt style="color:var(--truth)">True error (hidden)</dt><dd style="color:var(--truth)">${fmtNum(err, 1)} m</dd>` : ""}
    </dl>` : ""}
    <h4>Scouts out in this sortie's window</h4>
    <ul class="fleet">${fleet}</ul>
    <dl class="kv" style="margin-top:6px"><dt>Ice map</dt><dd>${counts.map(([s, n]) => `<span style="color:${STATUS_COLOR[s]}">${n} ${s}</span>`).join(", ") || "empty"}</dd></dl>
    <h4>Just now</h4>
    <ul class="feed">${feed || "<li class='dim'>Nothing yet</li>"}</ul>
    <div class="note">${state.truth ? '<span style="color:var(--truth)">Hidden truth shown: Scouts are drawn where they really were. White lines and dashed rings: where they really drove and the real ice.</span>' : "Scouts are drawn where they believed they were."} Coloured lines: estimated paths. Rings: 95% position bounds; a dashed ring is a search around another Scout's find. Saved poses only; nothing is re-simulated.</div>`;
}

main();
